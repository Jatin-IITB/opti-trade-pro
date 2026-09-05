"""Run the historical chain backfill against Upstox.

Single expiry, explicit dates:
    .venv/bin/python scripts/run_backfill.py 2026-09-01 2026-08-25 2026-09-01

Many expiries, constant tenor (the useful mode):
    .venv/bin/python scripts/run_backfill.py --expiries 50

Kept as a script rather than a route: a backfill is hundreds of broker calls
and a write to the snapshot store, so it should be an explicit, supervised act
rather than something a dashboard button can trigger.

Two scoping rules matter more than they look.

**Constant tenor.** Reconstructing every day of an expiry's life sweeps
time-to-expiry from 7 days down to 0, and 0-DTE implied vol is not comparable
with 7-DTE implied vol — a VRP series built that way varies with tenor as much
as with the market. Only days inside ``--dte-min .. --dte-max`` are rebuilt, so
each row of the series describes roughly the same option.

**Strike window.** Far wings rarely trade, so they cost a request each and
return nothing, while dragging the coverage ratio down and hiding genuinely
thin chains. Restricting to a band around spot makes the run faster *and* the
coverage number more honest.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from options_trading.config.settings import settings
from options_trading.services.backfill_service import (
    BackfillConfig,
    HistoricalChainBackfill,
    candles_to_epoch_frame,
    contracts_from_frame,
    spot_lookup_from_candles,
)
from options_trading.services.token_provider import TokenProvider
from options_trading.utils.http import get_session
from optitrade.data import SnapshotStore

IST = ZoneInfo("Asia/Kolkata")
UNDERLYING_KEY = "NSE_INDEX|Nifty 50"
CANDLE_COLUMNS = ["timestamp", "open", "high", "low", "close", "volume", "open_interest"]
SPOT_COLUMNS = ["timestamp", "open", "high", "low", "close", "volume", "oi"]


def tenor_days(expiry: date, dte_min: int, dte_max: int) -> list[date]:
    """Weekdays whose distance to ``expiry`` lies in the requested band."""
    days = []
    for offset in range(dte_min, dte_max + 1):
        day = expiry - timedelta(days=offset)
        if day.weekday() < 5:
            days.append(day)
    return sorted(days)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("expiry", nargs="?", help="single-expiry mode: ISO expiry date")
    parser.add_argument("first_day", nargs="?")
    parser.add_argument("last_day", nargs="?")
    parser.add_argument("--expiries", type=int, help="multi-expiry mode: how many, newest first")
    parser.add_argument("--dte-min", type=int, default=4, help="closest to expiry to rebuild")
    parser.add_argument("--dte-max", type=int, default=8, help="furthest from expiry to rebuild")
    parser.add_argument("--strike-window", type=float, default=0.08, help="fraction around spot")
    parser.add_argument("--target-days", type=int, help="stop once the store holds this many days")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    provider = TokenProvider()
    token = asyncio.run(provider.get())
    kind = "Analytics Token" if settings.upstox_analytics_token else "daily OAuth token"
    print(f"token resolved: {kind}")
    headers = {"Accept": "application/json", "Authorization": f"Bearer {token}"}
    session = get_session()

    store_root = (
        Path("/tmp/backfill_dryrun") if args.dry_run else Path(settings.snapshot_store_path)
    )
    store = SnapshotStore(store_root)
    config = BackfillConfig(instrument_key=UNDERLYING_KEY)

    def all_expiries() -> list[str]:
        response = session.get(
            settings.upstox_expired_expiries_url,
            headers=headers,
            params={"instrument_key": UNDERLYING_KEY},
            timeout=settings.api_timeout_seconds,
        )
        response.raise_for_status()
        return sorted(response.json()["data"])

    def spot_frame_for(first: date, last: date) -> pd.DataFrame:
        url = (
            f"{settings.upstox_spot_candles_url}/{UNDERLYING_KEY}"
            f"/minutes/1/{last.isoformat()}/{first.isoformat()}"
        )
        rows = (
            session.get(url, headers=headers, timeout=30).json().get("data", {}).get("candles", [])
        )
        return pd.DataFrame(rows, columns=SPOT_COLUMNS).set_index("timestamp")

    def make_contracts_fn(reference_spot: float | None):
        def contracts_fn(instrument_key: str, expiry_date: str):
            response = session.get(
                settings.upstox_expired_contracts_url,
                headers=headers,
                params={"instrument_key": instrument_key, "expiry_date": expiry_date},
                timeout=settings.api_timeout_seconds,
            )
            response.raise_for_status()
            contracts = contracts_from_frame(pd.DataFrame(response.json()["data"]))
            if reference_spot is None or args.strike_window <= 0:
                return contracts
            lo = reference_spot * (1.0 - args.strike_window)
            hi = reference_spot * (1.0 + args.strike_window)
            return [c for c in contracts if lo <= c.strike <= hi]

        return contracts_fn

    def candles_fn(instrument_key: str, first_iso: str, last_iso: str) -> pd.DataFrame:
        url = (
            f"{settings.upstox_option_candles_url}/{instrument_key}/1minute/{last_iso}/{first_iso}"
        )
        response = session.get(url, headers=headers, timeout=30)
        response.raise_for_status()
        rows = response.json().get("data", {}).get("candles", [])
        if not rows:
            raise ValueError("no candles")
        return candles_to_epoch_frame(
            pd.DataFrame(rows, columns=CANDLE_COLUMNS).set_index("timestamp")
        )

    def stored_days() -> int:
        return len({p.parent.name for p in store.list_snapshots(config.underlying)})

    if args.expiries:
        expiries = all_expiries()[-args.expiries :][::-1]  # newest first
        print(
            f"{len(expiries)} expiries, DTE {args.dte_min}-{args.dte_max}, "
            f"strikes +/-{args.strike_window:.0%}, store {store_root}"
        )
    else:
        if not (args.expiry and args.first_day and args.last_day):
            parser.error("give an expiry with dates, or --expiries N")
        expiries = [args.expiry]

    started = time.time()
    total_written = 0
    for index, expiry_str in enumerate(expiries, start=1):
        expiry = date.fromisoformat(expiry_str)
        if args.expiries:
            days = tenor_days(expiry, args.dte_min, args.dte_max)
        else:
            days = [
                d
                for d in tenor_days(expiry, 0, 60)
                if date.fromisoformat(args.first_day) <= d <= date.fromisoformat(args.last_day)
            ]
        if not days:
            continue

        reference = None
        try:
            lookup = spot_lookup_from_candles(spot_frame_for(min(days), max(days)), 86_400.0)
            reference = lookup(
                datetime.combine(max(days), datetime.min.time(), tzinfo=IST).timestamp() + 50_000
            )
        except Exception as exc:
            print(f"  [{expiry_str}] spot unavailable ({type(exc).__name__}); no strike filter")

        spot_fn = (
            spot_lookup_from_candles(spot_frame_for(min(days), max(days)), 900.0)
            if reference is not None
            else (lambda _at: None)
        )
        backfill = HistoricalChainBackfill(
            store,
            config,
            contracts_fn=make_contracts_fn(reference),
            candles_fn=candles_fn,
            spot_fn=spot_fn,
        )
        try:
            results = backfill.run(expiry_str, days, IST)
        except Exception as exc:
            print(f"  [{index}/{len(expiries)}] {expiry_str} FAILED: {type(exc).__name__}: {exc}")
            continue

        written = [r for r in results if r.written]
        total_written += len(written)
        coverage = (
            f"{min(r.coverage for r in written):.0%}-{max(r.coverage for r in written):.0%}"
            if written
            else "-"
        )
        have = stored_days()
        print(
            f"  [{index}/{len(expiries)}] {expiry_str}: {len(written)}/{len(results)} written, "
            f"coverage {coverage}, store now {have} days ({time.time() - started:.0f}s)"
        )
        if args.target_days and have >= args.target_days:
            print(f"reached target of {args.target_days} days; stopping")
            break

    print(
        f"\ndone in {time.time() - started:.0f}s | {total_written} instants | {stored_days()} days"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())

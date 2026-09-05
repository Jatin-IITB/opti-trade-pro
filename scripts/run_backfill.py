"""Run the historical chain backfill against Upstox.

Usage:
    .venv/bin/python scripts/run_backfill.py <expiry> <first-day> <last-day> [--dry-run]

    .venv/bin/python scripts/run_backfill.py 2026-09-01 2026-08-25 2026-09-01

Dates are ISO ``YYYY-MM-DD``; trading days are the weekdays in the range.
Kept as a script rather than a route: a backfill makes hundreds of broker
calls and writes to the snapshot store, so it should be an explicit,
supervised act rather than something a dashboard can trigger.
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


def weekdays(first: date, last: date) -> list[date]:
    """Weekdays in the inclusive range. Exchange holidays fall out naturally:
    they simply yield no candles and are reported as zero coverage."""
    days, day = [], first
    while day <= last:
        if day.weekday() < 5:
            days.append(day)
        day += timedelta(days=1)
    return days


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("expiry")
    parser.add_argument("first_day")
    parser.add_argument("last_day")
    parser.add_argument("--dry-run", action="store_true", help="fetch and report, write nothing")
    args = parser.parse_args()

    first = date.fromisoformat(args.first_day)
    last = date.fromisoformat(args.last_day)
    days = weekdays(first, last)

    # Through TokenProvider, not AuthService directly: the provider is what
    # knows about the Analytics Token. Calling the OAuth path here would
    # bypass a configured year-long token and fail at 03:30 like everything
    # else — which is exactly the bug this line replaces.
    provider = TokenProvider()
    token = await provider.get()
    print(
        "token resolved: "
        + (
            "Analytics Token (no daily login)"
            if not provider.needs_reauth and settings.upstox_analytics_token
            else "daily OAuth token"
        )
    )
    headers = {"Accept": "application/json", "Authorization": f"Bearer {token}"}
    session = get_session()

    def contracts_fn(instrument_key: str, expiry_date: str):
        response = session.get(
            settings.upstox_expired_contracts_url,
            headers=headers,
            params={"instrument_key": instrument_key, "expiry_date": expiry_date},
            timeout=settings.api_timeout_seconds,
        )
        response.raise_for_status()
        return contracts_from_frame(pd.DataFrame(response.json()["data"]))

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

    spot_url = (
        f"{settings.upstox_spot_candles_url}/{UNDERLYING_KEY}"
        f"/minutes/1/{last.isoformat()}/{first.isoformat()}"
    )
    spot_response = session.get(spot_url, headers=headers, timeout=30)
    spot_rows = spot_response.json().get("data", {}).get("candles", [])
    spot_frame = pd.DataFrame(spot_rows, columns=SPOT_COLUMNS).set_index("timestamp")
    spot_fn = spot_lookup_from_candles(spot_frame, 900.0)
    print(f"index cross-check: {len(spot_rows)} spot candles loaded")

    store_root = Path(settings.snapshot_store_path)
    store = SnapshotStore(store_root if not args.dry_run else Path("/tmp/backfill_dryrun"))
    config = BackfillConfig(instrument_key=UNDERLYING_KEY)

    print(f"expiry {args.expiry} | {len(days)} weekdays {first} .. {last}")
    print(f"snapshot times {config.snapshot_times} IST | store {store_root}")
    started = time.time()

    backfill = HistoricalChainBackfill(
        store,
        config,
        contracts_fn=contracts_fn,
        candles_fn=candles_fn,
        spot_fn=spot_fn,
    )
    results = backfill.run(args.expiry, days, IST)

    written = [r for r in results if r.written]
    print(f"\ncompleted in {time.time() - started:.0f}s")
    print(f"written {len(written)} of {len(results)} instants")
    for result in results:
        stamp = datetime.fromtimestamp(result.at_epoch, IST).strftime("%a %d %b %H:%M")
        mark = "OK " if result.written else "-- "
        detail = f"coverage {result.coverage:.0%}" if result.written else (result.reason or "")
        print(f"  {mark}{stamp}  {detail}")
    return 0 if written else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

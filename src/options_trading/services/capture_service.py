# src/options_trading/services/capture_service.py
"""Upstox chain-capture adapter: live option chains -> RawChain -> clean Parquet snapshots.

Completes flagship phase 0 (ADR-013). :class:`UpstoxCaptureSource` implements the
``optitrade.data.CaptureSource`` protocol over the Upstox v2 option-chain API, and
:func:`capture_and_store` runs the full pipeline: fetch -> hygiene filters -> snapshot
store. The quant core (``optitrade``) stays broker-free; this module is the one-way
bridge from the platform into it (ADR-002).
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import date, datetime
from datetime import time as dt_time
from typing import Any
from zoneinfo import ZoneInfo

from optitrade.core.types import OptionType
from optitrade.data import (
    DEFAULT_FILTER_CONFIG,
    CaptureSource,
    FilterConfig,
    RawChain,
    RawQuote,
    SnapshotStore,
    filter_chain,
)

from ..api.tools.option_chain_live import fetch_live_option_chain
from ..config.settings import settings
from ..utils.exceptions import DataQualityError

IST = ZoneInfo("Asia/Kolkata")
NSE_CLOSE_TIME = dt_time(15, 30)  # NSE close; index options settle at this instant
SECONDS_PER_YEAR = 365.0 * 86_400.0  # ACT/365 day count (ADR-003)
# Intraday floor: on expiry day near/after the close the raw fraction hits zero or
# goes negative, and a zero expiry breaks every vol solver downstream. One hour
# (1/365/24 years) is the smallest horizon the capture loop can act on.
MIN_EXPIRY_YEARS = 1.0 / 365.0 / 24.0
# Used only if the settings object lacks a risk_free_rate field.
FALLBACK_RISK_FREE_RATE = 0.065


def expiry_year_fraction(expiry_day: date, now_epoch: float) -> float:
    """ACT/365 year fraction from ``now_epoch`` to ``expiry_day`` 15:30 IST.

    Floored at :data:`MIN_EXPIRY_YEARS` (one hour) so expiry-day snapshots
    never produce a zero or negative time-to-expiry, which would break every
    vol solver downstream.

    Module-level so the live capture path and the historical backfill share
    one definition. Two copies of a day-count convention is two chances to
    disagree, and a surface fitted on one and replayed against the other would
    show the difference as a vol move nobody made.
    """
    expiry_epoch = datetime.combine(expiry_day, NSE_CLOSE_TIME, tzinfo=IST).timestamp()
    return max((expiry_epoch - now_epoch) / SECONDS_PER_YEAR, MIN_EXPIRY_YEARS)


# Authoritative Upstox chain-row field aliases (mirrors MarketDataService's mapping).
_STRIKE_KEYS = ("strike_price", "strike", "strikePrice")
_CALL_KEYS = ("call_options", "call_option", "CE", "call")
_PUT_KEYS = ("put_options", "put_option", "PE", "put")
_LTP_KEYS = ("ltp", "close_price")
_BID_KEYS = ("bid_price", "bid", "best_bid_price")
_ASK_KEYS = ("ask_price", "ask", "best_ask_price")
_VOLUME_KEYS = ("volume", "vol")
_OI_KEYS = ("open_interest", "oi")


@dataclass(frozen=True)
class CaptureReport:
    """Outcome of one capture run: where the snapshot landed and what survived."""

    path: str
    n_raw: int
    n_clean: int
    rejection_stats: dict[str, int]
    spot: float
    timestamp: float


def _first_present(mapping: dict[str, Any], keys: tuple[str, ...]) -> Any | None:
    """Return the value of the first key present with a non-None value."""
    for key in keys:
        value = mapping.get(key)
        if value is not None:
            return value
    return None


def _extract_rows(data: Any) -> list[dict[str, Any]]:
    """Unpack the Upstox chain payload into a list of strike rows.

    ``fetch_live_option_chain`` returns ``payload["data"]``, which is a list in
    the documented v2 shape; defensively also accepts the wrapper-dict shapes
    handled by MarketDataService (``data`` / ``option_chain`` / ``data.rows``).
    """
    if isinstance(data, list):
        return [row for row in data if isinstance(row, dict)]
    if isinstance(data, dict):
        for key in ("data", "option_chain"):
            value = data.get(key)
            if isinstance(value, list):
                return [row for row in value if isinstance(row, dict)]
        inner = data.get("data")
        if isinstance(inner, dict) and isinstance(inner.get("rows"), list):
            return [row for row in inner["rows"] if isinstance(row, dict)]
    return []


def _extract_spot(rows: list[dict[str, Any]], underlying: str, expiry_date: str) -> float:
    """Spot from the first row carrying a positive ``underlying_spot_price``.

    Fails closed: a chain without spot cannot anchor moneyness or forwards, so
    this raises instead of guessing.
    """
    for row in rows:
        value = row.get("underlying_spot_price")
        if value is not None and float(value) > 0.0:
            return float(value)
    raise DataQualityError(
        f"No underlying_spot_price in any of the {len(rows)} chain rows for "
        f"{underlying} expiry {expiry_date}; refusing to build a RawChain without spot"
    )


def _map_leg(strike: float, expiry: float, option_type: OptionType, leg: Any) -> RawQuote | None:
    """Map one Upstox leg dict (CE or PE) to a :class:`RawQuote`.

    ``ltp_age_seconds`` is always 0.0: Upstox chain payloads do not expose the
    last-trade age, so quotes are reported as fresh and the stale filter judges
    activity by volume/OI alone.
    """
    if not isinstance(leg, dict):
        return None
    market_data = leg.get("market_data")
    if not isinstance(market_data, dict):
        return None
    return RawQuote(
        strike=strike,
        expiry=expiry,
        option_type=option_type,
        bid=float(_first_present(market_data, _BID_KEYS) or 0.0),
        ask=float(_first_present(market_data, _ASK_KEYS) or 0.0),
        ltp=float(_first_present(market_data, _LTP_KEYS) or 0.0),
        volume=int(_first_present(market_data, _VOLUME_KEYS) or 0),
        open_interest=int(_first_present(market_data, _OI_KEYS) or 0),
        bid_qty=int(market_data.get("bid_qty") or 0),
        ask_qty=int(market_data.get("ask_qty") or 0),
        ltp_age_seconds=0.0,
    )


class UpstoxCaptureSource:
    """``optitrade.data.CaptureSource`` adapter over the Upstox live chain API.

    One instance is bound to a single underlying instrument key and expiry date;
    ``fetch_chain`` maps every CE/PE leg of the live payload into ``RawQuote``s in
    deterministic order (ascending strike, calls before puts). ``now_fn`` is
    injectable so tests can pin the clock (CLAUDE.md: deterministic tests).
    """

    def __init__(
        self,
        access_token: str | None = None,
        instrument_key: str = "",
        expiry_date: str = "",
        rate: float | None = None,
        now_fn: Callable[[], float] = time.time,
        token_fn: Callable[[], str] | None = None,
    ) -> None:
        """``token_fn`` is read per fetch; ``access_token`` pins one string.

        ``fetch_chain`` runs in a worker thread, so the token source must be
        synchronous. Pass ``TokenProvider.cached`` here and refresh the
        provider on the event loop before each capture.
        """
        if token_fn is None and access_token is None:
            raise ValueError("Either token_fn or access_token must be provided")
        self._token_fn = token_fn if token_fn is not None else (lambda: access_token or "")
        self._instrument_key = instrument_key
        self._expiry_date = expiry_date
        self._expiry_day = self._parse_expiry_date(expiry_date)
        if rate is None:
            rate = float(getattr(settings, "risk_free_rate", FALLBACK_RISK_FREE_RATE))
        self._rate = rate
        self._now_fn = now_fn

    @staticmethod
    def _parse_expiry_date(expiry_date: str) -> date:
        try:
            return datetime.strptime(expiry_date, "%Y-%m-%d").date()
        except ValueError as exc:
            raise DataQualityError(
                f"expiry_date {expiry_date!r} is not in YYYY-MM-DD format"
            ) from exc

    def _expiry_year_fraction(self, now_epoch: float) -> float:
        """ACT/365 year fraction from ``now_epoch`` to expiry-day 15:30 IST."""
        return expiry_year_fraction(self._expiry_day, now_epoch)

    def fetch_chain(self, underlying: str) -> RawChain:
        """Fetch the live chain and return it as an unfiltered :class:`RawChain`.

        Rows without a strike are skipped; rows may carry only one leg. Quotes
        are ordered by (strike ascending, call before put) so repeated captures
        of the same book serialize identically.
        """
        data = fetch_live_option_chain(self._instrument_key, self._expiry_date, self._token_fn())
        rows = _extract_rows(data)
        now = self._now_fn()
        expiry = self._expiry_year_fraction(now)
        spot = _extract_spot(rows, underlying, self._expiry_date)

        struck_rows: list[tuple[float, dict[str, Any]]] = []
        for row in rows:
            strike_value = _first_present(row, _STRIKE_KEYS)
            if strike_value is None:
                continue  # a row without a strike cannot be priced
            struck_rows.append((float(strike_value), row))

        quotes: list[RawQuote] = []
        for strike, row in sorted(struck_rows, key=lambda pair: pair[0]):
            call = _map_leg(strike, expiry, OptionType.CALL, _first_present(row, _CALL_KEYS))
            if call is not None:
                quotes.append(call)
            put = _map_leg(strike, expiry, OptionType.PUT, _first_present(row, _PUT_KEYS))
            if put is not None:
                quotes.append(put)

        return RawChain(
            underlying=underlying,
            spot=spot,
            rate=self._rate,
            timestamp=now,
            quotes=tuple(quotes),
        )


def capture_and_store(
    source: CaptureSource,
    store: SnapshotStore,
    underlying: str,
    config: FilterConfig | None = None,
    chain: RawChain | None = None,
) -> CaptureReport:
    """Filter a chain, persist only the clean quotes, and report.

    Pass ``chain`` when the caller has already fetched one, so a single
    broker round-trip serves both the stored snapshot and downstream
    analytics. Fetching twice would double the outbound call rate *and* leave
    the persisted history describing a different instant from the broadcast
    dashboard.

    Policy: the snapshot store is the *clean* history. Rejected quotes are
    dropped (their counts survive in ``rejection_stats``) because the raw
    payload can always be re-fetched from the broker, while every downstream
    consumer — surface fits, backtests, replay — must be able to trust that a
    stored snapshot needs no re-filtering. Chain metadata (underlying, spot,
    rate, timestamp, dividend yield) is preserved verbatim.
    """
    if chain is None:
        chain = source.fetch_chain(underlying)
    result = filter_chain(chain, config if config is not None else DEFAULT_FILTER_CONFIG)
    clean_chain = replace(chain, quotes=result.clean)
    path = store.write(clean_chain)
    return CaptureReport(
        path=str(path),
        n_raw=len(chain.quotes),
        n_clean=len(result.clean),
        rejection_stats={name: n for name, n in result.stats.items() if name != "clean"},
        spot=chain.spot,
        timestamp=chain.timestamp,
    )


__all__ = ["CaptureReport", "UpstoxCaptureSource", "capture_and_store"]

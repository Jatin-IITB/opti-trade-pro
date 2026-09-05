"""Rebuild historical option chains from Upstox expired-instrument candles.

The capture pipeline can only record the present, so history accrues one
session at a time: the strategy layer needs 3 stored days for a VRP signal and
11 for a walk-forward, which is two weeks of unbroken attendance before a
backtest can say anything. Upstox exposes settled contracts and their candles,
so that history can be reconstructed instead of waited for.

What comes back is **not** what capture records, and the difference is the
whole design. A candle is traded data: open/high/low/close, volume and open
interest for a one-minute bar. A live capture is quoted data: a two-sided book
with bid, ask and depth at an instant. Reconstruction therefore cannot produce
a spread, and every chain built here is tagged
:attr:`~optitrade.data.models.ChainSource.BACKFILL` so nothing downstream can
mistake one for the other.

Split deliberately in two: :func:`reconstruct_chain` is pure and takes candles
it is handed, so the assembly rules are testable without a broker; the fetching
and persistence live in :class:`HistoricalChainBackfill`, which takes its
network calls as injected callables for the same reason.
"""

from __future__ import annotations

import logging
import math
import statistics
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

import pandas as pd

from optitrade.core.types import OptionType
from optitrade.data import SnapshotStore
from optitrade.data.models import ChainSource, RawChain, RawQuote

from ..utils.exceptions import DataQualityError
from .capture_service import IST, expiry_year_fraction

logger = logging.getLogger(__name__)

#: A reconstructed bar's price is the close of the interval *containing or
#: preceding* the target instant, so it is always at least slightly stale. The
#: real age is recorded on each quote, which keeps ``stale_quote`` doing useful
#: work on backfilled chains even though the spread filters cannot.
DEFAULT_MAX_BAR_AGE_SECONDS = 900.0


@dataclass(frozen=True)
class HistoricalContract:
    """One settled option contract, as listed by the expired-contracts API."""

    instrument_key: str
    strike: float
    option_type: OptionType


@dataclass(frozen=True)
class ReconstructionReport:
    """What a single reconstruction used and what it discarded.

    Kept alongside the chain because the losses are the interesting part: a
    chain assembled from 40 of 150 contracts is a thin strip, not a surface,
    and the caller should be able to tell without re-deriving it.
    """

    n_contracts: int
    n_quotes: int
    n_never_traded: int
    n_too_stale: int

    @property
    def coverage(self) -> float:
        """Fraction of listed contracts that produced a usable quote."""
        return self.n_quotes / self.n_contracts if self.n_contracts else 0.0


def implied_forward_from_parity(
    quotes: Sequence[RawQuote],
    rate: float,
    *,
    min_pairs: int = 5,
) -> float | None:
    """Back the forward out of put-call parity: ``F = K + (C - P)e^{rT}``.

    Measured against real 2026-09-01 data, this is the only trustworthy
    anchor available to a reconstruction. Upstox's index minute candles go
    stale — observed holding one value across seven consecutive minutes and
    then jumping 75 points in a single bar — so a chain anchored to the index
    close carried an ~80 point error that did not decay even five minutes
    before expiry, where the basis must be zero.

    The options themselves are internally consistent to about 1.2 points
    across 27 strikes, so parity across many strikes is both more accurate and
    self-checking. The median is taken rather than the mean: one stale print
    on an illiquid wing moves a mean and does not move a median.

    Returns None when too few strikes have both legs, because a forward from
    two pairs is a guess wearing a number's clothing.
    """
    by_strike: dict[float, dict[OptionType, float]] = {}
    for quote in quotes:
        if quote.ltp > 0.0:
            by_strike.setdefault(quote.strike, {})[quote.option_type] = quote.ltp

    forwards: list[float] = []
    for strike, legs in by_strike.items():
        call, put = legs.get(OptionType.CALL), legs.get(OptionType.PUT)
        if call is None or put is None:
            continue
        expiry = next(q.expiry for q in quotes if q.strike == strike)
        forwards.append(strike + (call - put) * math.exp(rate * expiry))

    if len(forwards) < min_pairs:
        return None
    return float(statistics.median(forwards))


def implied_spot_from_parity(
    quotes: Sequence[RawQuote],
    rate: float,
    dividend_yield: float = 0.0,
    *,
    min_pairs: int = 5,
) -> float | None:
    """The spot consistent with the option-implied forward.

    Stored rather than the forward itself so the field keeps the meaning the
    live capture path gives it: downstream code computes ``F = S e^{(r-q)T}``
    from it, and feeding a forward in would compound the carry twice.
    """
    forward = implied_forward_from_parity(quotes, rate, min_pairs=min_pairs)
    if forward is None:
        return None
    expiry = min(q.expiry for q in quotes)
    return forward * math.exp(-(rate - dividend_yield) * expiry)


def _bar_at_or_before(candles: pd.DataFrame, at_epoch: float) -> tuple[float, float] | None:
    """Return ``(close, bar_epoch)`` for the latest bar at or before ``at_epoch``.

    Returns None when the contract had not traded yet at that instant, which
    is normal for far wings early in a session. A missing bar is skipped
    rather than filled: carrying a later price backwards would invent a trade,
    and interpolating between wings would invent a smile.
    """
    if candles.empty:
        return None
    epochs = candles.index
    mask = epochs <= at_epoch
    if not mask.any():
        return None
    row = candles[mask].iloc[-1]
    return float(row["close"]), float(epochs[mask][-1])


def reconstruct_chain(
    underlying: str,
    expiry_day: date,
    at_epoch: float,
    spot: float | None,
    rate: float,
    contracts: Sequence[HistoricalContract],
    candles: Mapping[str, pd.DataFrame],
    *,
    dividend_yield: float = 0.0,
    max_bar_age_seconds: float = DEFAULT_MAX_BAR_AGE_SECONDS,
) -> tuple[RawChain, ReconstructionReport]:
    """Assemble one historical chain from per-contract candles. Pure.

    ``candles`` maps instrument key to a frame indexed by epoch seconds with a
    ``close`` column, plus optional ``volume`` and ``open_interest``.

    Bid and ask are both set to the traded price. That is a statement, not a
    convenience: no book was recorded, so the only defensible spread is none
    at all. The alternative — inventing a spread from a rule of thumb — would
    put a fabricated number into the same field a live capture fills with an
    observed one. Consumers that need a spread must check ``source`` first.

    ``spot`` is the index level *if one can be trusted*; pass None to derive it
    from put-call parity instead, which is what the backfill does. Upstox's
    index minute candles were measured going stale for minutes at a time, and
    a chain anchored to one mislabels every moneyness and biases every implied
    vol while looking entirely ordinary.
    """
    quotes: list[RawQuote] = []
    n_never_traded = 0
    n_too_stale = 0
    expiry = expiry_year_fraction(expiry_day, at_epoch)

    for contract in contracts:
        frame = candles.get(contract.instrument_key)
        if frame is None:
            n_never_traded += 1
            continue
        bar = _bar_at_or_before(frame, at_epoch)
        if bar is None:
            n_never_traded += 1
            continue
        close, bar_epoch = bar
        age = at_epoch - bar_epoch
        if age > max_bar_age_seconds:
            n_too_stale += 1
            continue

        row = frame.loc[bar_epoch] if bar_epoch in frame.index else None
        quotes.append(
            RawQuote(
                strike=contract.strike,
                expiry=expiry,
                option_type=contract.option_type,
                # No book was recorded. Both sides carry the traded price so
                # the mid is the trade, and the spread is honestly zero.
                bid=close,
                ask=close,
                ltp=close,
                volume=int(row["volume"]) if row is not None and "volume" in row else 0,
                open_interest=(
                    int(row["open_interest"]) if row is not None and "open_interest" in row else 0
                ),
                bid_qty=0,
                ask_qty=0,
                # Real, not zero: this is what keeps the staleness filter
                # meaningful on a chain whose spread filters cannot bite.
                ltp_age_seconds=age,
            )
        )

    resolved_spot = spot
    if resolved_spot is None:
        resolved_spot = implied_spot_from_parity(quotes, rate, dividend_yield)
    if resolved_spot is None or resolved_spot <= 0.0:
        # Fails closed, matching the live path: a chain without a trustworthy
        # spot cannot anchor moneyness or a forward, so it is not built at all
        # rather than built around a guess.
        raise DataQualityError(
            f"No usable spot for {underlying} at {at_epoch}: the index feed was "
            f"not supplied and only {len(quotes)} quotes were reconstructed, too "
            f"few complete call/put pairs to imply a forward"
        )

    chain = RawChain(
        underlying=underlying,
        spot=resolved_spot,
        rate=rate,
        timestamp=at_epoch,
        quotes=tuple(quotes),
        dividend_yield=dividend_yield,
        source=ChainSource.BACKFILL,
    )
    report = ReconstructionReport(
        n_contracts=len(contracts),
        n_quotes=len(quotes),
        n_never_traded=n_never_traded,
        n_too_stale=n_too_stale,
    )
    return chain, report


@dataclass(frozen=True)
class BackfillConfig:
    """Which chains to rebuild, and how gently to ask for them.

    ``snapshot_times`` are IST wall-clock instants to reconstruct on each
    trading day. Four spread across the session is a deliberate default: the
    VRP and walk-forward layers consume one observation per day, and a denser
    grid multiplies API load without adding a single day of history — which is
    the quantity actually in short supply.
    """

    underlying: str = "NIFTY"
    instrument_key: str = "NSE_INDEX|Nifty 50"
    interval: str = "1minute"
    snapshot_times: tuple[str, ...] = ("09:30", "11:15", "13:00", "15:25")
    rate: float = 0.065
    max_bar_age_seconds: float = DEFAULT_MAX_BAR_AGE_SECONDS
    #: Pause between contract fetches. One expiry is ~150 contracts, so a
    #: careless loop is a burst of hundreds of requests; the broker's limit is
    #: shared with the live capture path this app depends on.
    request_pause_seconds: float = 0.2
    #: Refuse to build a chain thinner than this fraction of listed contracts.
    #: A handful of quotes is not a surface, and fitting one produces a
    #: confident-looking smile from three points.
    min_coverage: float = 0.5
    #: Log when the parity-implied spot and the index feed disagree by more
    #: than this fraction. Measured at ~0.35% against real 2026-09 data, where
    #: the index feed was the one at fault, so this warns rather than rejects.
    max_spot_divergence: float = 0.002
    #: Treat days that already hold a reconstruction as done, so a long run
    #: can be resumed after a rate limit without duplicating what it wrote.
    skip_existing_backfill: bool = True


@dataclass(frozen=True)
class BackfillDayResult:
    """Outcome for one reconstructed instant."""

    at_epoch: float
    written: bool
    reason: str | None
    coverage: float


def plan_snapshot_epochs(
    trading_days: Sequence[date],
    snapshot_times: Sequence[str],
    zone: ZoneInfo,
) -> list[float]:
    """Every (day x time) instant to reconstruct, chronologically.

    Pure and timezone-explicit: the times are exchange wall-clock, and
    resolving them anywhere but the exchange's own zone silently shifts every
    snapshot by the server's offset.
    """
    epochs: list[float] = []
    for day in trading_days:
        for hhmm in snapshot_times:
            parsed = datetime.strptime(hhmm, "%H:%M").time()
            epochs.append(datetime.combine(day, parsed, tzinfo=zone).timestamp())
    return sorted(epochs)


class HistoricalChainBackfill:
    """Drives reconstruction over a date range and persists the result.

    The network calls are injected rather than imported so the orchestration —
    which is where the coverage rules, the skip rules and the write rules live
    — is testable without a broker or a token.

    Candles are fetched **once per contract** for the whole range and reused
    across every reconstructed instant. Fetching per snapshot instead would
    multiply a 150-request expiry by the number of times sampled, for data
    already in hand.
    """

    def __init__(
        self,
        store: SnapshotStore,
        config: BackfillConfig,
        *,
        contracts_fn: Callable[[str, str], Sequence[HistoricalContract]],
        candles_fn: Callable[[str, str, str], pd.DataFrame],
        spot_fn: Callable[[float], float | None],
        sleep_fn: Callable[[float], None] | None = None,
    ) -> None:
        self._store = store
        self._config = config
        self._contracts_fn = contracts_fn
        self._candles_fn = candles_fn
        self._spot_fn = spot_fn
        self._sleep = sleep_fn or time.sleep

    def _existing_live_days(self) -> set[str]:
        """UTC dates a reconstruction must not write to.

        Always includes days covered by a *live* capture: backfill never
        overwrites a captured day, because a real book beats a reconstruction
        of one and silently replacing quoted data with traded data would be
        the worst possible outcome of a tool meant to add history.

        With ``skip_existing_backfill`` it also includes days already
        reconstructed, which makes a run resumable. Without it, re-running
        over the same range appends a second set of snapshots for every
        instant, and the duplicates are indistinguishable from a genuinely
        busier session.
        """
        days: set[str] = set()
        for path in self._store.list_snapshots(self._config.underlying):
            try:
                source = self._store.read(path).source
            except Exception:
                logger.warning("Could not read %s while checking coverage; skipping", path)
                continue
            if source is ChainSource.LIVE or self._config.skip_existing_backfill:
                days.add(path.parent.name)
        return days

    def run(
        self,
        expiry_date: str,
        trading_days: Sequence[date],
        zone: ZoneInfo,
    ) -> list[BackfillDayResult]:
        """Rebuild and store every configured instant across ``trading_days``."""
        contracts = list(self._contracts_fn(self._config.instrument_key, expiry_date))
        if not contracts:
            logger.warning("No contracts listed for expiry %s; nothing to rebuild", expiry_date)
            return []

        candles: dict[str, pd.DataFrame] = {}
        first, last = min(trading_days), max(trading_days)
        for index, contract in enumerate(contracts):
            try:
                candles[contract.instrument_key] = self._candles_fn(
                    contract.instrument_key, first.isoformat(), last.isoformat()
                )
            except Exception:
                # One dead contract must not abandon the expiry; it becomes a
                # never-traded skip and shows up in the coverage number.
                logger.warning("Candles unavailable for %s", contract.instrument_key)
            if index + 1 < len(contracts):
                self._sleep(self._config.request_pause_seconds)

        skip_days = self._existing_live_days()
        expiry_day = datetime.strptime(expiry_date, "%Y-%m-%d").date()
        results: list[BackfillDayResult] = []

        for at_epoch in plan_snapshot_epochs(trading_days, self._config.snapshot_times, zone):
            day_utc = datetime.fromtimestamp(at_epoch, tz=UTC).strftime("%Y-%m-%d")
            if day_utc in skip_days:
                results.append(BackfillDayResult(at_epoch, False, "live capture exists", 0.0))
                continue

            # Spot comes from put-call parity, not the index feed. The index
            # candles are still fetched, but only to cross-check: they were
            # measured going stale for minutes at a time, so they are evidence
            # rather than truth.
            try:
                chain, report = reconstruct_chain(
                    self._config.underlying,
                    expiry_day,
                    at_epoch,
                    None,
                    self._config.rate,
                    contracts,
                    candles,
                    max_bar_age_seconds=self._config.max_bar_age_seconds,
                )
            except DataQualityError as exc:
                results.append(BackfillDayResult(at_epoch, False, str(exc)[:80], 0.0))
                continue

            index_spot = self._spot_fn(at_epoch)
            if index_spot is not None and index_spot > 0.0:
                drift = abs(chain.spot - index_spot) / index_spot
                if drift > self._config.max_spot_divergence:
                    logger.warning(
                        "Implied spot %.2f differs from the index feed %.2f by %.2f%% at %s; "
                        "keeping the implied value (the index feed is the unreliable one)",
                        chain.spot,
                        index_spot,
                        drift * 100.0,
                        datetime.fromtimestamp(at_epoch, tz=zone).isoformat(),
                    )
            if report.coverage < self._config.min_coverage:
                results.append(
                    BackfillDayResult(
                        at_epoch,
                        False,
                        f"coverage {report.coverage:.0%} below floor",
                        report.coverage,
                    )
                )
                continue

            self._store.write(chain)
            results.append(BackfillDayResult(at_epoch, True, None, report.coverage))

        written = sum(1 for r in results if r.written)
        logger.info(
            "Backfill %s expiry %s: wrote %d of %d instants",
            self._config.underlying,
            expiry_date,
            written,
            len(results),
        )
        return results


_CALL_TYPES = frozenset({"CE", "CALL", "C"})
_PUT_TYPES = frozenset({"PE", "PUT", "P"})


def contracts_from_frame(frame: pd.DataFrame) -> list[HistoricalContract]:
    """Map the expired-contracts payload to contracts, dropping what it cannot type.

    A row whose ``instrument_type`` is neither a call nor a put is skipped
    rather than defaulted. Guessing would put a put's premium on a call's
    strike, which does not fail loudly — it fits as a lopsided smile.
    """
    contracts: list[HistoricalContract] = []
    for row in frame.to_dict(orient="records"):
        raw_type = str(row.get("instrument_type", "")).strip().upper()
        if raw_type in _CALL_TYPES:
            option_type = OptionType.CALL
        elif raw_type in _PUT_TYPES:
            option_type = OptionType.PUT
        else:
            logger.warning("Skipping contract with unrecognised type %r", raw_type)
            continue
        key = row.get("instrument_key")
        strike = row.get("strike_price")
        if not key or strike is None:
            continue
        contracts.append(HistoricalContract(str(key), float(strike), option_type))
    return contracts


def candles_to_epoch_frame(frame: pd.DataFrame) -> pd.DataFrame:
    """Re-index a candle frame from timestamps onto epoch seconds.

    The reconstruction compares bar times against an epoch instant, so the
    conversion happens once here rather than inside the hot loop. Upstox
    returns tz-aware IST timestamps; ``.timestamp()`` resolves them correctly,
    and a naive index would be read as UTC and shift every bar by 5h30m.
    """
    if frame.empty:
        return frame
    index = pd.to_datetime(frame.index)
    if index.tz is None:
        index = index.tz_localize(IST)
    out = frame.copy()
    out.index = pd.Index([ts.timestamp() for ts in index], name="epoch")
    return out.sort_index()


def spot_lookup_from_candles(
    frame: pd.DataFrame, max_age_seconds: float
) -> Callable[[float], float | None]:
    """Build an as-of spot lookup over index candles.

    Returns None rather than a number when the nearest bar is older than
    ``max_age_seconds``. Anchoring a whole chain to a spot from hours earlier
    would mislabel every moneyness while looking entirely reasonable.
    """
    epoch_frame = candles_to_epoch_frame(frame)

    def lookup(at_epoch: float) -> float | None:
        if epoch_frame.empty:
            return None
        mask = epoch_frame.index <= at_epoch
        if not mask.any():
            return None
        bar_epoch = float(epoch_frame.index[mask][-1])
        if at_epoch - bar_epoch > max_age_seconds:
            return None
        value = float(epoch_frame[mask].iloc[-1]["close"])
        return value if value > 0.0 else None

    return lookup


__all__ = [
    "DEFAULT_MAX_BAR_AGE_SECONDS",
    "BackfillConfig",
    "BackfillDayResult",
    "HistoricalChainBackfill",
    "HistoricalContract",
    "ReconstructionReport",
    "candles_to_epoch_frame",
    "contracts_from_frame",
    "implied_forward_from_parity",
    "implied_spot_from_parity",
    "plan_snapshot_epochs",
    "reconstruct_chain",
    "spot_lookup_from_candles",
]

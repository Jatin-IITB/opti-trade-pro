"""History-dependent dashboard panels: VRP signal, backtest, P&L explain.

These three panels differ from every other builder in
:mod:`~options_trading.services.live_analytics` in one way that drives the
whole design: they need *history*, not a snapshot. A single option chain
cannot say whether implied vol has been rich, cannot produce an equity curve,
and cannot explain a day's P&L. They read the captured Parquet history
instead, through :class:`~optitrade.backtest.market_replay.StoreReplay`.

Two consequences:

**Cost.** A replay refits a vol surface per stored day, so it scales with the
whole captured history rather than one chain — seconds to tens of seconds for
a year, against milliseconds for the live builders. That cannot run on the 60s
capture tick and must not run on the event loop. Results are cached and
recomputed at most every ``settings.history_refresh_seconds``, off-thread.

**Absence.** A freshly installed instance has captured nothing, and the
walk-forward harness needs 11 days at the default fold settings. Every builder
here returns a payload whose ``hasHistory`` is ``False`` and whose data fields
are ``None`` until the history exists — never zeros, never a demo curve. A
Sharpe of 0.00 and "no data yet" look identical on a chart and mean opposite
things, so the distinction is carried in the payload rather than left to the
reader.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Literal

import numpy as np

from optitrade.backtest import (
    BacktestConfig,
    BacktestResult,
    StoreReplay,
    WalkForwardResult,
    max_drawdown,
    min_days_for_walk_forward,
    run_backtest,
    run_walk_forward,
)
from optitrade.data import SnapshotStore
from optitrade.hedging import BandParams
from optitrade.journal import EventLog
from optitrade.risk import RiskLimits
from optitrade.strategy import VRPConfig, VRPStrategy

from .book_snapshot_store import BookSnapshotStore
from .pnl_attribution import explain_book_pnl

logger = logging.getLogger(__name__)

# Core Greeks conventions put vega per *unit* of vol (1.0 = 100 vol points),
# so a budget stated per vol point scales by this to reach the cap's units.
_VOL_POINTS_PER_UNIT = 100.0

# StoreReplay flags days whose realized vol is the neutral prior rather than a
# measurement; those days carry vrp == 0 by construction (market_replay.py).
_RV_IS_PRIOR = "rv_is_prior"
# Fewest measured days worth plotting as a VRP time series. Two points draw a
# line segment but support no statement about a regime.
MIN_DAYS_VRP = 3
# A P&L explain needs a start and an end; one book snapshot explains nothing.
_MIN_BOOK_SNAPSHOTS = 2
_SECONDS_PER_DAY = 86_400.0


@dataclass(frozen=True)
class HistoryAnalyticsConfig:
    """Typed configuration for the history panels — no literals in the flow.

    Defaults mirror :mod:`options_trading.config.settings`; construct via
    :func:`history_config_from_settings` rather than by hand so the deployed
    values and the tested values cannot diverge.
    """

    underlying: str = "NIFTY"
    surface: Literal["spline", "essvi"] = "spline"
    rv_window: int = 21
    tenor_days: int = 30
    n_folds: int = 4
    train_frac: float = 0.6
    initial_equity: float = 1_000_000.0
    lot_size: int = 75
    entry_vrp_grid: tuple[float, ...] = (0.02, 0.03, 0.05)
    band_proportional_cost: float = 5e-4
    band_risk_aversion: float = 1.0
    refresh_seconds: float = 1800.0
    # Risk budgets for the backtest's own account; see backtest_risk_limits.
    vega_budget_frac: float = 0.005
    delta_budget_frac: float = 0.02
    gamma_budget_frac: float = 0.005
    max_drawdown: float = 0.25
    max_concentration: float = 1.0
    #: Largest gap between the two books a P&L attribution will compare. The
    #: default spans a long weekend plus a holiday; beyond that the pair is not
    #: a daily move and the panel says so instead.
    max_explain_gap_days: float = 4.0

    def __post_init__(self) -> None:
        if not self.entry_vrp_grid:
            raise ValueError("entry_vrp_grid must be non-empty; it is the walk-forward search")
        if list(self.entry_vrp_grid) != sorted(self.entry_vrp_grid):
            # The VRP chart shades its entry band at the lowest threshold while
            # the equity curve runs the first one, so an unordered grid makes
            # the two panels describe different strategies. Ordering is cheaper
            # to require than to reconcile.
            raise ValueError(
                f"entry_vrp_grid must be ascending, got {self.entry_vrp_grid}; "
                "the chart shades the lowest threshold and the curve runs the first"
            )

    @property
    def min_days_backtest(self) -> int:
        """Days the walk-forward harness needs at these fold settings."""
        return min_days_for_walk_forward(self.n_folds, self.train_frac)


def history_config_from_settings() -> HistoryAnalyticsConfig:
    """Build the config from deployed settings (single source of truth)."""
    from options_trading.config.settings import settings

    return HistoryAnalyticsConfig(
        underlying=settings.capture_autostart_underlying,
        surface=settings.history_surface_model,
        rv_window=settings.history_rv_window,
        tenor_days=settings.history_tenor_days,
        n_folds=settings.history_walk_forward_folds,
        train_frac=settings.history_walk_forward_train_frac,
        initial_equity=settings.history_backtest_initial_equity,
        lot_size=settings.history_backtest_lot_size,
        entry_vrp_grid=tuple(settings.history_vrp_entry_grid),
        band_proportional_cost=settings.history_band_proportional_cost,
        band_risk_aversion=settings.history_band_risk_aversion,
        refresh_seconds=settings.history_refresh_seconds,
        vega_budget_frac=settings.history_vega_budget_frac,
        delta_budget_frac=settings.history_delta_budget_frac,
        gamma_budget_frac=settings.history_gamma_budget_frac,
        max_drawdown=settings.history_backtest_max_drawdown,
        max_concentration=settings.history_backtest_max_concentration,
        max_explain_gap_days=settings.history_max_explain_gap_days,
    )


@dataclass(frozen=True)
class HistoryPayload:
    """The three history panels plus the coverage that explains their state."""

    vrp_signal: dict[str, Any] | None = None
    backtest_equity: dict[str, Any] | None = None
    pnl_explain: dict[str, Any] | None = None
    days_available: int = 0
    days_required: int = 0
    underlying: str = ""
    computed_at: float = 0.0
    warnings: tuple[str, ...] = ()

    def to_wire_dict(self) -> dict[str, Any]:
        """camelCase keys the frontend reads; see live_analytics.to_wire_dict."""
        return {
            "vrpSignal": self.vrp_signal,
            "backtestEquity": self.backtest_equity,
            "pnlExplain": self.pnl_explain,
            "historyCoverage": {
                "daysAvailable": self.days_available,
                "daysRequired": self.days_required,
                "underlying": self.underlying,
                "computedAt": self.computed_at,
                "warnings": list(self.warnings),
            },
        }


# Bucket display order and colours for the P&L waterfall. Held here rather
# than in the component so the backend decides what the buckets *are* and the
# frontend only draws them.
_PNL_BUCKET_COLOURS = {
    "Theta": "#ef4444",
    "Delta": "#22c55e",
    "Gamma vs RV": "#3b82f6",
    "Vega": "#a855f7",
    "Vanna/Volga": "#f59e0b",
    "Residual": "#6b7280",
}


def _empty_pnl_explain(reason: str, days_available: int = 0) -> dict[str, Any]:
    """A waterfall that reports it cannot be drawn, rather than a flat one."""
    return {
        "hasHistory": False,
        "reason": reason,
        "daysAvailable": days_available,
        "daysRequired": _MIN_BOOK_SNAPSHOTS,
        "date": None,
        "totalPnl": None,
        "buckets": None,
        "explainedFraction": None,
        "coverage": None,
    }


def unavailable_history_wire(reason: str) -> dict[str, Any]:
    """Wire payload declaring all three history panels unavailable.

    Used when the history cannot be computed at all. The keys must be
    *present* and explicitly empty: a consumer that merges only the keys it
    receives treats an absent key as "unchanged", so omitting them leaves the
    previous (possibly demo) values on screen.
    """
    return {
        "vrpSignal": _empty_vrp(0, reason),
        "backtestEquity": _empty_backtest(0, 0, reason),
        "pnlExplain": _empty_pnl_explain(reason),
        "historyCoverage": {
            "daysAvailable": 0,
            "daysRequired": 0,
            "underlying": "",
            "computedAt": 0.0,
            "warnings": [reason],
        },
    }


def _dominant_skip_reason(journal: EventLog) -> str | None:
    """The most frequent ``order_skipped`` reason in a backtest journal.

    Returned verbatim from the risk engine rather than paraphrased: the
    engine's wording names the breached limit and its cap, which is what
    tells a user whether to widen a limit or fix the signal.
    """
    counts: Counter[str] = Counter()
    for event in journal.replay():
        if event.event_type == "order_skipped":
            reason = event.data.get("reason")
            if isinstance(reason, str) and reason:
                counts[reason] += 1
    if not counts:
        return None
    reason, hits = counts.most_common(1)[0]
    total = sum(counts.values())
    return f"{reason} ({hits} of {total} rejections)"


def backtest_risk_limits(config: HistoryAnalyticsConfig, spot: float) -> RiskLimits:
    """Risk caps for the replayed backtest, derived from its own equity.

    The backtest is a hypothetical account, not the user's book, so it must
    not inherit ``risk_max_abs_*``: those caps are sized for the real
    positions and, applied to a 10-lakh paper account trading NIFTY, forbid
    every entry (one lot-75 ATM straddle carries ~210,000 vega against a
    10,000 cap, and a single-strategy straddle book breaches a 35%
    concentration cap by construction). A backtest that structurally cannot
    trade reports a flat curve that looks like a break-even edge.

    Each cap is a risk budget converted into Greek units:

    - **vega** is per *unit* of vol in the core conventions, so a budget
      expressed per vol point multiplies by 100.
    - **delta** is in underlying units: ``delta * spot * 0.01`` is the P&L of
      a 1% spot move, so the cap divides the budget by ``0.01 * spot``.
    - **gamma** enters at second order: ``0.5 * gamma * (0.01 * spot)^2``.

    Args:
        config: History config carrying the equity and the budget fractions.
        spot: Representative underlying level for the replayed period; the
            delta and gamma conversions are spot-dependent, so a NIFTY cap
            and a stock cap cannot share a number.
    """
    if spot <= 0:
        raise ValueError(f"spot must be positive to convert risk budgets, got {spot}")
    equity = config.initial_equity
    one_percent = 0.01 * spot
    return RiskLimits(
        max_abs_delta=equity * config.delta_budget_frac / one_percent,
        max_abs_gamma=2.0 * equity * config.gamma_budget_frac / (one_percent * one_percent),
        max_abs_vega=equity * config.vega_budget_frac * _VOL_POINTS_PER_UNIT,
        max_drawdown=config.max_drawdown,
        max_concentration=config.max_concentration,
    )


def _empty_vrp(days_available: int, reason: str) -> dict[str, Any]:
    """A VRP panel that reports it has no signal, rather than a flat zero one."""
    return {
        "hasHistory": False,
        "reason": reason,
        "daysAvailable": days_available,
        "daysRequired": MIN_DAYS_VRP,
        "iv": None,
        "rv": None,
        "spread": None,
        "regimes": None,
        "dates": None,
        "nDays": 0,
        "meanSpread": None,
    }


def _empty_backtest(days_available: int, days_required: int, reason: str) -> dict[str, Any]:
    return {
        "hasHistory": False,
        "reason": reason,
        "daysAvailable": days_available,
        "daysRequired": days_required,
        "equity": None,
        "dailyPnl": None,
        "drawdown": None,
        "sharpe": None,
        "deflatedSharpe": None,
        "maxDrawdown": None,
        "totalCosts": None,
        "nTrades": None,
        "folds": None,
        "nDays": 0,
    }


class HistoryAnalytics:
    """Builds the history panels from a :class:`SnapshotStore`, with caching.

    ``build()`` is safe to call on every dashboard tick: it returns the cached
    payload unless the stored day set has changed *or* the refresh interval
    has elapsed. Use :meth:`build_async` from async code — it moves the replay
    to a worker thread so a 27s eSSVI rebuild cannot stall the event loop.
    """

    def __init__(
        self,
        store: SnapshotStore,
        config: HistoryAnalyticsConfig | None = None,
        book_store: BookSnapshotStore | None = None,
        *,
        clock: Any = time.monotonic,
    ) -> None:
        self._store = store
        self._config = config if config is not None else HistoryAnalyticsConfig()
        self._book_store = book_store
        self._clock = clock
        self._cached: HistoryPayload | None = None
        self._cached_key: tuple[str, ...] | None = None
        self._cached_at: float = -np.inf
        # Async lock guards task creation; the threading lock guards the cache
        # itself, which is written from worker threads that an asyncio lock
        # cannot see.
        self._lock = asyncio.Lock()
        self._cache_lock = threading.Lock()
        self._inflight: asyncio.Task[HistoryPayload] | None = None

    @property
    def config(self) -> HistoryAnalyticsConfig:
        return self._config

    # -- cache ------------------------------------------------------------

    def _day_key(self) -> tuple[str, ...]:
        """The stored end-of-day set, as StoreReplay would resolve it.

        Keyed on dates rather than file names: an intraday recapture rewrites
        today's end-of-day file every cycle, and rebuilding a year of surface
        fits because one day's closing mark moved is not a trade worth making.
        A new date is what actually changes these panels.
        """
        return tuple(
            sorted(
                {path.parent.name for path in self._store.list_snapshots(self._config.underlying)}
            )
        )

    def _cache_is_fresh(self, key: tuple[str, ...]) -> bool:
        if self._cached is None or self._cached_key != key:
            return False
        return (self._clock() - self._cached_at) < self._config.refresh_seconds

    def build(self) -> HistoryPayload:
        """Cached history payload; recomputes when stale. Blocking.

        Never raises. The per-builder guards below catch the data-shaped
        failures they expect, but the surface fitters raise ``CalibrationError``
        and ``NumericalError``, which derive from ``OptiTradeError`` rather
        than ``ValueError``, and config construction happens outside them. An
        escaped exception would strip the history keys from the broadcast and
        leave the demo curve on screen, so the catch-all is the fail-closed
        boundary (ADR-008) rather than defensive padding.
        """
        with self._cache_lock:
            key = self._day_key()
            if self._cache_is_fresh(key):
                assert self._cached is not None  # guaranteed by _cache_is_fresh
                return self._cached
            try:
                payload = self._build_uncached()
            except Exception as exc:
                logger.exception("History analytics build failed for %s", self._config.underlying)
                payload = self._unavailable(f"the history analytics failed: {type(exc).__name__}")
            # Published under the lock. The three fields are one logical value:
            # an interleaved writer could otherwise pair a stale payload with a
            # fresh timestamp and pin it for the whole refresh interval.
            self._cached = payload
            self._cached_key = key
            self._cached_at = self._clock()
            return payload

    def _unavailable(self, reason: str) -> HistoryPayload:
        cfg = self._config
        return HistoryPayload(
            vrp_signal=_empty_vrp(0, reason),
            backtest_equity=_empty_backtest(0, cfg.min_days_backtest, reason),
            pnl_explain=_empty_pnl_explain(reason),
            days_required=cfg.min_days_backtest,
            underlying=cfg.underlying,
            computed_at=time.time(),
            warnings=(reason,),
        )

    async def build_async(self) -> HistoryPayload:
        """:meth:`build` off the event loop, with concurrent callers coalesced.

        The freshness probe runs in the worker thread too: ``_day_key`` lists
        every stored date directory, which on a year of history is hundreds of
        milliseconds of ``iterdir`` — not something to do on the event loop
        merely to decide whether work is needed.

        Callers share one in-flight task rather than each awaiting their own
        ``to_thread``. Cancellation is why: ``asyncio.to_thread`` cancels the
        *future*, not the thread behind it, so a dashboard client that
        disconnects mid-build releases the async lock while its worker is
        still running. The next caller would then start a second full replay —
        seconds of duplicated surface fitting per reconnect, with both threads
        racing to publish. Shielding a shared task means a disconnect abandons
        the result, not the work.
        """
        async with self._lock:
            if self._inflight is None or self._inflight.done():
                self._inflight = asyncio.create_task(asyncio.to_thread(self.build))
            task = self._inflight
        return await asyncio.shield(task)

    # -- build ------------------------------------------------------------

    def _build_uncached(self) -> HistoryPayload:
        cfg = self._config
        started = time.perf_counter()
        try:
            replay = StoreReplay(
                self._store,
                cfg.underlying,
                rv_window=cfg.rv_window,
                tenor_days=cfg.tenor_days,
                surface=cfg.surface,
            )
        except (ValueError, OSError):
            # A store that cannot be replayed at all is an absence of history,
            # not a dashboard error: report it as such and keep the tab alive.
            logger.exception("StoreReplay failed for %s", cfg.underlying)
            return HistoryPayload(
                vrp_signal=_empty_vrp(0, "the captured history could not be replayed"),
                backtest_equity=_empty_backtest(
                    0, cfg.min_days_backtest, "the captured history could not be replayed"
                ),
                # The book history is stored separately from the chain
                # history, so an unreadable chain store does not blind it.
                pnl_explain=self._build_pnl_explain(),
                days_required=cfg.min_days_backtest,
                underlying=cfg.underlying,
                computed_at=time.time(),
            )

        days = list(replay)
        logger.info(
            "History replay for %s: %d day(s), %d warning(s), %.2fs",
            cfg.underlying,
            len(days),
            len(replay.warnings),
            time.perf_counter() - started,
        )
        payload = HistoryPayload(
            vrp_signal=self._build_vrp_signal(days),
            backtest_equity=self._build_backtest(replay, days),
            pnl_explain=self._build_pnl_explain(),
            days_available=len(days),
            days_required=cfg.min_days_backtest,
            underlying=cfg.underlying,
            computed_at=time.time(),
            warnings=tuple(replay.warnings),
        )
        return payload

    def _build_vrp_signal(self, days: list[Any]) -> dict[str, Any]:
        """IV, realized vol and their spread per captured day.

        Days whose realized vol is StoreReplay's neutral prior are dropped,
        not plotted: their ``vrp`` is zero by construction, so including them
        would drag ``meanSpread`` toward zero using days that never had a
        realized-vol estimate.
        """
        cfg = self._config
        measured = [d for d in days if not d.features.get(_RV_IS_PRIOR, 0.0)]
        n_primed = len(days) - len(measured)
        if len(measured) < MIN_DAYS_VRP:
            return _empty_vrp(
                len(measured),
                f"{len(measured)} day(s) with a measured realized vol; "
                f"{MIN_DAYS_VRP} needed. Capture runs daily, so this fills in "
                f"over the next {MIN_DAYS_VRP - len(measured)} trading day(s).",
            )

        iv = [float(d.features["atm_iv"]) for d in measured]
        rv = [float(d.realized_vol) for d in measured]
        spread = [a - b for a, b in zip(iv, rv, strict=True)]
        # Thresholds come from the strategy config that actually trades them,
        # so the shaded bands on the chart cannot drift from the entry rule.
        entry = min(cfg.entry_vrp_grid)
        exit_ = VRPConfig().exit_vrp_max
        regimes = ["rich" if s >= entry else ("cheap" if s <= exit_ else "neutral") for s in spread]
        return {
            "hasHistory": True,
            "reason": None,
            "daysAvailable": len(measured),
            "daysRequired": MIN_DAYS_VRP,
            "iv": iv,
            "rv": rv,
            "spread": spread,
            "regimes": regimes,
            "dates": [_utc_date(d.timestamp) for d in measured],
            "nDays": len(measured),
            "meanSpread": float(np.mean(spread)),
            "entryThreshold": entry,
            "exitThreshold": exit_,
            "rvWindow": cfg.rv_window,
            "tenorDays": cfg.tenor_days,
            "primedDaysExcluded": n_primed,
        }

    def _build_pnl_explain(self) -> dict[str, Any]:
        """Decompose the most recent day-over-day move in the user's own book.

        Unlike the other two panels this one describes the *account*, not the
        market, so it needs the persisted book history rather than the chain
        history. Without a connected broker there is nothing to explain and it
        says so — the previous implementation shipped a hardcoded waterfall
        that looked like a real trading day.
        """
        if self._book_store is None:
            return _empty_pnl_explain(
                "No book history is being recorded. Connect an Upstox account; "
                "the sync stores a snapshot each cycle and this fills in after "
                "the first full day."
            )
        try:
            # Counted separately from the two it decomposes: reporting
            # "2 days available" to a user with months of history is a lie the
            # fetch limit would otherwise tell.
            days_available = len(self._book_store.dates())
            snapshots = self._book_store.end_of_day_snapshots(limit=_MIN_BOOK_SNAPSHOTS)
        except OSError:
            logger.exception("Could not read book snapshots")
            return _empty_pnl_explain("the stored book history could not be read")

        if len(snapshots) < _MIN_BOOK_SNAPSHOTS:
            return _empty_pnl_explain(
                f"{len(snapshots)} day(s) of book history; a P&L attribution "
                f"compares two end-of-day books, so this appears after the "
                f"next trading day.",
                days_available=days_available,
            )

        start, end = snapshots[-2], snapshots[-1]
        gap_days = (end.timestamp - start.timestamp) / _SECONDS_PER_DAY
        if gap_days > self._config.max_explain_gap_days:
            # The two most recent *stored* days need not be adjacent: after the
            # app is offline for a stretch, they can be weeks apart. Decomposing
            # that and labelling it the latest daily move produces plausible
            # numbers for a period nobody asked about — and theta * dt over
            # weeks, on options that may have expired inside the gap.
            return _empty_pnl_explain(
                f"The last two recorded books are {gap_days:.0f} days apart "
                f"({_utc_date(start.timestamp)} to {_utc_date(end.timestamp)}), "
                f"so there is no recent daily move to attribute. This fills in "
                f"after the next two consecutive trading days.",
                days_available=days_available,
            )
        try:
            spots = self._book_store.intraday_spots(start.timestamp, end.timestamp)
            result = explain_book_pnl(start, end, intraday_spots=spots)
        except ValueError:
            logger.exception("P&L explain failed between book snapshots")
            return _empty_pnl_explain("the stored book snapshots could not be compared")

        if result is None:
            return _empty_pnl_explain(
                "Every position changed between the two end-of-day books, so "
                "no held P&L remains to attribute. Greeks explain what a "
                "position did while it was held, not the cash flow of trading it.",
                days_available=len(snapshots),
            )

        explain = result.explain
        vega_total = sum(explain.vega_from_factors.values()) + explain.vega_residual_move
        buckets = [
            ("Theta", explain.theta_carry),
            ("Delta", explain.delta_pnl),
            ("Gamma vs RV", explain.gamma_vs_rv),
            ("Vega", vega_total),
            ("Vanna/Volga", explain.vanna_volga),
            ("Residual", explain.residual),
        ]
        return {
            "hasHistory": True,
            "reason": None,
            "daysAvailable": len(snapshots),
            "daysRequired": _MIN_BOOK_SNAPSHOTS,
            "date": _utc_date(result.end_timestamp),
            "previousDate": _utc_date(result.start_timestamp),
            "totalPnl": float(explain.total),
            "buckets": [
                {"name": name, "value": float(value), "color": _PNL_BUCKET_COLOURS[name]}
                for name, value in buckets
            ],
            "explainedFraction": float(explain.explained_fraction),
            # Coverage and legsChanged are the caveats that keep the headline
            # honest: a 98%-explained decomposition of a fifth of the book is
            # not a 98%-explained decomposition of the book.
            "coverage": float(result.coverage),
            "legsCompared": result.n_legs_compared,
            "legsChanged": result.n_legs_changed,
            "spotMove": float(result.d_spot),
            "volMove": float(result.d_vol),
            "gammaMarkedAgainstRealizedVariance": result.realized_variance is not None,
        }

    def _backtest_config(self, spot: float) -> BacktestConfig:
        cfg = self._config
        return BacktestConfig(
            risk_limits=backtest_risk_limits(cfg, spot),
            band_params=BandParams(
                proportional_cost=cfg.band_proportional_cost,
                risk_aversion=cfg.band_risk_aversion,
            ),
            initial_equity=cfg.initial_equity,
            lot_size=cfg.lot_size,
        )

    def _build_backtest(self, replay: StoreReplay, days: list[Any]) -> dict[str, Any]:
        """Replayed VRP backtest plus its walk-forward folds.

        The headline number is the *out-of-sample* Sharpe with its deflated
        counterpart, not the in-sample equity curve: an in-sample Sharpe from
        a grid search over the same days is a selection artefact, and showing
        it alone would be the fabrication this phase exists to remove.
        """
        cfg = self._config
        required = cfg.min_days_backtest
        if len(days) < required:
            return _empty_backtest(
                len(days),
                required,
                f"{len(days)} captured day(s); the walk-forward harness needs "
                f"{required} for {cfg.n_folds} folds at a {cfg.train_frac:.0%} "
                f"training split.",
            )

        # The delta and gamma budgets convert through spot, so use the median
        # replayed level rather than the latest: one gap day should not resize
        # the caps the whole backtest ran under.
        spot = float(np.median([d.spot for d in days]))
        backtest_config = self._backtest_config(spot)
        grid = tuple(VRPConfig(entry_vrp_min=e) for e in cfg.entry_vrp_grid)

        def factory(vrp_config: VRPConfig) -> VRPStrategy:
            return VRPStrategy(vrp_config, lot_size=cfg.lot_size)

        with TemporaryDirectory(prefix="optitrade-backtest-journal-") as tmp:
            # Journalled so a zero-trade run can report why the orders died
            # (ADR-009). Scratch, not the durable journal: a grid search over
            # every fold would swamp it, and only the reason survives.
            journal = EventLog(Path(tmp), "history_backtest")
            try:
                result: BacktestResult = run_backtest(
                    factory(grid[0]), days, backtest_config, journal=journal
                )
                walk: WalkForwardResult[VRPConfig] = run_walk_forward(
                    factory, grid, days, backtest_config, cfg.n_folds, cfg.train_frac
                )
            except ValueError as exc:
                logger.warning("Backtest over %d day(s) failed: %s", len(days), exc)
                return _empty_backtest(len(days), required, f"the backtest could not run: {exc}")
            skip_reason = _dominant_skip_reason(journal) if result.n_trades == 0 else None

        equity = result.equity
        peaks = np.maximum.accumulate(equity)
        drawdown = np.where(peaks > 0, 1.0 - equity / peaks, 0.0)
        # A backtest that never traded produces a flat curve and a Sharpe of
        # exactly 0.0, which on a chart is indistinguishable from a strategy
        # that traded and broke even. Say which one it was — and say *why*
        # from the journal, because "the signal never fired" and "risk blocked
        # every order" both end in zero trades and imply opposite fixes.
        note = None
        if result.n_trades == 0:
            cause = (
                f"every order was rejected before it filled: {skip_reason}"
                if skip_reason
                else (
                    "the signal never cleared the lowest entry threshold in the "
                    f"grid ({min(cfg.entry_vrp_grid):.1%})"
                )
            )
            note = (
                f"No trades over these {len(days)} day(s) — {cause}. A flat "
                f"curve here means no trades, not a break-even edge."
            )
        return {
            "hasHistory": True,
            "reason": None,
            "note": note,
            "daysAvailable": len(days),
            "daysRequired": required,
            "equity": [float(v) for v in equity],
            "dailyPnl": [float(v) for v in result.daily_pnl],
            "drawdown": [float(v) for v in drawdown],
            "dates": [_utc_date(d.timestamp) for d in days],
            "sharpe": float(result.sharpe),
            "oosSharpe": float(walk.oos_sharpe),
            "deflatedSharpe": float(walk.deflated_sharpe),
            "maxDrawdown": float(max_drawdown(equity)),
            "totalCosts": float(result.total_costs),
            "nTrades": int(result.n_trades),
            "nTrials": int(walk.n_trials),
            "initialEquity": cfg.initial_equity,
            "nDays": len(days),
            "strategy": VRPStrategy(grid[0], lot_size=cfg.lot_size).name,
            "lotSize": cfg.lot_size,
            "folds": [
                {
                    "fold": fold.fold + 1,
                    "trainSharpe": float(fold.train_sharpe),
                    "testSharpe": float(fold.test_sharpe),
                    "startDay": fold.train_start,
                    "endDay": fold.test_stop,
                    "testTrades": int(fold.test_n_trades),
                    "chosenEntryVrp": float(fold.chosen_config.entry_vrp_min),
                }
                for fold in walk.folds
            ],
        }


def _utc_date(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp, tz=UTC).strftime("%Y-%m-%d")


__all__ = [
    "MIN_DAYS_VRP",
    "HistoryAnalytics",
    "HistoryAnalyticsConfig",
    "HistoryPayload",
    "history_config_from_settings",
]

"""Runs the paper desk over captured history and publishes what it did.

This is the platform layer around :func:`optitrade.desk.cycle.run_daily_cycle`.
The cycle itself — strategy, debate, fail-closed risk review, paper fill,
hedge decision, journal — is complete and tested in the quant core and is not
reimplemented here. What this module owns is everything the core deliberately
does not: where the market days come from, when to run, and how the result
survives a restart.

**PAPER ONLY.** Every fill this service records is computed inside the quant
core against a replayed snapshot. Nothing here can reach a broker: the
service imports no broker client, and the only account it mutates is a
notional one that starts at ``desk_initial_equity``. The user's real Upstox
account is read-only everywhere in this app and this service does not change
that. The wire payload carries ``isPaper`` on every response so no consumer
can render a paper fill as a real one by omission.

**Cadence: one cycle per captured trading day, advanced on demand.**
``run_daily_cycle`` is by construction one desk *day* — it marks the book
once, asks the strategy once, and takes one hedge decision. Running it more
often than the data changes would book the same entry repeatedly against a
snapshot that had not moved. The captured history gains at most one
end-of-day snapshot per date, so a day is the natural tick and
``processed_dates`` in the persisted state makes advancing idempotent: a
second pass over the same history does nothing.

Advancing is *not* wired to the 60-second capture tick. A replay refits a vol
surface per stored day, the same cost that keeps the history panels off that
tick (see :mod:`~options_trading.services.history_analytics`), and unlike
those panels a desk advance has side effects — fills, journal entries, a
mutated book. A cheap read (``build``) is safe to call on every dashboard
tick and reports the desk's state; the expensive, state-changing advance
happens when an operator asks for it. That keeps "the desk traded" an
attributable act rather than something a background timer did.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from optitrade.backtest import StoreReplay
from optitrade.core.types import Portfolio, Position
from optitrade.data import SnapshotStore
from optitrade.desk import DeskConfig, KillSwitch, run_daily_cycle
from optitrade.governance import DebatePanel, ExecutionExpert, RiskOfficer, StrategyExpert
from optitrade.hedging import BandParams
from optitrade.journal import EventLog
from optitrade.strategy import VRPConfig, VRPStrategy

from .desk_journal import build_decision_trail
from .desk_state_store import CycleRecord, DeskState, DeskStateError, DeskStateStore
from .history_analytics import backtest_risk_limits

logger = logging.getLogger(__name__)

#: One captured day is enough to run one cycle. Stated explicitly because the
#: panel reports it, and "1" is a claim about the desk rather than a constant
#: to be guessed at the call site.
MIN_DAYS_DESK = 1


@dataclass(frozen=True)
class DeskServiceConfig:
    """Typed configuration for the paper desk — no literals in the flow.

    Defaults mirror :mod:`options_trading.config.settings`; construct via
    :func:`desk_config_from_settings` so deployed and tested values cannot
    diverge. The budget fractions satisfy
    :class:`~options_trading.services.history_analytics.PaperAccountBudget`,
    which converts them into Greek caps.
    """

    underlying: str = "NIFTY"
    surface: Literal["spline", "essvi"] = "spline"
    rv_window: int = 21
    tenor_days: int = 30
    initial_equity: float = 1_000_000.0
    lot_size: int = 75
    quantity: float = 1.0
    entry_vrp_min: float = 0.03
    exit_vrp_max: float = 0.0
    spread_frac: float = 0.005
    require_debate: bool = True
    band_proportional_cost: float = 5e-4
    band_risk_aversion: float = 1.0
    refresh_seconds: float = 300.0
    max_cycles_retained: int = 250
    # Risk budgets for the desk's own notional account; see backtest_risk_limits.
    vega_budget_frac: float = 0.005
    delta_budget_frac: float = 0.02
    gamma_budget_frac: float = 0.005
    max_drawdown: float = 0.25
    max_concentration: float = 1.0
    journal_run_id: str = "desk"

    def __post_init__(self) -> None:
        if self.max_cycles_retained < 1:
            raise ValueError(
                f"max_cycles_retained must be >= 1, got {self.max_cycles_retained}; "
                "the desk must keep at least the cycle it just ran"
            )
        if self.initial_equity <= 0:
            raise ValueError(f"initial_equity must be positive, got {self.initial_equity}")


def desk_config_from_settings() -> DeskServiceConfig:
    """Build the config from deployed settings (single source of truth)."""
    from options_trading.config.settings import settings

    return DeskServiceConfig(
        underlying=settings.capture_autostart_underlying,
        # Was a hardcoded dataclass default this factory never set, so the
        # analyst panel — which is settings-driven — could be pointed at a
        # journal the desk would never write, and reported the desk as having
        # never run (ADR-028). One setting, both consumers.
        journal_run_id=settings.desk_journal_run_id,
        surface=settings.history_surface_model,
        rv_window=settings.history_rv_window,
        tenor_days=settings.history_tenor_days,
        initial_equity=settings.desk_initial_equity,
        lot_size=settings.desk_lot_size,
        quantity=settings.desk_quantity,
        entry_vrp_min=settings.desk_entry_vrp_min,
        exit_vrp_max=settings.desk_exit_vrp_max,
        spread_frac=settings.desk_spread_frac,
        require_debate=settings.desk_require_debate,
        band_proportional_cost=settings.history_band_proportional_cost,
        band_risk_aversion=settings.history_band_risk_aversion,
        refresh_seconds=settings.desk_refresh_seconds,
        max_cycles_retained=settings.desk_max_cycles_retained,
        vega_budget_frac=settings.history_vega_budget_frac,
        delta_budget_frac=settings.history_delta_budget_frac,
        gamma_budget_frac=settings.history_gamma_budget_frac,
        max_drawdown=settings.history_backtest_max_drawdown,
        max_concentration=settings.history_backtest_max_concentration,
    )


def _date_of(timestamp: float) -> str:
    """Stored-date key for a market day.

    ``SnapshotStore`` files live under a ``%Y-%m-%d`` directory derived from
    the chain timestamp in UTC, and ``StoreReplay`` carries that timestamp
    through onto the ``MarketDay``. Deriving the key the same way keeps
    ``processed_dates`` comparable with what is actually on disk.
    """
    return datetime.fromtimestamp(timestamp, tz=UTC).strftime("%Y-%m-%d")


def kill_switch_wire(switch: KillSwitch) -> dict[str, Any]:
    """Kill-switch state for the wire.

    Read from the filesystem on every call, never cached: the switch exists so
    that any process — the risk engine mid-cycle, a monitoring script, a human
    with ``touch`` — can halt the desk, and a cached answer could report a
    running desk seconds after one of them stopped it.
    """
    try:
        engaged = switch.is_engaged()
        reason = switch.reason() if engaged else None
    except OSError as exc:
        # Fail closed: if the latch cannot be read, report it as engaged.
        # A desk whose halt state is unknown must not look ready to trade.
        logger.exception("Kill switch at %s could not be read", switch.path)
        return {
            "engaged": True,
            "reason": (
                f"the kill-switch file at {switch.path} could not be read "
                f"({type(exc).__name__}); reporting the desk as halted"
            ),
            "path": str(switch.path),
            "readable": False,
        }
    return {
        "engaged": engaged,
        # A bare `touch` gives an engaged switch with no stated reason.
        "reason": reason or ("engaged with no stated reason" if engaged else None),
        "path": str(switch.path),
        "readable": True,
    }


def _empty_desk_history(days_available: int, reason: str) -> dict[str, Any]:
    """Cycle history that reports it has none, rather than a flat book.

    Shaped for ``HistoryGate.tsx`` so the desk tab reuses the same gate as
    the VRP and backtest panels instead of growing a parallel empty state.
    """
    return {
        "hasHistory": False,
        "reason": reason,
        "daysAvailable": days_available,
        "daysRequired": MIN_DAYS_DESK,
    }


def unavailable_desk_wire(reason: str) -> dict[str, Any]:
    """Wire payload declaring the desk unavailable, with every key present.

    The key must be *present* and explicitly empty. The frontend merges only
    the keys it receives, so omitting ``desk`` on a failure leaves the last
    good desk state on screen — a stale book beside a live spot, which is the
    failure mode this pattern exists to prevent (ADR-008).

    ``killSwitch`` is deliberately reported as engaged here: if the desk
    cannot be described, the honest summary of its trading state is "not
    running", and the control must not invite a reset of a switch whose real
    state is unknown.
    """
    return {
        "isPaper": True,
        "mode": "paper",
        "underlying": "",
        "computedAt": 0.0,
        "killSwitch": {
            "engaged": True,
            "reason": reason,
            "path": "",
            "readable": False,
        },
        "history": _empty_desk_history(0, reason),
        "cycles": [],
        "book": [],
        "account": None,
        "warnings": [reason],
    }


@dataclass(frozen=True)
class DeskPayload:
    """The desk tab's data plus the state that explains it."""

    underlying: str
    kill_switch: dict[str, Any]
    history: dict[str, Any]
    cycles: tuple[dict[str, Any], ...] = ()
    book: tuple[dict[str, Any], ...] = ()
    account: dict[str, Any] | None = None
    computed_at: float = 0.0
    warnings: tuple[str, ...] = ()

    def to_wire_dict(self) -> dict[str, Any]:
        """camelCase keys the frontend reads; see live_analytics.to_wire_dict.

        ``isPaper``/``mode`` are constants rather than configuration. There is
        no live-trading mode to switch to, and a consumer must be able to
        assert the fills are simulated from the payload alone.
        """
        return {
            "isPaper": True,
            "mode": "paper",
            "underlying": self.underlying,
            "computedAt": self.computed_at,
            "killSwitch": self.kill_switch,
            "history": self.history,
            "cycles": [dict(c) for c in self.cycles],
            "book": [dict(p) for p in self.book],
            "account": self.account,
            "warnings": list(self.warnings),
        }


def _position_wire(position: Position) -> dict[str, Any]:
    return {
        "symbol": position.contract.symbol,
        "strike": position.contract.strike,
        "expiry": position.contract.expiry,
        "optionType": position.contract.option_type.value,
        "lotSize": position.contract.lot_size,
        "quantity": position.quantity,
        "entryPrice": position.entry_price,
    }


def _account_wire(portfolio: Portfolio) -> dict[str, Any]:
    return {
        "cash": portfolio.cash,
        "equity": portfolio.equity,
        "highWaterMark": portfolio.high_water_mark,
        "drawdown": portfolio.drawdown,
        "grossNotional": portfolio.gross_notional,
        "nPositions": len(portfolio.positions),
    }


class DeskService:
    """Advances the paper desk over captured history and reports its state.

    ``build`` is the cheap read: it reflects persisted state and the current
    kill-switch latch, and is safe on every dashboard tick. ``advance`` is the
    expensive, state-changing run and is only invoked deliberately.
    """

    def __init__(
        self,
        store: SnapshotStore,
        state_store: DeskStateStore,
        journal_dir: Path,
        kill_switch: KillSwitch,
        config: DeskServiceConfig | None = None,
        *,
        clock: Any = time.monotonic,
    ) -> None:
        self._store = store
        self._state_store = state_store
        self._journal_dir = Path(journal_dir)
        self._kill_switch = kill_switch
        self._config = config if config is not None else DeskServiceConfig()
        self._clock = clock
        # Serialises advances: two concurrent runs over the same stored days
        # would both start from the same book and double the fills.
        self._advance_lock = threading.Lock()
        self._async_advance_lock = asyncio.Lock()
        self._replay_cache: tuple[tuple[str, ...], tuple[Any, ...], tuple[str, ...]] | None = None
        self._replay_cached_at = float("-inf")

    @property
    def config(self) -> DeskServiceConfig:
        return self._config

    @property
    def kill_switch(self) -> KillSwitch:
        return self._kill_switch

    # -- journal ----------------------------------------------------------

    def _journal(self) -> EventLog:
        """The desk's journal.

        One stable run id across restarts, so the file accumulates and
        ``EventLog`` resumes its sequence numbering from disk. A per-process
        run id would rotate the file and orphan the correlation ids stored in
        past cycle records, breaking exactly the trail this exists to serve.
        """
        return EventLog(self._journal_dir, self._config.journal_run_id)

    def decision_trail(self, correlation_id: str) -> dict[str, Any]:
        """The full decision trail for one cycle, by correlation id."""
        return build_decision_trail(self._journal(), correlation_id)

    # -- kill switch ------------------------------------------------------

    def kill_switch_state(self) -> dict[str, Any]:
        return kill_switch_wire(self._kill_switch)

    def engage_kill_switch(self, reason: str) -> dict[str, Any]:
        """Halt the desk now, and journal who stopped it and why.

        Engaging is unconditional and takes effect the moment the file exists;
        the journal append comes *after* so a journal failure cannot delay or
        prevent the halt.
        """
        self._kill_switch.engage(reason)
        try:
            self._journal().append("kill_switch_engaged", {"reason": reason, "source": "operator"})
        except Exception:
            logger.exception("Kill switch engaged but the journal append failed")
        return self.kill_switch_state()

    def reset_kill_switch(self, reason: str) -> dict[str, Any]:
        """Clear the halt. Deliberate by contract: the caller states why.

        The route enforces a typed confirmation on top of this; recording the
        stated reason is what makes a resume auditable rather than a silent
        return to trading.
        """
        previous = self._kill_switch.reason()
        self._kill_switch.reset()
        try:
            self._journal().append(
                "kill_switch_reset",
                {"reason": reason, "source": "operator", "previous": previous},
            )
        except Exception:
            logger.exception("Kill switch reset but the journal append failed")
        return self.kill_switch_state()

    # -- state ------------------------------------------------------------

    def _fresh_state(self) -> DeskState:
        equity = self._config.initial_equity
        return DeskState(
            journal_run_id=self._config.journal_run_id,
            book=(),
            portfolio=Portfolio(
                cash=equity,
                equity=equity,
                high_water_mark=equity,
                # A notional margin line for the paper account, tied to its
                # own equity rather than to anything the broker reports.
                margin_available=equity,
            ),
            processed_dates=(),
            cycles=(),
        )

    def _stored_dates(self) -> tuple[str, ...]:
        return tuple(
            sorted(
                {path.parent.name for path in self._store.list_snapshots(self._config.underlying)}
            )
        )

    # -- read -------------------------------------------------------------

    def build(self) -> DeskPayload:
        """Describe the desk from persisted state. Cheap; never raises.

        A read of the desk must not be able to break the dashboard, and it
        must not silently substitute a fresh book for state it failed to load
        (see :mod:`~options_trading.services.desk_state_store`).
        """
        switch = self.kill_switch_state()
        try:
            state = self._state_store.load()
        except DeskStateError as exc:
            logger.exception("Desk state could not be loaded")
            payload = unavailable_desk_wire(
                f"the persisted desk state could not be read: {exc}. The book is "
                "not shown rather than shown as empty; check the server logs."
            )
            return DeskPayload(
                underlying=self._config.underlying,
                kill_switch=payload["killSwitch"],
                history=payload["history"],
                computed_at=time.time(),
                warnings=(str(exc),),
            )
        except Exception as exc:
            logger.exception("Desk state load failed unexpectedly")
            reason = f"the desk state could not be read: {type(exc).__name__}"
            return DeskPayload(
                underlying=self._config.underlying,
                kill_switch=switch,
                history=_empty_desk_history(0, reason),
                computed_at=time.time(),
                warnings=(reason,),
            )

        try:
            days_available = len(self._stored_dates())
        except OSError:
            logger.exception("Could not list captured snapshots")
            days_available = 0

        if state is None or not state.cycles:
            reason = (
                "the paper desk has not run a cycle yet"
                if days_available >= MIN_DAYS_DESK
                else (
                    "no captured market days are stored yet, so the desk has "
                    "nothing to trade against"
                )
            )
            return DeskPayload(
                underlying=self._config.underlying,
                kill_switch=switch,
                history=_empty_desk_history(days_available, reason),
                account=None if state is None else _account_wire(state.portfolio),
                computed_at=time.time(),
            )

        return DeskPayload(
            underlying=self._config.underlying,
            kill_switch=switch,
            history={
                "hasHistory": True,
                "reason": None,
                "daysAvailable": days_available,
                "daysRequired": MIN_DAYS_DESK,
            },
            cycles=tuple(record.to_dict() for record in state.cycles),
            book=tuple(_position_wire(p) for p in state.book),
            account=_account_wire(state.portfolio),
            computed_at=time.time(),
        )

    async def build_async(self) -> DeskPayload:
        """:meth:`build` off the event loop.

        ``build`` lists the stored date directories, which on a year of
        history is hundreds of milliseconds of ``iterdir`` — not something to
        do on the event loop.
        """
        return await asyncio.to_thread(self.build)

    # -- advance ----------------------------------------------------------

    def _replay_days(self) -> tuple[tuple[Any, ...], tuple[str, ...]]:
        """Replayed market days and any replay warnings, cached by date set.

        The replay refits a surface per stored day, so an advance that finds
        nothing new should not pay for it twice in a row.
        """
        key = self._stored_dates()
        cached = self._replay_cache
        if (
            cached is not None
            and cached[0] == key
            and (self._clock() - self._replay_cached_at) < self._config.refresh_seconds
        ):
            return cached[1], cached[2]
        replay = StoreReplay(
            self._store,
            self._config.underlying,
            rv_window=self._config.rv_window,
            tenor_days=self._config.tenor_days,
            surface=self._config.surface,
        )
        days = tuple(replay)
        warnings = tuple(replay.warnings)
        self._replay_cache = (key, days, warnings)
        self._replay_cached_at = self._clock()
        return days, warnings

    def advance(self) -> DeskPayload:
        """Run the desk over every captured day it has not yet processed.

        Returns the payload as it stands afterwards. Refuses to run — rather
        than running and reporting a skip — when the kill switch is engaged:
        ``run_daily_cycle`` would journal one ``cycle_skipped`` event per
        pending day, filling the trail with noise that says nothing a single
        halted state does not.
        """
        with self._advance_lock:
            return self._advance_locked()

    async def advance_async(self) -> DeskPayload:
        """:meth:`advance` off the event loop, one run at a time."""
        async with self._async_advance_lock:
            return await asyncio.to_thread(self.advance)

    def _advance_locked(self) -> DeskPayload:
        switch = self.kill_switch_state()
        if switch["engaged"]:
            payload = self.build()
            return replace(
                payload,
                warnings=(
                    *payload.warnings,
                    f"the desk is halted and did not advance: {switch['reason']}",
                ),
            )

        try:
            state = self._state_store.load() or self._fresh_state()
        except DeskStateError as exc:
            # Fail closed: never advance from a book we could not read. Doing
            # so would trade from a fresh book while the real one sat
            # unparsed on disk.
            logger.exception("Refusing to advance: desk state unreadable")
            return DeskPayload(
                underlying=self._config.underlying,
                kill_switch=switch,
                history=_empty_desk_history(
                    0, f"the persisted desk state could not be read: {exc}"
                ),
                computed_at=time.time(),
                warnings=(
                    f"the desk did not advance: {exc}. Fix or remove the state "
                    "file; the desk will not trade from a book it cannot read.",
                ),
            )

        try:
            days, replay_warnings = self._replay_days()
        except Exception as exc:
            logger.exception("StoreReplay failed for %s", self._config.underlying)
            reason = f"the captured history could not be replayed: {type(exc).__name__}: {exc}"
            payload = self.build()
            return replace(payload, warnings=(*payload.warnings, reason))

        pending = [day for day in days if _date_of(day.timestamp) not in state.processed_dates]
        if not pending:
            payload = self.build()
            note = (
                "no captured market days are stored yet"
                if not days
                else f"all {len(days)} captured days have already been processed"
            )
            return replace(payload, warnings=(*payload.warnings, note, *replay_warnings))

        journal = self._journal()
        strategy = VRPStrategy(
            VRPConfig(
                entry_vrp_min=self._config.entry_vrp_min,
                exit_vrp_max=self._config.exit_vrp_max,
                tenor_days=self._config.tenor_days,
                quantity=self._config.quantity,
            ),
            lot_size=self._config.lot_size,
        )
        # Caps are spot-dependent, so they are derived from the first day the
        # desk is about to trade rather than from a constant.
        limits = backtest_risk_limits(self._config, pending[0].spot)
        desk_config = DeskConfig(
            limits=limits,
            band=BandParams(
                proportional_cost=self._config.band_proportional_cost,
                risk_aversion=self._config.band_risk_aversion,
            ),
            underlying_symbol=self._config.underlying,
            spread_frac=self._config.spread_frac,
            require_debate=self._config.require_debate,
        )
        panel = DebatePanel(
            experts=(RiskOfficer(limits), StrategyExpert(), ExecutionExpert()),
            journal=journal,
        )

        book = state.book
        portfolio = state.portfolio
        processed = list(state.processed_dates)
        cycles = list(state.cycles)
        ran = 0
        for day in pending:
            result, book, portfolio = run_daily_cycle(
                day, portfolio, book, strategy, desk_config, journal, self._kill_switch, panel
            )
            cycles.append(_cycle_record(day.timestamp, result, portfolio))
            processed.append(_date_of(day.timestamp))
            ran += 1
            state = DeskState(
                journal_run_id=self._config.journal_run_id,
                book=book,
                portfolio=portfolio,
                processed_dates=tuple(processed),
                cycles=tuple(cycles[-self._config.max_cycles_retained :]),
            )
            # Persisted per cycle, not once at the end: a crash partway
            # through a backlog must resume, not replay the fills it made.
            self._state_store.save(state)
            if result.halted:
                logger.warning(
                    "Desk halted on %s: %s", _date_of(day.timestamp), result.action_taken
                )
                break

        payload = self.build()
        return replace(
            payload,
            warnings=(
                *payload.warnings,
                f"advanced {ran} of {len(pending)} pending day(s)",
                *replay_warnings,
            ),
        )


def _cycle_record(timestamp: float, result: Any, portfolio: Portfolio) -> CycleRecord:
    """Summarise one :class:`~optitrade.desk.cycle.CycleResult` for display."""
    return CycleRecord(
        date=_date_of(timestamp),
        timestamp=timestamp,
        action=result.action_taken.split(":", 1)[0],
        action_taken=result.action_taken,
        n_fills=len(result.fills),
        n_rejected=len(result.rejected),
        equity=portfolio.equity,
        cash=portfolio.cash,
        drawdown=portfolio.drawdown,
        delta=result.book_greeks.delta,
        gamma=result.book_greeks.gamma,
        vega=result.book_greeks.vega,
        theta=result.book_greeks.theta,
        hedge_action=None if result.hedge is None else result.hedge.action,
        halted=result.halted,
        correlation_id=result.correlation_id,
        fills=tuple(
            {
                "symbol": order.symbol,
                "quantity": order.quantity,
                "price": order.price,
                "notional": order.notional,
            }
            for order in result.fills
        ),
        rejected=tuple(
            {
                "symbol": order.symbol,
                "quantity": order.quantity,
                "price": order.price,
                "reason": reason,
            }
            for order, reason in result.rejected
        ),
    )


__all__ = [
    "MIN_DAYS_DESK",
    "DeskPayload",
    "DeskService",
    "DeskServiceConfig",
    "desk_config_from_settings",
    "kill_switch_wire",
    "unavailable_desk_wire",
]

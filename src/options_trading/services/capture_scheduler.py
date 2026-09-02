# src/options_trading/services/capture_scheduler.py
"""Unattended market-hours capture scheduler (flagship phase 0 exit criterion).

Wraps a zero-argument capture callable (built by the caller around
``capture_and_store``) in a loop that fires every ``interval_seconds`` while the
market is open and sleeps through nights, weekends, and holidays. Clean
option-chain history therefore accumulates on market days without a human.

Design notes
------------
- :func:`is_market_open` / :func:`next_market_open` are pure functions of a
  timestamp and a :class:`ScheduleConfig`, so calendar logic is testable
  without a scheduler instance or wall clock.
- The clock and sleeper are injected; tests simulate days of market time in
  milliseconds (CLAUDE.md: deterministic tests, no wall-clock dependence).
- An unattended service never dies on one bad capture: each failure is
  recorded (counters, ``history``) and the loop continues. This is the
  survive-and-log complement to the fail-closed rule for risk checks.
- ``before_capture`` runs on the event loop before each threaded capture, so
  an async token refresh can happen while ``capture_fn`` stays synchronous.
  An ``AuthError`` there sets ``auth_required`` rather than being buried in
  the generic failure count: no amount of retrying fixes an expired token.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from datetime import time as dt_time
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

from ..utils.exceptions import AuthError
from .capture_service import CaptureReport

# Capture outcomes kept for the status route: at the default 15-minute cadence
# this covers several full trading days of history.
HISTORY_MAXLEN = 100
# next_market_open searches this many calendar days ahead. A config that admits
# no trading day within it (e.g. empty trading_days) is unusable, and the
# search fails closed with ValueError instead of looping forever.
MAX_LOOKAHEAD_DAYS = 400


def _parse_hhmm(value: str) -> dt_time:
    """Parse ``"HH:MM"`` into a naive time-of-day; ValueError on bad input."""
    try:
        return datetime.strptime(value, "%H:%M").time()
    except ValueError as exc:
        raise ValueError(f"time {value!r} is not in HH:MM format") from exc


@dataclass(frozen=True)
class ScheduleConfig:
    """Market-hours schedule for unattended chain capture.

    ``holidays`` holds NSE trading holidays as ISO ``YYYY-MM-DD`` dates. The
    exchange publishes that list yearly, so it is deployment configuration,
    not code: pass the current year's dates when constructing the config
    rather than baking them into this module.

    ``trading_days`` uses Python weekday numbers (Monday=0 .. Sunday=6).
    """

    interval_seconds: int = 900
    market_open: str = "09:15"
    market_close: str = "15:30"
    timezone: str = "Asia/Kolkata"
    trading_days: tuple[int, ...] = (0, 1, 2, 3, 4)  # Monday..Friday
    holidays: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.interval_seconds <= 0:
            raise ValueError(f"interval_seconds must be positive, got {self.interval_seconds}")
        if _parse_hhmm(self.market_open) >= _parse_hhmm(self.market_close):
            raise ValueError(
                f"market_open {self.market_open!r} must be before market_close "
                f"{self.market_close!r}"
            )
        bad_days = tuple(day for day in self.trading_days if day not in range(7))
        if bad_days:
            raise ValueError(f"trading_days must be weekday numbers 0..6, got {bad_days}")
        for holiday in self.holidays:
            try:
                datetime.strptime(holiday, "%Y-%m-%d")
            except ValueError as exc:
                raise ValueError(f"holiday {holiday!r} is not an ISO YYYY-MM-DD date") from exc
        try:
            ZoneInfo(self.timezone)  # unknown zone names fail here, not mid-loop
        except Exception as exc:
            raise ValueError(f"unknown timezone {self.timezone!r}") from exc


@dataclass(frozen=True)
class SchedulerStatus:
    """Point-in-time view of the scheduler for the status route.

    ``last_run_ts`` is the epoch of the most recent capture *attempt* (success
    or failure); ``last_error`` reflects the most recent attempt only, so it
    clears when a later capture succeeds. ``next_eligible_ts`` estimates the
    earliest instant a capture could next fire: now (or last attempt +
    interval) while the market is open, otherwise the next market open.
    """

    running: bool
    last_run_ts: float | None
    last_error: str | None
    n_captures: int
    n_failures: int
    next_eligible_ts: float | None
    #: True when captures are blocked on an expired/absent Upstox token. Only
    #: an interactive re-login clears this, so it is reported separately from
    #: ``n_failures`` — retrying cannot help and the user must be told.
    auth_required: bool = False


def is_market_open(ts: float, config: ScheduleConfig) -> bool:
    """True iff epoch ``ts`` falls inside the trading window in config's zone.

    The window is half-open ``[market_open, market_close)``: a tick at exactly
    the close instant reads the auction transition, not a live book, so it is
    excluded. Trading-day and holiday checks use the local calendar date.
    """
    moment = datetime.fromtimestamp(ts, tz=ZoneInfo(config.timezone))
    if moment.weekday() not in config.trading_days:
        return False
    if moment.date().isoformat() in config.holidays:
        return False
    return _parse_hhmm(config.market_open) <= moment.time() < _parse_hhmm(config.market_close)


def next_market_open(ts: float, config: ScheduleConfig) -> float:
    """Epoch of the first market-open instant strictly after ``ts``.

    Scans forward day by day, skipping non-trading weekdays and configured
    holidays. Fails closed with ValueError if no trading day exists within
    :data:`MAX_LOOKAHEAD_DAYS`.
    """
    tz = ZoneInfo(config.timezone)
    open_time = _parse_hhmm(config.market_open)
    start = datetime.fromtimestamp(ts, tz=tz)
    for offset in range(MAX_LOOKAHEAD_DAYS):
        day = (start + timedelta(days=offset)).date()
        if day.weekday() not in config.trading_days or day.isoformat() in config.holidays:
            continue
        candidate = datetime.combine(day, open_time, tzinfo=tz).timestamp()
        if candidate > ts:
            return candidate
    raise ValueError(
        f"no trading day within {MAX_LOOKAHEAD_DAYS} days of {start.date().isoformat()}; "
        "trading_days/holidays config admits no market open"
    )


class CaptureScheduler:
    """Runs ``capture_fn`` every ``interval_seconds`` during market hours.

    Contract for an unattended service:

    - a failing capture is counted and recorded in ``history``, and the loop
      continues — one bad fetch must never end a day of history;
    - :meth:`stop` sets an event that both terminates the loop and wakes any
      pending sleep, so shutdown is prompt even mid-weekend;
    - one instance drives at most one :meth:`run`; callers build a fresh
      scheduler per start (the routes do).

    ``capture_fn`` is synchronous (it wraps blocking broker I/O) and runs via
    ``asyncio.to_thread`` so the event loop stays responsive. ``clock`` and
    ``sleeper`` are injectable for deterministic tests.
    """

    def __init__(
        self,
        capture_fn: Callable[[], CaptureReport],
        config: ScheduleConfig,
        clock: Callable[[], float] = time.time,
        sleeper: Callable[[float], Awaitable[None]] = asyncio.sleep,
        on_capture: Callable[[CaptureReport], Awaitable[None]] | None = None,
        before_capture: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        self._capture_fn = capture_fn
        self._config = config
        self._clock = clock
        self._sleeper = sleeper
        self._on_capture = on_capture
        self._before_capture = before_capture
        self._stop_event = asyncio.Event()
        self._running = False
        self._last_run_ts: float | None = None
        self._last_error: str | None = None
        self._n_captures = 0
        self._n_failures = 0
        self._auth_required = False
        #: (epoch, ok, detail) per capture attempt, newest last, for the status route.
        self.history: deque[tuple[float, bool, str]] = deque(maxlen=HISTORY_MAXLEN)

    async def run(self) -> None:
        """Loop until :meth:`stop`: capture while the market is open, else sleep.

        When open, one capture attempt fires per ``interval_seconds``; when
        closed, the loop sleeps straight through to the next market open.
        Capture exceptions are recorded and never propagate.
        """
        if self._running:
            raise RuntimeError("CaptureScheduler.run() is already active on this instance")
        self._running = True
        try:
            while not self._stop_event.is_set():
                now = self._clock()
                if is_market_open(now, self._config):
                    await self._capture_once(now)
                    delay = float(self._config.interval_seconds)
                else:
                    delay = next_market_open(now, self._config) - now
                await self._sleep_or_stop(delay)
        finally:
            self._running = False

    def stop(self) -> None:
        """Request a clean exit: run() wakes from any sleep and returns."""
        self._stop_event.set()

    def status(self) -> SchedulerStatus:
        """Current counters plus the estimated next capture instant."""
        now = self._clock()
        return SchedulerStatus(
            running=self._running,
            last_run_ts=self._last_run_ts,
            last_error=self._last_error,
            n_captures=self._n_captures,
            n_failures=self._n_failures,
            next_eligible_ts=self._next_eligible_ts(now),
            auth_required=self._auth_required,
        )

    async def _capture_once(self, now: float) -> None:
        """One capture attempt in a worker thread; outcome recorded, never raised."""
        self._last_run_ts = now

        if self._before_capture is not None:
            try:
                await self._before_capture()
            except AuthError as exc:
                # Distinct from a capture failure: only re-login clears it, so
                # flag it for the UI instead of counting another silent retry.
                self._auth_required = True
                self._n_failures += 1
                self._last_error = f"Authentication required: {exc}"
                self.history.append((now, False, self._last_error))
                return
            except Exception as exc:
                self._n_failures += 1
                self._last_error = f"Pre-capture step failed: {type(exc).__name__}: {exc}"
                self.history.append((now, False, self._last_error))
                return

        try:
            report = await asyncio.to_thread(self._capture_fn)
        except Exception as exc:  # unattended loop: record and continue
            self._n_failures += 1
            self._last_error = f"{type(exc).__name__}: {exc}"
            self.history.append((now, False, self._last_error))
        else:
            self._n_captures += 1
            self._last_error = None
            self._auth_required = False
            detail = f"stored {report.n_clean}/{report.n_raw} clean quotes at {report.path}"
            self.history.append((now, True, detail))
            if self._on_capture is not None:
                try:
                    await self._on_capture(report)
                except Exception as cb_exc:
                    logger.warning("on_capture callback failed: %s", cb_exc)

    async def _sleep_or_stop(self, delay: float) -> None:
        """Wait ``delay`` seconds via the injected sleeper, waking early on stop()."""
        if self._stop_event.is_set():
            return
        sleep_task = asyncio.ensure_future(self._sleeper(delay))
        stop_task = asyncio.ensure_future(self._stop_event.wait())
        try:
            await asyncio.wait({sleep_task, stop_task}, return_when=asyncio.FIRST_COMPLETED)
        finally:
            sleep_task.cancel()
            stop_task.cancel()
            # Retrieve results so cancelled tasks don't warn at teardown.
            await asyncio.gather(sleep_task, stop_task, return_exceptions=True)

    def _next_eligible_ts(self, now: float) -> float:
        """Earliest epoch at which the loop could fire its next capture."""
        if is_market_open(now, self._config):
            if self._last_run_ts is None:
                return now
            return max(now, self._last_run_ts + self._config.interval_seconds)
        return next_market_open(now, self._config)


__all__ = [
    "CaptureScheduler",
    "ScheduleConfig",
    "SchedulerStatus",
    "is_market_open",
    "next_market_open",
]

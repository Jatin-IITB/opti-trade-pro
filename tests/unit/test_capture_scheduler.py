# tests/unit/test_capture_scheduler.py
"""Unit tests for the unattended capture scheduler (no network, no wall clock).

Calendar logic is tested against fixed 2026 timestamps built with zoneinfo
(Sat 2026-08-22 .. Tue 2026-08-25 around Monday 2026-08-24). The run loop is
driven by a simulated timeline: the injected clock only advances when the
injected sleeper is awaited, so days of market time replay deterministically in
milliseconds. Route tests use a stub scheduler class so no real loop or clock
is involved.
"""

from __future__ import annotations

import asyncio
from collections import deque
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
import pytest
from fastapi.testclient import TestClient

from options_trading.api import dependencies
from options_trading.api.routes import capture as capture_routes
from options_trading.services.capture_scheduler import (
    CaptureScheduler,
    ScheduleConfig,
    SchedulerStatus,
    is_market_open,
    next_market_open,
)
from options_trading.services.capture_service import CaptureReport
from optitrade.data import SnapshotStore

IST = ZoneInfo("Asia/Kolkata")
CONFIG = ScheduleConfig()
EXPIRY_DATE = "2026-08-27"

# Fixed 2026 calendar anchors: 22nd Saturday, 23rd Sunday, 24th Monday, 25th Tuesday.
SAT, SUN, MON, TUE = 22, 23, 24, 25


def ist_ts(day: int, hour: int, minute: int, second: int = 0) -> float:
    """Epoch of an August 2026 IST wall time (no wall clock involved)."""
    return datetime(2026, 8, day, hour, minute, second, tzinfo=IST).timestamp()


def _fake_report(ts: float) -> CaptureReport:
    return CaptureReport(
        path=f"runtime/snapshots/NIFTY/{int(ts)}.parquet",
        n_raw=10,
        n_clean=9,
        rejection_stats={"crossed_book": 1},
        spot=24_500.0,
        timestamp=ts,
    )


# ---------------------------------------------------------------------------
# is_market_open / next_market_open (pure calendar functions)
# ---------------------------------------------------------------------------


class TestIsMarketOpen:
    def test_weekday_inside_window(self) -> None:
        assert is_market_open(ist_ts(MON, 10, 0), CONFIG) is True

    def test_open_boundary_is_inclusive(self) -> None:
        assert is_market_open(ist_ts(MON, 9, 15), CONFIG) is True

    def test_before_open_is_closed(self) -> None:
        assert is_market_open(ist_ts(MON, 9, 14, 59), CONFIG) is False

    def test_close_boundary_is_exclusive(self) -> None:
        assert is_market_open(ist_ts(MON, 15, 29, 59), CONFIG) is True
        assert is_market_open(ist_ts(MON, 15, 30), CONFIG) is False

    def test_after_close_is_closed(self) -> None:
        assert is_market_open(ist_ts(MON, 16, 0), CONFIG) is False

    def test_weekend_is_closed(self) -> None:
        assert is_market_open(ist_ts(SAT, 10, 0), CONFIG) is False
        assert is_market_open(ist_ts(SUN, 10, 0), CONFIG) is False

    def test_configured_holiday_is_closed(self) -> None:
        holiday_config = ScheduleConfig(holidays=("2026-08-24",))
        assert is_market_open(ist_ts(MON, 10, 0), holiday_config) is False
        assert is_market_open(ist_ts(TUE, 10, 0), holiday_config) is True

    def test_timezone_conversion_from_utc_timestamps(self) -> None:
        # 04:30 UTC == 10:00 IST (open); a UTC-naive implementation would read
        # 04:30 and report closed. 10:30 UTC == 16:00 IST (closed).
        open_utc = datetime(2026, 8, 24, 4, 30, tzinfo=UTC).timestamp()
        closed_utc = datetime(2026, 8, 24, 10, 30, tzinfo=UTC).timestamp()
        assert is_market_open(open_utc, CONFIG) is True
        assert is_market_open(closed_utc, CONFIG) is False


class TestNextMarketOpen:
    def test_from_saturday_noon_is_monday_open(self) -> None:
        assert next_market_open(ist_ts(SAT, 12, 0), CONFIG) == ist_ts(MON, 9, 15)

    def test_holiday_pushes_to_next_trading_day(self) -> None:
        holiday_config = ScheduleConfig(holidays=("2026-08-24",))
        assert next_market_open(ist_ts(SAT, 12, 0), holiday_config) == ist_ts(TUE, 9, 15)

    def test_intraday_next_open_is_tomorrow(self) -> None:
        assert next_market_open(ist_ts(MON, 10, 0), CONFIG) == ist_ts(TUE, 9, 15)

    def test_config_with_no_trading_days_fails_closed(self) -> None:
        with pytest.raises(ValueError, match="no trading day"):
            next_market_open(ist_ts(SAT, 12, 0), ScheduleConfig(trading_days=()))


class TestScheduleConfigValidation:
    def test_non_positive_interval_rejected(self) -> None:
        with pytest.raises(ValueError, match="interval_seconds"):
            ScheduleConfig(interval_seconds=0)

    def test_bad_time_format_rejected(self) -> None:
        with pytest.raises(ValueError, match="HH:MM"):
            ScheduleConfig(market_open="0915")

    def test_open_must_precede_close(self) -> None:
        with pytest.raises(ValueError, match="before market_close"):
            ScheduleConfig(market_open="15:30", market_close="09:15")

    def test_bad_weekday_rejected(self) -> None:
        with pytest.raises(ValueError, match=r"0\.\.6"):
            ScheduleConfig(trading_days=(0, 7))

    def test_bad_holiday_date_rejected(self) -> None:
        with pytest.raises(ValueError, match="YYYY-MM-DD"):
            ScheduleConfig(holidays=("24-08-2026",))

    def test_unknown_timezone_rejected(self) -> None:
        with pytest.raises(ValueError, match="timezone"):
            ScheduleConfig(timezone="Mars/Olympus_Mons")


# ---------------------------------------------------------------------------
# Run loop on a simulated timeline
# ---------------------------------------------------------------------------


class SimClock:
    """Deterministic timeline: time advances only when the loop sleeps.

    When the simulated time reaches ``stop_at`` the scheduler is stopped from
    inside the sleeper (which runs on the event loop, where stop() is safe).
    """

    def __init__(self, start: float, stop_at: float) -> None:
        self.now = start
        self.stop_at = stop_at
        self.scheduler: CaptureScheduler | None = None

    def clock(self) -> float:
        return self.now

    async def sleep(self, delay: float) -> None:
        self.now += delay
        if self.now >= self.stop_at and self.scheduler is not None:
            self.scheduler.stop()
        await asyncio.sleep(0)


async def _run_to_completion(scheduler: CaptureScheduler) -> None:
    # The 10s bound is a hang guard only; the simulated run finishes in ms.
    await asyncio.wait_for(scheduler.run(), timeout=10.0)


class TestRunLoop:
    async def test_interval_cadence_during_market_hours(self) -> None:
        sim = SimClock(start=ist_ts(MON, 10, 0), stop_at=ist_ts(MON, 11, 0))
        captured: list[float] = []

        def capture() -> CaptureReport:
            captured.append(sim.now)
            return _fake_report(sim.now)

        scheduler = CaptureScheduler(
            capture_fn=capture, config=CONFIG, clock=sim.clock, sleeper=sim.sleep
        )
        sim.scheduler = scheduler
        await _run_to_completion(scheduler)

        expected = [
            ist_ts(MON, 10, 0),
            ist_ts(MON, 10, 15),
            ist_ts(MON, 10, 30),
            ist_ts(MON, 10, 45),
        ]
        assert captured == expected

        status = scheduler.status()
        assert status.running is False
        assert status.n_captures == 4
        assert status.n_failures == 0
        assert status.last_run_ts == ist_ts(MON, 10, 45)
        assert status.last_error is None
        # 11:00, market open: last run 10:45 + 900s == now.
        assert status.next_eligible_ts == ist_ts(MON, 11, 0)

        assert len(scheduler.history) == 4
        assert all(ok for _, ok, _ in scheduler.history)
        ts0, ok0, detail0 = scheduler.history[0]
        assert (ts0, ok0) == (ist_ts(MON, 10, 0), True)
        assert "9/10" in detail0 and detail0.endswith(".parquet")

    async def test_failure_is_recorded_and_loop_survives(self) -> None:
        sim = SimClock(start=ist_ts(MON, 10, 0), stop_at=ist_ts(MON, 11, 0))
        attempts: list[float] = []

        def capture() -> CaptureReport:
            attempts.append(sim.now)
            if len(attempts) == 2:
                raise RuntimeError("Upstox 429: rate limited")
            return _fake_report(sim.now)

        scheduler = CaptureScheduler(
            capture_fn=capture, config=CONFIG, clock=sim.clock, sleeper=sim.sleep
        )
        sim.scheduler = scheduler
        await _run_to_completion(scheduler)

        assert len(attempts) == 4  # the failure did not end the loop
        status = scheduler.status()
        assert status.n_captures == 3
        assert status.n_failures == 1
        assert status.last_error is None  # most recent attempt succeeded
        ts_fail, ok_fail, detail_fail = scheduler.history[1]
        assert (ts_fail, ok_fail) == (ist_ts(MON, 10, 15), False)
        assert detail_fail == "RuntimeError: Upstox 429: rate limited"

    async def test_weekend_sleeps_through_to_monday_open(self) -> None:
        sim = SimClock(start=ist_ts(SAT, 12, 0), stop_at=ist_ts(MON, 9, 20))
        captured: list[float] = []

        def capture() -> CaptureReport:
            captured.append(sim.now)
            return _fake_report(sim.now)

        scheduler = CaptureScheduler(
            capture_fn=capture, config=CONFIG, clock=sim.clock, sleeper=sim.sleep
        )
        sim.scheduler = scheduler
        await _run_to_completion(scheduler)

        assert captured == [ist_ts(MON, 9, 15)]  # nothing fired on Sat/Sun

    async def test_holiday_sleeps_through_to_tuesday_open(self) -> None:
        holiday_config = ScheduleConfig(holidays=("2026-08-24",))
        sim = SimClock(start=ist_ts(SAT, 12, 0), stop_at=ist_ts(TUE, 9, 20))
        captured: list[float] = []

        def capture() -> CaptureReport:
            captured.append(sim.now)
            return _fake_report(sim.now)

        scheduler = CaptureScheduler(
            capture_fn=capture, config=holiday_config, clock=sim.clock, sleeper=sim.sleep
        )
        sim.scheduler = scheduler
        await _run_to_completion(scheduler)

        assert captured == [ist_ts(TUE, 9, 15)]

    async def test_stop_during_closed_market_exits_without_captures(self) -> None:
        sim = SimClock(start=ist_ts(SAT, 12, 0), stop_at=ist_ts(SAT, 13, 0))
        captured: list[float] = []

        def capture() -> CaptureReport:
            captured.append(sim.now)
            return _fake_report(sim.now)

        scheduler = CaptureScheduler(
            capture_fn=capture, config=CONFIG, clock=sim.clock, sleeper=sim.sleep
        )
        sim.scheduler = scheduler
        await _run_to_completion(scheduler)

        assert captured == []
        status = scheduler.status()
        assert status.running is False
        assert status.n_captures == 0
        assert status.last_run_ts is None

    async def test_stop_wakes_a_pending_real_sleep(self) -> None:
        # Default sleeper (asyncio.sleep) with a ~2-day weekend delay: stop()
        # must wake it immediately instead of waiting the delay out.
        scheduler = CaptureScheduler(
            capture_fn=lambda: _fake_report(0.0),
            config=CONFIG,
            clock=lambda: ist_ts(SAT, 12, 0),
        )
        task = asyncio.create_task(scheduler.run())
        for _ in range(5):
            await asyncio.sleep(0)
        assert scheduler.status().running is True
        scheduler.stop()
        await asyncio.wait_for(task, timeout=5.0)  # hang guard only
        assert scheduler.status().running is False

    async def test_run_twice_on_one_instance_raises(self) -> None:
        scheduler = CaptureScheduler(
            capture_fn=lambda: _fake_report(0.0),
            config=CONFIG,
            clock=lambda: ist_ts(SAT, 12, 0),
        )
        task = asyncio.create_task(scheduler.run())
        for _ in range(5):
            await asyncio.sleep(0)
        with pytest.raises(RuntimeError, match="already active"):
            await scheduler.run()
        scheduler.stop()
        await asyncio.wait_for(task, timeout=5.0)

    def test_status_next_eligible_while_market_closed(self) -> None:
        scheduler = CaptureScheduler(
            capture_fn=lambda: _fake_report(0.0),
            config=CONFIG,
            clock=lambda: ist_ts(SAT, 12, 0),
        )
        status = scheduler.status()
        assert status == SchedulerStatus(
            running=False,
            last_run_ts=None,
            last_error=None,
            n_captures=0,
            n_failures=0,
            next_eligible_ts=ist_ts(MON, 9, 15),
        )


# ---------------------------------------------------------------------------
# Schedule routes
# ---------------------------------------------------------------------------

START_BODY = {
    "underlying": "NIFTY",
    "instrument_key": "NSE_INDEX|Nifty 50",
    "expiry_date": EXPIRY_DATE,
}


class StubScheduler:
    """Deterministic CaptureScheduler stand-in: run() waits for stop(), no clock."""

    def __init__(self, capture_fn, config, clock=None, sleeper=None, on_capture=None) -> None:  # type: ignore[no-untyped-def]
        self.capture_fn = capture_fn
        self.config = config
        self._running = False
        self._stop_event = asyncio.Event()
        self.history: deque[tuple[float, bool, str]] = deque(maxlen=100)

    async def run(self) -> None:
        self._running = True
        await self._stop_event.wait()
        self._running = False

    def stop(self) -> None:
        self._stop_event.set()

    def status(self) -> SchedulerStatus:
        return SchedulerStatus(
            running=self._running,
            last_run_ts=None,
            last_error=None,
            n_captures=0,
            n_failures=0,
            next_eligible_ts=None,
        )


class TestScheduleRoutes:
    @pytest.fixture
    def app(self):  # type: ignore[no-untyped-def]
        from options_trading.main import create_app

        application = create_app()
        yield application
        application.dependency_overrides.clear()

    @pytest.fixture
    def client(self, app) -> TestClient:  # type: ignore[no-untyped-def]
        # No context manager: lifespan (real AuthService startup) must not run.
        return TestClient(app)

    @pytest.fixture
    def stub_schedulers(self, monkeypatch: pytest.MonkeyPatch) -> list[StubScheduler]:
        created: list[StubScheduler] = []

        class RecordingStub(StubScheduler):
            def __init__(self, *args, **kwargs) -> None:  # type: ignore[no-untyped-def]
                super().__init__(*args, **kwargs)
                created.append(self)

        monkeypatch.setattr(capture_routes, "CaptureScheduler", RecordingStub)
        return created

    @pytest.fixture
    def authed(self, app, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
        app.dependency_overrides[capture_routes.get_access_token] = lambda: "sched-token"
        monkeypatch.setattr(capture_routes.settings, "snapshot_store_path", str(tmp_path))

    async def test_start_status_stop_lifecycle(
        self, app, authed: None, stub_schedulers: list[StubScheduler]
    ) -> None:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            idle = await client.get("/api/v1/capture/schedule/status")
            assert idle.status_code == 200
            assert idle.json()["running"] is False
            assert idle.json()["history"] == []

            start = await client.post("/api/v1/capture/schedule/start", json=START_BODY)
            assert start.status_code == 200
            body = start.json()
            assert body["started"] is True
            assert body["underlying"] == "NIFTY"
            assert body["expiry_date"] == EXPIRY_DATE
            assert body["interval_seconds"] == 900  # settings default
            assert len(stub_schedulers) == 1
            assert stub_schedulers[0].config.interval_seconds == 900

            await asyncio.sleep(0)  # let the scheduler task start
            status = await client.get("/api/v1/capture/schedule/status")
            assert status.status_code == 200
            assert status.json()["running"] is True

            duplicate = await client.post("/api/v1/capture/schedule/start", json=START_BODY)
            assert duplicate.status_code == 409
            assert len(stub_schedulers) == 1  # no second scheduler was built

            stop = await client.post("/api/v1/capture/schedule/stop")
            assert stop.status_code == 200
            assert stop.json()["stopped"] is True
            assert stop.json()["running"] is False

            second_stop = await client.post("/api/v1/capture/schedule/stop")
            assert second_stop.status_code == 409

            # A stopped scheduler does not block a fresh start.
            restart = await client.post(
                "/api/v1/capture/schedule/start", json={**START_BODY, "interval_seconds": 300}
            )
            assert restart.status_code == 200
            assert restart.json()["interval_seconds"] == 300
            assert stub_schedulers[1].config.interval_seconds == 300

            final_stop = await client.post("/api/v1/capture/schedule/stop")
            assert final_stop.status_code == 200

    async def test_start_builds_closure_around_capture_and_store(
        self,
        app,
        authed: None,
        stub_schedulers: list[StubScheduler],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        recorded: dict = {}

        class FakeSource:
            def __init__(self, access_token: str, instrument_key: str, expiry_date: str) -> None:
                recorded["access_token"] = access_token
                recorded["instrument_key"] = instrument_key
                recorded["expiry_date"] = expiry_date

        fake_report = _fake_report(1_000.0)

        def fake_capture_and_store(source, store, underlying, config=None):  # type: ignore[no-untyped-def]
            recorded["source"] = source
            recorded["store"] = store
            recorded["underlying"] = underlying
            return fake_report

        monkeypatch.setattr(capture_routes, "UpstoxCaptureSource", FakeSource)
        monkeypatch.setattr(capture_routes, "capture_and_store", fake_capture_and_store)

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            start = await client.post("/api/v1/capture/schedule/start", json=START_BODY)
            assert start.status_code == 200

            # The zero-arg closure handed to the scheduler must run one full
            # capture with the token, source params, and store from the route.
            report = stub_schedulers[0].capture_fn()
            assert report is fake_report
            assert recorded["access_token"] == "sched-token"
            assert recorded["instrument_key"] == "NSE_INDEX|Nifty 50"
            assert recorded["expiry_date"] == EXPIRY_DATE
            assert isinstance(recorded["source"], FakeSource)
            assert isinstance(recorded["store"], SnapshotStore)
            assert recorded["underlying"] == "NIFTY"

            stop = await client.post("/api/v1/capture/schedule/stop")
            assert stop.status_code == 200

    async def test_default_interval_comes_from_settings(
        self,
        app,
        authed: None,
        stub_schedulers: list[StubScheduler],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(capture_routes.settings, "capture_interval_seconds", 600)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            start = await client.post("/api/v1/capture/schedule/start", json=START_BODY)
            assert start.status_code == 200
            assert start.json()["interval_seconds"] == 600
            assert stub_schedulers[0].config.interval_seconds == 600
            await client.post("/api/v1/capture/schedule/stop")

    def test_start_unauthenticated_is_401(self, app, client: TestClient) -> None:
        class NoAuth:
            async def __aenter__(self) -> NoAuth:
                return self

            async def __aexit__(self, *exc: object) -> bool:
                return False

            async def get_valid_access_token(self, user_id: str = "default") -> str:
                raise RuntimeError("no stored token")

        app.dependency_overrides[dependencies.get_auth_service] = lambda: NoAuth()

        resp = client.post("/api/v1/capture/schedule/start", json=START_BODY)
        assert resp.status_code == 401

    def test_start_with_bad_expiry_is_422(self, app, authed: None, client: TestClient) -> None:
        resp = client.post(
            "/api/v1/capture/schedule/start",
            json={**START_BODY, "expiry_date": "27-08-2026"},
        )
        assert resp.status_code == 422
        assert "YYYY-MM-DD" in resp.json()["detail"]

    def test_stop_without_scheduler_is_409(self, app, client: TestClient) -> None:
        resp = client.post("/api/v1/capture/schedule/stop")
        assert resp.status_code == 409

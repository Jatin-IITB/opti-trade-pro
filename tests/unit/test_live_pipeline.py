"""Tests for the live pipeline orchestration service."""

from typing import ClassVar
from unittest.mock import AsyncMock, MagicMock

import pytest

from options_trading.services.capture_service import CaptureReport
from options_trading.services.live_analytics import LiveDashboardPayload
from options_trading.services.live_pipeline import LivePipelineConfig, LivePipelineService
from optitrade.data.capture import SyntheticSource


@pytest.fixture()
def ws_manager():
    mgr = MagicMock()
    mgr.send_dashboard_update = AsyncMock(return_value=1)
    return mgr


@pytest.fixture()
def pipeline(ws_manager):
    return LivePipelineService(
        ws_manager=ws_manager,
        config=LivePipelineConfig(underlying="NIFTY"),
    )


@pytest.fixture()
def chain():
    return SyntheticSource(seed=42).fetch_chain("NIFTY")


@pytest.fixture()
def report():
    return CaptureReport(
        path="/tmp/test.parquet",
        n_raw=50,
        n_clean=40,
        rejection_stats={"crossed_book": 5, "wide_spread": 5},
        spot=24500.0,
        timestamp=1_755_500_000.0,
    )


class TestLivePipelineService:
    def test_no_snapshot_initially(self, pipeline):
        assert pipeline.get_latest_snapshot() is None

    @pytest.mark.asyncio
    async def test_on_capture_skips_without_chain(self, pipeline, report, ws_manager):
        await pipeline.on_capture(report)
        ws_manager.send_dashboard_update.assert_not_called()

    @pytest.mark.asyncio
    async def test_on_capture_broadcasts(self, pipeline, report, chain, ws_manager):
        pipeline.cache_chain(chain)
        await pipeline.on_capture(report)
        ws_manager.send_dashboard_update.assert_called_once()
        payload = ws_manager.send_dashboard_update.call_args[0][0]
        assert "volSurface" in payload
        assert "spot" in payload
        assert payload["spot"] == chain.spot

    @pytest.mark.asyncio
    async def test_on_capture_stores_snapshot(self, pipeline, report, chain):
        pipeline.cache_chain(chain)
        await pipeline.on_capture(report)
        snapshot = pipeline.get_latest_snapshot()
        assert snapshot is not None
        assert snapshot.spot == chain.spot
        assert snapshot.underlying == chain.underlying

    @pytest.mark.asyncio
    async def test_on_capture_survives_analytics_failure(self, pipeline, report, ws_manager):
        pipeline.cache_chain(
            MagicMock(
                underlying="NIFTY",
                spot=24500.0,
                timestamp=0.0,
                rate=0.065,
                dividend_yield=0.0,
                quotes=(),
            )
        )
        await pipeline.on_capture(report)
        ws_manager.send_dashboard_update.assert_not_called()
        assert pipeline.get_latest_snapshot() is None

    def test_cache_chain(self, pipeline, chain):
        pipeline.cache_chain(chain)
        assert pipeline._last_chain is chain

    def test_latest_spot_is_none_before_first_capture(self, pipeline):
        assert pipeline.get_latest_spot() is None

    @pytest.mark.asyncio
    async def test_latest_spot_after_capture(self, pipeline, report, chain):
        pipeline.cache_chain(chain)
        await pipeline.on_capture(report)
        assert pipeline.get_latest_spot() == chain.spot


class TestWireFormat:
    """The push and pull paths must serialize identically.

    Regression guard: the snapshot paths used ``dataclasses.asdict``, which
    emits snake_case keys. The frontend only reads camelCase, so every
    ``request_snapshot`` reply and every ``GET /dashboard/live/snapshot`` was
    silently discarded while appearing to succeed.
    """

    EXPECTED_KEYS: ClassVar[set[str]] = {
        "volSurface",
        "optionChain",
        "greeksComparison",
        "essviCalibration",
        "riskDashboard",
        "scenarioGrid",
        "higherOrderGreeks",
        "timestamp",
        "underlying",
        "spot",
        # Freshness. Always emitted: the frontend fails closed and treats
        # anything but an explicit isLive=true as not current, so a payload
        # that dropped these would wear a "market closed" notice forever.
        "isLive",
        "asOfNote",
    }

    def test_wire_dict_uses_camel_case(self):
        payload = LiveDashboardPayload(
            vol_surface={"a": 1},
            option_chain={"b": 2},
            greeks_book={"c": 3},
            essvi_calibration={"d": 4},
            risk_dashboard={"e": 5},
            scenario_grid={"f": 6},
            higher_order_greeks={"g": 7},
            timestamp=1.0,
            underlying="NIFTY",
            spot=24500.0,
        )
        wire = payload.to_wire_dict()
        assert set(wire) == self.EXPECTED_KEYS
        assert not any("_" in k for k in wire)
        assert wire["greeksComparison"] == {"c": 3}
        assert wire["spot"] == 24500.0

    @pytest.mark.asyncio
    async def test_broadcast_and_snapshot_agree(self, pipeline, report, chain, ws_manager):
        """The live keys agree; the broadcast adds the history ones on top."""
        pipeline.cache_chain(chain)
        await pipeline.on_capture(report)
        broadcast = ws_manager.send_dashboard_update.call_args[0][0]
        snapshot = pipeline.get_latest_snapshot().to_wire_dict()
        assert set(snapshot) == self.EXPECTED_KEYS
        assert set(broadcast) >= self.EXPECTED_KEYS
        for key in self.EXPECTED_KEYS:
            assert broadcast[key] == snapshot[key]


class TestHistoryMerge:
    """The history panels ride the same broadcast as the live ones.

    Their keys must reach every delivery path: a key the backend computes but
    that never arrives leaves the frontend rendering bundled demo data
    forever, which is exactly the failure this phase set out to remove.
    """

    HISTORY_KEYS: ClassVar[set[str]] = {"vrpSignal", "backtestEquity", "pnlExplain"}

    @staticmethod
    def _history(wire: dict):
        history = MagicMock()
        history.build_async = AsyncMock(return_value=MagicMock(to_wire_dict=lambda: wire))
        return history

    @pytest.fixture()
    def wire(self):
        return {key: {"hasHistory": False} for key in self.HISTORY_KEYS} | {
            "historyCoverage": {"daysAvailable": 0}
        }

    async def test_broadcast_carries_the_history_panels(self, ws_manager, chain, report, wire):
        pipeline = LivePipelineService(
            ws_manager=ws_manager,
            config=LivePipelineConfig(underlying="NIFTY"),
            history=self._history(wire),
        )
        pipeline.cache_chain(chain)

        await pipeline.on_capture(report)

        payload = ws_manager.send_dashboard_update.call_args[0][0]
        assert set(payload) >= self.HISTORY_KEYS
        assert "volSurface" in payload, "live panels must survive the merge"

    async def test_history_failure_fails_closed_not_open(self, ws_manager, chain, report):
        """A failed replay must still emit the keys, marked unavailable.

        Omitting them fails *open*: the frontend merges only the keys it
        receives, so an absent key leaves whatever was there before. Before
        this was fixed, any exception in the replay put the previous panel
        contents back on screen beside a live spot and timestamp — the exact
        fabrication this phase removed (ADR-008).
        """
        history = MagicMock()
        history.build_async = AsyncMock(side_effect=RuntimeError("replay exploded"))
        pipeline = LivePipelineService(
            ws_manager=ws_manager,
            config=LivePipelineConfig(underlying="NIFTY"),
            history=history,
        )
        pipeline.cache_chain(chain)

        await pipeline.on_capture(report)

        payload = ws_manager.send_dashboard_update.call_args[0][0]
        assert "volSurface" in payload, "live panels must survive"
        assert set(payload) >= self.HISTORY_KEYS, "keys must be present, not dropped"
        for key in self.HISTORY_KEYS:
            assert payload[key]["hasHistory"] is False
            assert payload[key]["reason"]

    async def test_pull_path_matches_the_push_path(self, ws_manager, chain, report, wire):
        """A reconnecting client must not get a payload missing panels."""
        pipeline = LivePipelineService(
            ws_manager=ws_manager,
            config=LivePipelineConfig(underlying="NIFTY"),
            history=self._history(wire),
        )
        pipeline.cache_chain(chain)
        await pipeline.on_capture(report)

        pushed = ws_manager.send_dashboard_update.call_args[0][0]
        pulled = await pipeline.build_wire_dict(pipeline.get_latest_snapshot())

        assert set(pushed) == set(pulled)

    async def test_no_history_service_still_reports_the_panels_as_empty(
        self, pipeline, ws_manager, chain, report
    ):
        """Same fail-closed rule when no history service is wired at all."""
        pipeline.cache_chain(chain)

        await pipeline.on_capture(report)

        payload = ws_manager.send_dashboard_update.call_args[0][0]
        assert set(payload) >= self.HISTORY_KEYS
        assert all(payload[key]["hasHistory"] is False for key in self.HISTORY_KEYS)

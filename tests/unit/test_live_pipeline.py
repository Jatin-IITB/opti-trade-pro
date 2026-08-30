"""Tests for the live pipeline orchestration service."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from options_trading.services.capture_service import CaptureReport
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

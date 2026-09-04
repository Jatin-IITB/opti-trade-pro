"""Tests for the dashboard REST routes.

These cover the HTTP envelope rather than the payload maths — ``test_live_pipeline``
already pins the wire format. The envelope is what broke in production: the
"no capture yet" reply framed itself as a 204 and then wrote a body, which a
real ASGI server rejects mid-response.
"""

from __future__ import annotations

import asyncio
import pathlib
import re
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from options_trading.api.routes.dashboard import router as dashboard_router
from options_trading.services.capture_service import CaptureReport
from options_trading.services.live_pipeline import LivePipelineConfig, LivePipelineService
from optitrade.data.capture import SyntheticSource

pytestmark = pytest.mark.unit

ROUTES_ROOT = (
    pathlib.Path(__file__).resolve().parents[2] / "src" / "options_trading" / "api" / "routes"
)


def build_app(pipeline: LivePipelineService | None) -> TestClient:
    app = FastAPI()
    app.include_router(dashboard_router)
    app.state.live_pipeline = pipeline
    return TestClient(app)


@pytest.fixture()
def pipeline() -> LivePipelineService:
    ws_manager = MagicMock()
    ws_manager.send_dashboard_update = AsyncMock(return_value=1)
    return LivePipelineService(
        ws_manager=ws_manager,
        config=LivePipelineConfig(underlying="NIFTY"),
    )


class TestLiveSnapshotEnvelope:
    """The framing of GET /dashboard/live/snapshot in each of its three states."""

    def test_no_capture_yet_is_an_empty_204(self, pipeline):
        """A 204 must carry no body at all.

        The route used to answer ``JSONResponse(status_code=204, content=None)``,
        which serialises to b"null". uvicorn derives Content-Length: 0 from the
        status, rejects the four extra bytes, and tears the connection down
        while logging a 500 — every single time no capture had run yet.
        """
        response = build_app(pipeline).get("/dashboard/live/snapshot")

        assert response.status_code == 204
        assert response.content == b""

    def test_a_capture_still_answers_with_the_payload(self, pipeline):
        """The empty 204 must not be masking a snapshot that exists."""
        pipeline.cache_chain(SyntheticSource(seed=42).fetch_chain("NIFTY"))
        report = CaptureReport(
            path="/tmp/test.parquet",
            n_raw=50,
            n_clean=40,
            rejection_stats={},
            spot=24500.0,
            timestamp=1_755_500_000.0,
        )

        asyncio.run(pipeline.on_capture(report))
        response = build_app(pipeline).get("/dashboard/live/snapshot")

        assert response.status_code == 200
        assert "volSurface" in response.json()

    def test_an_uninitialised_pipeline_fails_closed(self):
        assert build_app(None).get("/dashboard/live/snapshot").status_code == 503


class TestBodilessStatusesCarryNoBody:
    """A repo-wide guard, because TestClient cannot see this class of bug.

    httpx's ASGI transport hands the body straight to the caller, so a 204 with
    a body looks fine in every test we can write against the app object. Only a
    real server enforces the Content-Length the status implies. The guard is
    therefore static: it reads the source of the routes package instead.
    """

    BODILESS_STATUSES = ("204", "304")
    RESPONSE_CALL = re.compile(r"\b(\w*Response)\(([^()]*)\)", re.DOTALL)

    def test_no_route_gives_a_bodiless_status_a_body(self):
        offenders = []
        for path in sorted(ROUTES_ROOT.glob("*.py")):
            for cls, args in self.RESPONSE_CALL.findall(path.read_text()):
                if not any(f"status_code={s}" in args for s in self.BODILESS_STATUSES):
                    continue
                if cls != "Response" or "content=" in args:
                    offenders.append(f"{path.name}: {cls}({' '.join(args.split())})")

        assert offenders == [], (
            "204/304 forbid a response body; use a bare Response(status_code=...). "
            f"Offenders: {offenders}"
        )

    def test_the_guard_detects_the_shape_it_exists_to_catch(self):
        """Pin the regex itself, so the guard cannot silently stop matching."""
        matches = self.RESPONSE_CALL.findall("return JSONResponse(status_code=204, content=None)")

        assert matches == [("JSONResponse", "status_code=204, content=None")]

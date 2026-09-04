"""Tests for the analyst endpoint and its place in the wire contract.

Three things are checked here that a service-level test cannot see:

1. The route is registered under ``/api/v1`` and returns JSON — not the SPA
   shell. A router mounted after the ``StaticFiles`` catch-all answers every
   path with ``index.html`` at status 200, which looks like success.
2. ``build_wire_dict`` always emits the ``analysts`` key, including when the
   analyst service raises. Omitting it fails *open*: the frontend merges only
   the keys it receives, so an absent key leaves the previous reports on
   screen — stale prose still wearing its old grounded badges.
3. Nothing in this path can place an order.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from options_trading.api.routes.analysts import router as analysts_router
from options_trading.services.analyst_service import (
    AnalystService,
    AnalystServiceConfig,
    unavailable_analysts_wire,
)
from optitrade.journal import EventLog

FULL_FEATURES = {
    "ts": "2026-09-03",
    "spot": 23_873.45,
    "realized_vol": 0.0966,
    "atm_iv": 0.1400,
    "vrp": 0.0434,
    "term_slope": 0.0120,
    "skew_25d": -0.0077,
}


def _app_with_journal(tmp_path, *, seed: bool) -> FastAPI:
    directory = tmp_path / "journal"
    directory.mkdir(exist_ok=True)
    if seed:
        EventLog(directory, "desk").append("market_features", dict(FULL_FEATURES))
    app = FastAPI()
    app.include_router(analysts_router, prefix="/api/v1")
    app.state.analyst_service = AnalystService(
        directory, AnalystServiceConfig(journal_run_id="desk")
    )
    return app


class TestAnalystReportEndpoint:
    def test_reports_on_a_seeded_journal(self, tmp_path):
        with TestClient(_app_with_journal(tmp_path, seed=True)) as client:
            response = client.get("/api/v1/analysts/report")
        assert response.status_code == 200
        body = response.json()
        assert body["hasJournal"] is True
        assert [a["name"] for a in body["analysts"]] == ["regime_analyst"]

    def test_an_empty_desk_is_200_not_404(self, tmp_path):
        """ "Nothing journaled yet" is a fact about the desk, not an error."""
        with TestClient(_app_with_journal(tmp_path, seed=False)) as client:
            response = client.get("/api/v1/analysts/report")
        assert response.status_code == 200
        assert response.json()["hasJournal"] is False

    def test_response_is_json_not_the_spa_shell(self, tmp_path):
        with TestClient(_app_with_journal(tmp_path, seed=True)) as client:
            response = client.get("/api/v1/analysts/report")
        assert response.headers["content-type"].startswith("application/json")
        assert "<!doctype html" not in response.text.lower()

    def test_every_claim_arrives_with_a_verdict_and_its_citations(self, tmp_path):
        with TestClient(_app_with_journal(tmp_path, seed=True)) as client:
            body = response_json = client.get("/api/v1/analysts/report").json()
        assert response_json is body
        for claim in body["analysts"][0]["claims"]:
            assert isinstance(claim["grounded"], bool)
            assert claim["citations"], "a claim must cite the journal"
            assert claim["statement"]

    def test_failures_reach_the_client(self, tmp_path):
        with TestClient(_app_with_journal(tmp_path, seed=True)) as client:
            body = client.get("/api/v1/analysts/report").json()
        assert {f["name"] for f in body["failures"]} == {
            "surface_auditor",
            "post_mortem_analyst",
        }

    def test_the_service_is_reused_across_requests(self, tmp_path):
        """A per-request service would re-audit the whole journal each call."""
        app = _app_with_journal(tmp_path, seed=True)
        with TestClient(app) as client:
            client.get("/api/v1/analysts/report")
            first = app.state.analyst_service
            client.get("/api/v1/analysts/report")
        assert app.state.analyst_service is first

    def test_the_route_works_without_preconfigured_state(self, tmp_path, monkeypatch):
        """Lazily constructed, so a bare app still answers rather than 500s."""
        monkeypatch.setattr(
            "options_trading.config.settings.settings.desk_journal_dir",
            str(tmp_path / "absent"),
        )
        app = FastAPI()
        app.include_router(analysts_router, prefix="/api/v1")
        with TestClient(app) as client:
            response = client.get("/api/v1/analysts/report")
        assert response.status_code == 200
        assert response.json()["hasJournal"] is False


class TestRegisteredInTheApp:
    """The real app, not a bare one: registration order is the risk."""

    def test_the_route_is_in_the_real_app(self):
        """Asserted through the OpenAPI schema.

        This FastAPI version keeps an included router as a single
        ``_IncludedRouter`` entry in ``app.routes`` rather than flattening its
        paths, so walking ``app.routes`` for a path finds nothing whether the
        router is registered or not — a check that cannot fail is worse than
        no check. The generated schema resolves the routers.
        """
        from options_trading.main import app

        assert "/api/v1/analysts/report" in app.openapi()["paths"]

    def test_it_answers_json_in_the_real_app(self):
        """The end-to-end version of the ordering rule.

        If the router were registered after the ``StaticFiles(html=True)``
        mount at ``/``, this path would return the HTML shell at status 200 —
        a failure that looks exactly like success to a fetch() call.
        """
        from options_trading.main import app

        with TestClient(app) as client:
            response = client.get("/api/v1/analysts/report")
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("application/json")
        assert "hasJournal" in response.json()

    def test_it_is_registered_before_the_static_mount(self):
        """Source order, because the mount only exists once a build has run.

        With no ``frontend/dist`` in the checkout there is no catch-all mount
        to be ordered against, so a runtime index comparison would pass
        vacuously in exactly the environment where the rule is easiest to
        break. The rule is about the order of two lines, so that is what is
        asserted.
        """
        source = (
            Path(__file__).resolve().parents[2] / "src" / "options_trading" / "main.py"
        ).read_text(encoding="utf-8")
        lines = source.splitlines()
        include = next(
            i for i, line in enumerate(lines) if "include_router(analysts_router" in line
        )
        mounts = [i for i, line in enumerate(lines) if "app.mount(" in line]
        assert mounts, "expected a StaticFiles mount in main.py"
        assert all(include < i for i in mounts), (
            "the analysts router is included after app.mount(); every "
            "/api/v1/analysts call would return index.html at status 200"
        )


class TestWireContractAlwaysEmitsTheKey:
    """The failure mode is silent, so it gets its own tests."""

    @pytest.fixture()
    def pipeline_parts(self, tmp_path):
        from options_trading.services.live_analytics import LiveDashboardPayload
        from options_trading.services.live_pipeline import (
            LivePipelineConfig,
            LivePipelineService,
        )

        class _Manager:
            async def send_dashboard_update(self, _payload):
                return 0

        return LivePipelineService, LivePipelineConfig, _Manager, LiveDashboardPayload

    def test_a_raising_analyst_service_still_emits_the_key(self, tmp_path, pipeline_parts):
        Service, Config, Manager, Payload = pipeline_parts

        class _Exploding:
            async def build_async(self):
                raise RuntimeError("journal on fire")

        pipeline = Service(
            ws_manager=Manager(),
            config=Config(),
            analysts=_Exploding(),
        )
        wire = asyncio.run(pipeline.build_wire_dict(Payload()))
        assert "analysts" in wire, "an absent key leaves stale prose on screen"
        assert wire["analysts"]["hasJournal"] is False
        assert wire["analysts"]["reason"]

    def test_no_analyst_service_at_all_still_emits_the_key(self, tmp_path, pipeline_parts):
        Service, Config, Manager, Payload = pipeline_parts
        pipeline = Service(ws_manager=Manager(), config=Config(), analysts=None)
        wire = asyncio.run(pipeline.build_wire_dict(Payload()))
        assert wire["analysts"]["hasJournal"] is False

    def test_a_working_service_reaches_the_wire(self, tmp_path, pipeline_parts):
        Service, Config, Manager, Payload = pipeline_parts
        directory = tmp_path / "journal"
        directory.mkdir()
        EventLog(directory, "desk").append("market_features", dict(FULL_FEATURES))
        pipeline = Service(
            ws_manager=Manager(),
            config=Config(),
            analysts=AnalystService(directory, AnalystServiceConfig(journal_run_id="desk")),
        )
        wire = asyncio.run(pipeline.build_wire_dict(Payload()))
        assert wire["analysts"]["hasJournal"] is True
        assert wire["analysts"]["groundedRate"] == pytest.approx(1.0)

    def test_the_failure_payload_matches_the_success_shape(self, tmp_path, pipeline_parts):
        Service, Config, Manager, Payload = pipeline_parts
        directory = tmp_path / "journal"
        directory.mkdir()
        EventLog(directory, "desk").append("market_features", dict(FULL_FEATURES))
        good = Service(
            ws_manager=Manager(),
            config=Config(),
            analysts=AnalystService(directory, AnalystServiceConfig(journal_run_id="desk")),
        )
        wire = asyncio.run(good.build_wire_dict(Payload()))
        assert set(wire["analysts"]) == set(unavailable_analysts_wire("x"))


class TestFrontendContract:
    """The two silent frontend mistakes, asserted against the source."""

    @staticmethod
    def _src(name: str) -> str:
        root = Path(__file__).resolve().parents[2] / "frontend" / "src"
        return (root / name).read_text(encoding="utf-8")

    def test_analysts_is_in_the_websocket_merge_allowlist(self):
        """Missing this is silent: the panel would simply never update."""
        source = self._src("hooks/useLiveData.ts")
        assert "d.analysts && { analysts: d.analysts }" in source

    def test_analysts_is_a_field_of_the_dashboard_data_type(self):
        assert "analysts: any;" in self._src("hooks/useLiveData.ts")

    def test_the_panel_gates_fail_closed(self):
        """`!== false` would render prose for a null or absent payload."""
        source = self._src("components/AnalystPanel.tsx")
        assert "data?.hasJournal !== true" in source
        assert "hasJournal !== false" not in source

    def test_the_panel_renders_a_badge_per_claim(self):
        source = self._src("components/AnalystPanel.tsx")
        assert "Ungrounded" in source and "Grounded" in source
        assert "claim.citations" in source

    def test_the_panel_shows_failed_analysts(self):
        source = self._src("components/AnalystPanel.tsx")
        assert "Could not report" in source
        assert "failures" in source

    def test_the_nav_item_exists(self):
        source = self._src("App.tsx")
        assert 'id: "analysts"' in source
        assert 'activeTab === "analysts"' in source


class TestPaperOnlyInvariant:
    """The app is read-only against a real account; this must not change it."""

    @staticmethod
    def _source(relative: str) -> str:
        root = Path(__file__).resolve().parents[2] / "src" / "options_trading"
        return (root / relative).read_text(encoding="utf-8")

    @pytest.mark.parametrize(
        "module",
        ["services/analyst_service.py", "api/routes/analysts.py"],
    )
    def test_no_order_placement_symbols(self, module):
        source = self._source(module)
        for forbidden in (
            "place_order",
            "cancel_order",
            "modify_order",
            "/order/place",
            "place_multi_order",
        ):
            assert forbidden not in source, f"{module} references {forbidden}"

    @pytest.mark.parametrize(
        "module",
        ["services/analyst_service.py", "api/routes/analysts.py"],
    )
    def test_no_broker_or_http_imports(self, module):
        """Checked against import statements, not prose in the docstrings."""
        imports = [
            line.strip()
            for line in self._source(module).splitlines()
            if line.strip().startswith(("import ", "from "))
        ]
        for line in imports:
            assert "upstox" not in line.lower(), f"{module} imports a broker client: {line}"
            for banned in ("httpx", "requests", "aiohttp"):
                assert banned not in line, f"{module} imports {banned}: {line}"

    def test_the_endpoint_is_read_only_http(self):
        """Only GET: a POST here would be a state change nobody asked for."""
        methods = {
            method
            for line in self._source("api/routes/analysts.py").splitlines()
            for method in ("@router.post", "@router.put", "@router.delete", "@router.patch")
            if method in line
        }
        assert methods == set(), f"the analyst router exposes writes: {methods}"

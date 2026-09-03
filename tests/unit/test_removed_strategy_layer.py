"""Guards the deletion of the fabricated strategy performance layer.

Two surfaces went together:

* ``GET /dashboard/strategies/performance`` called
  ``DashboardService.get_strategy_performance``, which never existed. Every
  request raised ``AttributeError``, was swallowed by the route's broad
  ``except Exception``, and returned a 500 reading as a generic outage.
* ``services/strategy_service.py`` held the only implementation of that method,
  and it was fabrication throughout: a hardcoded list of three strategies,
  hardcoded positions, a hardcoded attribution breakdown, and a
  ``Decimal("1.2")`` "mock fallback" Sharpe ratio. No route depended on it —
  ``get_strategy_service`` was defined but wired to nothing.

Real per-strategy performance is not derivable from what the app stores today:
there is no strategy store and no per-strategy trade history, only a live
broker book. So the surface was removed rather than backfilled with invented
numbers (CLAUDE.md rule 7; the same reasoning as HistoryGate on the frontend).
"""

from __future__ import annotations

import importlib.util

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from options_trading.api.routes.dashboard import router as dashboard_router


class TestStrategyServiceGone:
    def test_module_does_not_exist(self) -> None:
        assert importlib.util.find_spec("options_trading.services.strategy_service") is None

    def test_dependency_provider_gone(self) -> None:
        from options_trading.api import dependencies

        assert not hasattr(dependencies, "get_strategy_service")

    def test_surviving_service_providers_intact(self) -> None:
        from options_trading.api import dependencies

        for name in (
            "get_dashboard_service",
            "get_market_data_service",
            "get_market_data_manager",
        ):
            assert hasattr(dependencies, name)


class TestStrategyPerformanceEndpointRemoved:
    def test_route_is_not_registered(self) -> None:
        paths = {getattr(route, "path", "") for route in dashboard_router.routes}
        assert "/dashboard/strategies/performance" not in paths

    def test_returns_404(self) -> None:
        app = FastAPI()
        app.include_router(dashboard_router, prefix="/api/v1")
        resp = TestClient(app).get("/api/v1/dashboard/strategies/performance")
        assert resp.status_code == 404

    def test_dashboard_service_never_had_the_method(self) -> None:
        from options_trading.services.dashboard_service import DashboardService

        assert not hasattr(DashboardService, "get_strategy_performance")

    def test_surviving_dashboard_routes_intact(self) -> None:
        paths = {getattr(route, "path", "") for route in dashboard_router.routes}
        for path in (
            "/dashboard/status",
            "/dashboard/positions/summary",
            "/dashboard/risk/metrics",
            "/dashboard/config",
        ):
            assert path in paths


class TestOrphanedModelsRemoved:
    @pytest.mark.parametrize(
        "name",
        ["StrategyPerformance", "PositionData", "GreeksSnapshot", "StrategyStatus"],
    )
    def test_model_is_gone(self, name: str) -> None:
        from options_trading.models import dashboard

        assert not hasattr(dashboard, name)

    def test_surviving_dashboard_models_intact(self) -> None:
        from options_trading.models import dashboard

        for name in (
            "PositionSummary",
            "RiskMetrics",
            "SystemStatus",
            "DashboardConfig",
        ):
            assert hasattr(dashboard, name)

    def test_market_data_greeks_snapshot_is_untouched(self) -> None:
        # A distinct class of the same name lives in models/market_data.py and
        # is used by market_data_service; only the dashboard copy was orphaned.
        from options_trading.models.market_data import GreeksSnapshot

        assert GreeksSnapshot is not None

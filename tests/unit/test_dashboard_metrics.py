"""Tests for dashboard position and risk metrics.

Both endpoints previously returned hardcoded Decimals — including a
``stress_test_results`` table of invented historical-episode losses
("market_crash_2008": -125000) that no engine produced. These tests pin the
replacement: derive what the live book supports, report null for what needs a
persisted history, and revalue real shocks with the scenario engine.
"""

from __future__ import annotations

import pytest

from options_trading.services.dashboard_service import (
    STRESS_SCENARIOS,
    DashboardService,
)
from options_trading.services.live_analytics import BookContext
from optitrade.core.types import OptionContract, OptionType, Portfolio, Position

SPOT = 24_600.0


def _book(*, margin_used: float | None = 45_000.0) -> BookContext:
    call = Position(
        contract=OptionContract(
            symbol="NIFTY2490724500CE",
            strike=24_500.0,
            expiry=0.05,
            option_type=OptionType.CALL,
            lot_size=50,
        ),
        quantity=50.0,
        entry_price=180.0,
    )
    put = Position(
        contract=OptionContract(
            symbol="NIFTY2490724000PE",
            strike=24_000.0,
            expiry=0.05,
            option_type=OptionType.PUT,
            lot_size=50,
        ),
        quantity=-50.0,
        entry_price=120.0,
    )
    return BookContext(
        portfolio=Portfolio(positions=(call, put), equity=200_000.0),
        marks={"NIFTY2490724500CE": 260.0, "NIFTY2490724000PE": 95.0},
        equity=200_000.0,
        margin_used=margin_used,
        margin_available=155_000.0,
    )


def _service(book: BookContext | None = None, spot: float | None = SPOT) -> DashboardService:
    return DashboardService(book_fn=lambda: book, spot_fn=lambda: spot)


class TestNoBook:
    def test_summary_reports_nothing_rather_than_placeholders(self):
        summary = _service()._build_positions_summary()
        assert summary.total_positions == 0
        assert summary.portfolio_delta is None
        assert summary.daily_pnl is None

    def test_risk_metrics_are_null_not_zero(self):
        metrics = _service()._build_risk_metrics()
        assert metrics.portfolio_delta is None
        assert metrics.delta_limit_utilization is None
        assert metrics.stress_test_results == {}

    def test_no_spot_means_no_greeks_but_still_reports_positions(self):
        metrics = _service(_book(), spot=None)._build_risk_metrics()
        assert metrics.portfolio_delta is None

        summary = _service(_book(), spot=None)._build_positions_summary()
        assert summary.total_positions == 2
        assert summary.margin_used is not None  # funds do not need a spot


class TestUnavailableAlwaysNull:
    """Fields needing a persisted P&L history must never be invented."""

    @pytest.mark.parametrize(
        "field",
        ["var_1d", "var_1d_percentage", "expected_shortfall", "maximum_drawdown", "beta"],
    )
    def test_history_dependent_fields_stay_null(self, field):
        metrics = _service(_book())._build_risk_metrics()
        assert getattr(metrics, field) is None

    def test_daily_pnl_and_strategies_stay_null(self):
        summary = _service(_book())._build_positions_summary()
        assert summary.daily_pnl is None
        assert summary.active_strategies is None


class TestWithBook:
    def test_greeks_come_from_the_priced_book(self):
        metrics = _service(_book())._build_risk_metrics()
        assert metrics.portfolio_delta is not None
        assert metrics.portfolio_vega is not None
        # Long a call and short a put: both legs are long delta.
        assert float(metrics.portfolio_delta) > 0

    def test_limit_utilisation_is_a_real_ratio(self):
        metrics = _service(_book())._build_risk_metrics()
        assert metrics.delta_limit_utilization is not None
        assert metrics.delta_limit_utilization > 0
        assert metrics.vega_limit_utilization is not None

    def test_utilisation_is_not_clamped_at_100(self):
        """A book over its limit is exactly what this panel exists to show."""
        from options_trading.models.dashboard import RiskMetrics

        assert RiskMetrics(delta_limit_utilization=250.0).delta_limit_utilization == 250.0

    def test_margin_comes_from_funds(self):
        summary = _service(_book())._build_positions_summary()
        assert summary.margin_used == 45_000
        assert summary.available_margin == 155_000

    def test_missing_funds_leaves_margin_null(self):
        summary = _service(_book(margin_used=None))._build_positions_summary()
        assert summary.margin_used is None

    def test_concentration_is_computed_from_the_book(self):
        summary = _service(_book())._build_positions_summary()
        # Both legs are NIFTY, so it is 100% concentrated in one underlying.
        assert summary.concentration_risk == {"NIFTY": 100.0}


class TestStressTests:
    def test_every_named_scenario_is_revalued(self):
        results = _service(_book())._build_risk_metrics().stress_test_results
        assert set(results) == set(STRESS_SCENARIOS)

    def test_results_are_computed_not_stored(self):
        """Two different books must produce different stress P&Ls.

        The old implementation returned the same three Decimals regardless of
        what the account held.
        """
        one_lot = _book()
        big = BookContext(
            portfolio=Portfolio(
                positions=tuple(
                    Position(
                        contract=p.contract, quantity=p.quantity * 10, entry_price=p.entry_price
                    )
                    for p in one_lot.portfolio.positions
                ),
                equity=200_000.0,
            ),
            marks=one_lot.marks,
        )
        small_pnl = _service(one_lot)._build_risk_metrics().stress_test_results
        big_pnl = _service(big)._build_risk_metrics().stress_test_results

        assert small_pnl != big_pnl
        # Ten times the position, ten times the shock P&L.
        key = "spot_down_10pct"
        assert float(big_pnl[key]) == pytest.approx(float(small_pnl[key]) * 10, rel=1e-6)

    def test_spot_down_hurts_a_long_delta_book(self):
        results = _service(_book())._build_risk_metrics().stress_test_results
        assert float(results["spot_down_10pct"]) < 0
        assert float(results["spot_up_10pct"]) > 0

    def test_vol_spike_helps_a_long_call_short_put_book(self):
        """Long call (+vega) and short put (-vega) partially offset; the sign
        follows the net vega rather than a fixed assumption."""
        metrics = _service(_book())._build_risk_metrics()
        net_vega = float(metrics.portfolio_vega)
        vol_pnl = float(metrics.stress_test_results["vol_spike_10pts"])
        assert (vol_pnl > 0) == (net_vega > 0)

"""Tests for the daily P&L explain decomposition."""

import numpy as np
import pytest

from optitrade.core import Greeks
from optitrade.explain import PnLExplain, SurfaceFactorModel, explain_pnl, reconstruct
from optitrade.journal import EventLog

pytestmark = pytest.mark.unit

SPOT = 100.0
D_SPOT = 1.2
DT = 1.0 / 252.0

GREEKS = Greeks(delta=50.0, gamma=2.0, vega=30.0, theta=-80.0, rho=12.0, vanna=1.5, volga=4.0)


def _taylor_total(greeks: Greeks, d_spot: float, dt: float, d_vol: float) -> float:
    """Exact sum of the Taylor terms the simple-path explain decomposes into."""
    return (
        greeks.theta * dt
        + greeks.delta * d_spot
        + 0.5 * greeks.gamma * d_spot * d_spot
        + greeks.vega * d_vol
        + greeks.vanna * d_spot * d_vol
        + 0.5 * greeks.volga * d_vol * d_vol
    )


def _two_factor_model() -> SurfaceFactorModel:
    """Hand-built orthonormal level/term model on a 2x2 grid."""
    return SurfaceFactorModel(
        grid_moneyness=np.array([0.95, 1.05]),
        grid_expiries=np.array([0.1, 0.5]),
        mean_move=np.zeros(4),
        components=np.array(
            [
                [0.5, 0.5, 0.5, 0.5],  # level
                [-0.5, 0.5, -0.5, 0.5],  # short-end down, long-end up
            ]
        ),
        explained_variance_ratio=np.array([0.7, 0.2]),
        factor_names=("level", "term"),
    )


class TestSimplePath:
    def test_exact_taylor_pnl_is_fully_explained(self):
        d_vol = 0.008
        total = _taylor_total(GREEKS, D_SPOT, DT, d_vol)

        result = explain_pnl(
            GREEKS, SPOT, D_SPOT, DT, surface_move_scores={"level": d_vol}, total_pnl=total
        )

        assert result.residual == pytest.approx(0.0, abs=1e-12)
        assert result.explained_fraction > 0.999
        assert result.theta_carry == pytest.approx(GREEKS.theta * DT)
        assert result.delta_pnl == pytest.approx(GREEKS.delta * D_SPOT)
        assert result.gamma_vs_rv == pytest.approx(0.5 * GREEKS.gamma * D_SPOT**2)
        assert result.vega_from_factors == {"vega": pytest.approx(GREEKS.vega * d_vol)}
        assert result.vega_residual_move == 0.0
        assert result.vanna_volga == pytest.approx(
            GREEKS.vanna * D_SPOT * d_vol + 0.5 * GREEKS.volga * d_vol**2
        )
        assert result.total == total

    def test_buckets_plus_residual_sum_to_total(self):
        result = explain_pnl(
            GREEKS, SPOT, D_SPOT, DT, surface_move_scores={"level": 0.01}, total_pnl=42.0
        )
        explained = (
            result.theta_carry
            + result.delta_pnl
            + result.gamma_vs_rv
            + sum(result.vega_from_factors.values())
            + result.vega_residual_move
            + result.vanna_volga
        )
        assert explained + result.residual == pytest.approx(42.0)

    def test_explained_fraction_clamps_to_unit_interval(self):
        base = {
            "theta_carry": 0.0,
            "delta_pnl": 0.0,
            "gamma_vs_rv": 0.0,
            "vega_from_factors": {},
            "vega_residual_move": 0.0,
            "vanna_volga": 0.0,
        }
        assert PnLExplain(**base, residual=5.0, total=1.0).explained_fraction == 0.0
        assert PnLExplain(**base, residual=0.0, total=0.0).explained_fraction == 1.0

    def test_input_validation(self):
        with pytest.raises(ValueError, match="spot"):
            explain_pnl(GREEKS, -1.0, D_SPOT, DT, {"level": 0.0}, total_pnl=0.0)
        with pytest.raises(ValueError, match="dt"):
            explain_pnl(GREEKS, SPOT, D_SPOT, -DT, {"level": 0.0}, total_pnl=0.0)
        with pytest.raises(ValueError, match="realized_variance"):
            explain_pnl(
                GREEKS, SPOT, D_SPOT, DT, {"level": 0.0}, total_pnl=0.0, realized_variance=-0.1
            )


class TestRealizedVarianceForm:
    def test_realized_matching_squared_move_reconciles_exactly(self):
        # RV chosen so 0.5*gamma*S^2*rv*dt == 0.5*gamma*dS^2: both gamma
        # conventions agree and the decomposition stays exact.
        d_vol = 0.008
        rv = (D_SPOT / SPOT) ** 2 / DT
        total = _taylor_total(GREEKS, D_SPOT, DT, d_vol)

        result = explain_pnl(
            GREEKS,
            SPOT,
            D_SPOT,
            DT,
            surface_move_scores={"level": d_vol},
            total_pnl=total,
            realized_variance=rv,
        )

        assert result.gamma_vs_rv == pytest.approx(0.5 * GREEKS.gamma * D_SPOT**2)
        assert result.residual == pytest.approx(0.0, abs=1e-12)
        assert result.explained_fraction > 0.999

    def test_rv_gap_lands_in_residual(self):
        # Gamma-only book, P&L built from the single squared move; marking
        # gamma against a different realized variance must push exactly the
        # convention gap into the residual.
        greeks = Greeks(gamma=2.0)
        rv = 0.5 * (D_SPOT / SPOT) ** 2 / DT  # realized ran at half the move
        total = 0.5 * greeks.gamma * D_SPOT**2

        result = explain_pnl(
            greeks,
            SPOT,
            D_SPOT,
            DT,
            surface_move_scores={},
            total_pnl=total,
            realized_variance=rv,
        )

        expected_gamma = 0.5 * greeks.gamma * SPOT**2 * rv * DT
        assert result.gamma_vs_rv == pytest.approx(expected_gamma)
        assert result.residual == pytest.approx(total - expected_gamma)


class TestFactorVegaPath:
    def test_known_scores_recover_per_factor_contributions(self):
        model = _two_factor_model()
        profile = np.array([10.0, 20.0, 30.0, 40.0])
        scores = {"level": 0.02, "term": -0.01}
        move = reconstruct(model, np.array([scores["level"], scores["term"]]))

        expected_level = scores["level"] * float(profile @ model.components[0])  # 0.02 * 50
        expected_term = scores["term"] * float(profile @ model.components[1])  # -0.01 * 10
        d_sigma_level = scores["level"] * 0.5  # mean level loading = 0.5
        expected_vv = GREEKS.vanna * D_SPOT * d_sigma_level + 0.5 * GREEKS.volga * d_sigma_level**2
        total = (
            GREEKS.theta * DT
            + GREEKS.delta * D_SPOT
            + 0.5 * GREEKS.gamma * D_SPOT**2
            + expected_level
            + expected_term
            + expected_vv
        )

        result = explain_pnl(
            GREEKS,
            SPOT,
            D_SPOT,
            DT,
            surface_move_scores=scores,
            total_pnl=total,
            factor_model=model,
            book_vega_profile=profile,
            surface_move=move,
        )

        assert result.vega_from_factors["level"] == pytest.approx(expected_level, abs=1e-6)
        assert result.vega_from_factors["term"] == pytest.approx(expected_term, abs=1e-6)
        assert result.vega_residual_move == pytest.approx(0.0, abs=1e-9)
        assert result.vanna_volga == pytest.approx(expected_vv)
        assert result.residual == pytest.approx(0.0, abs=1e-9)
        assert result.explained_fraction > 0.999

    def test_off_span_surface_move_prices_as_residual_move(self):
        model = _two_factor_model()
        profile = np.array([10.0, 20.0, 30.0, 40.0])
        scores = {"level": 0.02, "term": -0.01}
        off_span = 0.004 * np.array([0.5, 0.5, -0.5, -0.5])  # orthogonal to both factors
        move = reconstruct(model, np.array([0.02, -0.01])) + off_span

        result = explain_pnl(
            Greeks(),
            SPOT,
            0.0,
            DT,
            surface_move_scores=scores,
            total_pnl=0.0,
            factor_model=model,
            book_vega_profile=profile,
            surface_move=move,
        )

        assert result.vega_residual_move == pytest.approx(float(profile @ off_span), abs=1e-12)

    def test_profile_scores_path_multiplies_loadings_by_scores(self):
        result = explain_pnl(
            Greeks(),
            SPOT,
            0.0,
            DT,
            surface_move_scores={"level": 0.02, "term": -0.01},
            vega_profile_scores={"level": 50.0, "term": 10.0},
            total_pnl=0.9,
        )
        assert result.vega_from_factors["level"] == pytest.approx(1.0)
        assert result.vega_from_factors["term"] == pytest.approx(-0.1)
        assert result.residual == pytest.approx(0.0, abs=1e-12)

    def test_profile_size_mismatch_raises(self):
        with pytest.raises(ValueError, match="book_vega_profile"):
            explain_pnl(
                GREEKS,
                SPOT,
                D_SPOT,
                DT,
                surface_move_scores={},
                total_pnl=0.0,
                factor_model=_two_factor_model(),
                book_vega_profile=np.zeros(5),
            )


class TestJournalIntegration:
    def test_appends_pnl_explain_event(self, tmp_path):
        journal = EventLog(tmp_path, "explain-test")
        d_vol = 0.008
        total = _taylor_total(GREEKS, D_SPOT, DT, d_vol)

        result = explain_pnl(
            GREEKS,
            SPOT,
            D_SPOT,
            DT,
            surface_move_scores={"level": d_vol},
            total_pnl=total,
            journal=journal,
        )

        events = list(journal.replay())
        assert len(events) == 1
        assert events[0].event_type == "pnl_explain"
        assert events[0].data["total"] == pytest.approx(total)
        assert events[0].data["explained_fraction"] == pytest.approx(result.explained_fraction)
        assert events[0].data["vega_from_factors"] == {"vega": pytest.approx(GREEKS.vega * d_vol)}

    def test_no_journal_writes_nothing(self, tmp_path):
        journal = EventLog(tmp_path, "untouched")
        explain_pnl(GREEKS, SPOT, D_SPOT, DT, surface_move_scores={}, total_pnl=1.0)
        assert list(journal.replay()) == []

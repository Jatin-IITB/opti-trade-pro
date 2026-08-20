"""Tests for PCA surface-factor extraction (level/term/skew)."""

import numpy as np
import pytest

from optitrade.explain import fit_surface_factors, project, reconstruct

pytestmark = pytest.mark.unit

MONEYNESS = np.array([0.90, 0.95, 1.00, 1.05, 1.10])
EXPIRIES = np.array([0.05, 0.15, 0.40, 1.00])
N_GRID = MONEYNESS.size * EXPIRIES.size


def _unit(v: np.ndarray) -> np.ndarray:
    return v / np.linalg.norm(v)


def _shapes() -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Orthonormal level/term/skew grid shapes, flattened row-major [m, e].

    Term and skew are mean-zero along their axis so the three shapes are
    mutually orthogonal and PCA can recover them cleanly.
    """
    level = _unit(np.ones(N_GRID))
    term = _unit(np.tile(EXPIRIES - EXPIRIES.mean(), MONEYNESS.size))
    skew = _unit(np.repeat(MONEYNESS - MONEYNESS.mean(), EXPIRIES.size))
    return level, term, skew


def _synthetic_moves(n_days: int = 250, seed: int = 7) -> np.ndarray:
    """Daily IV moves = level + term + skew components plus grid noise."""
    rng = np.random.default_rng(seed)
    level, term, skew = _shapes()
    scores = rng.normal(0.0, [0.010, 0.005, 0.003], size=(n_days, 3))
    moves = scores[:, 0:1] * level + scores[:, 1:2] * term + scores[:, 2:3] * skew
    return moves + rng.normal(0.0, 0.0005, size=moves.shape)


class TestFitSurfaceFactors:
    def test_three_factors_explain_most_variance(self):
        model = fit_surface_factors(_synthetic_moves(), MONEYNESS, EXPIRIES, n_factors=3)

        assert model.components.shape == (3, N_GRID)
        assert model.explained_variance_ratio.shape == (3,)
        assert float(model.explained_variance_ratio.sum()) > 0.95
        # Variance ordering: PC1 (level, largest score std) dominates.
        assert model.explained_variance_ratio[0] > model.explained_variance_ratio[1]

    def test_factor_names_from_heuristics(self):
        # Naming is heuristic, so assert leniently: the dominant parallel
        # component must read "level" and at least one curve-shaped component
        # must be recognised; noise may push the third to a "pc{i}" fallback.
        model = fit_surface_factors(_synthetic_moves(), MONEYNESS, EXPIRIES, n_factors=3)

        assert len(model.factor_names) == 3
        assert "level" in model.factor_names
        assert ("term" in model.factor_names) or ("skew" in model.factor_names)

    def test_components_are_orthonormal(self):
        model = fit_surface_factors(_synthetic_moves(), MONEYNESS, EXPIRIES, n_factors=3)
        gram = model.components @ model.components.T
        np.testing.assert_allclose(gram, np.eye(3), atol=1e-10)

    def test_wrong_grid_size_raises(self):
        with pytest.raises(ValueError, match="moves must have shape"):
            fit_surface_factors(np.zeros((50, 7)), MONEYNESS, EXPIRIES)

    def test_bad_n_factors_raises(self):
        with pytest.raises(ValueError, match="n_factors"):
            fit_surface_factors(_synthetic_moves(), MONEYNESS, EXPIRIES, n_factors=0)

    def test_zero_variance_moves_raise(self):
        with pytest.raises(ValueError, match="zero variance"):
            fit_surface_factors(np.ones((10, N_GRID)), MONEYNESS, EXPIRIES)


class TestProjectReconstruct:
    def test_in_span_move_round_trips_with_small_error(self):
        model = fit_surface_factors(_synthetic_moves(), MONEYNESS, EXPIRIES, n_factors=3)
        level, term, skew = _shapes()
        move = 0.02 * level - 0.01 * term + 0.005 * skew

        scores = project(model, move)
        recon = reconstruct(model, scores)

        assert scores.shape == (3,)
        # The fitted subspace matches the synthetic span up to noise-driven
        # rotation, so an in-span move reconstructs to a few % at worst.
        assert np.linalg.norm(recon - move) < 0.05 * np.linalg.norm(move)

    def test_component_round_trip_is_exact(self):
        model = fit_surface_factors(_synthetic_moves(), MONEYNESS, EXPIRIES, n_factors=3)
        component = model.components[1]
        recon = reconstruct(model, project(model, component))
        np.testing.assert_allclose(recon, component, atol=1e-12)

    def test_remainder_is_orthogonal_to_components(self):
        model = fit_surface_factors(_synthetic_moves(), MONEYNESS, EXPIRIES, n_factors=3)
        rng = np.random.default_rng(11)
        move = rng.normal(0.0, 0.01, size=N_GRID)
        remainder = move - reconstruct(model, project(model, move))
        np.testing.assert_allclose(model.components @ remainder, np.zeros(3), atol=1e-12)

    def test_size_mismatches_raise(self):
        model = fit_surface_factors(_synthetic_moves(), MONEYNESS, EXPIRIES, n_factors=3)
        with pytest.raises(ValueError, match="grid points"):
            project(model, np.zeros(N_GRID + 1))
        with pytest.raises(ValueError, match="scores"):
            reconstruct(model, np.zeros(4))

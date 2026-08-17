"""Breeden-Litzenberger RND gate: clean SSVI passes, arbitrageable surface fails."""

import math

import numpy as np
import pytest

from optitrade.core import ArbitrageViolationError
from optitrade.pricing.black_scholes import ArrayLike
from optitrade.vol import ESSVIParams, ESSVISurface, extract_rnd, rnd_gate, validate_rnd

SPOT, RATE = 100.0, 0.05

PARAMS = ESSVIParams(
    rho=-0.3,
    eta=0.8,
    gamma_=0.45,
    theta_by_expiry=((0.25, 0.011), (0.5, 0.021), (1.0, 0.042)),
)


def _clean_surface() -> ESSVISurface:
    return ESSVISurface(PARAMS, SPOT, RATE)


@pytest.mark.parametrize("expiry", [0.25, 0.5, 1.0])
def test_clean_ssvi_density_is_a_probability_density(expiry: float) -> None:
    surface = _clean_surface()
    result = extract_rnd(surface, expiry, SPOT, RATE)
    forward = SPOT * math.exp(RATE * expiry)
    assert abs(result.integral - 1.0) < 0.02  # integrates to one within 2%
    assert result.min_pdf >= -1e-4  # non-negative everywhere (FD tolerance)
    assert abs(result.implied_mean - forward) < 0.01 * forward  # martingale mean
    assert validate_rnd(result, forward) == []


def test_extract_rnd_grid_shape_and_diagnostics() -> None:
    result = extract_rnd(_clean_surface(), 0.5, SPOT, RATE, n_grid=200)
    assert result.strikes.shape == result.pdf.shape == (198,)  # interior points
    assert result.min_pdf == pytest.approx(float(result.pdf.min()))
    assert np.all(np.diff(result.strikes) > 0.0)
    with pytest.raises(ValueError, match="expiry"):
        extract_rnd(_clean_surface(), 0.0, SPOT, RATE)


class _SpikeSurface:
    """Localised off-ATM vol spike => non-convex call prices => negative density."""

    expiries = np.array([0.5])

    def forward(self, expiry: float) -> float:
        return SPOT * math.exp(RATE * expiry)

    def vol(self, strike: ArrayLike, expiry: ArrayLike) -> ArrayLike:
        fwd = SPOT * np.exp(RATE * np.asarray(expiry, dtype=float))
        lm = np.log(np.asarray(strike, dtype=float) / fwd)
        return 0.2 + 0.4 * np.exp(-(((lm - 0.1) / 0.05) ** 2))


def test_arbitrageable_surface_produces_negative_density() -> None:
    result = extract_rnd(_SpikeSurface(), 0.5, SPOT, RATE)
    assert result.min_pdf < -1e-3  # clearly negative mass, not FD noise
    forward = SPOT * math.exp(RATE * 0.5)
    violations = validate_rnd(result, forward)
    assert violations
    assert all(v.kind == "density" for v in violations)
    assert any("negative risk-neutral density" in v.detail for v in violations)
    assert all(v.magnitude > 0.0 for v in violations)


def test_validate_rnd_flags_bad_integral_and_mean() -> None:
    result = extract_rnd(_clean_surface(), 0.5, SPOT, RATE)
    forward = SPOT * math.exp(RATE * 0.5)
    # Same result, absurdly tight tolerances: aggregate checks must trip.
    violations = validate_rnd(result, forward, tol_integral=1e-12, tol_mean_rel=1e-12)
    kinds_hit = [v.detail for v in violations]
    assert any("integrates to" in d for d in kinds_hit)
    assert any("mean" in d for d in kinds_hit)


def test_rnd_gate_clean_and_raising() -> None:
    assert rnd_gate(_clean_surface(), [0.25, 0.5, 1.0], SPOT, RATE) == []
    violations = rnd_gate(_SpikeSurface(), [0.5], SPOT, RATE)
    assert violations
    with pytest.raises(ArbitrageViolationError, match="density gate failed"):
        rnd_gate(_SpikeSurface(), [0.5], SPOT, RATE, raise_on_violation=True)

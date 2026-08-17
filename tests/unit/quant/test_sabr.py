"""SABR: Hagan formula limits, smile shape, calibration round-trip."""

import numpy as np
import pytest

from optitrade.core import CalibrationError
from optitrade.vol import SABRParams, calibrate_sabr, hagan_implied_vol

FORWARD, EXPIRY = 100.0, 0.75


def _atm_vol(p: SABRParams) -> float:
    # Hagan et al. (2002) eq. (2.18), the exact ATM limit.
    omb = 1.0 - p.beta
    f_pow = p.forward**omb
    return (p.alpha / f_pow) * (
        1.0
        + p.expiry
        * (
            (omb**2 / 24.0) * p.alpha**2 / f_pow**2
            + 0.25 * p.rho * p.beta * p.nu * p.alpha / f_pow
            + ((2.0 - 3.0 * p.rho**2) / 24.0) * p.nu**2
        )
    )


@pytest.mark.parametrize(
    "params",
    [
        SABRParams(0.25, 1.0, -0.4, 0.9, FORWARD, EXPIRY),
        SABRParams(0.5, 0.5, -0.3, 0.6, 0.05, 1.0),
        SABRParams(0.3, 0.0, 0.2, 0.4, 1.5, 0.25),
    ],
)
def test_hagan_atm_limit_matches_closed_form(params: SABRParams) -> None:
    atm = _atm_vol(params)
    assert abs(float(hagan_implied_vol(params.forward, params)) - atm) < 1e-12
    # The tolerance guard makes near-ATM strikes continuous, not special-cased.
    assert abs(float(hagan_implied_vol(params.forward * (1.0 + 1e-10), params)) - atm) < 1e-8


def test_hagan_vectorised_matches_scalar() -> None:
    params = SABRParams(0.25, 1.0, -0.4, 0.9, FORWARD, EXPIRY)
    strikes = FORWARD * np.exp(np.linspace(-0.3, 0.3, 11))
    vec = np.asarray(hagan_implied_vol(strikes, params))
    scal = np.array([hagan_implied_vol(float(k), params) for k in strikes])
    np.testing.assert_allclose(vec, scal, rtol=0, atol=1e-15)


def test_negative_rho_produces_downward_skew() -> None:
    params = SABRParams(0.2, 1.0, -0.6, 0.8, FORWARD, EXPIRY)
    low = float(hagan_implied_vol(0.9 * FORWARD, params))
    high = float(hagan_implied_vol(1.1 * FORWARD, params))
    assert low > high


@pytest.mark.parametrize(
    ("alpha", "beta", "rho", "nu"),
    [
        (-0.1, 1.0, 0.0, 0.5),
        (0.2, 1.5, 0.0, 0.5),
        (0.2, 1.0, 1.0, 0.5),
        (0.2, 1.0, 0.0, -0.5),
    ],
)
def test_invalid_params_rejected(alpha: float, beta: float, rho: float, nu: float) -> None:
    with pytest.raises(ValueError):
        SABRParams(alpha, beta, rho, nu, FORWARD, EXPIRY)


def test_calibration_round_trip_hits_rmse_target() -> None:
    true = SABRParams(0.25, 1.0, -0.4, 0.9, FORWARD, EXPIRY)
    strikes = FORWARD * np.exp(np.linspace(-0.25, 0.25, 9))
    rng = np.random.default_rng(42)
    ivs = np.asarray(hagan_implied_vol(strikes, true)) + rng.normal(0.0, 0.001, strikes.size)
    fit = calibrate_sabr(strikes, ivs, FORWARD, EXPIRY, beta=1.0)
    assert fit.rmse_vol_points < 0.3  # headline product target
    assert fit.params.beta == 1.0
    assert abs(fit.params.alpha - true.alpha) < 0.05
    assert abs(fit.params.rho - true.rho) < 0.25
    assert abs(fit.params.nu - true.nu) < 0.4
    assert 1 <= fit.n_starts_used <= 8


def test_calibration_is_deterministic() -> None:
    true = SABRParams(0.22, 1.0, -0.3, 0.7, FORWARD, EXPIRY)
    strikes = FORWARD * np.exp(np.linspace(-0.2, 0.2, 9))
    ivs = np.asarray(hagan_implied_vol(strikes, true))
    fit_a = calibrate_sabr(strikes, ivs, FORWARD, EXPIRY, seed=7)
    fit_b = calibrate_sabr(strikes, ivs, FORWARD, EXPIRY, seed=7)
    assert fit_a.params == fit_b.params
    assert fit_a.rmse_vol_points == fit_b.rmse_vol_points


def test_calibration_error_when_rmse_target_unreachable() -> None:
    true = SABRParams(0.25, 1.0, -0.4, 0.9, FORWARD, EXPIRY)
    strikes = FORWARD * np.exp(np.linspace(-0.25, 0.25, 9))
    rng = np.random.default_rng(1)
    ivs = np.asarray(hagan_implied_vol(strikes, true)) + rng.normal(0.0, 0.001, strikes.size)
    with pytest.raises(CalibrationError, match="RMSE"):
        calibrate_sabr(strikes, ivs, FORWARD, EXPIRY, max_rmse_vol_points=1e-8)


def test_calibration_rejects_too_few_quotes() -> None:
    with pytest.raises(CalibrationError, match=">= 3"):
        calibrate_sabr([95.0, 105.0], [0.2, 0.21], FORWARD, EXPIRY)

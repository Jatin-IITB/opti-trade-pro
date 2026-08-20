"""eSSVI joint calibration: round-trip, no-arbitrage, and SABR benchmark."""

import math
from itertools import pairwise

import numpy as np
import pytest

from optitrade.core import MarketSnapshot, OptionQuote, OptionType
from optitrade.pricing import IVPoint, bs_price, strip_chain
from optitrade.vol import (
    ESSVIFit,
    ESSVIParams,
    ESSVISurface,
    SABRParams,
    SABRSurface,
    SurfaceLike,
    calibrate_essvi,
    check_calendar,
    check_durrleman,
    essvi_total_variance,
    hagan_implied_vol,
)

SPOT, RATE = 100.0, 0.05

# Ground truth for the round-trip: 4 expiries with ~20-21% ATM vol, well inside
# the Gatheral-Jacquier (2014, Thm 4.2) butterfly bounds.
TRUE = ESSVIParams(
    rho=-0.3,
    eta=0.8,
    gamma_=0.45,
    theta_by_expiry=((30 / 365, 0.0035), (0.25, 0.011), (0.5, 0.021), (1.0, 0.042)),
)
QUOTED_LM_RANGE = 0.30  # quoted strikes span |k| <= 0.30


def _essvi_points(noise_sd: float = 0.001, seed: int = 11) -> list[IVPoint]:
    """9 strikes x 4 expiries from TRUE, plus seeded N(0, 0.1 vol-pt) noise."""
    rng = np.random.default_rng(seed)
    points: list[IVPoint] = []
    for expiry, _theta in TRUE.theta_by_expiry:
        forward = SPOT * math.exp(RATE * expiry)
        for lm in np.linspace(-QUOTED_LM_RANGE, QUOTED_LM_RANGE, 9):
            w = float(np.asarray(essvi_total_variance(float(lm), expiry, TRUE)))
            iv = math.sqrt(w / expiry) + float(rng.normal(0.0, noise_sd))
            points.append(
                IVPoint(
                    strike=forward * math.exp(float(lm)),
                    expiry=expiry,
                    iv=iv,
                    option_type=OptionType.CALL,
                    forward=forward,
                    log_moneyness=float(lm),
                )
            )
    return points


@pytest.fixture(scope="module")
def fitted() -> ESSVIFit:
    return calibrate_essvi(_essvi_points(), SPOT, RATE)


def test_round_trip_rmse_below_target(fitted: ESSVIFit) -> None:
    assert fitted.rmse_vol_points < 0.3  # headline product target
    assert fitted.n_iterations >= 1
    assert -0.999 <= fitted.params.rho <= 0.999
    assert fitted.params.eta > 0.0
    assert 0.01 <= fitted.params.gamma_ <= 0.99


def test_fitted_surface_has_zero_durrleman_violations(fitted: ESSVIFit) -> None:
    surface = ESSVISurface(fitted.params, SPOT, RATE)
    k_grid = np.linspace(-1.5 * QUOTED_LM_RANGE, 1.5 * QUOTED_LM_RANGE, 241)
    for expiry, _theta in fitted.params.theta_by_expiry:
        violations = check_durrleman(surface, expiry, surface.forward(expiry), k_grid=k_grid)
        assert violations == [], f"Durrleman violations at T={expiry}: {violations}"


def test_fitted_surface_is_calendar_free(fitted: ESSVIFit) -> None:
    surface = ESSVISurface(fitted.params, SPOT, RATE)
    assert check_calendar(surface) == []
    # Total variance non-decreasing in expiry at fixed log-moneyness, directly.
    for lm in (-0.3, -0.1, 0.0, 0.1, 0.3):
        w_term = [
            float(np.asarray(essvi_total_variance(lm, t, fitted.params)))
            for t, _ in fitted.params.theta_by_expiry
        ]
        assert all(w_far >= w_near for w_near, w_far in pairwise(w_term))


def test_calibrated_thetas_strictly_increasing(fitted: ESSVIFit) -> None:
    # Monotonicity is enforced by construction: the optimiser works on
    # positive theta increments, so the cumulative knots must increase.
    thetas = [theta for _, theta in fitted.params.theta_by_expiry]
    assert all(b > a for a, b in pairwise(thetas))


def test_calibration_is_deterministic() -> None:
    points = _essvi_points()
    fit_a = calibrate_essvi(points, SPOT, RATE, seed=3)
    fit_b = calibrate_essvi(points, SPOT, RATE, seed=3)
    assert fit_a.params == fit_b.params
    assert fit_a.rmse_vol_points == fit_b.rmse_vol_points


def _synthetic_sabr_snapshot() -> MarketSnapshot:
    """The demo's synthetic SABR market (optitrade.cli._synthetic_snapshot),
    replicated inline — tests must not import the CLI."""
    quotes: list[OptionQuote] = []
    for expiry, alpha, rho, nu in (
        (30 / 365, 0.22, -0.35, 0.9),
        (91 / 365, 0.21, -0.30, 0.7),
        (182 / 365, 0.20, -0.25, 0.55),
    ):
        forward = SPOT * math.exp(RATE * expiry)
        params = SABRParams(alpha=alpha, beta=1.0, rho=rho, nu=nu, forward=forward, expiry=expiry)
        strikes = np.round(np.linspace(0.85, 1.15, 9) * SPOT)
        vols = np.asarray(hagan_implied_vol(strikes, params))
        for strike, vol in zip(strikes, vols, strict=True):
            opt_type = OptionType.CALL if strike >= SPOT else OptionType.PUT
            mid = float(bs_price(SPOT, float(strike), expiry, RATE, float(vol), opt_type))
            quotes.append(
                OptionQuote(strike=float(strike), expiry=expiry, option_type=opt_type, mid=mid)
            )
    return MarketSnapshot(spot=SPOT, rate=RATE, timestamp=1_700_000_000.0, quotes=tuple(quotes))


def _rmse_vol_points(surface: SurfaceLike, points: list[IVPoint]) -> float:
    errors = np.array([float(np.asarray(surface.vol(p.strike, p.expiry))) - p.iv for p in points])
    return 100.0 * float(np.sqrt(np.mean(np.square(errors))))


def test_benchmark_essvi_vs_sabr_on_sabr_market() -> None:
    snapshot = _synthetic_sabr_snapshot()
    points = strip_chain(snapshot)
    sabr = SABRSurface.from_snapshot(snapshot)
    essvi = ESSVISurface.from_snapshot(snapshot)
    sabr_rmse = _rmse_vol_points(sabr, points)
    essvi_rmse = _rmse_vol_points(essvi, points)
    print(
        f"\n[benchmark] SABR-generated market: SABR RMSE {sabr_rmse:.4f} vol-pt, "
        f"eSSVI RMSE {essvi_rmse:.4f} vol-pt"
    )
    # SABR wins on its own generated data — that's expected and fine: the
    # market IS three per-expiry SABR smiles, so per-expiry SABR reprices it
    # (near) exactly, while the 6-parameter joint eSSVI trades pointwise fit
    # for cross-expiry consistency. SABR here is the benchmark, not the rival.
    assert sabr_rmse < essvi_rmse
    assert essvi_rmse < 1.0  # joint fit must stay within 1 vol-pt of the market


def test_invalid_params_rejected() -> None:
    knots = ((0.25, 0.01), (1.0, 0.04))
    with pytest.raises(ValueError, match="rho"):
        ESSVIParams(rho=1.0, eta=0.8, gamma_=0.45, theta_by_expiry=knots)
    with pytest.raises(ValueError, match="eta"):
        ESSVIParams(rho=-0.3, eta=0.0, gamma_=0.45, theta_by_expiry=knots)
    with pytest.raises(ValueError, match="gamma_"):
        ESSVIParams(rho=-0.3, eta=0.8, gamma_=1.2, theta_by_expiry=knots)
    with pytest.raises(ValueError, match="strictly increasing"):
        ESSVIParams(rho=-0.3, eta=0.8, gamma_=0.45, theta_by_expiry=((0.25, 0.04), (1.0, 0.01)))
    with pytest.raises(ValueError, match="strictly increasing"):
        ESSVIParams(rho=-0.3, eta=0.8, gamma_=0.45, theta_by_expiry=((1.0, 0.01), (0.25, 0.04)))

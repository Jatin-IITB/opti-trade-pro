"""Vol surface: spline node fidelity, total-variance interpolation, end-to-end build."""

import math

import numpy as np
import pytest

from optitrade.core import CalibrationError, MarketSnapshot, OptionQuote, OptionType
from optitrade.pricing import IVPoint, bs_price
from optitrade.vol import SABRSurface, VolSurface

SPOT, RATE, DIV = 100.0, 0.03, 0.01
EXPIRIES = (0.25, 0.5, 1.0)
LM_NODES = np.linspace(-0.3, 0.3, 7)


def _smile(log_moneyness: float) -> float:
    return 0.2 + 0.05 * log_moneyness**2 - 0.03 * log_moneyness


def _forward(expiry: float) -> float:
    return SPOT * math.exp((RATE - DIV) * expiry)


def _points() -> list[IVPoint]:
    points = []
    for expiry in EXPIRIES:
        fwd = _forward(expiry)
        for lm in LM_NODES:
            points.append(
                IVPoint(
                    strike=fwd * math.exp(lm),
                    expiry=expiry,
                    iv=_smile(lm),
                    option_type=OptionType.CALL,
                    forward=fwd,
                    log_moneyness=lm,
                )
            )
    return points


@pytest.fixture()
def surface() -> VolSurface:
    return VolSurface.from_points(_points(), SPOT, RATE, DIV)


def test_spline_reproduces_node_ivs_exactly(surface: VolSurface) -> None:
    for expiry in EXPIRIES:
        fwd = _forward(expiry)
        strikes = fwd * np.exp(LM_NODES)
        vols = np.asarray(surface.vol(strikes, expiry))
        np.testing.assert_allclose(vols, [_smile(lm) for lm in LM_NODES], rtol=0, atol=1e-14)


def test_between_expiry_interpolation_is_total_variance_linear(surface: VolSurface) -> None:
    lm = float(LM_NODES[4])  # a node, so slice vols are exact
    t = 0.4
    strike = _forward(t) * math.exp(lm)
    w_lo = _smile(lm) ** 2 * 0.25
    w_hi = _smile(lm) ** 2 * 0.5
    w_expected = w_lo + (t - 0.25) / (0.5 - 0.25) * (w_hi - w_lo)
    assert abs(float(surface.vol(strike, t)) - math.sqrt(w_expected / t)) < 1e-12
    assert abs(float(surface.total_variance(strike, t)) - w_expected) < 1e-12


def test_flat_extrapolation_beyond_expiries_and_strikes(surface: VolSurface) -> None:
    lm = 0.1
    short = float(surface.vol(_forward(0.05) * math.exp(lm), 0.05))
    long = float(surface.vol(_forward(3.0) * math.exp(lm), 3.0))
    assert abs(short - _smile(lm)) < 1e-12
    assert abs(long - _smile(lm)) < 1e-12
    fwd = _forward(0.25)
    far_otm = float(surface.vol(fwd * math.exp(1.5), 0.25))
    edge = float(surface.vol(fwd * math.exp(0.3), 0.25))
    assert abs(far_otm - edge) < 1e-14


def test_from_snapshot_end_to_end() -> None:
    quotes = []
    for expiry in (0.25, 0.5):
        fwd = _forward(expiry)
        for lm in np.linspace(-0.2, 0.2, 5):
            strike = fwd * math.exp(lm)
            option_type = OptionType.CALL if lm >= 0 else OptionType.PUT
            mid = float(bs_price(SPOT, strike, expiry, RATE, _smile(lm), option_type, DIV))
            quotes.append(
                OptionQuote(strike=strike, expiry=expiry, option_type=option_type, mid=mid)
            )
    snapshot = MarketSnapshot(
        spot=SPOT, rate=RATE, timestamp=0.0, quotes=tuple(quotes), dividend_yield=DIV
    )
    surface = VolSurface.from_snapshot(snapshot)
    assert list(surface.expiries) == [0.25, 0.5]
    for lm in np.linspace(-0.2, 0.2, 5):
        vol = float(surface.vol(_forward(0.25) * math.exp(lm), 0.25))
        assert abs(vol - _smile(float(lm))) < 1e-6


def test_thin_slices_dropped_with_warning() -> None:
    points = _points()
    fwd = _forward(2.0)
    thin = [
        IVPoint(fwd * math.exp(lm), 2.0, 0.2, OptionType.CALL, fwd, lm)
        for lm in (-0.1, 0.0, 0.1)  # only 3 strikes
    ]
    surface = VolSurface.from_points(points + thin, SPOT, RATE, DIV)
    assert len(surface.warnings) == 1
    assert "T=2" in surface.warnings[0]
    assert list(surface.expiries) == list(EXPIRIES)


def test_all_thin_slices_raises() -> None:
    fwd = _forward(0.5)
    thin = [
        IVPoint(fwd * math.exp(lm), 0.5, 0.2, OptionType.CALL, fwd, lm) for lm in (-0.1, 0.0, 0.1)
    ]
    with pytest.raises(CalibrationError, match=">= 4"):
        VolSurface.from_points(thin, SPOT, RATE, DIV)


def test_sabr_surface_same_interface() -> None:
    surface = SABRSurface.from_points(_points(), SPOT, RATE, DIV, beta=1.0, n_starts=4)
    assert len(surface.slice_fits) == len(EXPIRIES)
    assert surface.worst_rmse_vol_points == max(f.rmse_vol_points for f in surface.slice_fits)
    # SABR is a 3-parameter fit of a smooth smile: close, not exact, at the nodes.
    vol = float(surface.vol(_forward(0.25), 0.25))
    assert abs(vol - _smile(0.0)) < 0.01
    grid = np.asarray(surface.vol(_forward(0.25) * np.exp(LM_NODES), 0.25))
    assert grid.shape == LM_NODES.shape

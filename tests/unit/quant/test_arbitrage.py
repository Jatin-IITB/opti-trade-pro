"""No-arbitrage checks: clean surface passes, seeded violations are caught."""

import math

import numpy as np
import pytest

from optitrade.core import ArbitrageViolationError, OptionType
from optitrade.pricing import IVPoint
from optitrade.vol import (
    SABRParams,
    SABRSurface,
    check_butterfly,
    check_calendar,
    hagan_implied_vol,
    validate_surface,
)

SPOT, RATE = 100.0, 0.03


def _clean_sabr_surface() -> SABRSurface:
    points = []
    for expiry in (0.25, 1.0):
        fwd = SPOT * math.exp(RATE * expiry)
        params = SABRParams(0.2, 1.0, -0.2, 0.3, fwd, expiry)
        for lm in np.linspace(-0.25, 0.25, 9):
            strike = fwd * math.exp(lm)
            points.append(
                IVPoint(
                    strike=strike,
                    expiry=expiry,
                    iv=float(hagan_implied_vol(strike, params)),
                    option_type=OptionType.CALL,
                    forward=fwd,
                    log_moneyness=float(lm),
                )
            )
    return SABRSurface.from_points(points, SPOT, RATE, n_starts=4)


def test_clean_sabr_surface_passes() -> None:
    surface = _clean_sabr_surface()
    assert surface.worst_rmse_vol_points < 0.3
    violations = validate_surface(surface, SPOT, RATE)
    assert violations == []
    # raise_on_violation must be a no-op on a clean surface
    assert validate_surface(surface, SPOT, RATE, raise_on_violation=True) == []


class _VolSpikeStub:
    """A sharp localised vol spike implies a negative density (butterfly arb)."""

    expiries = np.array([0.5])

    def forward(self, expiry: float) -> float:
        return SPOT

    def vol(self, strike: object, expiry: object) -> np.ndarray:
        lm = np.log(np.asarray(strike, dtype=float) / SPOT)
        return 0.2 + 0.5 * np.exp(-((lm / 0.05) ** 2))


def test_butterfly_violation_detected() -> None:
    grid = SPOT * np.exp(np.linspace(-0.2, 0.2, 41))
    violations = check_butterfly(_VolSpikeStub(), 0.5, SPOT, 0.0, strike_grid=grid)
    assert violations
    assert all(v.kind == "butterfly" for v in violations)
    assert all(v.magnitude > 0 for v in violations)
    assert all(v.expiry == 0.5 for v in violations)


class _DecayingVarianceStub:
    """Total variance falls from 0.3^2*0.5 to 0.15^2*1.0: calendar arbitrage."""

    expiries = np.array([0.5, 1.0])

    def forward(self, expiry: float) -> float:
        return SPOT

    def vol(self, strike: object, expiry: object) -> np.ndarray:
        level = 0.3 if float(np.asarray(expiry, dtype=float).flat[0]) < 0.75 else 0.15
        return np.full_like(np.asarray(strike, dtype=float), level)


def test_calendar_violation_detected() -> None:
    violations = check_calendar(_DecayingVarianceStub())
    assert violations
    assert all(v.kind == "calendar" for v in violations)
    expected = 0.3**2 * 0.5 - 0.15**2 * 1.0
    assert all(abs(v.magnitude - expected) < 1e-12 for v in violations)


def test_validate_surface_raises_when_asked() -> None:
    stub = _DecayingVarianceStub()
    violations = validate_surface(stub, SPOT, RATE)
    assert any(v.kind == "calendar" for v in violations)
    with pytest.raises(ArbitrageViolationError, match=r"total variance"):
        validate_surface(stub, SPOT, RATE, raise_on_violation=True)

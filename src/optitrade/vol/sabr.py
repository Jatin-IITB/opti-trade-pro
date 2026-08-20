"""SABR stochastic-volatility smile: Hagan asymptotic vol and calibration.

Implements the lognormal implied-vol expansion of Hagan, Kumar, Lesniewski &
Woodward, "Managing Smile Risk", Wilmott (2002), eq. (2.17a), with the ATM
limit (2.18) recovered smoothly via a series expansion of ``z / x(z)`` for
small ``z`` rather than a branch on exact equality.

Calibration fixes ``beta`` (a modelling choice — 1.0 for equities, 0.5 for
rates) and fits ``(alpha, rho, nu)`` by multi-start trust-region least
squares; starts are stratified (Latin-hypercube-style) draws from a seeded
generator so results are deterministic.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.optimize import least_squares

from optitrade.core import CalibrationError
from optitrade.pricing.black_scholes import ArrayLike

_Z_SERIES_TOL = 1e-6


@dataclass(frozen=True, slots=True)
class SABRParams:
    """SABR model parameters for a single expiry slice."""

    alpha: float
    beta: float
    rho: float
    nu: float
    forward: float
    expiry: float

    def __post_init__(self) -> None:
        if self.alpha <= 0.0:
            raise ValueError(f"alpha must be positive, got {self.alpha}")
        if not 0.0 <= self.beta <= 1.0:
            raise ValueError(f"beta must be in [0, 1], got {self.beta}")
        if not -1.0 < self.rho < 1.0:
            raise ValueError(f"rho must be in (-1, 1), got {self.rho}")
        if self.nu < 0.0:
            raise ValueError(f"nu must be non-negative, got {self.nu}")
        if self.forward <= 0.0:
            raise ValueError(f"forward must be positive, got {self.forward}")
        if self.expiry <= 0.0:
            raise ValueError(f"expiry must be positive, got {self.expiry}")


@dataclass(frozen=True, slots=True)
class SABRFit:
    """Result of a SABR slice calibration; RMSE is in vol points (x100)."""

    params: SABRParams
    rmse_vol_points: float
    n_starts_used: int


def hagan_implied_vol(strike: ArrayLike, params: SABRParams) -> float | np.ndarray:
    """Hagan et al. (2002) lognormal implied vol, vectorised over strike.

    Uses eq. (2.17a); the ``z / x(z)`` factor is replaced by its Taylor series
    ``1 - rho z / 2 + (2 - 3 rho^2) z^2 / 12`` for ``|z|`` below a tolerance,
    which also yields the correct ATM limit (2.18) as ``K -> F``.
    """
    k = np.asarray(strike, dtype=float)
    if np.any(k <= 0.0):
        raise ValueError("strikes must be positive")
    f, t = params.forward, params.expiry
    alpha, beta, rho, nu = params.alpha, params.beta, params.rho, params.nu
    one_m_beta = 1.0 - beta

    log_fk = np.log(f / k)
    fk_pow = (f * k) ** (0.5 * one_m_beta)  # (FK)^{(1-beta)/2}

    z = (nu / alpha) * fk_pow * log_fk
    sqrt_term = np.sqrt(1.0 - 2.0 * rho * z + z * z)
    # x(z) -> 0 as z -> 0; evaluate the ratio via its series there to avoid 0/0.
    small = np.abs(z) < _Z_SERIES_TOL
    z_safe = np.where(small, 1.0, z)
    x_z = np.log((sqrt_term + z_safe - rho) / (1.0 - rho))
    ratio = np.where(
        small,
        1.0 - 0.5 * rho * z + (2.0 - 3.0 * rho * rho) * z * z / 12.0,
        z_safe / np.where(x_z == 0.0, 1.0, x_z),
    )

    denom = fk_pow * (
        1.0 + (one_m_beta**2 / 24.0) * log_fk**2 + (one_m_beta**4 / 1920.0) * log_fk**4
    )
    time_corr = 1.0 + t * (
        (one_m_beta**2 / 24.0) * alpha**2 / fk_pow**2
        + 0.25 * rho * beta * nu * alpha / fk_pow
        + ((2.0 - 3.0 * rho * rho) / 24.0) * nu * nu
    )
    vol = (alpha / denom) * ratio * time_corr
    return float(vol) if vol.ndim == 0 else vol


def _lhs_starts(rng: np.random.Generator, n_starts: int, alpha_scale: float) -> np.ndarray:
    """Stratified (Latin-hypercube) starts over (alpha, rho, nu)."""
    u = np.empty((n_starts, 3))
    for j in range(3):
        u[:, j] = (rng.permutation(n_starts) + rng.uniform(size=n_starts)) / n_starts
    alpha0 = alpha_scale * (0.3 + 1.7 * u[:, 0])  # 0.3x .. 2x ATM-implied alpha
    rho0 = -0.95 + 1.9 * u[:, 1]
    nu0 = 0.05 + 2.95 * u[:, 2]
    return np.column_stack([alpha0, rho0, nu0])


def calibrate_sabr(
    strikes: ArrayLike,
    ivs: ArrayLike,
    forward: float,
    expiry: float,
    beta: float = 1.0,
    n_starts: int = 8,
    seed: int = 0,
    max_rmse_vol_points: float | None = None,
) -> SABRFit:
    """Fit (alpha, rho, nu) with ``beta`` held fixed, keeping the min-RMSE start.

    Each stratified start is refined with :func:`scipy.optimize.least_squares`
    (method ``"trf"``, bounds ``alpha > 0``, ``rho`` in (-0.999, 0.999),
    ``nu >= 1e-6``). Raises :class:`CalibrationError` when fewer than 3 quotes
    are supplied or the best RMSE exceeds ``max_rmse_vol_points``.
    """
    k = np.atleast_1d(np.asarray(strikes, dtype=float))
    v = np.atleast_1d(np.asarray(ivs, dtype=float))
    if k.shape != v.shape:
        raise CalibrationError(f"strikes {k.shape} and ivs {v.shape} must align")
    if k.size < 3:
        raise CalibrationError(f"need >= 3 quotes to fit 3 SABR params, got {k.size}")
    if not 0.0 <= beta <= 1.0:
        raise CalibrationError(f"beta must be in [0, 1], got {beta}")
    if n_starts < 1:
        raise CalibrationError(f"n_starts must be >= 1, got {n_starts}")

    def residuals(x: np.ndarray) -> np.ndarray:
        p = SABRParams(
            alpha=float(x[0]),
            beta=beta,
            rho=float(x[1]),
            nu=float(x[2]),
            forward=forward,
            expiry=expiry,
        )
        return np.asarray(hagan_implied_vol(k, p), dtype=float) - v

    # ATM lognormal vol ~ alpha / F^{1-beta}, so scale alpha starts accordingly.
    atm_iv = float(v[np.argmin(np.abs(np.log(k / forward)))])
    alpha_scale = max(atm_iv, 1e-4) * forward ** (1.0 - beta)
    rng = np.random.default_rng(seed)
    starts = _lhs_starts(rng, n_starts, alpha_scale)

    lb = np.array([1e-10, -0.999, 1e-6])
    ub = np.array([np.inf, 0.999, np.inf])
    best_x: np.ndarray | None = None
    best_rmse = np.inf
    n_used = 0
    for x0 in starts:
        n_used += 1
        try:
            res = least_squares(
                residuals,
                np.clip(x0, lb + 1e-12, None),
                method="trf",
                bounds=(lb, ub),
                xtol=1e-14,
                ftol=1e-14,
                gtol=1e-14,
            )
        except (ValueError, FloatingPointError):  # pathological start; try the next one
            continue
        rmse = float(np.sqrt(np.mean(np.square(res.fun))))
        if rmse < best_rmse:
            best_rmse = rmse
            best_x = np.asarray(res.x, dtype=float)
        if best_rmse < 1e-12:  # exact fit; further starts cannot improve
            break
    if best_x is None:
        raise CalibrationError(f"all {n_starts} SABR starts failed for T={expiry}")

    rmse_vol_points = best_rmse * 100.0
    if max_rmse_vol_points is not None and rmse_vol_points > max_rmse_vol_points:
        raise CalibrationError(
            f"SABR calibration RMSE {rmse_vol_points:.4f} vol points exceeds "
            f"tolerance {max_rmse_vol_points} for T={expiry}"
        )
    params = SABRParams(
        alpha=float(best_x[0]),
        beta=beta,
        rho=float(best_x[1]),
        nu=float(best_x[2]),
        forward=forward,
        expiry=expiry,
    )
    return SABRFit(params=params, rmse_vol_points=rmse_vol_points, n_starts_used=n_used)


__all__ = ["SABRFit", "SABRParams", "calibrate_sabr", "hagan_implied_vol"]

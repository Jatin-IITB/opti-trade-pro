"""SSVI volatility surface with power-law phi, calibrated jointly across expiries.

Implements the "surface SVI" (SSVI) parameterisation of Gatheral & Jacquier,
"Arbitrage-free SVI volatility surfaces", Quantitative Finance 14(1), 2014,
eq. (4.1):

    w(k, theta_t) = (theta_t / 2) [1 + rho phi(theta_t) k
                                   + sqrt((phi(theta_t) k + rho)^2 + 1 - rho^2)]

with power-law ``phi(theta) = eta * theta^(-gamma)`` and log-moneyness
``k = ln(K / F_t)``. The ATM total-variance term structure ``theta_t`` is a
knot vector interpolated linearly in expiry (flat beyond the ends), and all
parameters are fitted *jointly* across expiries in one bounded least-squares
problem — theta knots are optimised as positive increments so monotonicity
(hence calendar consistency, Gatheral-Jacquier Theorem 4.1) holds by
construction.

A single global ``rho`` is shared by every expiry. The documented extension is
per-expiry ``rho_t`` — the full eSSVI of Hendriks & Martini, "The eSSVI
volatility surface" (2019) — which slots into the same joint calibration by
widening the parameter vector; it is not implemented here.

Butterfly no-arbitrage is encouraged during calibration through soft penalty
residuals on the Gatheral-Jacquier sufficient conditions (Theorem 4.2)

    theta phi(theta) (1 + |rho|) <= 4    and    theta phi(theta)^2 (1 + |rho|) <= 4

evaluated at each theta knot, and verified post-fit via Durrleman's condition
(:func:`optitrade.vol.arbitrage.check_durrleman`) and the Breeden-Litzenberger
density gate (:mod:`optitrade.vol.density`).
"""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from itertools import pairwise

import numpy as np
from scipy.optimize import least_squares

from optitrade.core import CalibrationError, MarketSnapshot
from optitrade.pricing.black_scholes import ArrayLike
from optitrade.pricing.implied_vol import IVPoint, strip_chain

_MIN_THETA_INCREMENT = 1e-8
_MIN_EXPIRY = 1e-12
_BUTTERFLY_BOUND = 4.0


@dataclass(frozen=True, slots=True)
class ESSVIParams:
    """SSVI surface parameters (Gatheral & Jacquier 2014, power-law phi).

    ``theta_by_expiry`` holds ``(expiry, ATM total variance)`` knots, strictly
    increasing in both coordinates. ``gamma_`` carries a trailing underscore to
    avoid colliding with the Greek "gamma" used for the option sensitivity.
    """

    rho: float
    eta: float
    gamma_: float
    theta_by_expiry: tuple[tuple[float, float], ...]

    def __post_init__(self) -> None:
        if not -1.0 < self.rho < 1.0:
            raise ValueError(f"rho must be in (-1, 1), got {self.rho}")
        if self.eta <= 0.0:
            raise ValueError(f"eta must be positive, got {self.eta}")
        if not 0.0 < self.gamma_ < 1.0:
            raise ValueError(f"gamma_ must be in (0, 1), got {self.gamma_}")
        if not self.theta_by_expiry:
            raise ValueError("theta_by_expiry needs at least one (expiry, theta) knot")
        expiries = [t for t, _ in self.theta_by_expiry]
        thetas = [th for _, th in self.theta_by_expiry]
        if any(t <= 0.0 for t in expiries) or any(th <= 0.0 for th in thetas):
            raise ValueError("expiries and thetas must all be positive")
        if any(b <= a for a, b in pairwise(expiries)):
            raise ValueError(f"expiries must be strictly increasing, got {expiries}")
        if any(b <= a for a, b in pairwise(thetas)):
            raise ValueError(f"ATM total variances must be strictly increasing, got {thetas}")


@dataclass(frozen=True, slots=True)
class ESSVIFit:
    """Result of a joint SSVI calibration; RMSE is in vol points (x100)."""

    params: ESSVIParams
    rmse_vol_points: float
    n_iterations: int


def _ssvi_w(k: np.ndarray, theta: np.ndarray, rho: float, eta: float, gamma_: float) -> np.ndarray:
    """Raw SSVI total variance, eq. (4.1) of Gatheral-Jacquier (2014)."""
    phi_k = eta * np.power(theta, -gamma_) * k
    return np.asarray(
        0.5 * theta * (1.0 + rho * phi_k + np.sqrt((phi_k + rho) ** 2 + 1.0 - rho * rho)),
        dtype=float,
    )


def essvi_total_variance(k: ArrayLike, expiry: ArrayLike, params: ESSVIParams) -> ArrayLike:
    """Total variance ``w(k, theta_t)``, broadcast over ``k`` and ``expiry``.

    ``theta_t`` is interpolated linearly in expiry between the knots of
    ``params.theta_by_expiry`` and held flat beyond the first/last knot.
    """
    k_b, t_b = np.broadcast_arrays(np.asarray(k, dtype=float), np.asarray(expiry, dtype=float))
    shape = k_b.shape
    k_flat = np.atleast_1d(k_b).ravel()
    t_flat = np.atleast_1d(t_b).ravel()
    knot_t = np.array([t for t, _ in params.theta_by_expiry], dtype=float)
    knot_theta = np.array([th for _, th in params.theta_by_expiry], dtype=float)
    theta = np.interp(t_flat, knot_t, knot_theta)  # linear inside, flat beyond ends
    w = _ssvi_w(k_flat, theta, params.rho, params.eta, params.gamma_).reshape(shape)
    return float(w) if w.ndim == 0 else w


def essvi_vol(
    strike: ArrayLike,
    expiry: ArrayLike,
    params: ESSVIParams,
    forward_fn: Callable[[np.ndarray], ArrayLike],
) -> ArrayLike:
    """Implied vol ``sqrt(w(ln(K/F_t), theta_t) / t)`` broadcast over inputs.

    ``forward_fn`` maps an ndarray of expiries to forwards and must be
    vectorised (e.g. ``lambda t: spot * np.exp((rate - q) * t)``).
    """
    k_b, t_b = np.broadcast_arrays(np.asarray(strike, dtype=float), np.asarray(expiry, dtype=float))
    fwd = np.broadcast_to(np.asarray(forward_fn(t_b), dtype=float), t_b.shape)
    lm = np.log(k_b / fwd)
    w = np.asarray(essvi_total_variance(lm, t_b, params), dtype=float)
    vol = np.sqrt(w / np.maximum(t_b, _MIN_EXPIRY))
    return float(vol) if vol.ndim == 0 else vol


def _group_points(
    points: Sequence[IVPoint], spot: float, rate: float, dividend_yield: float
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Per-point (k, T, iv, expiry-index) arrays plus the sorted expiry vector."""
    expiries = np.array(sorted({p.expiry for p in points}), dtype=float)
    index = {t: i for i, t in enumerate(expiries)}
    t_pt = np.array([p.expiry for p in points], dtype=float)
    fwd_pt = spot * np.exp((rate - dividend_yield) * t_pt)
    k_pt = np.log(np.array([p.strike for p in points], dtype=float) / fwd_pt)
    iv_pt = np.array([p.iv for p in points], dtype=float)
    idx_pt = np.array([index[p.expiry] for p in points], dtype=int)
    return k_pt, t_pt, iv_pt, idx_pt, expiries


def _seed_thetas(
    k_pt: np.ndarray, iv_pt: np.ndarray, idx_pt: np.ndarray, expiries: np.ndarray
) -> np.ndarray:
    """Seed theta_i from the ATM total variance of each slice, forced increasing."""
    theta0 = np.empty(expiries.size)
    for i, t in enumerate(expiries):
        mask = idx_pt == i
        atm = np.argmin(np.abs(k_pt[mask]))
        theta0[i] = float(iv_pt[mask][atm]) ** 2 * float(t)
    for i in range(1, theta0.size):  # strictly increasing seeds
        theta0[i] = max(theta0[i], theta0[i - 1] * (1.0 + 1e-6) + _MIN_THETA_INCREMENT)
    return theta0


def calibrate_essvi(
    points: Sequence[IVPoint],
    spot: float,
    rate: float,
    dividend_yield: float = 0.0,
    n_starts: int = 5,
    seed: int = 0,
    butterfly_penalty: float = 10.0,
) -> ESSVIFit:
    """Jointly fit ``(rho, eta, gamma, theta_1..theta_n)`` to stripped IV points.

    Theta knots are seeded from each slice's ATM total variance, then refined
    together with the global shape parameters by bounded
    :func:`scipy.optimize.least_squares` (method ``"trf"``). Monotonicity of
    theta is enforced *by construction*: the optimiser works on positive
    increments ``dtheta_i`` with ``theta_i = sum_{j<=i} dtheta_j``. The
    Gatheral-Jacquier (2014, Thm 4.2) butterfly sufficient conditions
    ``theta phi(theta) (1+|rho|) <= 4`` and ``theta phi(theta)^2 (1+|rho|) <= 4``
    enter as soft penalty residuals (weight ``butterfly_penalty``) at each knot.
    Multistart over ``(rho, eta, gamma)`` is drawn from a generator seeded with
    ``seed``, so the calibration is deterministic.
    """
    if not points:
        raise CalibrationError("calibrate_essvi needs at least one IV point")
    if n_starts < 1:
        raise CalibrationError(f"n_starts must be >= 1, got {n_starts}")
    k_pt, t_pt, iv_pt, idx_pt, expiries = _group_points(points, spot, rate, dividend_yield)
    n_theta = expiries.size
    n_params = 3 + n_theta
    if len(points) < n_params:
        raise CalibrationError(
            f"need >= {n_params} points to fit rho, eta, gamma and {n_theta} theta knot(s), "
            f"got {len(points)}"
        )

    theta0 = _seed_thetas(k_pt, iv_pt, idx_pt, expiries)
    d_theta0 = np.diff(theta0, prepend=0.0)

    def residuals(x: np.ndarray) -> np.ndarray:
        rho, eta, gamma_ = float(x[0]), float(x[1]), float(x[2])
        thetas = np.cumsum(x[3:])
        w = _ssvi_w(k_pt, thetas[idx_pt], rho, eta, gamma_)
        fit_res = np.sqrt(w / t_pt) - iv_pt
        # Soft butterfly penalties, Gatheral-Jacquier (2014) Theorem 4.2.
        theta_phi = eta * np.power(thetas, 1.0 - gamma_)  # theta * phi(theta)
        scale = 1.0 + abs(rho)
        pen1 = np.maximum(0.0, theta_phi * scale - _BUTTERFLY_BOUND)
        pen2 = np.maximum(
            0.0, theta_phi * (eta * np.power(thetas, -gamma_)) * scale - _BUTTERFLY_BOUND
        )
        return np.concatenate([fit_res, butterfly_penalty * pen1, butterfly_penalty * pen2])

    lb = np.concatenate([[-0.999, 1e-6, 0.01], np.full(n_theta, _MIN_THETA_INCREMENT)])
    ub = np.concatenate([[0.999, np.inf, 0.99], np.full(n_theta, np.inf)])
    rng = np.random.default_rng(seed)
    starts = [np.array([-0.3, 1.0, 0.5])] + [
        np.array([rng.uniform(-0.8, 0.8), rng.uniform(0.2, 2.0), rng.uniform(0.15, 0.85)])
        for _ in range(n_starts - 1)
    ]

    n_fit = iv_pt.size
    best_x: np.ndarray | None = None
    best_rmse = np.inf
    best_nfev = 0
    for shape0 in starts:
        x0 = np.clip(np.concatenate([shape0, d_theta0]), lb + 1e-12, None)
        try:
            res = least_squares(
                residuals,
                x0,
                method="trf",
                bounds=(lb, ub),
                xtol=1e-14,
                ftol=1e-14,
                gtol=1e-14,
            )
        except (ValueError, FloatingPointError):  # pathological start; try the next one
            continue
        rmse = float(np.sqrt(np.mean(np.square(np.asarray(res.fun)[:n_fit]))))
        if rmse < best_rmse:
            best_rmse = rmse
            best_x = np.asarray(res.x, dtype=float)
            best_nfev = int(res.nfev)
        if best_rmse < 1e-12:  # exact fit; further starts cannot improve
            break
    if best_x is None:
        raise CalibrationError(f"all {n_starts} eSSVI starts failed")

    thetas = np.cumsum(best_x[3:])
    params = ESSVIParams(
        rho=float(best_x[0]),
        eta=float(best_x[1]),
        gamma_=float(best_x[2]),
        theta_by_expiry=tuple(
            (float(t), float(th)) for t, th in zip(expiries, thetas, strict=True)
        ),
    )
    return ESSVIFit(params=params, rmse_vol_points=best_rmse * 100.0, n_iterations=best_nfev)


class ESSVISurface:
    """SSVI surface exposing the same duck-type interface as ``VolSurface``.

    Implements ``expiries`` / ``forward`` / ``vol`` / ``total_variance``, so it
    satisfies :class:`optitrade.vol.arbitrage.SurfaceLike` and plugs into
    ``validate_surface``, ``check_durrleman`` and the density gate unchanged.
    """

    def __init__(
        self,
        params: ESSVIParams,
        spot: float,
        rate: float,
        dividend_yield: float = 0.0,
        fit: ESSVIFit | None = None,
        warnings: Sequence[str] = (),
    ) -> None:
        self.params = params
        self.spot = spot
        self.rate = rate
        self.dividend_yield = dividend_yield
        self.fit = fit
        self.warnings: list[str] = list(warnings)
        self._expiries = np.array([t for t, _ in params.theta_by_expiry], dtype=float)

    @property
    def expiries(self) -> np.ndarray:
        return self._expiries.copy()

    def forward(self, expiry: float) -> float:
        return self.spot * math.exp((self.rate - self.dividend_yield) * expiry)

    def vol(self, strike: ArrayLike, expiry: ArrayLike) -> ArrayLike:
        """Implied vol at (strike, expiry), broadcast over both inputs."""

        def forward_fn(t: np.ndarray) -> ArrayLike:
            return self.spot * np.exp((self.rate - self.dividend_yield) * t)

        return essvi_vol(strike, expiry, self.params, forward_fn)

    def total_variance(self, strike: ArrayLike, expiry: ArrayLike) -> ArrayLike:
        v = np.asarray(self.vol(strike, expiry), dtype=float)
        w = v * v * np.asarray(expiry, dtype=float)
        return float(w) if w.ndim == 0 else w

    @classmethod
    def from_points(
        cls,
        points: Sequence[IVPoint],
        spot: float,
        rate: float,
        dividend_yield: float = 0.0,
        n_starts: int = 5,
        seed: int = 0,
    ) -> ESSVISurface:
        """Jointly calibrate SSVI to the points and wrap the fit as a surface."""
        fit = calibrate_essvi(
            points, spot, rate, dividend_yield=dividend_yield, n_starts=n_starts, seed=seed
        )
        return cls(fit.params, spot, rate, dividend_yield, fit=fit)

    @classmethod
    def from_snapshot(
        cls, snapshot: MarketSnapshot, n_starts: int = 5, seed: int = 0
    ) -> ESSVISurface:
        """Strip IVs from the snapshot's chain, then calibrate jointly."""
        points = strip_chain(snapshot)
        return cls.from_points(
            points,
            snapshot.spot,
            snapshot.rate,
            snapshot.dividend_yield,
            n_starts=n_starts,
            seed=seed,
        )


__all__ = [
    "ESSVIFit",
    "ESSVIParams",
    "ESSVISurface",
    "calibrate_essvi",
    "essvi_total_variance",
    "essvi_vol",
]

"""Volatility surfaces: cubic-spline smiles and SABR slices with
total-variance interpolation in expiry.

Each expiry slice is a smile in log-moneyness ``ln(K / F)``; between quoted
expiries the surface interpolates linearly in *total variance*
``w = iv^2 T`` at fixed log-moneyness, which preserves calendar
no-arbitrage (``w`` non-decreasing in ``T``) whenever the quoted slices are
themselves calendar-ordered (Gatheral 2006, ch. 4). Beyond the first/last
expiry the surface is flat in vol; beyond a slice's quoted strike range the
smile extrapolates flat.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, field

import numpy as np
from scipy.interpolate import CubicSpline

from optitrade.core import CalibrationError, MarketSnapshot
from optitrade.pricing.black_scholes import ArrayLike
from optitrade.pricing.implied_vol import IVPoint, strip_chain
from optitrade.vol.sabr import SABRFit, calibrate_sabr, hagan_implied_vol

_MIN_STRIKES_PER_SLICE = 4


@dataclass(frozen=True)
class SmileSlice:
    """A single-expiry smile: natural cubic spline of iv vs log-moneyness.

    ``log_moneyness`` must be strictly increasing; evaluation clips to the
    quoted range (flat extrapolation).
    """

    expiry: float
    forward: float
    log_moneyness: np.ndarray
    ivs: np.ndarray
    spline: CubicSpline = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        lm = np.asarray(self.log_moneyness, dtype=float)
        iv = np.asarray(self.ivs, dtype=float)
        if lm.ndim != 1 or lm.shape != iv.shape:
            raise ValueError("log_moneyness and ivs must be 1-D arrays of equal length")
        if lm.size < 2:
            raise ValueError(f"need >= 2 nodes for a spline, got {lm.size}")
        if not np.all(np.diff(lm) > 0.0):
            raise ValueError("log_moneyness must be strictly increasing")
        object.__setattr__(self, "log_moneyness", lm)
        object.__setattr__(self, "ivs", iv)
        object.__setattr__(self, "spline", CubicSpline(lm, iv, bc_type="natural"))

    def vol_from_log_moneyness(self, log_moneyness: ArrayLike) -> np.ndarray:
        lm = np.clip(
            np.asarray(log_moneyness, dtype=float),
            self.log_moneyness[0],
            self.log_moneyness[-1],
        )
        return np.asarray(self.spline(lm), dtype=float)

    def vol(self, strike: ArrayLike) -> float | np.ndarray:
        k = np.asarray(strike, dtype=float)
        out = self.vol_from_log_moneyness(np.log(k / self.forward))
        return float(out) if out.ndim == 0 else out

    def total_variance(self, strike: ArrayLike) -> float | np.ndarray:
        v = np.asarray(self.vol(strike), dtype=float)
        w = v * v * self.expiry
        return float(w) if w.ndim == 0 else w


def _group_by_expiry(points: Sequence[IVPoint]) -> list[list[IVPoint]]:
    by_expiry: dict[float, list[IVPoint]] = {}
    for p in points:
        by_expiry.setdefault(p.expiry, []).append(p)
    return [by_expiry[t] for t in sorted(by_expiry)]


def _dedupe_nodes(group: Sequence[IVPoint]) -> tuple[np.ndarray, np.ndarray]:
    """Sorted (log_moneyness, iv) nodes; call/put quotes at one strike are averaged."""
    lm = np.array([p.log_moneyness for p in group], dtype=float)
    iv = np.array([p.iv for p in group], dtype=float)
    order = np.argsort(lm)
    lm, iv = lm[order], iv[order]
    uniq, inverse = np.unique(np.round(lm, 12), return_inverse=True)
    iv_avg = np.zeros_like(uniq)
    counts = np.zeros_like(uniq)
    np.add.at(iv_avg, inverse, iv)
    np.add.at(counts, inverse, 1.0)
    return uniq, iv_avg / counts


class _SurfaceBase:
    """Shared expiry-direction logic; subclasses supply the per-slice smile."""

    def __init__(
        self,
        spot: float,
        rate: float,
        dividend_yield: float,
        expiries: Sequence[float],
        warnings: Sequence[str] = (),
    ) -> None:
        self.spot = spot
        self.rate = rate
        self.dividend_yield = dividend_yield
        self._expiries = np.asarray(expiries, dtype=float)
        self.warnings: list[str] = list(warnings)

    @property
    def expiries(self) -> np.ndarray:
        return self._expiries.copy()

    def forward(self, expiry: float) -> float:
        return self.spot * math.exp((self.rate - self.dividend_yield) * expiry)

    def _slice_vol_lm(self, index: int, log_moneyness: np.ndarray) -> np.ndarray:
        raise NotImplementedError

    def vol(self, strike: ArrayLike, expiry: ArrayLike) -> float | np.ndarray:
        """Implied vol at (strike, expiry), broadcast over both inputs.

        Within a quoted slice the smile is evaluated directly. Between quoted
        expiries, total variance ``w = iv^2 T`` is interpolated linearly in
        ``T`` at fixed log-moneyness ``ln(K / F(T))`` — this preserves calendar
        monotonicity of ``w`` when the quoted slices are calendar-ordered.
        Beyond the first/last expiry the smile is held flat in vol.
        """
        k_in, t_in = np.broadcast_arrays(
            np.asarray(strike, dtype=float), np.asarray(expiry, dtype=float)
        )
        shape = k_in.shape
        k = np.atleast_1d(k_in).ravel()
        t = np.atleast_1d(t_in).ravel()
        fwd = self.spot * np.exp((self.rate - self.dividend_yield) * t)
        lm = np.log(k / fwd)

        exps = self._expiries
        out = np.empty_like(lm)
        below = t <= exps[0]
        above = t >= exps[-1]
        if below.any():
            out[below] = self._slice_vol_lm(0, lm[below])
        if above.any():
            out[above] = self._slice_vol_lm(len(exps) - 1, lm[above])
        interior = ~(below | above)
        if interior.any():
            hi = np.searchsorted(exps, t)  # exps[hi-1] < t <= exps[hi] on interior
            for i in range(1, len(exps)):
                m = interior & (hi == i)
                if not m.any():
                    continue
                t_lo, t_hi = exps[i - 1], exps[i]
                w_lo = self._slice_vol_lm(i - 1, lm[m]) ** 2 * t_lo
                w_hi = self._slice_vol_lm(i, lm[m]) ** 2 * t_hi
                weight = (t[m] - t_lo) / (t_hi - t_lo)
                out[m] = np.sqrt((w_lo + weight * (w_hi - w_lo)) / t[m])
        result = out.reshape(shape)
        return float(result) if result.ndim == 0 else result

    def total_variance(self, strike: ArrayLike, expiry: ArrayLike) -> float | np.ndarray:
        v = np.asarray(self.vol(strike, expiry), dtype=float)
        w = v * v * np.asarray(expiry, dtype=float)
        return float(w) if w.ndim == 0 else w


def _partition_slices(
    points: Sequence[IVPoint],
) -> tuple[list[tuple[float, float, np.ndarray, np.ndarray]], list[str]]:
    """Group points by expiry, dropping slices with < 4 distinct strikes."""
    kept: list[tuple[float, float, np.ndarray, np.ndarray]] = []
    dropped: list[str] = []
    for group in _group_by_expiry(points):
        lm, iv = _dedupe_nodes(group)
        expiry, forward = group[0].expiry, group[0].forward
        if lm.size < _MIN_STRIKES_PER_SLICE:
            dropped.append(
                f"dropped slice T={expiry:.6g}: {lm.size} distinct strikes "
                f"< {_MIN_STRIKES_PER_SLICE}"
            )
            continue
        kept.append((expiry, forward, lm, iv))
    return kept, dropped


class VolSurface(_SurfaceBase):
    """Spline-smile surface built from stripped :class:`IVPoint` observations."""

    def __init__(
        self,
        slices: Sequence[SmileSlice],
        spot: float,
        rate: float,
        dividend_yield: float = 0.0,
        warnings: Sequence[str] = (),
    ) -> None:
        if not slices:
            raise CalibrationError("VolSurface needs at least one smile slice")
        ordered = sorted(slices, key=lambda s: s.expiry)
        super().__init__(spot, rate, dividend_yield, [s.expiry for s in ordered], warnings)
        self.slices: list[SmileSlice] = list(ordered)

    @classmethod
    def from_points(
        cls,
        points: Sequence[IVPoint],
        spot: float,
        rate: float,
        dividend_yield: float = 0.0,
    ) -> VolSurface:
        """Group points by expiry into spline slices (>= 4 strikes each).

        Thinner slices are dropped with a warning recorded on ``warnings``;
        if none survive a :class:`CalibrationError` is raised.
        """
        kept, dropped = _partition_slices(points)
        if not kept:
            raise CalibrationError(
                f"no slice has >= {_MIN_STRIKES_PER_SLICE} distinct strikes "
                f"({len(points)} points supplied)"
            )
        slices = [
            SmileSlice(expiry=t, forward=f, log_moneyness=lm, ivs=iv) for t, f, lm, iv in kept
        ]
        return cls(slices, spot, rate, dividend_yield, warnings=dropped)

    @classmethod
    def from_snapshot(cls, snapshot: MarketSnapshot) -> VolSurface:
        """Strip IVs from the snapshot's chain, then build the surface."""
        points = strip_chain(snapshot)
        return cls.from_points(points, snapshot.spot, snapshot.rate, snapshot.dividend_yield)

    def _slice_vol_lm(self, index: int, log_moneyness: np.ndarray) -> np.ndarray:
        return self.slices[index].vol_from_log_moneyness(log_moneyness)


class SABRSurface(_SurfaceBase):
    """Surface whose slices are per-expiry SABR calibrations (fixed beta)."""

    def __init__(
        self,
        slice_fits: Sequence[SABRFit],
        spot: float,
        rate: float,
        dividend_yield: float = 0.0,
        warnings: Sequence[str] = (),
    ) -> None:
        if not slice_fits:
            raise CalibrationError("SABRSurface needs at least one calibrated slice")
        ordered = sorted(slice_fits, key=lambda f: f.params.expiry)
        super().__init__(spot, rate, dividend_yield, [f.params.expiry for f in ordered], warnings)
        self.slice_fits: list[SABRFit] = list(ordered)

    @property
    def worst_rmse_vol_points(self) -> float:
        return max(f.rmse_vol_points for f in self.slice_fits)

    @classmethod
    def from_points(
        cls,
        points: Sequence[IVPoint],
        spot: float,
        rate: float,
        dividend_yield: float = 0.0,
        beta: float = 1.0,
        n_starts: int = 8,
        seed: int = 0,
        max_rmse_vol_points: float | None = None,
    ) -> SABRSurface:
        """Calibrate one SABR slice per expiry (>= 4 strikes each).

        Thinner slices are dropped with a warning; if none survive a
        :class:`CalibrationError` is raised. ``max_rmse_vol_points`` is
        forwarded to :func:`calibrate_sabr` per slice.
        """
        kept, dropped = _partition_slices(points)
        if not kept:
            raise CalibrationError(
                f"no slice has >= {_MIN_STRIKES_PER_SLICE} distinct strikes "
                f"({len(points)} points supplied)"
            )
        fits = [
            calibrate_sabr(
                strikes=forward * np.exp(lm),
                ivs=iv,
                forward=forward,
                expiry=expiry,
                beta=beta,
                n_starts=n_starts,
                seed=seed,
                max_rmse_vol_points=max_rmse_vol_points,
            )
            for expiry, forward, lm, iv in kept
        ]
        return cls(fits, spot, rate, dividend_yield, warnings=dropped)

    @classmethod
    def from_snapshot(
        cls,
        snapshot: MarketSnapshot,
        beta: float = 1.0,
        n_starts: int = 8,
        seed: int = 0,
        max_rmse_vol_points: float | None = None,
    ) -> SABRSurface:
        """Strip IVs from the snapshot's chain, then calibrate per expiry."""
        points = strip_chain(snapshot)
        return cls.from_points(
            points,
            snapshot.spot,
            snapshot.rate,
            snapshot.dividend_yield,
            beta=beta,
            n_starts=n_starts,
            seed=seed,
            max_rmse_vol_points=max_rmse_vol_points,
        )

    def _slice_vol_lm(self, index: int, log_moneyness: np.ndarray) -> np.ndarray:
        fit = self.slice_fits[index]
        strikes = fit.params.forward * np.exp(log_moneyness)
        return np.asarray(hagan_implied_vol(strikes, fit.params), dtype=float)


__all__ = ["SABRSurface", "SmileSlice", "VolSurface"]

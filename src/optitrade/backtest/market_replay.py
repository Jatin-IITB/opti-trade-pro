"""Market replays: day sequences the backtester iterates over.

:class:`SyntheticVRPMarket` manufactures a world with a *known* variance
risk premium so the strategy layer can be tested against economic ground
truth: the spot diffuses as GBM at realized vol ``sigma_R`` while the quoted
ATM implied vol runs at ``sigma_R + vrp`` plus mean-reverting AR(1) noise.
A short-vol strategy must make money when ``vrp > 0`` and must not when
``vrp = 0`` — those are the tests, not "the code ran".

Model notes (documented, not hidden):
- Clock: one bar per calendar day with ``dt = 1/365``, so option time decay
  (ACT/365 per ADR-003) and the diffusion share a single clock; timestamps
  advance 86,400 s per day starting at 0.
- Drift equals the risk-free rate (risk-neutral), matching the hedging
  simulator: a delta-hedged book is drift-insensitive to first order, and
  the forward-ATM entry strike then centres on ``E[S_T]``.
- OHLC bars come from ``n_intraday_steps`` GBM sub-steps per day, so the
  range-based estimators in :mod:`optitrade.vol.realized` can be run on the
  generated bars (note the discrete-monitoring low bias documented there).
- ``MarketDay.realized_vol`` is the *true* diffusion vol, so the measured
  VRP is exactly ``vrp + AR(1) noise`` and tests are not confounded by RV
  estimation error; estimator behaviour is tested separately.
- The per-day surface is an analytic SABR slice pair (Hagan et al. 2002) at
  ``tenor_days`` and ``2 * tenor_days``; alpha is fixed-point adjusted so the
  ATM Hagan vol matches the generated ATM IV, ``rho``/``nu`` shape the skew.

Features populated per day: ``atm_iv``, ``term_slope``, ``skew_25d``,
``vrp`` (= atm_iv - true realized vol), and the OHLC bar as ``open``,
``high``, ``low``, ``close``.
"""

from __future__ import annotations

import math
from collections.abc import Iterator
from typing import Protocol, cast, runtime_checkable

import numpy as np
import numpy.typing as npt

from optitrade.backtest.gbm import simulate_gbm_paths
from optitrade.core.types import OptionType
from optitrade.strategy.base import MarketDay, VolLookup
from optitrade.strategy.vrp import strike_from_delta
from optitrade.vol.sabr import SABRFit, SABRParams
from optitrade.vol.surface import SABRSurface

_DAYS_PER_YEAR = 365.0  # synthetic calendar: every day trades (see module doc)
_SECONDS_PER_DAY = 86400.0
_ALPHA_FIXED_POINT_ITERS = 3


@runtime_checkable
class MarketReplay(Protocol):
    """A replayable sequence of :class:`MarketDay` (must support re-iteration)."""

    def __iter__(self) -> Iterator[MarketDay]: ...


def _sabr_alpha_for_atm(atm_iv: float, expiry: float, rho: float, nu: float) -> float:
    """Alpha such that the Hagan (2002) ATM vol matches ``atm_iv`` (beta=1).

    At the money with beta=1 the Hagan expansion reduces to
    ``vol = alpha * (1 + T * (rho*nu*alpha/4 + (2 - 3*rho^2)*nu^2/24))``;
    a few fixed-point iterations invert it to machine-adequate accuracy for
    short tenors.
    """
    alpha = atm_iv
    for _ in range(_ALPHA_FIXED_POINT_ITERS):
        correction = 1.0 + expiry * (
            0.25 * rho * nu * alpha + (2.0 - 3.0 * rho * rho) / 24.0 * nu * nu
        )
        alpha = atm_iv / correction
    return alpha


class SyntheticVRPMarket:
    """Seeded synthetic market with a configured variance risk premium.

    All generation happens in ``__init__`` so the instance replays the same
    days on every iteration (deterministic for a given seed). See the module
    docstring for the model; every knob below is explicit configuration.

    Args:
        n_days: Number of daily bars to generate (>= 2).
        spot: Initial spot level.
        rate: Continuously compounded risk-free rate (also the GBM drift).
        realized_vol: True diffusion vol ``sigma_R`` (annualised decimal).
        vrp: Mean implied-minus-realized gap in vol points (decimal).
        seed: Seed for both the spot path and the IV noise (independent
            streams derived from it).
        tenor_days: Near tenor of the SABR slice grid (far slice = 2x).
        term_slope: Far-slice ATM vol minus near-slice ATM vol.
        iv_noise_vol: Std of the AR(1) innovation added to ATM IV per day.
        iv_noise_phi: AR(1) persistence of the IV noise, in [0, 1).
        sabr_rho: SABR correlation (negative for equity-style skew).
        sabr_nu: SABR vol-of-vol (smile curvature).
        n_intraday_steps: GBM sub-steps per day used to synthesise OHLC.
        iv_floor: Hard floor on the generated ATM IV (keeps alpha positive).
    """

    def __init__(
        self,
        n_days: int,
        *,
        spot: float = 100.0,
        rate: float = 0.05,
        realized_vol: float = 0.18,
        vrp: float = 0.03,
        seed: int = 0,
        tenor_days: int = 30,
        term_slope: float = 0.01,
        iv_noise_vol: float = 0.005,
        iv_noise_phi: float = 0.8,
        sabr_rho: float = -0.3,
        sabr_nu: float = 0.6,
        n_intraday_steps: int = 13,
        iv_floor: float = 0.02,
    ) -> None:
        if n_days < 2:
            raise ValueError(f"n_days must be >= 2, got {n_days}")
        if realized_vol <= 0.0:
            raise ValueError(f"realized_vol must be positive, got {realized_vol}")
        if tenor_days < 1:
            raise ValueError(f"tenor_days must be >= 1, got {tenor_days}")
        if n_intraday_steps < 2:
            raise ValueError(f"n_intraday_steps must be >= 2, got {n_intraday_steps}")
        if not 0.0 <= iv_noise_phi < 1.0:
            raise ValueError(f"iv_noise_phi must be in [0, 1), got {iv_noise_phi}")
        if iv_floor <= 0.0:
            raise ValueError(f"iv_floor must be positive, got {iv_floor}")

        self.realized_vol = realized_vol
        self.vrp = vrp
        self.tenor_days = tenor_days

        m = n_intraday_steps
        sub_dt = 1.0 / (_DAYS_PER_YEAR * m)
        path = simulate_gbm_paths(spot, rate, realized_vol, sub_dt, n_days * m, 1, seed)[0]
        starts = np.arange(n_days) * m
        self.opens: npt.NDArray[np.float64] = path[starts]
        self.closes: npt.NDArray[np.float64] = path[starts + m]
        body = path[: n_days * m]
        self.highs: npt.NDArray[np.float64] = np.maximum(
            np.maximum.reduceat(body, starts), self.closes
        )
        self.lows: npt.NDArray[np.float64] = np.minimum(
            np.minimum.reduceat(body, starts), self.closes
        )

        # Independent, seeded AR(1) noise stream for the implied-vol level.
        iv_rng = np.random.default_rng([seed, 1])
        shocks = iv_rng.standard_normal(n_days) * iv_noise_vol
        noise = np.empty(n_days)
        level = 0.0
        for i in range(n_days):
            level = iv_noise_phi * level + shocks[i]
            noise[i] = level
        atm_ivs = np.maximum(realized_vol + vrp + noise, iv_floor)

        t_near = tenor_days / _DAYS_PER_YEAR
        t_far = 2.0 * tenor_days / _DAYS_PER_YEAR
        days: list[MarketDay] = []
        for i in range(n_days):
            close = float(self.closes[i])
            iv_near = float(atm_ivs[i])
            iv_far = max(iv_near + term_slope, iv_floor)
            fits = tuple(
                SABRFit(
                    params=SABRParams(
                        alpha=_sabr_alpha_for_atm(iv, t, sabr_rho, sabr_nu),
                        beta=1.0,
                        rho=sabr_rho,
                        nu=sabr_nu,
                        forward=close * math.exp(rate * t),
                        expiry=t,
                    ),
                    rmse_vol_points=0.0,
                    n_starts_used=0,
                )
                for iv, t in ((iv_near, t_near), (iv_far, t_far))
            )
            surface = SABRSurface(fits, spot=close, rate=rate)
            forward = close * math.exp(rate * t_near)
            strike_put = strike_from_delta(forward, t_near, iv_near, 0.25, OptionType.PUT)
            strike_call = strike_from_delta(forward, t_near, iv_near, 0.25, OptionType.CALL)
            skew_25d = float(surface.vol(strike_put, t_near)) - float(
                surface.vol(strike_call, t_near)
            )
            days.append(
                MarketDay(
                    timestamp=i * _SECONDS_PER_DAY,
                    spot=close,
                    rate=rate,
                    realized_vol=realized_vol,
                    snapshot=None,
                    # SABRSurface satisfies VolLookup by duck typing; its vol()
                    # signature is narrower than the protocol's `object`, hence
                    # the cast (base.py is a frozen contract).
                    surface=cast("VolLookup", surface),
                    features={
                        "atm_iv": iv_near,
                        "term_slope": iv_far - iv_near,
                        "skew_25d": skew_25d,
                        "vrp": iv_near - realized_vol,
                        "open": float(self.opens[i]),
                        "high": float(self.highs[i]),
                        "low": float(self.lows[i]),
                        "close": close,
                    },
                )
            )
        self._days: tuple[MarketDay, ...] = tuple(days)

    def __iter__(self) -> Iterator[MarketDay]:
        return iter(self._days)

    def __len__(self) -> int:
        return len(self._days)


class StoreReplay:
    """Replay of real captured snapshots — deferred until data accumulates.

    The intended wiring is :class:`optitrade.data.SnapshotStore` snapshots →
    quote filters → ``VolSurface.from_points`` per day, with realized vol
    from the accumulated close history. That needs weeks of captured NSE
    chains to be meaningful (a handful of days cannot feed a walk-forward),
    so this is an explicit stub rather than silently synthetic data:
    :class:`SyntheticVRPMarket` covers the harness until real snapshots
    accumulate.
    """

    def __init__(self) -> None:
        raise NotImplementedError("wired when real snapshots accumulate")

    def __iter__(self) -> Iterator[MarketDay]:  # pragma: no cover - unreachable
        raise NotImplementedError("wired when real snapshots accumulate")


__all__ = ["MarketReplay", "StoreReplay", "SyntheticVRPMarket"]

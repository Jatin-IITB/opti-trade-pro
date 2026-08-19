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
from pathlib import Path
from typing import Literal, Protocol, SupportsFloat, cast, runtime_checkable

import numpy as np
import numpy.typing as npt

from optitrade.backtest.gbm import simulate_gbm_paths
from optitrade.core.errors import CalibrationError, NumericalError
from optitrade.core.types import MarketSnapshot, OptionType
from optitrade.data.capture import to_market_snapshot
from optitrade.data.models import RawChain
from optitrade.data.quote_filters import DEFAULT_FILTER_CONFIG, FilterConfig
from optitrade.data.snapshot_store import SnapshotStore
from optitrade.strategy.base import MarketDay, VolLookup
from optitrade.strategy.vrp import strike_from_delta
from optitrade.vol.essvi import ESSVISurface
from optitrade.vol.realized import close_to_close_vol
from optitrade.vol.sabr import SABRFit, SABRParams
from optitrade.vol.surface import SABRSurface, VolSurface

_DAYS_PER_YEAR = 365.0  # synthetic calendar: every day trades (see module doc)
_SECONDS_PER_DAY = 86400.0
_ALPHA_FIXED_POINT_ITERS = 3
# StoreReplay skew proxy: fixed-moneyness wings standing in for the 25-delta
# strikes (see the class docstring for why the proxy).
_SKEW_PUT_MONEYNESS = 0.95
_SKEW_CALL_MONEYNESS = 1.05
# close_to_close_vol needs >= 3 closes (>= 2 returns) for a sample std.
_MIN_SPOTS_FOR_RV = 3
# Stored dates are trading days (no weekend snapshots), so RV annualises at
# the trading-day convention, not the synthetic market's 365-day calendar.
_TRADING_DAYS_PER_YEAR = 252


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
    """Replay of captured Parquet history: one :class:`MarketDay` per stored date.

    Feeds the *identical* walk-forward harness that :class:`SyntheticVRPMarket`
    feeds, but from real snapshots persisted by
    :class:`~optitrade.data.snapshot_store.SnapshotStore`. Per stored UTC date
    the pipeline is: read the raw chain → hygiene filters →
    :func:`~optitrade.data.capture.to_market_snapshot` → fit a surface
    (:class:`~optitrade.vol.surface.VolSurface` spline, or
    :class:`~optitrade.vol.essvi.ESSVISurface` when ``surface="essvi"``) →
    derive the strategy features documented below.

    Judgment calls (documented, not hidden):

    - **End-of-day selection**: when a date holds several snapshots, the
      *last* one (latest UTC time) is used — the closest thing an intraday
      capture has to an official close.
    - **Skip-and-warn**: a date whose chain fails filtering or surface
      fitting is skipped and a message appended to ``self.warnings`` —
      an unattended pipeline must not die because one day's quotes were
      junk. Only data-shaped failures (``ValueError``,
      :class:`~optitrade.core.errors.CalibrationError`,
      :class:`~optitrade.core.errors.NumericalError`) are absorbed; genuine
      bugs still raise.
    - **Realized vol**: close-to-close over the trailing ``rv_window`` stored
      EOD spots (including the current day's), annualised at 252 because
      stored dates are trading days. With fewer than 3 spots accumulated the
      estimator is undefined, so ``realized_vol = atm_iv`` as a neutral
      prior — it forces ``vrp = 0`` rather than fabricating a signal before
      history exists. Spots from dates whose *option chain* failed hygiene
      still enter the history: the underlying's close is valid data even
      when the quotes around it are junk.
    - **Features**: ``atm_iv`` is the surface vol at the forward-ATM strike
      for ``tenor_days`` (ACT/365). ``term_slope`` is the ATM vol at the
      longest stored expiry minus at the shortest, *per year of expiry gap*
      (0.0 for a single-expiry chain) — note this differs from
      :class:`SyntheticVRPMarket`'s raw far-minus-near difference; producers
      document their keys (strategy/base.py). ``skew_25d`` is approximated
      as ``vol(0.95 F) - vol(1.05 F)`` at the tenor: a fixed-moneyness proxy
      for the 25-delta wings that avoids a delta inversion on real chains
      (same sign convention — positive for equity-style put skew).
      ``vrp = atm_iv - realized_vol``.
    - **Eager build**: everything is materialised in ``__init__`` so the
      instance supports cheap re-iteration and ``__len__`` (the
      :class:`MarketReplay` contract); a year of EOD history is only ~250
      surface fits.

    Args:
        store: Snapshot store holding the captured Parquet history.
        underlying: Underlying whose snapshots to replay.
        filter_config: Quote-hygiene thresholds; ``None`` uses the defaults.
        rv_window: Trailing EOD spots fed to close-to-close realized vol.
        tenor_days: Tenor (calendar days, ACT/365) for the ATM/skew features.
        min_quotes: Minimum clean quotes per day on top of the pipeline's own
            floor (:data:`~optitrade.data.capture.MIN_CLEAN_QUOTES` = 4, which
            ``to_market_snapshot`` always enforces — values below it add
            nothing).
        surface: ``"spline"`` for per-expiry cubic smiles, ``"essvi"`` for
            the jointly calibrated arbitrage-aware SSVI surface.
    """

    def __init__(
        self,
        store: SnapshotStore,
        underlying: str,
        filter_config: FilterConfig | None = None,
        rv_window: int = 21,
        tenor_days: int = 30,
        min_quotes: int = 4,
        surface: Literal["spline", "essvi"] = "spline",
    ) -> None:
        if rv_window < _MIN_SPOTS_FOR_RV:
            raise ValueError(f"rv_window must be >= {_MIN_SPOTS_FOR_RV}, got {rv_window}")
        if tenor_days < 1:
            raise ValueError(f"tenor_days must be >= 1, got {tenor_days}")
        if min_quotes < 1:
            raise ValueError(f"min_quotes must be >= 1, got {min_quotes}")
        if surface not in ("spline", "essvi"):
            raise ValueError(f"surface must be 'spline' or 'essvi', got {surface!r}")
        self.underlying = underlying
        self.rv_window = rv_window
        self.tenor_days = tenor_days
        self.min_quotes = min_quotes
        self.surface_kind: Literal["spline", "essvi"] = surface
        self.warnings: list[str] = []
        config = filter_config if filter_config is not None else DEFAULT_FILTER_CONFIG

        # Last snapshot per UTC date = end-of-day (paths are chronologically
        # sorted, so later writes for the same date overwrite earlier ones).
        eod_by_date: dict[str, Path] = {}
        for path in store.list_snapshots(underlying):
            eod_by_date[path.parent.name] = path
        if not eod_by_date:
            self.warnings.append(f"no snapshots stored for {underlying}")

        days: list[MarketDay] = []
        spots: list[float] = []
        for date in sorted(eod_by_date):
            path = eod_by_date[date]
            try:
                chain = store.read(path)
            except (ValueError, OSError) as exc:
                self.warnings.append(f"{date}: unreadable snapshot {path.name}: {exc}")
                continue
            spots.append(chain.spot)  # valid even when the quotes are junk (see docstring)
            try:
                snapshot = to_market_snapshot(chain, config)
                if len(snapshot.quotes) < self.min_quotes:
                    raise ValueError(
                        f"only {len(snapshot.quotes)} clean quotes; min_quotes is {self.min_quotes}"
                    )
                fitted = self._fit_surface(snapshot)
            except (ValueError, CalibrationError, NumericalError) as exc:
                self.warnings.append(f"{date}: skipped: {exc}")
                continue
            days.append(self._build_day(chain, snapshot, fitted, spots))
        self._days: tuple[MarketDay, ...] = tuple(days)

    def _fit_surface(self, snapshot: MarketSnapshot) -> VolSurface | ESSVISurface:
        if self.surface_kind == "essvi":
            return ESSVISurface.from_snapshot(snapshot)  # deterministic default seed
        return VolSurface.from_snapshot(snapshot)

    @staticmethod
    def _scalar_vol(fitted: VolSurface | ESSVISurface, strike: float, expiry: float) -> float:
        return float(cast("SupportsFloat", fitted.vol(strike, expiry)))

    def _build_day(
        self,
        chain: RawChain,
        snapshot: MarketSnapshot,
        fitted: VolSurface | ESSVISurface,
        spots: list[float],
    ) -> MarketDay:
        tenor = self.tenor_days / _DAYS_PER_YEAR
        atm_iv = self._scalar_vol(fitted, fitted.forward(tenor), tenor)

        window = spots[-self.rv_window :]
        if len(window) < _MIN_SPOTS_FOR_RV:
            realized_vol = atm_iv  # neutral prior: vrp = 0 until history exists
        else:
            realized_vol = close_to_close_vol(window, periods_per_year=_TRADING_DAYS_PER_YEAR)

        expiries = fitted.expiries
        t_near, t_far = float(expiries[0]), float(expiries[-1])
        if expiries.size >= 2 and t_far > t_near:
            iv_near = self._scalar_vol(fitted, fitted.forward(t_near), t_near)
            iv_far = self._scalar_vol(fitted, fitted.forward(t_far), t_far)
            term_slope = (iv_far - iv_near) / (t_far - t_near)
        else:
            term_slope = 0.0

        forward = fitted.forward(tenor)
        skew_25d = self._scalar_vol(
            fitted, _SKEW_PUT_MONEYNESS * forward, tenor
        ) - self._scalar_vol(fitted, _SKEW_CALL_MONEYNESS * forward, tenor)

        return MarketDay(
            timestamp=chain.timestamp,
            spot=chain.spot,
            rate=chain.rate,
            realized_vol=realized_vol,
            snapshot=snapshot,
            # Both surface classes satisfy VolLookup by duck typing; their
            # vol() signatures are narrower than the protocol's `object`,
            # hence the cast (base.py is a frozen contract).
            surface=cast("VolLookup", fitted),
            features={
                "atm_iv": atm_iv,
                "term_slope": term_slope,
                "skew_25d": skew_25d,
                "vrp": atm_iv - realized_vol,
            },
        )

    def __iter__(self) -> Iterator[MarketDay]:
        return iter(self._days)

    def __len__(self) -> int:
        return len(self._days)


__all__ = ["MarketReplay", "StoreReplay", "SyntheticVRPMarket"]

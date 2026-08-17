"""Scenario-grid revaluation engine (spot x vol x time PnL cube).

Full revaluation, not a delta-gamma Taylor expansion (cf. Hull, *Options,
Futures, and Other Derivatives*, ch. on scenario analysis / stress testing):
every grid cell reprices the entire book with Black-Scholes-Merton at shifted
market data, so the cube is exact for a BS book at any shift size.

Vectorisation: the whole cube is one broadcasted
:func:`~optitrade.pricing.bs_price` call per option type over the 4-D array
``(n_spot, n_vol, n_time, n_positions)`` — no Python loop over scenario cells.
``bs_price`` floors expiry and vol at ~0 internally, so positions that expire
within a time shift (or whose bumped vol goes non-positive) degrade to
(discounted) intrinsic value instead of NaN.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
import numpy.typing as npt

from optitrade.core import OptionType
from optitrade.pricing.black_scholes import bs_price

FloatArray = npt.NDArray[np.float64]

_DAYS_PER_YEAR = 365.0


@dataclass(frozen=True)
class ScenarioGrid:
    """Axes of the revaluation grid.

    - ``spot_shifts``: relative spot moves (-0.10 == spot down 10%),
    - ``vol_shifts``: absolute vol-point moves as decimals (0.01 == +1 vol pt),
    - ``time_shifts``: year fractions of calendar time forward (>= 0).
    """

    spot_shifts: FloatArray
    vol_shifts: FloatArray
    time_shifts: FloatArray

    def __post_init__(self) -> None:
        for name in ("spot_shifts", "vol_shifts", "time_shifts"):
            axis: FloatArray = getattr(self, name)
            if axis.ndim != 1 or axis.size == 0:
                raise ValueError(f"{name} must be a non-empty 1-D array")

    @classmethod
    def regular(
        cls,
        n_spot: int = 11,
        spot_width: float = 0.10,
        n_vol: int = 7,
        vol_width: float = 0.05,
        n_time: int = 7,
        max_days: float = 30.0,
    ) -> ScenarioGrid:
        """Evenly spaced symmetric spot/vol axes and a forward-only time axis.

        Spot spans ``+/- spot_width`` (relative), vol spans ``+/- vol_width``
        (absolute), time runs from 0 to ``max_days`` calendar days (ACT/365).
        """
        spot = np.linspace(-spot_width, spot_width, n_spot)
        vol = np.linspace(-vol_width, vol_width, n_vol)
        # linspace midpoints carry ~1e-17 rounding; pin the exact base
        # scenario into odd-sized axes so the unshifted cell has PnL == 0.
        if n_spot % 2:
            spot[n_spot // 2] = 0.0
        if n_vol % 2:
            vol[n_vol // 2] = 0.0
        time = np.linspace(0.0, max_days / _DAYS_PER_YEAR, n_time)
        return cls(spot_shifts=spot, vol_shifts=vol, time_shifts=time)

    @property
    def size(self) -> int:
        """Total number of scenario cells (n_spot * n_vol * n_time)."""
        return int(self.spot_shifts.size * self.vol_shifts.size * self.time_shifts.size)


@dataclass(frozen=True)
class BookPosition:
    """A flat, array-friendly option position for scenario revaluation.

    Deliberately denormalised (no nested contract object) so books map
    directly onto numpy arrays; the platform layer converts
    :class:`~optitrade.core.types.Position` into this shape.
    """

    strike: float
    expiry: float  # year fraction, time to expiry
    option_type: OptionType
    quantity: float  # signed: positive = long
    vol: float  # per-position implied vol (decimal)

    def __post_init__(self) -> None:
        if self.strike <= 0:
            raise ValueError(f"strike must be positive, got {self.strike}")
        if self.expiry <= 0:
            raise ValueError(f"expiry must be positive, got {self.expiry}")
        if self.vol <= 0:
            raise ValueError(f"vol must be positive, got {self.vol}")
        # OptionType subclasses str, so enums silently degrade to plain (or
        # numpy) strings through array round-trips; the engine matches legs by
        # identity, so coerce here rather than pricing a leg as zero.
        if not isinstance(self.option_type, OptionType):
            object.__setattr__(self, "option_type", OptionType(str(self.option_type)))


@dataclass(frozen=True)
class ScenarioResult:
    """PnL cube over the grid axes, relative to the unshifted book value."""

    spot_shifts: FloatArray
    vol_shifts: FloatArray
    time_shifts: FloatArray
    pnl: FloatArray  # shape (n_spot, n_vol, n_time)
    base_value: float

    def _cell(self, flat_index: int) -> tuple[float, float, float, float]:
        i, j, k = np.unravel_index(flat_index, self.pnl.shape)
        return (
            float(self.pnl[i, j, k]),
            float(self.spot_shifts[i]),
            float(self.vol_shifts[j]),
            float(self.time_shifts[k]),
        )

    @property
    def worst(self) -> tuple[float, float, float, float]:
        """``(pnl, spot_shift, vol_shift, time_shift)`` of the minimum-PnL cell."""
        return self._cell(int(np.argmin(self.pnl)))

    @property
    def best(self) -> tuple[float, float, float, float]:
        """``(pnl, spot_shift, vol_shift, time_shift)`` of the maximum-PnL cell."""
        return self._cell(int(np.argmax(self.pnl)))


def run_scenario_grid(
    book: Sequence[BookPosition],
    spot: float,
    rate: float,
    grid: ScenarioGrid,
    dividend_yield: float = 0.0,
) -> ScenarioResult:
    """Revalue ``book`` over every grid cell and return the PnL cube.

    Each cell (i, j, k) reprices every position at
    ``spot * (1 + spot_shifts[i])``, ``vol + vol_shifts[j]`` and
    ``expiry - time_shifts[k]``; PnL is cell value minus ``base_value``.
    One broadcasted ``bs_price`` call per option type (at most two) covers all
    ``grid.size * len(book)`` reprices.
    """
    if spot <= 0:
        raise ValueError(f"spot must be positive, got {spot}")

    shape = (grid.spot_shifts.size, grid.vol_shifts.size, grid.time_shifts.size)
    value = np.zeros(shape, dtype=np.float64)
    base_value = 0.0

    # Scenario axes, shaped to broadcast against the trailing positions axis.
    spots = (spot * (1.0 + grid.spot_shifts))[:, None, None, None]
    vol_shifts = grid.vol_shifts[None, :, None, None]
    time_shifts = grid.time_shifts[None, None, :, None]

    for option_type in (OptionType.CALL, OptionType.PUT):
        legs = [p for p in book if p.option_type is option_type]
        if not legs:
            continue
        strikes = np.array([p.strike for p in legs], dtype=np.float64)
        expiries = np.array([p.expiry for p in legs], dtype=np.float64)
        vols = np.array([p.vol for p in legs], dtype=np.float64)
        quantities = np.array([p.quantity for p in legs], dtype=np.float64)

        base_prices = np.asarray(
            bs_price(spot, strikes, expiries, rate, vols, option_type, dividend_yield)
        )
        base_value += float(quantities @ base_prices)

        remaining = expiries[None, None, None, :] - time_shifts
        cube_prices = np.asarray(
            bs_price(
                spots,
                strikes[None, None, None, :],
                remaining,
                rate,
                vols[None, None, None, :] + vol_shifts,
                option_type,
                dividend_yield,
            )
        )
        # bs_price floors expiry at ~1e-12, leaving residual time value at the
        # ATM point; a position at or past expiry is worth exactly intrinsic.
        if option_type is OptionType.CALL:
            intrinsic = np.maximum(spots - strikes[None, None, None, :], 0.0)
        else:
            intrinsic = np.maximum(strikes[None, None, None, :] - spots, 0.0)
        cube_prices = np.where(remaining <= 0.0, intrinsic, cube_prices)
        value += cube_prices @ quantities

    return ScenarioResult(
        spot_shifts=grid.spot_shifts,
        vol_shifts=grid.vol_shifts,
        time_shifts=grid.time_shifts,
        pnl=value - base_value,
        base_value=base_value,
    )


__all__ = ["BookPosition", "FloatArray", "ScenarioGrid", "ScenarioResult", "run_scenario_grid"]

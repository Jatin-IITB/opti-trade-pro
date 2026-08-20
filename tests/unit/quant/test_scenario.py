"""Scenario-grid engine: correctness spot checks and the headline latency target."""

from __future__ import annotations

import time

import numpy as np
import pytest

from optitrade.core import OptionType
from optitrade.greeks import BookPosition, ScenarioGrid, run_scenario_grid
from optitrade.pricing import bs_price

SPOT = 100.0
RATE = 0.04


@pytest.mark.unit
def test_regular_grid_axes_and_size() -> None:
    grid = ScenarioGrid.regular()
    assert grid.size == 11 * 7 * 7 == 539
    assert grid.spot_shifts[0] == pytest.approx(-0.10)
    assert grid.spot_shifts[-1] == pytest.approx(0.10)
    assert grid.spot_shifts[5] == 0.0  # base scenario pinned exactly
    assert grid.vol_shifts[3] == 0.0
    assert grid.time_shifts[0] == 0.0
    assert grid.time_shifts[-1] == pytest.approx(30.0 / 365.0)


@pytest.mark.unit
def test_single_position_cube_matches_direct_reprice() -> None:
    pos = BookPosition(
        strike=105.0, expiry=0.5, option_type=OptionType.CALL, quantity=3.0, vol=0.22
    )
    grid = ScenarioGrid.regular()
    res = run_scenario_grid([pos], SPOT, RATE, grid)

    assert res.pnl.shape == (11, 7, 7)
    base = 3.0 * float(bs_price(SPOT, 105.0, 0.5, RATE, 0.22, OptionType.CALL))
    assert res.base_value == pytest.approx(base, rel=1e-12)

    for i, j, k in [(0, 0, 0), (10, 6, 6), (2, 5, 4), (7, 1, 3)]:
        ds = float(grid.spot_shifts[i])
        dv = float(grid.vol_shifts[j])
        dt = float(grid.time_shifts[k])
        direct = 3.0 * float(
            bs_price(SPOT * (1.0 + ds), 105.0, 0.5 - dt, RATE, 0.22 + dv, OptionType.CALL)
        )
        assert res.pnl[i, j, k] == pytest.approx(direct - base, rel=1e-10, abs=1e-10)


@pytest.mark.unit
def test_base_cell_pnl_is_zero() -> None:
    book = [
        BookPosition(strike=105.0, expiry=0.5, option_type=OptionType.CALL, quantity=3.0, vol=0.22),
        BookPosition(strike=95.0, expiry=0.25, option_type=OptionType.PUT, quantity=-2.0, vol=0.28),
    ]
    grid = ScenarioGrid.regular()
    res = run_scenario_grid(book, SPOT, RATE, grid)
    assert res.pnl[5, 3, 0] == pytest.approx(0.0, abs=1e-9)


@pytest.mark.unit
def test_worst_and_best_are_sane_for_a_long_call() -> None:
    pos = BookPosition(
        strike=100.0, expiry=0.25, option_type=OptionType.CALL, quantity=1.0, vol=0.2
    )
    res = run_scenario_grid([pos], SPOT, RATE, ScenarioGrid.regular())

    worst_pnl, worst_ds, worst_dv, worst_dt = res.worst
    best_pnl, best_ds, best_dv, best_dt = res.best
    assert worst_pnl < 0.0 < best_pnl
    # A long call is monotone increasing in spot, vol and time-to-expiry, so
    # the extremes sit at the grid corners.
    assert worst_ds == pytest.approx(-0.10)
    assert worst_dv == pytest.approx(-0.05)
    assert worst_dt == pytest.approx(30.0 / 365.0)
    assert best_ds == pytest.approx(0.10)
    assert best_dv == pytest.approx(0.05)
    assert best_dt == 0.0


@pytest.mark.unit
def test_position_expiring_within_horizon_floors_at_intrinsic() -> None:
    pos = BookPosition(
        strike=90.0, expiry=5.0 / 365.0, option_type=OptionType.CALL, quantity=1.0, vol=0.2
    )
    grid = ScenarioGrid.regular()  # time shifts reach 30 days > 5-day expiry
    res = run_scenario_grid([pos], SPOT, RATE, grid)

    # Cell (spot +10%, vol unshifted, +30 days): the call has expired, so its
    # value must be the intrinsic value at the shifted spot.
    shifted_spot = SPOT * (1.0 + float(grid.spot_shifts[10]))
    intrinsic = shifted_spot - 90.0
    assert res.pnl[10, 3, 6] == pytest.approx(intrinsic - res.base_value, rel=1e-9, abs=1e-9)

    # OTM at expiry: worthless, PnL = -base_value.
    otm_spot_pnl = res.pnl[0, 3, 6]  # spot -10% -> spot 90 == strike, intrinsic 0
    assert otm_spot_pnl == pytest.approx(-res.base_value, rel=1e-9, abs=1e-9)


@pytest.mark.unit
def test_empty_and_single_type_books() -> None:
    grid = ScenarioGrid.regular(n_spot=3, n_vol=3, n_time=3)
    res = run_scenario_grid([], SPOT, RATE, grid)
    assert res.base_value == 0.0
    assert not np.any(res.pnl)

    puts_only = [
        BookPosition(strike=95.0, expiry=0.5, option_type=OptionType.PUT, quantity=1.0, vol=0.2)
    ]
    res = run_scenario_grid(puts_only, SPOT, RATE, grid)
    assert res.base_value > 0.0


def _mixed_book(n_positions: int, seed: int = 7) -> list[BookPosition]:
    rng = np.random.default_rng(seed)
    book = []
    for i in range(n_positions):
        book.append(
            BookPosition(
                strike=float(rng.uniform(80.0, 120.0)),
                expiry=float(rng.uniform(0.05, 1.0)),
                option_type=OptionType.CALL if i % 2 == 0 else OptionType.PUT,
                quantity=float(rng.integers(1, 11)) * (1.0 if i % 3 else -1.0),
                vol=float(rng.uniform(0.15, 0.35)),
            )
        )
    return book


@pytest.mark.benchmark
def test_scenario_latency_50_positions_539_cells() -> None:
    """Headline product claim: 500+ cells x 50 positions in < 200 ms."""
    book = _mixed_book(50)
    grid = ScenarioGrid.regular()  # 11 x 7 x 7 = 539 cells
    assert grid.size >= 500

    run_scenario_grid(book, SPOT, RATE, grid)  # warm-up (ufunc/cache effects)
    timings = []
    for _ in range(3):
        start = time.perf_counter()
        run_scenario_grid(book, SPOT, RATE, grid)
        timings.append(time.perf_counter() - start)
    assert min(timings) < 0.2, f"scenario grid too slow: min={min(timings):.4f}s of {timings}"


def test_book_position_coerces_plain_strings_and_rejects_garbage() -> None:
    """OptionType subclasses str, so enums degrade to strings through array
    round-trips (numpy even truncates them). Plain "call"/"put" coerce; anything
    else must raise loudly rather than silently price a leg as zero."""
    pos = BookPosition(strike=100.0, expiry=0.5, option_type="call", quantity=1.0, vol=0.2)
    assert pos.option_type is OptionType.CALL

    grid = ScenarioGrid.regular(
        n_spot=3, spot_width=0.05, n_vol=1, vol_width=0.0, n_time=1, max_days=0
    )
    res = run_scenario_grid([pos], 100.0, 0.05, grid)
    assert res.base_value > 0.0

    with pytest.raises(ValueError, match="not a valid OptionType"):
        # what numpy leaves behind after truncating str(OptionType.CALL)
        BookPosition(strike=100.0, expiry=0.5, option_type="Opti", quantity=1.0, vol=0.2)

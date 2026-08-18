"""Decision-path tests for the VRP strategy on hand-built MarketDays."""

import math

import pytest

from optitrade.core import OptionContract, OptionType, Position
from optitrade.strategy import (
    IndianCostRates,
    IndianOptionsCostModel,
    MarketDay,
    VRPConfig,
    VRPStrategy,
    strike_from_delta,
)

pytestmark = pytest.mark.unit

SPOT = 100.0
RATE = 0.05
TENOR_YEARS = 30 / 365.0

ZERO_RATES = IndianCostRates(
    brokerage_per_order=0.0,
    stt_sell_frac=0.0,
    exchange_txn_frac=0.0,
    gst_frac=0.0,
    sebi_frac=0.0,
    stamp_buy_frac=0.0,
    hedge_cost_frac=0.0,
)


def make_day(atm_iv=None, realized_vol=0.20, features=None, surface=None):
    feats = dict(features or {})
    if atm_iv is not None:
        feats["atm_iv"] = atm_iv
    return MarketDay(
        timestamp=0.0,
        spot=SPOT,
        rate=RATE,
        realized_vol=realized_vol,
        surface=surface,
        features=feats,
    )


def short_straddle_positions(quantity=-1.0):
    call = OptionContract("VRP-C-100.41-30D", 100.41, TENOR_YEARS, OptionType.CALL, 25)
    put = OptionContract("VRP-P-100.41-30D", 100.41, TENOR_YEARS, OptionType.PUT, 25)
    return (
        Position(contract=call, quantity=quantity, entry_price=2.5),
        Position(contract=put, quantity=quantity, entry_price=2.3),
    )


class FlatSurface:
    """Constant-vol VolLookup stand-in."""

    def __init__(self, vol):
        self._vol = vol

    def vol(self, strike, expiry):
        return self._vol


class TestEntry:
    def test_enters_short_straddle_when_vrp_clears_threshold(self):
        strategy = VRPStrategy(lot_size=25)
        decision = strategy.decide(make_day(atm_iv=0.25, realized_vol=0.20), ())
        assert decision.action == "enter"
        assert len(decision.orders) == 2
        forward = SPOT * math.exp(RATE * TENOR_YEARS)
        types = set()
        for order in decision.orders:
            assert order.quantity == -1.0
            assert order.price > 0.0
            assert order.contract is not None
            assert order.contract.strike == pytest.approx(forward)
            assert order.contract.lot_size == 25
            types.add(order.contract.option_type)
        assert types == {OptionType.CALL, OptionType.PUT}

    def test_thesis_and_diagnostics_carry_the_numbers(self):
        strategy = VRPStrategy(lot_size=25)
        decision = strategy.decide(make_day(atm_iv=0.25, realized_vol=0.20), ())
        assert "0.0500" in decision.thesis  # the VRP
        assert "0.2500" in decision.thesis  # the ATM IV
        assert "0.0300" in decision.thesis  # the entry threshold
        assert decision.diagnostics["vrp"] == pytest.approx(0.05)
        assert decision.expected_edge > 0.0
        assert decision.estimated_cost > 0.0  # default Indian cost model

    def test_expected_edge_is_vega_times_vrp(self):
        strategy = VRPStrategy(cost_model=IndianOptionsCostModel(ZERO_RATES), lot_size=25)
        decision = strategy.decide(make_day(atm_iv=0.25, realized_vol=0.20), ())
        vega = decision.diagnostics["vega_structure"]
        assert decision.expected_edge == pytest.approx(vega * 0.05)
        assert decision.estimated_cost == 0.0  # zero-rate model

    def test_holds_below_entry_threshold(self):
        strategy = VRPStrategy()
        decision = strategy.decide(make_day(atm_iv=0.22, realized_vol=0.20), ())
        assert decision.action == "hold"
        assert decision.orders == ()
        assert "0.0200" in decision.thesis

    def test_falls_back_to_surface_atm_vol(self):
        strategy = VRPStrategy()
        day = make_day(atm_iv=None, realized_vol=0.20, surface=FlatSurface(0.26))
        decision = strategy.decide(day, ())
        assert decision.action == "enter"
        assert decision.diagnostics["atm_iv"] == pytest.approx(0.26)

    def test_holds_without_any_vol_signal(self):
        strategy = VRPStrategy()
        decision = strategy.decide(make_day(atm_iv=None), ())
        assert decision.action == "hold"
        assert "no ATM IV" in decision.thesis


class TestRegimeFilters:
    def test_term_slope_filter_blocks_entry(self):
        strategy = VRPStrategy(VRPConfig(max_term_slope=0.02))
        day = make_day(atm_iv=0.25, features={"term_slope": 0.05})
        decision = strategy.decide(day, ())
        assert decision.action == "hold"
        assert "term_slope" in decision.thesis

    def test_term_slope_filter_passes_below_threshold(self):
        strategy = VRPStrategy(VRPConfig(max_term_slope=0.02))
        day = make_day(atm_iv=0.25, features={"term_slope": 0.01})
        assert strategy.decide(day, ()).action == "enter"

    def test_missing_feature_bypasses_filter(self):
        strategy = VRPStrategy(VRPConfig(max_term_slope=0.02, min_skew=0.01))
        assert strategy.decide(make_day(atm_iv=0.25), ()).action == "enter"

    def test_skew_filter_blocks_entry(self):
        strategy = VRPStrategy(VRPConfig(min_skew=0.01))
        day = make_day(atm_iv=0.25, features={"skew_25d": -0.005})
        decision = strategy.decide(day, ())
        assert decision.action == "hold"
        assert "skew_25d" in decision.thesis


class TestExitAndHold:
    def test_exits_when_vrp_collapses(self):
        strategy = VRPStrategy(lot_size=25)
        positions = short_straddle_positions()
        day = make_day(atm_iv=0.18, realized_vol=0.20, features={"days_in_trade": 10.0})
        decision = strategy.decide(day, positions)
        assert decision.action == "exit"
        assert len(decision.orders) == 2
        symbols = {order.symbol for order in decision.orders}
        assert symbols == {p.contract.symbol for p in positions}
        for order in decision.orders:
            assert order.quantity == 1.0  # buy back the short
            assert order.price > 0.0
        assert "-0.0200" in decision.thesis

    def test_time_stop_exits_even_with_rich_vol(self):
        strategy = VRPStrategy(VRPConfig(max_days_in_trade=30), lot_size=25)
        day = make_day(atm_iv=0.25, realized_vol=0.20, features={"days_in_trade": 31.0})
        decision = strategy.decide(day, short_straddle_positions())
        assert decision.action == "exit"
        assert "days_in_trade" in decision.thesis
        assert "31" in decision.thesis

    def test_holds_open_position_while_vrp_positive(self):
        strategy = VRPStrategy()
        day = make_day(atm_iv=0.22, realized_vol=0.20, features={"days_in_trade": 5.0})
        decision = strategy.decide(day, short_straddle_positions())
        assert decision.action == "hold"
        assert decision.orders == ()
        assert "0.0200" in decision.thesis


class TestStrangle:
    def test_strangle_strikes_bracket_the_forward(self):
        config = VRPConfig(structure="strangle", strangle_delta=0.25)
        strategy = VRPStrategy(config, lot_size=25)
        decision = strategy.decide(make_day(atm_iv=0.25, realized_vol=0.20), ())
        assert decision.action == "enter"
        forward = SPOT * math.exp(RATE * TENOR_YEARS)
        by_type = {o.contract.option_type: o.contract.strike for o in decision.orders}
        assert by_type[OptionType.CALL] > forward
        assert by_type[OptionType.PUT] < forward
        expected_call = strike_from_delta(forward, TENOR_YEARS, 0.25, 0.25, OptionType.CALL)
        assert by_type[OptionType.CALL] == pytest.approx(expected_call)

    def test_strike_from_delta_round_numbers(self):
        forward = 100.0
        # A 50-delta call sits essentially at the forward.
        k50 = strike_from_delta(forward, TENOR_YEARS, 0.2, 0.5, OptionType.CALL)
        assert k50 == pytest.approx(forward * math.exp(0.5 * 0.04 * TENOR_YEARS))
        with pytest.raises(ValueError, match="delta"):
            strike_from_delta(forward, TENOR_YEARS, 0.2, 1.5, OptionType.CALL)


class TestConfigValidation:
    def test_entry_must_exceed_exit(self):
        with pytest.raises(ValueError, match="entry_vrp_min"):
            VRPConfig(entry_vrp_min=0.0, exit_vrp_max=0.0)

    def test_bad_strangle_delta_rejected(self):
        with pytest.raises(ValueError, match="strangle_delta"):
            VRPConfig(strangle_delta=0.6)

    def test_bad_quantity_rejected(self):
        with pytest.raises(ValueError, match="quantity"):
            VRPConfig(quantity=0.0)

    def test_strategy_name_reflects_structure(self):
        assert VRPStrategy().name == "vrp_straddle"
        assert VRPStrategy(VRPConfig(structure="strangle")).name == "vrp_strangle"

"""Tests for pricing the synced book against live market data.

The two invariants that matter here are the ones that were previously broken:
IV must come from the *current* mark (not the entry fill), and a leg that
cannot be priced must be excluded and counted rather than assigned a default
vol (ADR-008, fail closed).
"""

from __future__ import annotations

import pytest

from options_trading.services.book_pricing import (
    price_book,
    risk_limits_from_settings,
)
from optitrade.core.types import OptionContract, OptionType, Portfolio, Position

SPOT = 24_600.0


def _contract(
    symbol: str = "NIFTY2490724500CE",
    strike: float = 24_500.0,
    expiry: float = 0.05,
    option_type: OptionType = OptionType.CALL,
) -> OptionContract:
    return OptionContract(
        symbol=symbol,
        strike=strike,
        expiry=expiry,
        option_type=option_type,
        lot_size=50,
    )


def _portfolio(*positions: Position) -> Portfolio:
    return Portfolio(positions=tuple(positions), equity=200_000.0)


class TestPriceBook:
    def test_prices_a_single_leg(self):
        pos = Position(contract=_contract(), quantity=50.0, entry_price=180.0)
        book = price_book(_portfolio(pos), marks={"NIFTY2490724500CE": 260.0}, spot=SPOT)

        assert book.n_priced == 1
        assert book.n_excluded == 0
        leg = book.legs[0]
        assert leg.mark == 260.0
        assert leg.iv > 0
        assert leg.greeks.delta > 0

    def test_iv_comes_from_mark_not_entry_price(self):
        """Regression: IV was inverted from the entry fill, describing a dead market."""
        pos = Position(contract=_contract(), quantity=50.0, entry_price=180.0)
        cheap = price_book(_portfolio(pos), marks={"NIFTY2490724500CE": 200.0}, spot=SPOT)
        rich = price_book(_portfolio(pos), marks={"NIFTY2490724500CE": 400.0}, spot=SPOT)

        # A richer mark on the same contract must imply a higher vol. If IV came
        # from entry_price (identical in both) these would be equal.
        assert rich.legs[0].iv > cheap.legs[0].iv

    def test_aggregate_scales_by_quantity(self):
        pos = Position(contract=_contract(), quantity=50.0, entry_price=180.0)
        one = price_book(
            _portfolio(Position(contract=_contract(), quantity=1.0, entry_price=180.0)),
            marks={"NIFTY2490724500CE": 260.0},
            spot=SPOT,
        )
        fifty = price_book(_portfolio(pos), marks={"NIFTY2490724500CE": 260.0}, spot=SPOT)
        assert fifty.aggregate_greeks.delta == pytest.approx(one.aggregate_greeks.delta * 50.0)

    def test_short_leg_flips_delta_sign(self):
        short = Position(contract=_contract(), quantity=-50.0, entry_price=180.0)
        book = price_book(_portfolio(short), marks={"NIFTY2490724500CE": 260.0}, spot=SPOT)
        assert book.aggregate_greeks.delta < 0


class TestFailClosed:
    def test_missing_mark_excludes_leg(self):
        pos = Position(contract=_contract(), quantity=50.0, entry_price=180.0)
        book = price_book(_portfolio(pos), marks={}, spot=SPOT)
        assert book.n_priced == 0
        assert book.n_excluded == 1

    def test_zero_mark_excludes_leg(self):
        pos = Position(contract=_contract(), quantity=50.0, entry_price=180.0)
        book = price_book(_portfolio(pos), marks={"NIFTY2490724500CE": 0.0}, spot=SPOT)
        assert book.n_excluded == 1

    def test_uninvertible_mark_excludes_rather_than_defaults(self):
        """A mark below intrinsic has no implied vol; the leg must vanish, not
        acquire a stand-in 0.20 vol as the old code did."""
        pos = Position(contract=_contract(), quantity=50.0, entry_price=180.0)
        # Intrinsic for a 24500 call at spot 24600 is ~100; 1.0 is unpriceable.
        book = price_book(_portfolio(pos), marks={"NIFTY2490724500CE": 1.0}, spot=SPOT)
        assert book.n_priced == 0
        assert book.n_excluded == 1
        assert book.aggregate_greeks.delta == 0.0

    def test_expired_leg_excluded(self):
        pos = Position(
            contract=_contract(expiry=0.0),
            quantity=50.0,
            entry_price=180.0,
        )
        book = price_book(_portfolio(pos), marks={"NIFTY2490724500CE": 260.0}, spot=SPOT)
        assert book.n_excluded == 1

    def test_partial_book_prices_what_it_can(self):
        good = Position(contract=_contract(), quantity=50.0, entry_price=180.0)
        bad = Position(
            contract=_contract(symbol="NIFTY2490724000PE", option_type=OptionType.PUT),
            quantity=-50.0,
            entry_price=120.0,
        )
        book = price_book(_portfolio(good, bad), marks={"NIFTY2490724500CE": 260.0}, spot=SPOT)
        assert book.n_priced == 1
        assert book.n_excluded == 1

    def test_non_positive_spot_is_rejected(self):
        pos = Position(contract=_contract(), quantity=50.0, entry_price=180.0)
        with pytest.raises(ValueError, match="spot must be positive"):
            price_book(_portfolio(pos), marks={"NIFTY2490724500CE": 260.0}, spot=0.0)


class TestScenarioConversion:
    def test_to_scenario_book_carries_per_leg_vol(self):
        pos = Position(contract=_contract(), quantity=50.0, entry_price=180.0)
        book = price_book(_portfolio(pos), marks={"NIFTY2490724500CE": 260.0}, spot=SPOT)
        scenario = book.to_scenario_book()

        assert len(scenario) == 1
        leg = scenario[0]
        assert leg.strike == 24_500.0
        assert leg.option_type is OptionType.CALL
        assert leg.quantity == 50.0
        assert leg.vol == pytest.approx(book.legs[0].iv)

    def test_excluded_legs_are_absent_from_scenario_book(self):
        pos = Position(contract=_contract(), quantity=50.0, entry_price=180.0)
        book = price_book(_portfolio(pos), marks={}, spot=SPOT)
        assert book.to_scenario_book() == []


class TestRiskLimitsConfig:
    def test_limits_come_from_settings_not_literals(self):
        limits = risk_limits_from_settings()
        assert limits.max_abs_delta > 0
        assert limits.max_abs_vega > 0
        assert 0 < limits.max_drawdown <= 1.0
        assert limits.margin_buffer >= 1.0

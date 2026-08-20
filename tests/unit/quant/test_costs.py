"""Hand-checked tests of the Indian options cost model against the rate card.

The reference numbers below are computed by hand from the documented 2025
NSE/SEBI card for a premium notional of 7,500 INR (price 100, 1 contract,
lot 75):

Buy side:  brokerage 20; STT 0; txn 7500*0.0003503 = 2.62725;
           SEBI 7500*1e-6 = 0.0075; GST 0.18*(20 + 2.62725 + 0.0075)
           = 4.074255; stamp 7500*0.00003 = 0.225  -> total 26.934005.
Sell side: brokerage 20; STT 7500*0.001 = 7.5; txn 2.62725; SEBI 0.0075;
           GST 4.074255; stamp 0                   -> total 34.209005.
"""

import pytest

from optitrade.strategy import IndianCostRates, IndianOptionsCostModel

pytestmark = pytest.mark.unit

PRICE = 100.0
LOT = 75


class TestRateCardBreakdown:
    def test_buy_side_breakdown_exact(self):
        breakdown = IndianOptionsCostModel().cost_of(PRICE, 1.0, LOT, is_buy=True)
        assert breakdown.brokerage == pytest.approx(20.0, abs=1e-9)
        assert breakdown.stt == 0.0
        assert breakdown.exchange_txn == pytest.approx(2.62725, abs=1e-9)
        assert breakdown.sebi == pytest.approx(0.0075, abs=1e-9)
        assert breakdown.gst == pytest.approx(4.074255, abs=1e-9)
        assert breakdown.stamp == pytest.approx(0.225, abs=1e-9)
        assert breakdown.total == pytest.approx(26.934005, abs=1e-9)

    def test_sell_side_breakdown_exact(self):
        breakdown = IndianOptionsCostModel().cost_of(PRICE, -1.0, LOT, is_buy=False)
        assert breakdown.brokerage == pytest.approx(20.0, abs=1e-9)
        assert breakdown.stt == pytest.approx(7.5, abs=1e-9)
        assert breakdown.exchange_txn == pytest.approx(2.62725, abs=1e-9)
        assert breakdown.sebi == pytest.approx(0.0075, abs=1e-9)
        assert breakdown.gst == pytest.approx(4.074255, abs=1e-9)
        assert breakdown.stamp == 0.0
        assert breakdown.total == pytest.approx(34.209005, abs=1e-9)

    def test_side_comes_from_is_buy_not_quantity_sign(self):
        model = IndianOptionsCostModel()
        buy_to_close = model.cost_of(PRICE, 1.0, LOT, is_buy=True)
        buy_to_open = model.cost_of(PRICE, -1.0, LOT, is_buy=True)
        assert buy_to_close.total == buy_to_open.total

    def test_premium_scales_with_quantity_and_lot(self):
        model = IndianOptionsCostModel()
        one = model.cost_of(PRICE, 1.0, LOT, is_buy=False)
        two = model.cost_of(PRICE, 2.0, LOT, is_buy=False)
        assert two.stt == pytest.approx(2.0 * one.stt, abs=1e-9)
        assert two.brokerage == one.brokerage  # flat per order, not per lot


class TestRoundTrip:
    def test_short_first_round_trip_sums_both_sides(self):
        # Sell to open, buy to close at the same price: sell + buy totals.
        breakdown = IndianOptionsCostModel().round_trip(PRICE, PRICE, -1.0, LOT)
        assert breakdown.total == pytest.approx(34.209005 + 26.934005, abs=1e-9)
        assert breakdown.brokerage == pytest.approx(40.0, abs=1e-9)
        assert breakdown.stt == pytest.approx(7.5, abs=1e-9)  # sell leg only
        assert breakdown.stamp == pytest.approx(0.225, abs=1e-9)  # buy leg only

    def test_long_first_round_trip_swaps_sides(self):
        breakdown = IndianOptionsCostModel().round_trip(PRICE, 2.0 * PRICE, 1.0, LOT)
        # STT applies to the exit (sell at 200): 15000 * 0.001.
        assert breakdown.stt == pytest.approx(15.0, abs=1e-9)
        # Stamp applies to the entry (buy at 100): 7500 * 0.00003.
        assert breakdown.stamp == pytest.approx(0.225, abs=1e-9)


class TestHedgeCostAndConfig:
    def test_hedge_cost_is_proportional(self):
        model = IndianOptionsCostModel()
        assert model.hedge_cost(100.0, -10.0) == pytest.approx(5e-4 * 10.0 * 100.0, abs=1e-12)

    def test_zero_rates_cost_nothing(self):
        zero = IndianCostRates(
            brokerage_per_order=0.0,
            stt_sell_frac=0.0,
            exchange_txn_frac=0.0,
            gst_frac=0.0,
            sebi_frac=0.0,
            stamp_buy_frac=0.0,
            hedge_cost_frac=0.0,
        )
        model = IndianOptionsCostModel(zero)
        assert model.cost_of(PRICE, -1.0, LOT, is_buy=False).total == 0.0
        assert model.hedge_cost(100.0, 5.0) == 0.0

    def test_validation(self):
        with pytest.raises(ValueError, match="stt_sell_frac"):
            IndianCostRates(stt_sell_frac=-0.001)
        model = IndianOptionsCostModel()
        with pytest.raises(ValueError, match="price"):
            model.cost_of(0.0, 1.0, LOT, is_buy=True)
        with pytest.raises(ValueError, match="quantity"):
            model.cost_of(PRICE, 0.0, LOT, is_buy=True)
        with pytest.raises(ValueError, match="lot_size"):
            model.cost_of(PRICE, 1.0, 0, is_buy=True)

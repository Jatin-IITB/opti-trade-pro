"""Tests for quote hygiene filters and the capture pipeline."""

import pytest

from optitrade.core.types import OptionType
from optitrade.data import (
    FilterConfig,
    RawChain,
    RawQuote,
    SyntheticSource,
    crossed_book,
    filter_chain,
    non_positive_mid,
    stale_quote,
    to_market_snapshot,
    wide_spread,
    zero_bid_wing,
)

pytestmark = pytest.mark.unit


def make_quote(**overrides) -> RawQuote:
    base = {
        "strike": 24_500.0,
        "expiry": 28.0 / 365.0,
        "option_type": OptionType.CALL,
        "bid": 199.0,
        "ask": 201.0,
        "ltp": 200.0,
        "volume": 1_000,
        "open_interest": 10_000,
        "bid_qty": 50,
        "ask_qty": 75,
        "ltp_age_seconds": 5.0,
    }
    base.update(overrides)
    return RawQuote(**base)


def make_chain(quotes) -> RawChain:
    return RawChain(
        underlying="NIFTY",
        spot=24_500.0,
        rate=0.065,
        timestamp=1_755_000_000.0,
        quotes=tuple(quotes),
    )


class TestCrossedBook:
    def test_rejects_bid_above_ask(self):
        reason = crossed_book(make_quote(bid=210.0, ask=190.0))
        assert reason is not None
        assert "crossed book" in reason
        assert "210.00" in reason and "190.00" in reason

    def test_passes_normal_book(self):
        assert crossed_book(make_quote()) is None

    def test_passes_locked_book(self):
        # bid == ask is locked, not crossed.
        assert crossed_book(make_quote(bid=200.0, ask=200.0)) is None

    def test_passes_one_sided_book(self):
        # An absent side (0.0) cannot cross anything.
        assert crossed_book(make_quote(bid=0.0, ask=5.0)) is None


class TestStaleQuote:
    def test_rejects_zero_volume_and_zero_oi(self):
        reason = stale_quote(make_quote(volume=0, open_interest=0))
        assert reason is not None
        assert "zero volume and zero open interest" in reason

    def test_passes_zero_volume_with_open_interest(self):
        assert stale_quote(make_quote(volume=0, open_interest=500)) is None

    def test_rejects_old_last_trade(self):
        reason = stale_quote(make_quote(ltp_age_seconds=301.0))
        assert reason is not None
        assert "301" in reason and "300" in reason

    def test_passes_recent_last_trade(self):
        assert stale_quote(make_quote(ltp_age_seconds=299.0)) is None

    def test_custom_threshold(self):
        quote = make_quote(ltp_age_seconds=61.0)
        assert stale_quote(quote) is None
        assert stale_quote(quote, max_ltp_age_seconds=60.0) is not None


class TestWideSpread:
    def test_rejects_wide_book(self):
        # (13 - 7) / 10 = 60% of mid.
        reason = wide_spread(make_quote(bid=7.0, ask=13.0))
        assert reason is not None
        assert "60.0%" in reason and "25.0%" in reason

    def test_passes_tight_book(self):
        # (201 - 199) / 200 = 1% of mid.
        assert wide_spread(make_quote()) is None

    def test_not_applicable_to_one_sided_book(self):
        assert wide_spread(make_quote(bid=0.0, ask=13.0)) is None

    def test_custom_threshold(self):
        quote = make_quote(bid=7.0, ask=13.0)
        assert wide_spread(quote, max_spread_frac=0.8) is None
        assert wide_spread(quote, max_spread_frac=0.5) is not None


class TestZeroBidWing:
    def test_rejects_zero_bid(self):
        reason = zero_bid_wing(make_quote(bid=0.0, ask=0.6))
        assert reason is not None
        assert "zero bid wing" in reason

    def test_passes_two_sided_book(self):
        assert zero_bid_wing(make_quote(bid=0.05, ask=0.6)) is None


class TestNonPositiveMid:
    def test_rejects_zero_mid(self):
        reason = non_positive_mid(make_quote(bid=0.0, ask=0.0))
        assert reason is not None
        assert "non-positive mid" in reason

    def test_rejects_negative_mid(self):
        assert non_positive_mid(make_quote(bid=1.0, ask=-3.0)) is not None

    def test_passes_positive_mid(self):
        assert non_positive_mid(make_quote()) is None


class TestFilterChain:
    def build_mixed_chain(self) -> RawChain:
        return make_chain(
            [
                make_quote(),  # clean
                make_quote(option_type=OptionType.PUT),  # clean
                make_quote(bid=210.0, ask=190.0),  # crossed
                make_quote(volume=0, open_interest=0),  # stale
                make_quote(bid=7.0, ask=13.0),  # wide
                make_quote(bid=0.0, ask=0.6),  # zero-bid wing
                make_quote(bid=1.0, ask=-3.0),  # non-positive mid
            ]
        )

    def test_stats_add_up_and_partition(self):
        chain = self.build_mixed_chain()
        result = filter_chain(chain)
        assert sum(result.stats.values()) == len(chain.quotes)
        assert len(result.clean) + len(result.rejected) == len(chain.quotes)
        assert result.stats == {
            "clean": 2,
            "crossed_book": 1,
            "stale_quote": 1,
            "wide_spread": 1,
            "zero_bid_wing": 1,
            "non_positive_mid": 1,
        }

    def test_clean_preserves_input_order(self):
        chain = self.build_mixed_chain()
        result = filter_chain(chain)
        assert result.clean == chain.quotes[:2]

    def test_first_reason_wins_crossed_before_stale(self):
        # Crossed AND stale: crossed_book runs first and provides the reason.
        chain = make_chain([make_quote(bid=210.0, ask=190.0, volume=0, open_interest=0)])
        result = filter_chain(chain)
        (_, reason) = result.rejected[0]
        assert "crossed book" in reason
        assert result.stats["crossed_book"] == 1
        assert result.stats["stale_quote"] == 0

    def test_first_reason_wins_stale_before_wide(self):
        chain = make_chain([make_quote(bid=7.0, ask=13.0, volume=0, open_interest=0)])
        result = filter_chain(chain)
        (_, reason) = result.rejected[0]
        assert "stale quote" in reason
        assert result.stats["stale_quote"] == 1
        assert result.stats["wide_spread"] == 0

    def test_disabled_filter_falls_through_to_next(self):
        chain = make_chain([make_quote(bid=210.0, ask=190.0, volume=0, open_interest=0)])
        config = FilterConfig(check_crossed_book=False)
        result = filter_chain(chain, config)
        (_, reason) = result.rejected[0]
        assert "stale quote" in reason
        assert result.stats["crossed_book"] == 0

    def test_config_thresholds_apply(self):
        chain = make_chain([make_quote(bid=7.0, ask=13.0)])
        assert filter_chain(chain).stats["wide_spread"] == 1
        relaxed = FilterConfig(max_spread_frac=0.8)
        assert filter_chain(chain, relaxed).stats["clean"] == 1

    def test_deterministic(self):
        chain = self.build_mixed_chain()
        assert filter_chain(chain) == filter_chain(chain)


class TestToMarketSnapshot:
    def test_end_to_end_from_synthetic_source(self):
        chain = SyntheticSource().fetch_chain("NIFTY")
        snapshot = to_market_snapshot(chain)
        assert len(snapshot.quotes) >= 4
        assert len(snapshot.quotes) < len(chain.quotes)  # the wings got filtered
        for quote in snapshot.quotes:
            assert quote.mid > 0.0
            assert quote.bid is not None and quote.ask is not None
            assert quote.mid == pytest.approx(0.5 * (quote.bid + quote.ask))
        assert snapshot.spot == chain.spot
        assert snapshot.rate == chain.rate
        assert snapshot.timestamp == chain.timestamp
        assert snapshot.dividend_yield == chain.dividend_yield

    def test_synthetic_source_is_deterministic(self):
        source = SyntheticSource(seed=11)
        assert source.fetch_chain("NIFTY") == source.fetch_chain("NIFTY")
        assert source.fetch_chain("NIFTY") != source.fetch_chain("BANKNIFTY")

    def test_too_few_clean_quotes_raises(self):
        chain = make_chain([make_quote(strike=24_000.0 + 100.0 * i) for i in range(3)])
        with pytest.raises(ValueError, match="need at least 4"):
            to_market_snapshot(chain)

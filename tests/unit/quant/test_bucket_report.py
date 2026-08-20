"""Tests for the expiry-bucketed exposure report."""

import pytest

from optitrade.core import Greeks, OptionType
from optitrade.explain import EXPIRY_BUCKETS, bucket_exposures
from optitrade.greeks import BookPosition
from optitrade.pricing import bs_greeks_at

pytestmark = pytest.mark.unit

SPOT, RATE, DIV_YIELD = 100.0, 0.03, 0.01

GREEK_FIELDS = ("delta", "gamma", "vega", "theta", "rho", "vanna", "volga")


def _mixed_book() -> list[BookPosition]:
    """Signed calls and puts spread across all four expiry buckets."""
    return [
        BookPosition(
            strike=100.0, expiry=3 / 365, option_type=OptionType.CALL, quantity=10, vol=0.25
        ),
        BookPosition(
            strike=95.0, expiry=5 / 365, option_type=OptionType.PUT, quantity=-4, vol=0.30
        ),
        BookPosition(
            strike=105.0, expiry=14 / 365, option_type=OptionType.CALL, quantity=6, vol=0.22
        ),
        BookPosition(
            strike=100.0, expiry=45 / 365, option_type=OptionType.PUT, quantity=-3, vol=0.24
        ),
        BookPosition(
            strike=110.0, expiry=60 / 365, option_type=OptionType.CALL, quantity=2, vol=0.21
        ),
        BookPosition(
            strike=100.0, expiry=200 / 365, option_type=OptionType.CALL, quantity=-5, vol=0.20
        ),
        BookPosition(strike=90.0, expiry=1.2, option_type=OptionType.PUT, quantity=7, vol=0.28),
    ]


def _whole_book_greeks(book: list[BookPosition]) -> Greeks:
    """Independent per-position aggregate to check the bucket invariant against."""
    total = Greeks()
    for p in book:
        g = bs_greeks_at(SPOT, p.strike, p.expiry, RATE, p.vol, p.option_type, DIV_YIELD)
        total = total + g.scaled(p.quantity)
    return total


class TestBucketExposures:
    def test_bucket_sums_equal_whole_book_greeks(self):
        book = _mixed_book()
        report = bucket_exposures(book, SPOT, RATE, DIV_YIELD)
        expected = _whole_book_greeks(book)

        for field in GREEK_FIELDS:
            assert getattr(report.totals, field) == pytest.approx(
                getattr(expected, field), abs=1e-10
            )

    def test_totals_equal_sum_of_rows(self):
        report = bucket_exposures(_mixed_book(), SPOT, RATE, DIV_YIELD)
        summed = Greeks()
        for _, greeks, _ in report.rows:
            summed = summed + greeks
        for field in GREEK_FIELDS:
            assert getattr(report.totals, field) == pytest.approx(getattr(summed, field))

    def test_labels_and_position_counts(self):
        report = bucket_exposures(_mixed_book(), SPOT, RATE, DIV_YIELD)

        assert [label for label, _, _ in report.rows] == ["0-7d", "7-30d", "30-90d", "90d+"]
        assert [label for label, _, _ in report.rows] == [b[2] for b in EXPIRY_BUCKETS]
        assert [n for _, _, n in report.rows] == [2, 1, 2, 2]

    def test_empty_buckets_present_with_zero_greeks(self):
        book = [
            BookPosition(
                strike=100.0, expiry=2 / 365, option_type=OptionType.CALL, quantity=1, vol=0.2
            ),
            BookPosition(strike=100.0, expiry=1.0, option_type=OptionType.PUT, quantity=1, vol=0.2),
        ]
        report = bucket_exposures(book, SPOT, RATE)

        assert len(report.rows) == 4
        for label in ("7-30d", "30-90d"):
            row = next(r for r in report.rows if r[0] == label)
            assert row[1] == Greeks()
            assert row[2] == 0

    def test_boundary_expiry_belongs_to_longer_bucket(self):
        # Half-open [lo, hi) buckets: exactly 7d is the first day of "7-30d".
        book = [
            BookPosition(
                strike=100.0, expiry=7 / 365, option_type=OptionType.CALL, quantity=1, vol=0.2
            )
        ]
        report = bucket_exposures(book, SPOT, RATE)

        assert report.rows[0][2] == 0  # "0-7d"
        assert report.rows[1][2] == 1  # "7-30d"

    def test_empty_book_gives_all_zero_report(self):
        report = bucket_exposures([], SPOT, RATE)
        assert report.totals == Greeks()
        assert all(greeks == Greeks() and n == 0 for _, greeks, n in report.rows)

    def test_invalid_spot_raises(self):
        with pytest.raises(ValueError, match="spot"):
            bucket_exposures(_mixed_book(), -1.0, RATE)

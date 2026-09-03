"""Tests for book snapshot persistence and the P&L attribution built on it.

The decomposition is only meaningful for a position that was *held*: if the
user traded between the two snapshots, the change in book value carries fill
cash flow that no Greek explains. The tests below pin that exclusion rule
alongside the arithmetic, because a waterfall that quietly folds a trade into
its residual still presents the rest as explained.
"""

import json
import math
from itertools import pairwise

import pytest

from options_trading.services.book_pricing import PricedBook, PricedLeg
from options_trading.services.book_snapshot_store import (
    SCHEMA_VERSION,
    BookSnapshot,
    BookSnapshotStore,
    LegSnapshot,
    snapshot_from_priced_book,
)
from options_trading.services.pnl_attribution import explain_book_pnl
from optitrade.core.types import Greeks, OptionContract, OptionType

pytestmark = pytest.mark.unit

SECONDS_PER_DAY = 86_400.0
BASE_TIMESTAMP = 1_700_000_000.0
SPOT = 24_000.0
STRIKE = 24_000.0
EXPIRY = 0.08
LOT = 75.0

# Greeks chosen so each bucket is separately hand-checkable below.
LEG_GREEKS = Greeks(
    delta=0.5, gamma=0.0004, vega=2800.0, theta=-9000.0, rho=0.0, vanna=1.0, volga=2.0
)


def make_leg(
    symbol: str = "NIFTY24000CE",
    quantity: float = LOT,
    mark: float = 300.0,
    iv: float = 0.14,
    greeks: Greeks = LEG_GREEKS,
) -> LegSnapshot:
    return LegSnapshot(
        symbol=symbol,
        strike=STRIKE,
        expiry=EXPIRY,
        option_type="call",
        quantity=quantity,
        mark=mark,
        iv=iv,
        greeks=greeks,
    )


def make_snapshot(
    timestamp: float = BASE_TIMESTAMP,
    spot: float = SPOT,
    legs: tuple[LegSnapshot, ...] | None = None,
    equity: float | None = 1_000_000.0,
) -> BookSnapshot:
    return BookSnapshot(
        timestamp=timestamp,
        spot=spot,
        rate=0.0679,
        legs=legs if legs is not None else (make_leg(),),
        equity=equity,
    )


class TestBookSnapshotStore:
    def test_round_trips_a_book(self, tmp_path):
        store = BookSnapshotStore(tmp_path)
        original = make_snapshot()

        restored = store.read(store.write(original))

        assert restored == original

    def test_greeks_survive_the_round_trip(self, tmp_path):
        """Per-leg Greeks are the whole point of persisting; losing one would
        silently zero a bucket in the explain."""
        store = BookSnapshotStore(tmp_path)

        restored = store.read(store.write(make_snapshot()))

        for field in ("delta", "gamma", "vega", "theta", "vanna", "volga"):
            assert getattr(restored.legs[0].greeks, field) == getattr(LEG_GREEKS, field)

    def test_refuses_to_store_an_empty_book(self, tmp_path):
        """A flat book and a failed sync must not become indistinguishable."""
        store = BookSnapshotStore(tmp_path)

        with pytest.raises(ValueError, match="0 priced legs"):
            store.write(make_snapshot(legs=()))

    def test_path_layout_is_chronological(self, tmp_path):
        store = BookSnapshotStore(tmp_path)
        second = store.write(make_snapshot(timestamp=BASE_TIMESTAMP + 3_600.0))
        first = store.write(make_snapshot(timestamp=BASE_TIMESTAMP))

        assert store.list_snapshots() == [first, second]

    def test_end_of_day_takes_the_last_snapshot_per_date(self, tmp_path):
        """Matches StoreReplay's convention so the two histories align."""
        store = BookSnapshotStore(tmp_path)
        store.write(make_snapshot(timestamp=BASE_TIMESTAMP, spot=100.0))
        store.write(make_snapshot(timestamp=BASE_TIMESTAMP + 3_600.0, spot=200.0))
        store.write(make_snapshot(timestamp=BASE_TIMESTAMP + SECONDS_PER_DAY, spot=300.0))

        eod = store.end_of_day_snapshots()

        assert [s.spot for s in eod] == [200.0, 300.0]

    def test_end_of_day_limit_keeps_the_most_recent_days(self, tmp_path):
        store = BookSnapshotStore(tmp_path)
        for day in range(5):
            store.write(
                make_snapshot(timestamp=BASE_TIMESTAMP + day * SECONDS_PER_DAY, spot=100.0 + day)
            )

        assert [s.spot for s in store.end_of_day_snapshots(limit=2)] == [103.0, 104.0]

    def test_missing_root_lists_nothing(self, tmp_path):
        assert BookSnapshotStore(tmp_path / "absent").list_snapshots() == []

    def test_rejects_a_future_schema_version(self, tmp_path):
        store = BookSnapshotStore(tmp_path)
        path = store.write(make_snapshot())
        raw = json.loads(path.read_text())
        raw["schema_version"] = SCHEMA_VERSION + 1
        path.write_text(json.dumps(raw))

        with pytest.raises(ValueError, match="schema_version"):
            store.read(path)

    def test_one_corrupt_day_does_not_blind_the_others(self, tmp_path):
        """Skip-and-warn: an unattended writer must not lose the whole history."""
        store = BookSnapshotStore(tmp_path)
        store.write(make_snapshot(timestamp=BASE_TIMESTAMP, spot=100.0))
        corrupt = store.write(make_snapshot(timestamp=BASE_TIMESTAMP + SECONDS_PER_DAY))
        corrupt.write_text("{ not json")

        assert [s.spot for s in store.end_of_day_snapshots()] == [100.0]

    def test_intraday_spots_include_the_starting_snapshot(self, tmp_path):
        """Both ends inclusive, so the overnight gap enters realized variance.

        The caller passes the previous end-of-day snapshot's own timestamp.
        Excluding it would drop the first return of the period — for an Indian
        index, routinely the largest single move — from the gamma bucket.
        """
        store = BookSnapshotStore(tmp_path)
        for i in range(4):
            store.write(make_snapshot(timestamp=BASE_TIMESTAMP + i * 3_600.0, spot=100.0 + i))

        spots = store.intraday_spots(BASE_TIMESTAMP, BASE_TIMESTAMP + 2 * 3_600.0)

        assert spots == [100.0, 101.0, 102.0]

    def test_intraday_spots_only_reads_the_spanned_dates(self, tmp_path):
        """Scanning the whole store cost minutes once history accumulated."""
        store = BookSnapshotStore(tmp_path)
        for day in range(5):
            store.write(
                make_snapshot(timestamp=BASE_TIMESTAMP + day * SECONDS_PER_DAY, spot=100.0 + day)
            )

        window_start = BASE_TIMESTAMP + 3 * SECONDS_PER_DAY
        spots = store.intraday_spots(window_start, window_start + SECONDS_PER_DAY)

        assert spots == [103.0, 104.0]

    def test_prune_keeps_only_the_most_recent_days(self, tmp_path):
        """Unpruned, a 60s sync reaches gigabytes and 500k files in a year."""
        store = BookSnapshotStore(tmp_path)
        for day in range(6):
            store.write(make_snapshot(timestamp=BASE_TIMESTAMP + day * SECONDS_PER_DAY))

        removed = store.prune(keep_days=2)

        assert removed == 4
        assert len(store.dates()) == 2

    def test_prune_rejects_keeping_nothing(self, tmp_path):
        with pytest.raises(ValueError, match="keep_days"):
            BookSnapshotStore(tmp_path).prune(keep_days=0)

    def test_end_of_day_limit_zero_returns_nothing(self, tmp_path):
        """`dates[-0:]` is the whole list — the wrong direction to fail."""
        store = BookSnapshotStore(tmp_path)
        for day in range(3):
            store.write(make_snapshot(timestamp=BASE_TIMESTAMP + day * SECONDS_PER_DAY))

        assert store.end_of_day_snapshots(limit=0) == []

    def test_duplicate_symbols_are_combined_not_dropped(self, tmp_path):
        """Same strike held MIS and NRML arrives as two rows, one symbol.

        A plain dict comprehension keeps the last, silently halving the
        position in the attribution while `value` still counts both.
        """
        snapshot = make_snapshot(
            legs=(make_leg("DUP", quantity=75.0), make_leg("DUP", quantity=-25.0))
        )

        combined = snapshot.by_symbol

        assert len(combined) == 1
        assert combined["DUP"].quantity == pytest.approx(50.0)
        assert snapshot.value == pytest.approx(combined["DUP"].value)

    def test_gross_value_does_not_net_a_spread(self, tmp_path):
        snapshot = make_snapshot(legs=(make_leg("A", quantity=75.0), make_leg("B", quantity=-75.0)))

        assert snapshot.value == pytest.approx(0.0)
        assert snapshot.gross_value == pytest.approx(2 * 75.0 * 300.0)

    def test_snapshot_from_priced_book_carries_only_priced_legs(self):
        priced = PricedBook(
            legs=(
                PricedLeg(
                    contract=OptionContract(
                        symbol="A",
                        strike=STRIKE,
                        expiry=EXPIRY,
                        option_type=OptionType.CALL,
                        lot_size=1,
                    ),
                    quantity=LOT,
                    mark=300.0,
                    iv=0.14,
                    greeks=LEG_GREEKS,
                ),
            ),
            spot=SPOT,
            rate=0.0679,
            n_excluded=3,
        )

        snapshot = snapshot_from_priced_book(priced, timestamp=BASE_TIMESTAMP, equity=1e6)

        assert len(snapshot.legs) == 1
        assert snapshot.n_excluded == 3, "how much of the book is missing must survive"


class TestExplainArithmetic:
    """Each bucket is checked against the closed form it claims to compute."""

    START = make_snapshot(timestamp=BASE_TIMESTAMP, spot=SPOT)
    END = make_snapshot(
        timestamp=BASE_TIMESTAMP + SECONDS_PER_DAY,
        spot=SPOT + 120.0,
        legs=(make_leg(mark=340.0, iv=0.15),),
    )

    @pytest.fixture()
    def result(self):
        return explain_book_pnl(self.START, self.END)

    def test_total_is_the_mark_to_market_move(self, result):
        assert result.explain.total == pytest.approx(LOT * (340.0 - 300.0))

    def test_theta_carry_is_scaled_by_act_365(self, result):
        assert result.explain.theta_carry == pytest.approx(LOT * -9000.0 / 365.0)

    def test_delta_pnl_is_delta_times_the_spot_move(self, result):
        assert result.explain.delta_pnl == pytest.approx(LOT * 0.5 * 120.0)

    def test_gamma_uses_the_single_move_form_without_a_path(self, result):
        """No intraday spots means realized variance is unmeasurable, so the
        Taylor form is used rather than a fabricated variance."""
        assert result.realized_variance is None
        assert result.explain.gamma_vs_rv == pytest.approx(0.5 * LOT * 0.0004 * 120.0**2)

    def test_vega_bucket_is_vega_times_the_vol_move(self, result):
        assert sum(result.explain.vega_from_factors.values()) == pytest.approx(LOT * 2800.0 * 0.01)

    def test_buckets_and_residual_reconstruct_the_total(self, result):
        explain = result.explain
        rebuilt = (
            explain.theta_carry
            + explain.delta_pnl
            + explain.gamma_vs_rv
            + sum(explain.vega_from_factors.values())
            + explain.vega_residual_move
            + explain.vanna_volga
            + explain.residual
        )
        assert rebuilt == pytest.approx(explain.total)

    def test_dt_is_one_day_as_a_year_fraction(self, result):
        assert result.dt == pytest.approx(1.0 / 365.0)


class TestTradeExclusion:
    """Greeks explain a held position, not the cash flow of trading it."""

    START = make_snapshot(legs=(make_leg("A"), make_leg("B", mark=200.0)))
    LATER = BASE_TIMESTAMP + SECONDS_PER_DAY

    def test_a_resized_leg_is_excluded_and_counted(self):
        end = make_snapshot(
            timestamp=self.LATER,
            legs=(make_leg("A", mark=340.0), make_leg("B", quantity=LOT * 2, mark=210.0)),
        )

        result = explain_book_pnl(self.START, end)

        assert result.n_legs_compared == 1
        assert result.n_legs_changed == 1
        assert result.explain.total == pytest.approx(LOT * 40.0), "only leg A's P&L"

    def test_a_closed_leg_is_excluded(self):
        end = make_snapshot(timestamp=self.LATER, legs=(make_leg("A", mark=340.0),))

        result = explain_book_pnl(self.START, end)

        assert result.n_legs_compared == 1
        assert result.n_legs_changed == 1

    def test_a_newly_opened_leg_counts_as_a_change(self):
        """Its end value was never predictable from a starting Greek."""
        end = make_snapshot(
            timestamp=self.LATER,
            legs=(make_leg("A"), make_leg("B", mark=200.0), make_leg("C", mark=50.0)),
        )

        result = explain_book_pnl(self.START, end)

        assert result.n_legs_compared == 2
        assert result.n_legs_changed == 1

    def test_a_fully_turned_over_book_explains_nothing(self):
        """Absence, not a zero decomposition."""
        end = make_snapshot(timestamp=self.LATER, legs=(make_leg("Z", mark=10.0),))

        assert explain_book_pnl(self.START, end) is None

    def test_coverage_reports_the_share_of_the_book_explained(self):
        """A 98%-explained third of the book is not a 98%-explained book."""
        end = make_snapshot(
            timestamp=self.LATER,
            legs=(make_leg("A", mark=340.0), make_leg("B", quantity=1.0, mark=210.0)),
        )

        result = explain_book_pnl(self.START, end)

        start_total = LOT * 300.0 + LOT * 200.0
        assert result.coverage == pytest.approx(LOT * 300.0 / start_total)
        assert result.coverage < 1.0

    def test_coverage_is_gross_not_net_for_a_spread_book(self):
        """Regression: a net denominator reported 100% for a dropped leg.

        A short leg against a long one nets toward zero, so dividing by net
        value gave a ratio far above 1 that ``min(1.0, ...)`` then clamped —
        printing "100% covered" for a decomposition that excluded the larger
        half of the book.
        """
        spread = make_snapshot(
            legs=(make_leg("SHORT", quantity=-75.0, mark=300.0), make_leg("LONG", mark=290.0))
        )
        assert abs(spread.value) < spread.gross_value / 10, "the fixture must nearly net out"

        end = make_snapshot(timestamp=self.LATER, legs=(make_leg("LONG", mark=295.0),))
        result = explain_book_pnl(spread, end)

        assert result.coverage == pytest.approx((75.0 * 290.0) / (75.0 * 300.0 + 75.0 * 290.0))
        assert result.coverage < 0.6


class TestVolMoveWeighting:
    def test_vol_move_is_vega_weighted_across_legs(self):
        """``total_vega * d_vol`` must equal ``sum(vega_i * dvol_i)``.

        A plain average would misattribute whenever the smile moved
        non-parallel, which is most days.
        """
        big = Greeks(vega=1000.0)
        small = Greeks(vega=100.0)
        start = make_snapshot(
            legs=(
                make_leg("A", quantity=1.0, iv=0.20, greeks=big),
                make_leg("B", quantity=1.0, iv=0.20, greeks=small),
            )
        )
        end = make_snapshot(
            timestamp=BASE_TIMESTAMP + SECONDS_PER_DAY,
            legs=(
                make_leg("A", quantity=1.0, iv=0.21, greeks=big),
                make_leg("B", quantity=1.0, iv=0.30, greeks=small),
            ),
        )

        result = explain_book_pnl(start, end)

        # (1000*0.01 + 100*0.10) / 1100
        assert result.d_vol == pytest.approx((1000 * 0.01 + 100 * 0.10) / 1100)
        assert sum(result.explain.vega_from_factors.values()) == pytest.approx(
            1000 * 0.01 + 100 * 0.10
        )

    def test_a_near_cancelling_book_does_not_explode_the_volga_bucket(self):
        """Regression: the guard was absolute on a quantity of scale 1e5.

        A calendar spread leaves a net vega of ~1 against gross legs of ~4e5.
        Dividing by it gave a d_vol of ~1e3 — a hundred thousand vol points —
        which explain_pnl then squared into a ~1e12 volga bucket with an
        equal, opposite residual. The vega bucket looked fine because it is
        the same division inverted, so nothing flagged it.
        """
        near = make_snapshot(
            legs=(
                make_leg("A", quantity=1.0, iv=0.20, greeks=Greeks(vega=400_000.0, volga=2.0)),
                make_leg("B", quantity=-1.0, iv=0.20, greeks=Greeks(vega=399_999.0, volga=2.0)),
            )
        )
        end = make_snapshot(
            timestamp=BASE_TIMESTAMP + SECONDS_PER_DAY,
            legs=(
                make_leg("A", quantity=1.0, iv=0.21, greeks=Greeks(vega=400_000.0, volga=2.0)),
                make_leg("B", quantity=-1.0, iv=0.22, greeks=Greeks(vega=399_999.0, volga=2.0)),
            ),
        )

        result = explain_book_pnl(near, end)

        assert abs(result.d_vol) < 1.0, "a vol move above 100 vol points is not a vol move"
        assert abs(result.explain.vanna_volga) < 1e3

    def test_a_vega_neutral_book_falls_back_to_an_unweighted_mean(self):
        """The weighted mean is 0/0 there; the bucket is ~0 either way."""
        start = make_snapshot(
            legs=(
                make_leg("A", quantity=1.0, iv=0.20, greeks=Greeks(vega=500.0)),
                make_leg("B", quantity=-1.0, iv=0.20, greeks=Greeks(vega=500.0)),
            )
        )
        end = make_snapshot(
            timestamp=BASE_TIMESTAMP + SECONDS_PER_DAY,
            legs=(
                make_leg("A", quantity=1.0, iv=0.22, greeks=Greeks(vega=500.0)),
                make_leg("B", quantity=-1.0, iv=0.24, greeks=Greeks(vega=500.0)),
            ),
        )

        result = explain_book_pnl(start, end)

        assert result.d_vol == pytest.approx(0.03)


class TestRealizedVariance:
    def test_an_intraday_path_switches_the_gamma_bucket(self):
        """Gamma accrues along the path, not against one close-to-close move."""
        start = make_snapshot()
        end = make_snapshot(
            timestamp=BASE_TIMESTAMP + SECONDS_PER_DAY,
            spot=SPOT + 120.0,
            legs=(make_leg(mark=340.0, iv=0.15),),
        )
        path = [SPOT, SPOT + 200.0, SPOT - 50.0, SPOT + 120.0]

        with_path = explain_book_pnl(start, end, intraday_spots=path)
        without = explain_book_pnl(start, end)

        assert with_path.realized_variance is not None
        assert with_path.explain.gamma_vs_rv != without.explain.gamma_vs_rv

    def test_variance_is_quadratic_variation_not_a_sample_variance(self):
        """``realized_variance * dt`` must equal ``sum(log return^2)``.

        ``explain_pnl`` computes ``0.5 * gamma * S^2 * rv * dt``, and the true
        path-wise gamma P&L is ``0.5 * gamma * sum(dS^2)``. Any demeaned or
        ddof-corrected estimator breaks that identity.
        """
        path = [24_000.0, 24_120.0, 24_060.0, 24_180.0]
        start = make_snapshot()
        end = make_snapshot(timestamp=BASE_TIMESTAMP + SECONDS_PER_DAY, spot=path[-1])

        result = explain_book_pnl(start, end, intraday_spots=path)

        expected = sum(math.log(b / a) ** 2 for a, b in pairwise(path))
        assert result.realized_variance * result.dt == pytest.approx(expected)

    def test_a_trending_path_has_real_variance(self):
        """Regression: a demeaned estimator reports ~0 for a steady trend.

        Gamma accrues on every move regardless of direction, so a day that
        rises in equal steps has genuine quadratic variation. Subtracting the
        mean removed exactly the drift that *is* the gamma P&L, sending the
        whole bucket into the residual.
        """
        path = [24_000.0 * (1.002**i) for i in range(6)]
        start = make_snapshot()
        end = make_snapshot(timestamp=BASE_TIMESTAMP + SECONDS_PER_DAY, spot=path[-1])

        result = explain_book_pnl(start, end, intraday_spots=path)

        assert result.realized_variance * result.dt == pytest.approx(5 * math.log(1.002) ** 2)
        assert result.explain.gamma_vs_rv != pytest.approx(0.0)

    def test_a_whipsaw_is_not_inflated_at_the_minimum_sample(self):
        """Regression: ddof=1 over n annualisation doubled it at three spots."""
        path = [24_000.0, 24_120.0, 24_000.0]
        start = make_snapshot()
        end = make_snapshot(timestamp=BASE_TIMESTAMP + SECONDS_PER_DAY, spot=path[-1])

        result = explain_book_pnl(start, end, intraday_spots=path)

        expected = math.log(24_120 / 24_000) ** 2 + math.log(24_000 / 24_120) ** 2
        assert result.realized_variance * result.dt == pytest.approx(expected)

    def test_a_flat_path_is_unmeasurable_rather_than_zero(self):
        """Zero variance is not evidence; report absence and use the Taylor form."""
        start = make_snapshot()
        end = make_snapshot(timestamp=BASE_TIMESTAMP + SECONDS_PER_DAY, spot=SPOT)

        result = explain_book_pnl(start, end, intraday_spots=[SPOT] * 5)

        assert result.realized_variance is None

    def test_too_few_spots_leave_the_variance_unmeasured(self):
        start = make_snapshot()
        end = make_snapshot(timestamp=BASE_TIMESTAMP + SECONDS_PER_DAY, spot=SPOT + 120.0)

        result = explain_book_pnl(start, end, intraday_spots=[SPOT, SPOT + 10.0])

        assert result.realized_variance is None


class TestGuards:
    def test_rejects_a_non_advancing_period(self):
        snapshot = make_snapshot()

        with pytest.raises(ValueError, match="must be later"):
            explain_book_pnl(snapshot, snapshot)

    def test_rejects_a_non_positive_start_spot(self):
        with pytest.raises(ValueError, match="spot must be positive"):
            explain_book_pnl(
                make_snapshot(spot=0.0),
                make_snapshot(timestamp=BASE_TIMESTAMP + SECONDS_PER_DAY),
            )

"""Tests for the history-dependent dashboard panels.

The governing rule for this module is that an empty history must be
*reported*, never filled in. Before this phase these panels rendered a seeded
random walk from ``demo.json``; the tests below pin the states that replaced
it, including the two ways a backtest reaches zero trades — a signal that
never fired versus a risk engine that rejected every order — which look
identical on a chart and imply opposite fixes.

The synthetic market is built by restamping one ``SyntheticSource`` chain onto
consecutive UTC dates with a controlled spot path, which fixes realized vol:
a near-flat path leaves implied vol far above realized (VRP rich, the strategy
trades), a choppy one closes the gap.
"""

import pathlib

import pytest

from options_trading.services.book_snapshot_store import (
    BookSnapshot,
    BookSnapshotStore,
    LegSnapshot,
)
from options_trading.services.history_analytics import (
    MIN_DAYS_VRP,
    HistoryAnalytics,
    HistoryAnalyticsConfig,
    backtest_risk_limits,
    history_config_from_settings,
)
from optitrade.backtest import min_days_for_walk_forward
from optitrade.core.types import Greeks
from optitrade.data import SnapshotStore, SyntheticSource
from optitrade.data.models import RawChain

pytestmark = pytest.mark.unit

UNDERLYING = "NIFTY"
BASE_TIMESTAMP = 1_700_000_000.0
SECONDS_PER_DAY = 86_400.0
# Spot drift per day as a fraction. RICH keeps realized vol far under implied
# so VRP clears the entry gate; CHOPPY raises realized vol to close the gap.
RICH_DRIFT = 0.0002
CHOPPY_DRIFT = 0.004


def build_store(tmp_path: pathlib.Path, n_days: int, drift: float = RICH_DRIFT) -> SnapshotStore:
    """A snapshot store holding ``n_days`` consecutive end-of-day chains."""
    store = SnapshotStore(tmp_path)
    source = SyntheticSource(seed=1)
    for i in range(n_days):
        chain = source.fetch_chain(UNDERLYING)
        store.write(
            RawChain(
                underlying=chain.underlying,
                spot=chain.spot * (1 + drift * ((i % 3) - 1)),
                rate=chain.rate,
                timestamp=BASE_TIMESTAMP + i * SECONDS_PER_DAY,
                quotes=chain.quotes,
                dividend_yield=chain.dividend_yield,
            )
        )
    return store


@pytest.fixture()
def config() -> HistoryAnalyticsConfig:
    return HistoryAnalyticsConfig(underlying=UNDERLYING)


def analytics(store: SnapshotStore, config: HistoryAnalyticsConfig) -> HistoryAnalytics:
    return HistoryAnalytics(store, config)


class TestNoHistory:
    """A fresh install has captured nothing; that must read as absence."""

    def test_empty_store_reports_no_history_rather_than_zeros(self, tmp_path, config):
        payload = analytics(build_store(tmp_path, 0), config).build()
        wire = payload.to_wire_dict()

        for panel in (wire["vrpSignal"], wire["backtestEquity"]):
            assert panel["hasHistory"] is False
            assert panel["reason"]
            assert panel["nDays"] == 0

    def test_absent_series_are_none_not_empty_lists(self, tmp_path, config):
        """``[]`` plots as a blank chart; ``None`` forces the empty state."""
        wire = analytics(build_store(tmp_path, 0), config).build().to_wire_dict()

        assert wire["vrpSignal"]["iv"] is None
        assert wire["vrpSignal"]["spread"] is None
        assert wire["backtestEquity"]["equity"] is None
        assert wire["backtestEquity"]["sharpe"] is None
        assert wire["backtestEquity"]["deflatedSharpe"] is None

    def test_reason_says_how_much_history_is_missing(self, tmp_path, config):
        wire = analytics(build_store(tmp_path, 4), config).build().to_wire_dict()
        backtest = wire["backtestEquity"]

        assert backtest["hasHistory"] is False
        assert str(backtest["daysRequired"]) in backtest["reason"]
        assert backtest["daysAvailable"] == 4

    def test_days_required_matches_the_harness(self, config):
        """The number shown to the user is the harness's real floor."""
        assert config.min_days_backtest == min_days_for_walk_forward(
            config.n_folds, config.train_frac
        )


class TestVrpSignal:
    def test_series_are_real_and_aligned(self, tmp_path, config):
        vrp = analytics(build_store(tmp_path, 20), config).build().to_wire_dict()["vrpSignal"]

        assert vrp["hasHistory"] is True
        n = vrp["nDays"]
        assert n > 0
        for key in ("iv", "rv", "spread", "regimes", "dates"):
            assert len(vrp[key]) == n, f"{key} is not aligned with nDays"

    def test_spread_is_iv_minus_rv(self, tmp_path, config):
        """The headline series must be the difference it claims to be."""
        vrp = analytics(build_store(tmp_path, 20), config).build().to_wire_dict()["vrpSignal"]

        for iv, rv, spread in zip(vrp["iv"], vrp["rv"], vrp["spread"], strict=True):
            assert spread == pytest.approx(iv - rv)

    def test_vols_are_positive_decimals(self, tmp_path, config):
        vrp = analytics(build_store(tmp_path, 20), config).build().to_wire_dict()["vrpSignal"]

        for series in ("iv", "rv"):
            for value in vrp[series]:
                assert 0.0 < value < 5.0, f"{series} should be an annualised decimal, got {value}"

    def test_priming_days_are_excluded_not_plotted_as_zero(self, tmp_path, config):
        """StoreReplay sets rv = atm_iv before it can measure realized vol.

        Those days carry vrp == 0 by construction. Plotting them would drag
        meanSpread toward zero with days that were never measured.
        """
        vrp = analytics(build_store(tmp_path, 20), config).build().to_wire_dict()["vrpSignal"]

        assert vrp["primedDaysExcluded"] > 0
        assert vrp["nDays"] == 20 - vrp["primedDaysExcluded"]
        assert all(s != 0.0 for s in vrp["spread"])

    def test_thresholds_come_from_the_strategy_config(self, tmp_path, config):
        """Shaded bands must be the thresholds that actually trade."""
        vrp = analytics(build_store(tmp_path, 20), config).build().to_wire_dict()["vrpSignal"]

        assert vrp["entryThreshold"] == min(config.entry_vrp_grid)
        assert vrp["exitThreshold"] < vrp["entryThreshold"]

    def test_regimes_follow_the_thresholds(self, tmp_path, config):
        vrp = analytics(build_store(tmp_path, 20), config).build().to_wire_dict()["vrpSignal"]

        for spread, regime in zip(vrp["spread"], vrp["regimes"], strict=True):
            if spread >= vrp["entryThreshold"]:
                assert regime == "rich"
            elif spread <= vrp["exitThreshold"]:
                assert regime == "cheap"
            else:
                assert regime == "neutral"

    def test_needs_measured_days_not_merely_captured_ones(self, tmp_path, config):
        """Two captured days are all priming, so there is still no signal."""
        vrp = analytics(build_store(tmp_path, 2), config).build().to_wire_dict()["vrpSignal"]

        assert vrp["hasHistory"] is False
        assert vrp["daysRequired"] == MIN_DAYS_VRP


class TestBacktest:
    def test_runs_on_enough_history(self, tmp_path, config):
        backtest = (
            analytics(build_store(tmp_path, 40), config).build().to_wire_dict()["backtestEquity"]
        )

        assert backtest["hasHistory"] is True
        assert backtest["nTrades"] > 0, "the rich-VRP fixture should trade"
        assert backtest["note"] is None

    def test_curves_are_aligned_and_start_at_initial_equity(self, tmp_path, config):
        backtest = (
            analytics(build_store(tmp_path, 40), config).build().to_wire_dict()["backtestEquity"]
        )

        n = backtest["nDays"]
        for key in ("equity", "dailyPnl", "drawdown", "dates"):
            assert len(backtest[key]) == n
        assert backtest["initialEquity"] == config.initial_equity
        assert all(d >= 0.0 for d in backtest["drawdown"])

    def test_reports_out_of_sample_and_deflated_sharpe(self, tmp_path, config):
        """In-sample Sharpe alone is a selection artefact of the grid search."""
        backtest = (
            analytics(build_store(tmp_path, 40), config).build().to_wire_dict()["backtestEquity"]
        )

        assert backtest["oosSharpe"] is not None
        assert 0.0 <= backtest["deflatedSharpe"] <= 1.0
        assert backtest["nTrials"] == len(config.entry_vrp_grid) * config.n_folds

    def test_deflated_sharpe_separates_a_rich_market_from_a_marginal_one(self, tmp_path, config):
        """The headline statistic has to respond to the underlying edge."""
        rich = (
            analytics(build_store(tmp_path / "rich", 40, RICH_DRIFT), config)
            .build()
            .to_wire_dict()["backtestEquity"]
        )
        choppy = (
            analytics(build_store(tmp_path / "choppy", 40, CHOPPY_DRIFT), config)
            .build()
            .to_wire_dict()["backtestEquity"]
        )

        assert rich["deflatedSharpe"] > choppy["deflatedSharpe"]

    def test_costs_are_charged(self, tmp_path, config):
        backtest = (
            analytics(build_store(tmp_path, 40), config).build().to_wire_dict()["backtestEquity"]
        )

        assert backtest["totalCosts"] > 0.0, "real Indian costs must not be free"

    def test_folds_are_reported_with_the_config_each_chose(self, tmp_path, config):
        backtest = (
            analytics(build_store(tmp_path, 40), config).build().to_wire_dict()["backtestEquity"]
        )

        assert len(backtest["folds"]) == config.n_folds
        for fold in backtest["folds"]:
            assert fold["chosenEntryVrp"] in config.entry_vrp_grid
            assert fold["startDay"] < fold["endDay"]

    def test_is_deterministic(self, tmp_path, config):
        """CLAUDE.md: seeded only, no wall-clock or network dependence."""
        first = analytics(build_store(tmp_path, 20), config).build().to_wire_dict()
        second = analytics(build_store(tmp_path, 20), config).build().to_wire_dict()

        assert first["backtestEquity"]["equity"] == second["backtestEquity"]["equity"]
        assert first["vrpSignal"]["spread"] == second["vrpSignal"]["spread"]


class TestZeroTradeDiagnosis:
    """Zero trades has two causes that a flat curve cannot distinguish."""

    def test_risk_rejection_is_reported_from_the_journal(self, tmp_path, config):
        """A vega budget this small rejects every order; say so, do not guess.

        The failure this guards against is real: the first cut of this panel
        blamed the signal whenever nTrades was 0, while the risk engine was in
        fact rejecting every order on a vega cap. The two need opposite fixes.
        """
        starved = HistoryAnalyticsConfig(underlying=UNDERLYING, vega_budget_frac=1e-9)
        backtest = (
            analytics(build_store(tmp_path, 40), starved).build().to_wire_dict()["backtestEquity"]
        )

        assert backtest["nTrades"] == 0
        assert "rejected" in backtest["note"]
        assert "vega" in backtest["note"], "the note must name the breached limit"

    def test_a_signal_that_never_fires_is_reported_as_such(self, tmp_path, config):
        """An unreachable entry threshold blames the signal, not the risk engine."""
        never = HistoryAnalyticsConfig(underlying=UNDERLYING, entry_vrp_grid=(9.0,))
        backtest = (
            analytics(build_store(tmp_path, 40), never).build().to_wire_dict()["backtestEquity"]
        )

        assert backtest["nTrades"] == 0
        assert "never cleared" in backtest["note"]

    def test_a_flat_curve_is_never_left_unexplained(self, tmp_path, config):
        never = HistoryAnalyticsConfig(underlying=UNDERLYING, entry_vrp_grid=(9.0,))
        backtest = (
            analytics(build_store(tmp_path, 40), never).build().to_wire_dict()["backtestEquity"]
        )

        assert backtest["equity"][0] == backtest["equity"][-1]
        assert backtest["sharpe"] == 0.0
        assert "not a break-even edge" in backtest["note"]


class TestBacktestRiskLimits:
    """The backtest is a separate account and gets caps sized to its equity."""

    SPOT = 24_630.0

    def test_vega_cap_converts_per_vol_point_budget_to_per_unit_vol(self, config):
        limits = backtest_risk_limits(config, self.SPOT)

        # 0.5% of 10 lakh per vol point = 5,000/point = 500,000 per unit vol.
        assert limits.max_abs_vega == pytest.approx(
            config.initial_equity * config.vega_budget_frac * 100.0
        )

    def test_delta_cap_is_the_budget_for_a_one_percent_spot_move(self, config):
        limits = backtest_risk_limits(config, self.SPOT)

        pnl_of_one_percent_move = limits.max_abs_delta * 0.01 * self.SPOT
        assert pnl_of_one_percent_move == pytest.approx(
            config.initial_equity * config.delta_budget_frac
        )

    def test_gamma_cap_is_the_second_order_budget(self, config):
        limits = backtest_risk_limits(config, self.SPOT)

        move = 0.01 * self.SPOT
        second_order = 0.5 * limits.max_abs_gamma * move * move
        assert second_order == pytest.approx(config.initial_equity * config.gamma_budget_frac)

    def test_caps_scale_with_equity(self, config):
        small = backtest_risk_limits(config, self.SPOT)
        large = backtest_risk_limits(
            HistoryAnalyticsConfig(initial_equity=config.initial_equity * 10), self.SPOT
        )

        assert large.max_abs_vega == pytest.approx(small.max_abs_vega * 10)
        assert large.max_abs_delta == pytest.approx(small.max_abs_delta * 10)

    def test_delta_cap_falls_as_spot_rises(self, config):
        """A cap in underlying units cannot be shared across price levels."""
        cheap = backtest_risk_limits(config, 100.0)
        rich = backtest_risk_limits(config, 10_000.0)

        assert cheap.max_abs_delta > rich.max_abs_delta

    def test_rejects_a_non_positive_spot(self, config):
        for bad in (0.0, -1.0):
            with pytest.raises(ValueError, match="spot must be positive"):
                backtest_risk_limits(config, bad)

    def test_permits_the_strategy_it_is_meant_to_run(self, tmp_path, config):
        """Regression: the live book's caps forbade every entry outright."""
        backtest = (
            analytics(build_store(tmp_path, 40), config).build().to_wire_dict()["backtestEquity"]
        )
        assert backtest["nTrades"] > 0


class TestCaching:
    """A replay costs seconds; it must not run on the capture tick."""

    def test_second_build_is_served_from_cache(self, tmp_path, config):
        service = analytics(build_store(tmp_path, 20), config)
        first = service.build()

        assert service.build() is first, "an unchanged store must not be replayed again"

    def test_a_new_day_invalidates_the_cache(self, tmp_path, config):
        store = build_store(tmp_path, 20)
        service = analytics(store, config)
        first = service.build()

        source = SyntheticSource(seed=1)
        chain = source.fetch_chain(UNDERLYING)
        store.write(
            RawChain(
                underlying=chain.underlying,
                spot=chain.spot,
                rate=chain.rate,
                timestamp=BASE_TIMESTAMP + 20 * SECONDS_PER_DAY,
                quotes=chain.quotes,
                dividend_yield=chain.dividend_yield,
            )
        )

        assert service.build() is not first

    def test_an_intraday_recapture_does_not_trigger_a_rebuild(self, tmp_path, config):
        """Today's file is rewritten each cycle; the day set is what matters.

        Refitting a year of surfaces because one closing mark moved would put
        a multi-second replay back on the capture tick.
        """
        store = build_store(tmp_path, 20)
        service = analytics(store, config)
        first = service.build()

        source = SyntheticSource(seed=2)
        chain = source.fetch_chain(UNDERLYING)
        store.write(
            RawChain(
                underlying=chain.underlying,
                spot=chain.spot,
                rate=chain.rate,
                # Same UTC date as the last stored day, later in the session.
                timestamp=BASE_TIMESTAMP + 19 * SECONDS_PER_DAY + 3_600.0,
                quotes=chain.quotes,
                dividend_yield=chain.dividend_yield,
            )
        )

        assert service.build() is first

    def test_cache_survives_until_the_refresh_interval_elapses(self, tmp_path, config):
        now = [0.0]
        service = HistoryAnalytics(
            build_store(tmp_path, 20),
            HistoryAnalyticsConfig(underlying=UNDERLYING, refresh_seconds=1800.0),
            clock=lambda: now[0],
        )
        first = service.build()

        now[0] = 1799.0
        assert service.build() is first

    def test_cache_expires_after_the_refresh_interval(self, tmp_path, config):
        now = [0.0]
        service = HistoryAnalytics(
            build_store(tmp_path, 20),
            HistoryAnalyticsConfig(underlying=UNDERLYING, refresh_seconds=1800.0),
            clock=lambda: now[0],
        )
        first = service.build()

        now[0] = 1801.0
        assert service.build() is not first

    async def test_build_async_matches_build(self, tmp_path, config):
        service = analytics(build_store(tmp_path, 20), config)
        expected = service.build()

        assert await service.build_async() is expected


class TestWireFormat:
    def test_keys_are_camel_case_for_the_frontend(self, tmp_path, config):
        wire = analytics(build_store(tmp_path, 20), config).build().to_wire_dict()

        assert set(wire) == {"vrpSignal", "backtestEquity", "pnlExplain", "historyCoverage"}
        assert set(wire["historyCoverage"]) == {
            "daysAvailable",
            "daysRequired",
            "underlying",
            "computedAt",
            "warnings",
        }

    def test_coverage_reports_the_underlying_and_day_count(self, tmp_path, config):
        coverage = (
            analytics(build_store(tmp_path, 20), config).build().to_wire_dict()["historyCoverage"]
        )

        assert coverage["underlying"] == UNDERLYING
        assert coverage["daysAvailable"] == 20
        assert coverage["daysRequired"] == config.min_days_backtest

    def test_payload_is_json_serialisable(self, tmp_path, config):
        """Numpy floats survive the dataclass but not json.dumps."""
        import json

        json.dumps(analytics(build_store(tmp_path, 20), config).build().to_wire_dict())


class TestConfig:
    def test_settings_config_matches_the_deployed_defaults(self):
        from options_trading.config.settings import settings

        config = history_config_from_settings()
        assert config.surface == settings.history_surface_model
        assert config.entry_vrp_grid == tuple(settings.history_vrp_entry_grid)
        assert config.refresh_seconds == settings.history_refresh_seconds

    def test_an_empty_search_grid_is_rejected(self):
        with pytest.raises(ValueError, match="entry_vrp_grid"):
            HistoryAnalyticsConfig(entry_vrp_grid=())


class TestPnlExplainPanel:
    """The account panel: needs the book history, not the chain history."""

    @staticmethod
    def book_store(tmp_path, n_days: int, marks=(300.0, 340.0)) -> BookSnapshotStore:
        """A store with one end-of-day book per day, marks walking upward."""
        store = BookSnapshotStore(tmp_path / "books")
        for day in range(n_days):
            mark = marks[min(day, len(marks) - 1)]
            store.write(
                BookSnapshot(
                    timestamp=BASE_TIMESTAMP + day * SECONDS_PER_DAY,
                    spot=24_000.0 + day * 50.0,
                    rate=0.0679,
                    legs=(
                        LegSnapshot(
                            symbol="NIFTY24000CE",
                            strike=24_000.0,
                            expiry=0.08,
                            option_type="call",
                            quantity=75.0,
                            mark=mark,
                            iv=0.14 + day * 0.005,
                            greeks=Greeks(delta=0.5, gamma=0.0004, vega=2800.0, theta=-9000.0),
                        ),
                    ),
                    equity=1_000_000.0,
                )
            )
        return store

    def test_absent_without_a_book_store(self, tmp_path, config):
        """No broker connected is an absence, not a zero waterfall."""
        panel = analytics(build_store(tmp_path, 20), config).build().to_wire_dict()["pnlExplain"]

        assert panel["hasHistory"] is False
        assert panel["buckets"] is None
        assert panel["totalPnl"] is None

    def test_one_day_of_book_history_is_not_enough(self, tmp_path, config):
        service = HistoryAnalytics(build_store(tmp_path, 20), config, self.book_store(tmp_path, 1))

        panel = service.build().to_wire_dict()["pnlExplain"]

        assert panel["hasHistory"] is False
        assert panel["daysAvailable"] == 1

    def test_decomposes_two_end_of_day_books(self, tmp_path, config):
        service = HistoryAnalytics(build_store(tmp_path, 20), config, self.book_store(tmp_path, 2))

        panel = service.build().to_wire_dict()["pnlExplain"]

        assert panel["hasHistory"] is True
        assert panel["totalPnl"] == pytest.approx(75.0 * (340.0 - 300.0))
        assert panel["legsCompared"] == 1
        assert panel["legsChanged"] == 0

    def test_buckets_plus_residual_reconstruct_the_total(self, tmp_path, config):
        """The waterfall must add up to the bar it is drawn against."""
        service = HistoryAnalytics(build_store(tmp_path, 20), config, self.book_store(tmp_path, 2))

        panel = service.build().to_wire_dict()["pnlExplain"]

        assert sum(b["value"] for b in panel["buckets"]) == pytest.approx(panel["totalPnl"])

    def test_every_bucket_has_a_colour(self, tmp_path, config):
        service = HistoryAnalytics(build_store(tmp_path, 20), config, self.book_store(tmp_path, 2))

        panel = service.build().to_wire_dict()["pnlExplain"]

        names = [b["name"] for b in panel["buckets"]]
        assert names == ["Theta", "Delta", "Gamma vs RV", "Vega", "Vanna/Volga", "Residual"]
        assert all(b["color"].startswith("#") for b in panel["buckets"])

    def test_reports_coverage_alongside_the_explained_fraction(self, tmp_path, config):
        """98% explained of a fifth of the book is not 98% of the book."""
        service = HistoryAnalytics(build_store(tmp_path, 20), config, self.book_store(tmp_path, 2))

        panel = service.build().to_wire_dict()["pnlExplain"]

        assert 0.0 <= panel["explainedFraction"] <= 1.0
        assert panel["coverage"] == pytest.approx(1.0)

    def test_survives_an_unreadable_chain_store(self, tmp_path, config):
        """The book history is stored separately and must not be blinded."""
        service = HistoryAnalytics(
            SnapshotStore(tmp_path / "does-not-exist"), config, self.book_store(tmp_path, 2)
        )

        panel = service.build().to_wire_dict()["pnlExplain"]

        assert panel["hasHistory"] is True

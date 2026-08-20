"""StoreReplay tests: captured Parquet history through the identical harness.

Each stored day is a chain generated from a known SABR smile over a seeded
spot walk (the cli._synthetic_snapshot pattern, but as RawQuotes with sane
bid/ask books), so the replay's recovered features can be asserted against
the generating parameters: ATM IV near the planted level, put skew positive
for rho < 0, realized vol finite once history accumulates.
"""

import math
from datetime import UTC, datetime
from itertools import pairwise

import numpy as np
import pytest

from optitrade.backtest import BacktestConfig, StoreReplay, run_backtest
from optitrade.core.types import OptionType
from optitrade.data import RawChain, RawQuote, SnapshotStore
from optitrade.hedging import BandParams
from optitrade.pricing.black_scholes import bs_price
from optitrade.risk import RiskLimits
from optitrade.strategy import IndianCostRates, IndianOptionsCostModel, VRPConfig, VRPStrategy
from optitrade.vol.sabr import SABRParams, hagan_implied_vol

pytestmark = pytest.mark.unit

T0 = 1_700_000_000.0  # 2023-11-14 22:13:20 UTC — fixed, no wall clock
DAY = 86_400.0
UNDERLYING = "TESTIDX"
RATE = 0.06
GEN_RV = 0.15  # diffusion vol of the generating spot walk
GEN_VRP = 0.06  # planted premium: quoted ATM IV = GEN_RV + GEN_VRP
SEED = 3
FEATURE_KEYS = ("atm_iv", "term_slope", "skew_25d", "vrp")

FEE_FREE = IndianCostRates(
    brokerage_per_order=0.0,
    stt_sell_frac=0.0,
    exchange_txn_frac=0.0,
    gst_frac=0.0,
    sebi_frac=0.0,
    stamp_buy_frac=0.0,
    hedge_cost_frac=0.0,
)
PERMISSIVE_LIMITS = RiskLimits(
    max_abs_delta=1e9,
    max_abs_gamma=1e9,
    max_abs_vega=1e12,
    max_drawdown=1.0,
    max_concentration=1.0,
    margin_buffer=1.0,
)
BAND = BandParams(proportional_cost=0.0, risk_aversion=1.0, min_half_width=1.0, max_half_width=5.0)


def date_of(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp, tz=UTC).strftime("%Y-%m-%d")


def make_chain(
    spot: float,
    timestamp: float,
    atm_vol: float,
    *,
    crossed: bool = False,
    expiries_days: tuple[float, ...] = (30.0, 60.0),
) -> RawChain:
    """SABR-smile chain as RawQuotes: tight two-sided books, healthy volume/OI."""
    quotes: list[RawQuote] = []
    for tenor_days in expiries_days:
        expiry = tenor_days / 365.0
        forward = spot * math.exp(RATE * expiry)
        params = SABRParams(
            alpha=atm_vol, beta=1.0, rho=-0.3, nu=0.4, forward=forward, expiry=expiry
        )
        strikes = np.linspace(0.90, 1.10, 9) * spot
        vols = np.asarray(hagan_implied_vol(strikes, params))
        for strike, vol in zip(strikes, vols, strict=True):
            option_type = OptionType.CALL if strike >= spot else OptionType.PUT
            mid = float(bs_price(spot, float(strike), expiry, RATE, float(vol), option_type))
            half = max(0.005 * mid, 0.01)  # tight book; tiny floor for cheap wings
            bid, ask = mid - half, mid + half
            if crossed:
                bid, ask = ask, bid  # every book crossed -> the whole day is junk
            quotes.append(
                RawQuote(
                    strike=float(strike),
                    expiry=expiry,
                    option_type=option_type,
                    bid=bid,
                    ask=ask,
                    ltp=mid,
                    volume=1_000,
                    open_interest=10_000,
                    bid_qty=100,
                    ask_qty=100,
                    ltp_age_seconds=5.0,
                )
            )
    return RawChain(
        underlying=UNDERLYING, spot=spot, rate=RATE, timestamp=timestamp, quotes=tuple(quotes)
    )


def build_store(
    root,
    n_days: int = 12,
    *,
    seed: int = SEED,
    vrp: float = GEN_VRP,
    bad_day: int | None = None,
    intraday_extra_on: int | None = None,
    expiries_days: tuple[float, ...] = (30.0, 60.0),
) -> tuple[SnapshotStore, list[float]]:
    """Write n_days of EOD chains from a seeded GBM spot walk; return (store, spots)."""
    rng = np.random.default_rng(seed)
    store = SnapshotStore(root)
    dt = 1.0 / 252.0
    spot = 100.0
    spots: list[float] = []
    for i in range(n_days):
        spot *= math.exp(-0.5 * GEN_RV**2 * dt + GEN_RV * math.sqrt(dt) * rng.standard_normal())
        ts = T0 + i * DAY
        if intraday_extra_on == i:
            # An earlier same-date snapshot with a visibly different spot;
            # the replay must prefer the later (EOD) one.
            store.write(make_chain(spot * 1.05, ts - 3_600.0, GEN_RV + vrp))
        store.write(
            make_chain(spot, ts, GEN_RV + vrp, crossed=(i == bad_day), expiries_days=expiries_days)
        )
        spots.append(spot)
    return store, spots


class TestStoreReplayDays:
    def test_one_market_day_per_stored_date_with_finite_features(self, tmp_path):
        store, spots = build_store(tmp_path, n_days=12)
        replay = StoreReplay(store, UNDERLYING, rv_window=10)

        assert len(replay) == 12
        days = list(replay)
        assert [d.spot for d in days] == pytest.approx(spots)
        assert all(b.timestamp < a.timestamp for b, a in pairwise(days))
        for day in days:
            assert day.snapshot is not None
            assert day.surface is not None
            for key in FEATURE_KEYS:
                assert key in day.features
                assert np.isfinite(day.features[key]), f"{key} not finite"
            # ATM IV recovered from the fitted spline within a vol point of
            # the generating SABR ATM level.
            assert day.features["atm_iv"] == pytest.approx(GEN_RV + GEN_VRP, abs=0.01)
            assert np.isfinite(day.realized_vol) and day.realized_vol > 0.0
            assert day.features["vrp"] == pytest.approx(day.features["atm_iv"] - day.realized_vol)
            # rho = -0.3 generates equity-style put skew; the proxy must see it.
            assert day.features["skew_25d"] > 0.0
        assert replay.warnings == []

    def test_neutral_rv_prior_before_history_then_estimated(self, tmp_path):
        store, _ = build_store(tmp_path, n_days=12)
        days = list(StoreReplay(store, UNDERLYING, rv_window=10))
        # Fewer than 3 stored spots: realized_vol = atm_iv, so vrp is exactly 0.
        for day in days[:2]:
            assert day.realized_vol == pytest.approx(day.features["atm_iv"])
            assert day.features["vrp"] == 0.0
        # From day 3 on the close-to-close estimator runs on the actual walk.
        for day in days[2:]:
            assert day.realized_vol != pytest.approx(day.features["atm_iv"], abs=1e-6)

    def test_replay_supports_reiteration(self, tmp_path):
        store, _ = build_store(tmp_path, n_days=5)
        replay = StoreReplay(store, UNDERLYING)
        first = [d.timestamp for d in replay]
        second = [d.timestamp for d in replay]
        assert first == second and len(first) == 5

    def test_last_snapshot_of_a_date_wins(self, tmp_path):
        store, spots = build_store(tmp_path, n_days=8, intraday_extra_on=4)
        days = list(StoreReplay(store, UNDERLYING))
        assert len(days) == 8  # two snapshots on one date still yield one day
        assert days[4].spot == pytest.approx(spots[4])  # EOD spot, not the noon one

    def test_bad_day_is_skipped_with_warning(self, tmp_path):
        store, _ = build_store(tmp_path, n_days=12, bad_day=6)
        replay = StoreReplay(store, UNDERLYING, rv_window=10)
        assert len(replay) == 11
        bad_date = date_of(T0 + 6 * DAY)
        assert len(replay.warnings) == 1
        assert bad_date in replay.warnings[0]
        assert bad_date not in {date_of(d.timestamp) for d in replay}

    def test_single_expiry_chain_has_zero_term_slope(self, tmp_path):
        store, _ = build_store(tmp_path, n_days=3, expiries_days=(30.0,))
        days = list(StoreReplay(store, UNDERLYING))
        assert len(days) == 3
        assert all(day.features["term_slope"] == 0.0 for day in days)

    def test_essvi_surface_variant(self, tmp_path):
        store, _ = build_store(tmp_path, n_days=4)
        days = list(StoreReplay(store, UNDERLYING, surface="essvi"))
        assert len(days) == 4
        for day in days:
            # Joint SSVI fit is coarser than the spline; 2 vol points of slack.
            assert day.features["atm_iv"] == pytest.approx(GEN_RV + GEN_VRP, abs=0.02)

    def test_invalid_surface_kind_rejected(self, tmp_path):
        store, _ = build_store(tmp_path, n_days=3)
        with pytest.raises(ValueError, match="surface"):
            StoreReplay(store, UNDERLYING, surface="sabr")  # type: ignore[arg-type]

    def test_empty_store_yields_no_days_and_a_warning(self, tmp_path):
        replay = StoreReplay(SnapshotStore(tmp_path), UNDERLYING)
        assert len(replay) == 0
        assert any(UNDERLYING in w for w in replay.warnings)


class TestEndToEndHarness:
    def test_run_backtest_on_store_replay_completes_and_trades(self, tmp_path):
        """The identical walk-forward harness runs on captured Parquet history;
        with a planted 6-vol-point premium the VRP strategy must trade."""
        store, _ = build_store(tmp_path, n_days=12)
        replay = StoreReplay(store, UNDERLYING, rv_window=10)
        strategy = VRPStrategy(
            VRPConfig(entry_vrp_min=0.02),
            cost_model=IndianOptionsCostModel(FEE_FREE),
            lot_size=25,
        )
        config = BacktestConfig(
            risk_limits=PERMISSIVE_LIMITS,
            band_params=BAND,
            cost_model=IndianOptionsCostModel(FEE_FREE),
            lot_size=25,
            hedge_cost_frac=0.0,
            spread_frac=0.0,
        )
        result = run_backtest(strategy, replay, config)
        assert result.equity.shape == (len(replay),)
        assert np.all(np.isfinite(result.equity))
        assert np.isfinite(result.final_equity)
        # No assertion on P&L sign — real-ish data over 12 days — but the
        # planted premium must trigger entries (one straddle = 2 fills).
        assert result.n_trades >= 2

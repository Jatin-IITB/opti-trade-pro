"""Reconstructing historical chains from candles.

The reconstruction is the point where real-but-different data enters a
pipeline built for live quotes. These tests pin what it may infer (a price
from the bar containing the instant) and what it must refuse to invent (a
spread, a trade that never happened, a price carried backwards in time).
"""

from __future__ import annotations

from datetime import date, datetime
from typing import ClassVar
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from options_trading.services.backfill_service import (
    BackfillConfig,
    HistoricalChainBackfill,
    HistoricalContract,
    candles_to_epoch_frame,
    contracts_from_frame,
    reconstruct_chain,
    spot_lookup_from_candles,
)
from options_trading.services.capture_service import expiry_year_fraction
from optitrade.core.types import OptionType
from optitrade.data import SnapshotStore
from optitrade.data.models import ChainSource, RawChain, RawQuote
from optitrade.data.quote_filters import DEFAULT_FILTER_CONFIG, filter_chain

pytestmark = pytest.mark.unit

IST = ZoneInfo("Asia/Kolkata")
EXPIRY_DAY = date(2026, 9, 8)


def epoch(hour: int, minute: int, day: int = 4) -> float:
    return datetime(2026, 9, day, hour, minute, tzinfo=IST).timestamp()


def bars(*rows: tuple[float, float]) -> pd.DataFrame:
    """Candles indexed by epoch: ``(epoch, close)`` pairs."""
    return pd.DataFrame(
        {
            "close": [c for _, c in rows],
            "volume": [10] * len(rows),
            "open_interest": [99] * len(rows),
        },
        index=[e for e, _ in rows],
    )


CONTRACTS = [
    HistoricalContract("NSE_FO|CE24000", 24000.0, OptionType.CALL),
    HistoricalContract("NSE_FO|PE24000", 24000.0, OptionType.PUT),
]


class TestPriceSelection:
    def test_uses_the_bar_at_or_before_the_instant(self) -> None:
        """Not the nearest bar — the last one that had actually happened."""
        candles = {
            "NSE_FO|CE24000": bars(
                (epoch(14, 0), 100.0), (epoch(14, 15), 111.0), (epoch(14, 30), 999.0)
            ),
            "NSE_FO|PE24000": bars((epoch(14, 15), 50.0)),
        }

        chain, report = reconstruct_chain(
            "NIFTY", EXPIRY_DAY, epoch(14, 20), 24000.0, 0.065, CONTRACTS, candles
        )

        call = next(q for q in chain.quotes if q.option_type is OptionType.CALL)
        assert call.ltp == 111.0, "must not look ahead to the 14:30 bar"
        assert report.n_quotes == 2

    def test_a_contract_that_never_traded_is_skipped_not_filled(self) -> None:
        """Carrying a later price backwards would invent a trade."""
        candles = {
            "NSE_FO|CE24000": bars((epoch(14, 30), 100.0)),  # first trade is later
            "NSE_FO|PE24000": bars((epoch(14, 0), 50.0)),
        }

        chain, report = reconstruct_chain(
            "NIFTY", EXPIRY_DAY, epoch(14, 15), 24000.0, 0.065, CONTRACTS, candles
        )

        assert report.n_quotes == 1
        assert report.n_never_traded == 1
        assert all(q.option_type is OptionType.PUT for q in chain.quotes)

    def test_a_missing_contract_is_counted_not_crashed(self) -> None:
        chain, report = reconstruct_chain(
            "NIFTY", EXPIRY_DAY, epoch(14, 15), 24000.0, 0.065, CONTRACTS, {}
        )

        assert chain.quotes == ()
        assert report.n_never_traded == 2
        assert report.coverage == 0.0

    def test_a_bar_older_than_the_limit_is_dropped(self) -> None:
        """A wing whose last trade was hours ago is not a quote at this instant."""
        candles = {
            "NSE_FO|CE24000": bars((epoch(10, 0), 100.0)),
            "NSE_FO|PE24000": bars((epoch(14, 10), 50.0)),
        }

        _, report = reconstruct_chain(
            "NIFTY",
            EXPIRY_DAY,
            epoch(14, 15),
            24000.0,
            0.065,
            CONTRACTS,
            candles,
            max_bar_age_seconds=900.0,
        )

        assert report.n_too_stale == 1
        assert report.n_quotes == 1


class TestItRefusesToInventABook:
    """The spread is the thing a candle cannot tell you."""

    def test_bid_and_ask_are_the_traded_price_with_zero_spread(self) -> None:
        candles = {"NSE_FO|CE24000": bars((epoch(14, 10), 123.5))}

        chain, _ = reconstruct_chain(
            "NIFTY", EXPIRY_DAY, epoch(14, 15), 24000.0, 0.065, CONTRACTS[:1], candles
        )

        quote = chain.quotes[0]
        assert quote.bid == quote.ask == quote.ltp == 123.5
        assert quote.bid_qty == 0 and quote.ask_qty == 0

    def test_the_chain_is_tagged_backfill(self) -> None:
        """Provenance is carried, never inferred. Mixing the two silently is
        the failure this tag exists to make impossible."""
        candles = {"NSE_FO|CE24000": bars((epoch(14, 10), 123.5))}

        chain, _ = reconstruct_chain(
            "NIFTY", EXPIRY_DAY, epoch(14, 15), 24000.0, 0.065, CONTRACTS[:1], candles
        )

        assert chain.source is ChainSource.BACKFILL

    def test_the_staleness_filter_still_bites(self) -> None:
        """Spread filters go no-op on a book-less chain, so the age must be real.

        If ``ltp_age_seconds`` were left at 0.0 the hygiene pass would accept
        every backfilled quote unconditionally — a filter that cannot fail is
        not a filter.
        """
        candles = {
            "NSE_FO|CE24000": bars((epoch(14, 10), 100.0)),  # 5 min old: kept
            "NSE_FO|PE24000": bars((epoch(14, 8), 50.0)),  # 7 min old: too stale
        }

        chain, _ = reconstruct_chain(
            "NIFTY",
            EXPIRY_DAY,
            epoch(14, 15),
            24000.0,
            0.065,
            CONTRACTS,
            candles,
            max_bar_age_seconds=3600.0,  # let both through reconstruction
        )
        result = filter_chain(chain, DEFAULT_FILTER_CONFIG)  # 300s staleness limit

        assert len(chain.quotes) == 2
        assert len(result.clean) == 1, "the 7-minute-old quote must be filtered out"


class TestOrchestration:
    """The rules that decide what gets written, with the network injected out."""

    ZONE = IST
    DAYS: ClassVar[list[date]] = [date(2026, 9, 4)]

    def _backfill(self, tmp_path, *, candles=None, spot=24000.0, config=None, contracts=None):
        self.calls: list[str] = []

        def candles_fn(key, first, last):
            self.calls.append(key)
            if candles is None:
                return bars((epoch(14, 0), 100.0), (epoch(15, 20), 105.0))
            if key not in candles:
                raise RuntimeError("no candles")
            return candles[key]

        return HistoricalChainBackfill(
            SnapshotStore(tmp_path),
            config or BackfillConfig(snapshot_times=("15:25",)),
            contracts_fn=lambda _k, _e: contracts if contracts is not None else CONTRACTS,
            candles_fn=candles_fn,
            spot_fn=lambda _at: spot,
            sleep_fn=lambda _s: None,
        )

    def test_it_writes_a_backfilled_chain(self, tmp_path) -> None:
        results = self._backfill(tmp_path).run("2026-09-08", self.DAYS, self.ZONE)

        assert [r.written for r in results] == [True]
        stored = SnapshotStore(tmp_path).list_snapshots("NIFTY")
        assert len(stored) == 1
        assert SnapshotStore(tmp_path).read(stored[0]).source is ChainSource.BACKFILL

    def test_it_never_overwrites_a_live_captured_day(self, tmp_path) -> None:
        """A real book beats a reconstruction of one.

        Replacing quoted data with traded data would be the worst outcome of a
        tool whose entire purpose is adding history.
        """
        store = SnapshotStore(tmp_path)
        live = RawChain(
            underlying="NIFTY",
            spot=24000.0,
            rate=0.065,
            timestamp=epoch(15, 25),
            quotes=(RawQuote(24000.0, 0.01, OptionType.CALL, 99.0, 101.0, 100.0, 5, 5),),
            source=ChainSource.LIVE,
        )
        store.write(live)

        results = self._backfill(tmp_path).run("2026-09-08", self.DAYS, self.ZONE)

        assert [r.written for r in results] == [False]
        assert results[0].reason == "live capture exists"
        assert all(store.read(p).source is ChainSource.LIVE for p in store.list_snapshots("NIFTY"))

    def test_a_thin_chain_is_refused(self, tmp_path) -> None:
        """Three points make a confident-looking smile, not a surface."""
        many = [HistoricalContract(f"K{i}", 24000.0 + 100 * i, OptionType.CALL) for i in range(10)]
        backfill = self._backfill(
            tmp_path,
            contracts=many,
            candles={"K0": bars((epoch(15, 20), 100.0))},  # 1 of 10 traded
        )

        results = backfill.run("2026-09-08", self.DAYS, self.ZONE)

        assert [r.written for r in results] == [False]
        assert "coverage" in (results[0].reason or "")
        assert SnapshotStore(tmp_path).list_snapshots("NIFTY") == []

    def test_candles_are_fetched_once_per_contract_not_per_snapshot(self, tmp_path) -> None:
        """Four instants from one fetch. The alternative multiplies a
        150-request expiry by the sampling density, for data already in hand."""
        backfill = self._backfill(
            tmp_path,
            config=BackfillConfig(snapshot_times=("11:15", "13:00", "14:00", "15:25")),
        )

        backfill.run("2026-09-08", self.DAYS, self.ZONE)

        assert self.calls == [c.instrument_key for c in CONTRACTS], "one fetch per contract"

    def test_a_missing_spot_skips_rather_than_guesses(self, tmp_path) -> None:
        backfill = self._backfill(tmp_path, spot=None)

        results = backfill.run("2026-09-08", self.DAYS, self.ZONE)

        assert [r.reason for r in results] == ["no spot"]
        assert SnapshotStore(tmp_path).list_snapshots("NIFTY") == []

    def test_one_dead_contract_does_not_abandon_the_expiry(self, tmp_path) -> None:
        backfill = self._backfill(
            tmp_path,
            candles={"NSE_FO|CE24000": bars((epoch(15, 20), 100.0))},  # PE fetch raises
            config=BackfillConfig(snapshot_times=("15:25",), min_coverage=0.5),
        )

        results = backfill.run("2026-09-08", self.DAYS, self.ZONE)

        assert [r.written for r in results] == [True]
        assert results[0].coverage == pytest.approx(0.5)

    def test_no_contracts_returns_empty_rather_than_writing_nothing_silently(
        self, tmp_path
    ) -> None:
        backfill = self._backfill(tmp_path, contracts=[])

        assert backfill.run("2026-09-08", self.DAYS, self.ZONE) == []


class TestPayloadAdapters:
    """Turning the broker's shapes into the reconstruction's shapes."""

    def test_call_and_put_types_are_mapped(self) -> None:
        frame = pd.DataFrame(
            [
                {"instrument_key": "A", "strike_price": 24000, "instrument_type": "CE"},
                {"instrument_key": "B", "strike_price": 24000, "instrument_type": "PE"},
            ]
        )

        contracts = contracts_from_frame(frame)

        assert [c.option_type for c in contracts] == [OptionType.CALL, OptionType.PUT]
        assert [c.strike for c in contracts] == [24000.0, 24000.0]

    def test_an_untypeable_row_is_dropped_not_guessed(self) -> None:
        """Defaulting would put a put's premium on a call's strike, which does
        not fail loudly — it fits as a lopsided smile."""
        frame = pd.DataFrame(
            [
                {"instrument_key": "A", "strike_price": 24000, "instrument_type": "CE"},
                {"instrument_key": "B", "strike_price": 24000, "instrument_type": "XX"},
            ]
        )

        contracts = contracts_from_frame(frame)

        assert len(contracts) == 1
        assert contracts[0].instrument_key == "A"

    def test_a_naive_timestamp_index_is_read_as_exchange_time(self) -> None:
        """The 5h30m trap: a naive IST index read as UTC shifts every bar."""
        naive = pd.DataFrame({"close": [100.0]}, index=pd.to_datetime(["2026-09-04 14:10:00"]))

        converted = candles_to_epoch_frame(naive)

        assert float(converted.index[0]) == epoch(14, 10)

    def test_a_tz_aware_index_is_preserved(self) -> None:
        aware = pd.DataFrame(
            {"close": [100.0]},
            index=pd.to_datetime(["2026-09-04 14:10:00+05:30"]),
        )

        converted = candles_to_epoch_frame(aware)

        assert float(converted.index[0]) == epoch(14, 10)


class TestSpotLookup:
    FRAME = pd.DataFrame(
        {"close": [23900.0, 23950.0]},
        index=pd.to_datetime(["2026-09-04 14:00:00", "2026-09-04 14:10:00"]),
    )

    def test_uses_the_bar_at_or_before(self) -> None:
        lookup = spot_lookup_from_candles(self.FRAME, max_age_seconds=900.0)

        assert lookup(epoch(14, 12)) == 23950.0
        assert lookup(epoch(14, 5)) == 23900.0

    def test_returns_none_when_the_nearest_bar_is_too_old(self) -> None:
        """A chain anchored to a spot from hours earlier mislabels every
        moneyness while looking entirely reasonable."""
        lookup = spot_lookup_from_candles(self.FRAME, max_age_seconds=900.0)

        assert lookup(epoch(16, 0)) is None

    def test_returns_none_before_the_first_bar(self) -> None:
        lookup = spot_lookup_from_candles(self.FRAME, max_age_seconds=900.0)

        assert lookup(epoch(9, 30)) is None


class TestTimeToExpiry:
    def test_shares_the_live_capture_convention(self) -> None:
        """Two day-count conventions would show up as a vol move nobody made."""
        candles = {"NSE_FO|CE24000": bars((epoch(14, 10), 100.0))}
        at = epoch(14, 15)

        chain, _ = reconstruct_chain(
            "NIFTY", EXPIRY_DAY, at, 24000.0, 0.065, CONTRACTS[:1], candles
        )

        assert chain.quotes[0].expiry == pytest.approx(expiry_year_fraction(EXPIRY_DAY, at))
        assert chain.quotes[0].expiry > 0.0

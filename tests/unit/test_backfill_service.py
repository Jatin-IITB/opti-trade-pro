"""Reconstructing historical chains from candles.

The reconstruction is the point where real-but-different data enters a
pipeline built for live quotes. These tests pin what it may infer (a price
from the bar containing the instant) and what it must refuse to invent (a
spread, a trade that never happened, a price carried backwards in time).
"""

from __future__ import annotations

import math
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
    implied_forward_from_parity,
    implied_spot_from_parity,
    reconstruct_chain,
    spot_lookup_from_candles,
)
from options_trading.services.capture_service import expiry_year_fraction
from options_trading.utils.exceptions import DataQualityError
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

# A realistic strip: the spot anchor comes from put-call parity, which needs
# several complete call/put pairs, so a two-contract fixture is not a chain.
STRIKES = tuple(range(23800, 24300, 50))
PAIRED_CONTRACTS = [
    HistoricalContract(f"NSE_FO|{t.value[0].upper()}E{k}", float(k), t)
    for k in STRIKES
    for t in (OptionType.CALL, OptionType.PUT)
]


def paired_candles(at: float, forward: float = 24100.0, rate: float = 0.065) -> dict:
    """Candles whose closes satisfy parity against ``forward``."""
    out = {}
    for contract in PAIRED_CONTRACTS:
        diff = math.exp(-rate * 0.011) * (forward - contract.strike)
        put = 60.0
        price = put + diff if contract.option_type is OptionType.CALL else put
        out[contract.instrument_key] = bars((at, max(price, 0.05)))
    return out


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

        default_candles = paired_candles(epoch(15, 20))

        def candles_fn(key, first, last):
            self.calls.append(key)
            source = default_candles if candles is None else candles
            if key not in source:
                raise RuntimeError("no candles")
            return source[key]

        return HistoricalChainBackfill(
            SnapshotStore(tmp_path),
            config or BackfillConfig(snapshot_times=("15:25",)),
            contracts_fn=lambda _k, _e: contracts if contracts is not None else PAIRED_CONTRACTS,
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
        """A strip is not a surface, even when it can price its own forward.

        Enough pairs trade to imply a spot, so this isolates the coverage
        floor rather than tripping the parity guard first.
        """
        traded = paired_candles(epoch(15, 20))
        wide = PAIRED_CONTRACTS + [
            HistoricalContract(f"FAR{i}", 25000.0 + 100 * i, OptionType.CALL) for i in range(30)
        ]
        backfill = self._backfill(tmp_path, contracts=wide, candles=traded)

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

        assert self.calls == [c.instrument_key for c in PAIRED_CONTRACTS], "one fetch per contract"

    def test_an_absent_index_feed_no_longer_blocks_a_chain(self, tmp_path) -> None:
        """The index feed is a cross-check, not the anchor.

        It was demoted after being measured going stale for minutes at a time,
        so its absence must not stop a chain the options can price themselves.
        """
        backfill = self._backfill(tmp_path, spot=None)

        results = backfill.run("2026-09-08", self.DAYS, self.ZONE)

        assert [r.written for r in results] == [True]
        stored = SnapshotStore(tmp_path).list_snapshots("NIFTY")
        assert SnapshotStore(tmp_path).read(stored[0]).spot > 0.0

    def test_too_few_pairs_is_reported_not_written(self, tmp_path) -> None:
        """Calls only: parity has nothing to solve, so no spot can be implied."""
        calls_only = [c for c in PAIRED_CONTRACTS if c.option_type is OptionType.CALL]
        backfill = self._backfill(
            tmp_path,
            contracts=calls_only,
            candles={c.instrument_key: bars((epoch(15, 20), 100.0)) for c in calls_only},
        )

        results = backfill.run("2026-09-08", self.DAYS, self.ZONE)

        assert [r.written for r in results] == [False]
        assert "No usable spot" in (results[0].reason or "")
        assert SnapshotStore(tmp_path).list_snapshots("NIFTY") == []

    def test_one_dead_contract_does_not_abandon_the_expiry(self, tmp_path) -> None:
        partial = paired_candles(epoch(15, 20))
        partial.pop(PAIRED_CONTRACTS[0].instrument_key)  # that fetch raises
        backfill = self._backfill(
            tmp_path,
            candles=partial,
            config=BackfillConfig(snapshot_times=("15:25",), min_coverage=0.5),
        )

        results = backfill.run("2026-09-08", self.DAYS, self.ZONE)

        assert [r.written for r in results] == [True]
        assert results[0].coverage == pytest.approx(1 - 1 / len(PAIRED_CONTRACTS))

    def test_no_contracts_returns_empty_rather_than_writing_nothing_silently(
        self, tmp_path
    ) -> None:
        backfill = self._backfill(tmp_path, contracts=[])

        assert backfill.run("2026-09-08", self.DAYS, self.ZONE) == []


class TestImpliedSpotFromParity:
    """The spot anchor comes from the options, not the index feed.

    Measured against real 2026-09 data, Upstox's index minute candles hold one
    value for minutes and then jump — leaving an ~80 point error that did not
    decay even five minutes before expiry, where the basis must be zero. The
    options were internally consistent to ~1.2 points across 27 strikes, so
    parity is both the more accurate anchor and a self-checking one.

    This matters beyond tidiness: spot anchors moneyness and the forward, so
    an 80-point error biases every implied vol in the reconstructed history.
    """

    RATE = 0.065
    T = 0.011

    def _pair(self, strike: float, forward: float, extra_call: float = 0.0):
        """Quotes that satisfy C - P = e^{-rT}(F - K) exactly."""
        diff = math.exp(-self.RATE * self.T) * (forward - strike)
        put = 50.0
        call = put + diff + extra_call
        return [
            RawQuote(strike, self.T, OptionType.CALL, call, call, call, 1, 1),
            RawQuote(strike, self.T, OptionType.PUT, put, put, put, 1, 1),
        ]

    def test_it_recovers_a_known_forward(self) -> None:
        quotes = [q for k in range(23800, 24300, 50) for q in self._pair(k, 24223.32)]

        assert implied_forward_from_parity(quotes, self.RATE) == pytest.approx(24223.32, abs=0.01)

    def test_the_median_survives_one_bad_print(self) -> None:
        """An illiquid wing with a stale close moves a mean, not a median."""
        quotes = [q for k in range(23800, 24300, 50) for q in self._pair(k, 24223.32)]
        quotes += self._pair(24350, 24223.32, extra_call=500.0)  # nonsense strike

        assert implied_forward_from_parity(quotes, self.RATE) == pytest.approx(24223.32, abs=0.01)

    def test_too_few_pairs_returns_none(self) -> None:
        """A forward from two pairs is a guess wearing a number's clothing."""
        quotes = [q for k in (24000, 24050) for q in self._pair(k, 24223.32)]

        assert implied_forward_from_parity(quotes, self.RATE) is None

    def test_the_spot_undoes_the_carry(self) -> None:
        """Storing the forward would compound carry twice downstream, since
        consumers rebuild F = S*exp((r-q)T) from this field."""
        quotes = [q for k in range(23800, 24300, 50) for q in self._pair(k, 24223.32)]

        spot = implied_spot_from_parity(quotes, self.RATE)

        assert spot is not None
        assert spot * math.exp(self.RATE * self.T) == pytest.approx(24223.32, abs=0.01)

    def test_reconstruct_derives_spot_when_none_is_given(self) -> None:
        """The forward the candles were built around comes back out."""
        candles = paired_candles(epoch(14, 10), forward=24100.0)

        chain, _ = reconstruct_chain(
            "NIFTY", EXPIRY_DAY, epoch(14, 15), None, 0.065, PAIRED_CONTRACTS, candles
        )

        implied_forward = chain.spot * math.exp(0.065 * chain.quotes[0].expiry)
        assert implied_forward == pytest.approx(24100.0, abs=1.0)

    def test_it_fails_closed_when_the_spot_cannot_be_implied(self) -> None:
        """Matching the live path: no trustworthy spot means no chain at all,
        rather than a chain built around a guess."""
        candles = {"NSE_FO|CE24000": bars((epoch(14, 10), 150.0))}

        with pytest.raises(DataQualityError, match="No usable spot"):
            reconstruct_chain(
                "NIFTY", EXPIRY_DAY, epoch(14, 15), None, 0.065, CONTRACTS[:1], candles
            )


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

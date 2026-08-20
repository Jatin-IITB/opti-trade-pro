"""Tests for the Parquet snapshot store."""

import dataclasses

import pytest

from optitrade.data import RawChain, SnapshotStore, SyntheticSource

# 1_700_000_000 epoch seconds == 2023-11-14 22:13:20 UTC — fixed, no wall clock.
T0 = 1_700_000_000.0
T0_PLUS_1H = 1_700_003_600.0  # 2023-11-14 23:13:20 UTC, same day
T0_PLUS_25H = 1_700_090_000.0  # 2023-11-15 23:13:20 UTC, next day

pytestmark = pytest.mark.unit


class TestRoundTrip:
    def test_write_read_round_trip_preserves_chain_exactly(self, tmp_path):
        chain = SyntheticSource(timestamp=T0).fetch_chain("NIFTY")
        store = SnapshotStore(tmp_path)
        path = store.write(chain)
        assert path == tmp_path / "NIFTY" / "2023-11-14" / "221320.parquet"
        assert path.is_file()
        restored = store.read(path)
        # Frozen-dataclass equality: exact float64 comparison on every field
        # of every quote plus the chain-level fields.
        assert restored == chain

    def test_empty_chain_is_rejected(self, tmp_path):
        empty = RawChain(underlying="NIFTY", spot=24_500.0, rate=0.065, timestamp=T0, quotes=())
        with pytest.raises(ValueError, match="0 quotes"):
            SnapshotStore(tmp_path).write(empty)


class TestListing:
    def test_list_snapshots_sorted_across_writes(self, tmp_path):
        early = SyntheticSource(timestamp=T0).fetch_chain("NIFTY")
        late = dataclasses.replace(early, timestamp=T0_PLUS_1H)
        next_day = dataclasses.replace(early, timestamp=T0_PLUS_25H)
        store = SnapshotStore(tmp_path)
        # Write out of chronological order; listing must still sort by time.
        path_next_day = store.write(next_day)
        path_late = store.write(late)
        path_early = store.write(early)
        assert store.list_snapshots("NIFTY") == [path_early, path_late, path_next_day]
        assert store.list_snapshots("NIFTY", date="2023-11-14") == [path_early, path_late]
        assert store.list_snapshots("NIFTY", date="2023-11-15") == [path_next_day]

    def test_missing_underlying_or_day_lists_empty(self, tmp_path):
        store = SnapshotStore(tmp_path)
        assert store.list_snapshots("BANKNIFTY") == []
        store.write(SyntheticSource(timestamp=T0).fetch_chain("NIFTY"))
        assert store.list_snapshots("NIFTY", date="1999-01-01") == []


class TestReadDay:
    def test_read_day_returns_chains_in_time_order(self, tmp_path):
        base = SyntheticSource(timestamp=T0).fetch_chain("NIFTY")
        second = dataclasses.replace(base, timestamp=T0_PLUS_1H)
        store = SnapshotStore(tmp_path)
        store.write(second)
        store.write(base)
        chains = store.read_day("NIFTY", "2023-11-14")
        assert chains == [base, second]
        assert store.read_day("NIFTY", "2023-11-15") == []

"""Tests for the Parquet snapshot store."""

import dataclasses

import pandas as pd
import pytest

from optitrade.data import RawChain, SnapshotStore, SyntheticSource
from optitrade.data.models import ChainSource

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


class TestProvenance:
    """A backfilled chain must never come back looking captured.

    Reconstructed chains have no book — bid and ask are both the traded price
    — so a consumer that mistook one for a live capture would read a zero
    spread as an infinitely liquid market. The tag is the only thing telling
    them apart, so it has to survive the round trip.
    """

    @pytest.mark.parametrize("source", [ChainSource.LIVE, ChainSource.BACKFILL])
    def test_source_round_trips(self, tmp_path, source):
        chain = dataclasses.replace(
            SyntheticSource(timestamp=T0).fetch_chain("NIFTY"), source=source
        )
        store = SnapshotStore(tmp_path)

        restored = store.read(store.write(chain))

        assert restored.source is source
        assert restored == chain

    def test_a_v1_snapshot_still_reads_as_live(self, tmp_path):
        """The captures already on disk predate provenance; refusing them would
        strand every day of history gathered before this change.

        v1 had exactly one writer — the live capture path — so reading those
        files as LIVE restates a fact rather than assuming a default.
        """
        chain = SyntheticSource(timestamp=T0).fetch_chain("NIFTY")
        store = SnapshotStore(tmp_path)
        path = store.write(chain)
        # Rewrite as the old schema: version 1, no source column.
        frame = pd.read_parquet(path, engine="pyarrow").drop(columns=["source"])
        frame["schema_version"] = 1
        frame.to_parquet(path, engine="pyarrow", index=False)

        restored = store.read(path)

        assert restored.source is ChainSource.LIVE
        assert restored.quotes == chain.quotes

    def test_an_unknown_future_version_is_still_refused(self, tmp_path):
        """Back-compat must not degrade into accepting anything."""
        chain = SyntheticSource(timestamp=T0).fetch_chain("NIFTY")
        store = SnapshotStore(tmp_path)
        path = store.write(chain)
        frame = pd.read_parquet(path, engine="pyarrow")
        frame["schema_version"] = 99
        frame.to_parquet(path, engine="pyarrow", index=False)

        with pytest.raises(ValueError, match="schema_version 99"):
            store.read(path)


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

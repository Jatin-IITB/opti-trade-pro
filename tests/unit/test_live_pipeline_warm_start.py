"""Warm-starting the dashboard from the snapshot store.

The live payload lives only in process memory, so restarting the server
discards it while the Parquet captures survive on disk. Outside market hours
the next capture that could refill it is a trading day away, so the dashboard
showed nothing at all with a full chain sitting unread in the store.

These tests pin the three halves of the contract that make showing it safe:
the restored payload is flagged not-live everywhere it is serialised, its
spot is withheld from the path that prices the user's book, and the sentence
describing it never overstates which session's prices it holds.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock
from zoneinfo import ZoneInfo

import pytest

from options_trading.services.capture_scheduler import ScheduleConfig
from options_trading.services.live_analytics import LiveDashboardPayload
from options_trading.services.live_pipeline import (
    LivePipelineConfig,
    LivePipelineService,
    describe_stored_snapshot,
)
from optitrade.data import SnapshotStore
from optitrade.data.capture import SyntheticSource

IST = ZoneInfo("Asia/Kolkata")
SCHEDULE = ScheduleConfig()


def ist(year: int, month: int, day: int, hour: int, minute: int) -> float:
    """A fixed IST instant as epoch seconds. No wall clock in these tests."""
    return datetime(year, month, day, hour, minute, tzinfo=IST).timestamp()


pytestmark = pytest.mark.unit


@pytest.fixture()
def store(tmp_path: Path) -> SnapshotStore:
    """A store holding two captures, so "newest" is a real choice."""
    store = SnapshotStore(tmp_path)
    source = SyntheticSource(seed=42)
    earlier = source.fetch_chain("NIFTY")
    # Same seed, so only the instant and spot differ — a later snapshot the
    # warm start must prefer over the earlier one.
    later = type(earlier)(
        underlying=earlier.underlying,
        spot=earlier.spot + 100.0,
        rate=earlier.rate,
        timestamp=earlier.timestamp + 3600.0,
        quotes=earlier.quotes,
        dividend_yield=earlier.dividend_yield,
    )
    store.write(earlier)
    store.write(later)
    return store


def build_pipeline(store: SnapshotStore | None) -> LivePipelineService:
    ws_manager = MagicMock()
    ws_manager.send_dashboard_update = AsyncMock(return_value=0)
    return LivePipelineService(
        ws_manager=ws_manager,
        config=LivePipelineConfig(underlying="NIFTY"),
        store=store,
    )


class TestWarmStart:
    def test_restores_the_newest_capture(self, store: SnapshotStore) -> None:
        pipeline = build_pipeline(store)

        assert asyncio.run(pipeline.warm_start_from_store()) is True

        snapshot = pipeline.get_latest_snapshot()
        assert snapshot is not None
        newest = store.read(store.list_snapshots("NIFTY")[-1])
        assert snapshot.spot == pytest.approx(newest.spot)
        assert snapshot.timestamp == pytest.approx(newest.timestamp)

    def test_the_restored_payload_is_flagged_not_live(self, store: SnapshotStore) -> None:
        """The flag is what separates this from presenting old data as current."""
        pipeline = build_pipeline(store)
        asyncio.run(pipeline.warm_start_from_store())

        snapshot = pipeline.get_latest_snapshot()
        assert snapshot is not None
        assert snapshot.is_live is False
        assert snapshot.as_of_note is not None
        assert snapshot.as_of_note != ""

    def test_the_wire_format_carries_the_freshness(self, store: SnapshotStore) -> None:
        """Both delivery paths serialise through to_wire_dict, so it must say so.

        A stale payload that reached the browser without these keys would
        render identically to a live one.
        """
        pipeline = build_pipeline(store)
        asyncio.run(pipeline.warm_start_from_store())
        snapshot = pipeline.get_latest_snapshot()
        assert snapshot is not None

        wire = snapshot.to_wire_dict()

        assert wire["isLive"] is False
        assert wire["asOfNote"]

    def test_a_live_payload_says_so_on_the_wire(self) -> None:
        """The default must be explicit, because the frontend fails closed.

        The client treats anything other than an explicit true as not current,
        so a live payload that omitted the key would wear a "market closed"
        notice forever.
        """
        wire = LiveDashboardPayload(spot=24500.0).to_wire_dict()

        assert wire["isLive"] is True
        assert wire["asOfNote"] is None

    def test_an_empty_store_leaves_the_dashboard_empty(self, tmp_path: Path) -> None:
        pipeline = build_pipeline(SnapshotStore(tmp_path / "empty"))

        assert asyncio.run(pipeline.warm_start_from_store()) is False
        assert pipeline.get_latest_snapshot() is None

    def test_no_store_is_not_an_error(self) -> None:
        """Warm start is optional; a pipeline without a store still serves."""
        pipeline = build_pipeline(None)

        assert asyncio.run(pipeline.warm_start_from_store()) is False
        assert pipeline.get_latest_snapshot() is None

    def test_an_unreadable_store_does_not_stop_the_app(self, tmp_path: Path) -> None:
        """Best effort: a corrupt snapshot must not prevent boot."""
        root = tmp_path / "corrupt"
        (root / "NIFTY" / "2026-09-04").mkdir(parents=True)
        (root / "NIFTY" / "2026-09-04" / "070217.parquet").write_bytes(b"not parquet")
        pipeline = build_pipeline(SnapshotStore(root))

        assert asyncio.run(pipeline.warm_start_from_store()) is False
        assert pipeline.get_latest_snapshot() is None


class TestTheWordingNeverOverstates:
    """What the notice may and may not claim.

    A closed market is the normal state for most of the week, so the notice
    reads as a dateline rather than a fault. That makes the precision matter
    more, not less: "closing prices" is a specific claim, and a capture taken
    87 minutes before the bell does not support it.
    """

    # 2026-09-04 is a Friday; the NSE session is 09:15-15:30 IST.
    SATURDAY = ist(2026, 9, 5, 11, 0)
    MID_SESSION = ist(2026, 9, 4, 12, 0)

    def test_a_capture_at_the_close_may_be_called_closing_prices(self) -> None:
        note = describe_stored_snapshot(ist(2026, 9, 4, 15, 22), self.SATURDAY, SCHEDULE)

        assert "closing prices" in note
        assert "15:22" in note

    def test_a_capture_long_before_the_close_may_not(self) -> None:
        """The live case. The real 2026-09-04 capture landed at 14:02:38 and is
        described as 87 minutes early; this pins the round 14:02:00 at 88.
        """
        note = describe_stored_snapshot(ist(2026, 9, 4, 14, 2), self.SATURDAY, SCHEDULE)

        assert "closing prices" not in note
        assert "last prices captured" in note
        assert "88 minutes before the 15:30 close" in note

    def test_the_boundary_is_one_capture_interval(self) -> None:
        """Inside one interval nothing further was due, so it is the last word.

        Pinned at both sides of the boundary: a rule stated only on the side
        that passes is a rule that can silently move.
        """
        interval_minutes = SCHEDULE.interval_seconds // 60
        close = ist(2026, 9, 4, 15, 30)

        inside = describe_stored_snapshot(close - interval_minutes * 60, self.SATURDAY, SCHEDULE)
        outside = describe_stored_snapshot(
            close - (interval_minutes + 1) * 60, self.SATURDAY, SCHEDULE
        )

        assert "closing prices" in inside
        assert "closing prices" not in outside

    def test_during_a_session_it_says_the_session_has_not_captured_yet(self) -> None:
        """Mid-session, "market closed" would be plainly false."""
        note = describe_stored_snapshot(ist(2026, 9, 3, 15, 25), self.MID_SESSION, SCHEDULE)

        assert "Market closed" not in note
        assert "No capture has run yet this session" in note

    def test_the_instant_is_stated_in_exchange_time(self) -> None:
        """IST, not the server's or the browser's zone, and never relative.

        "2 days ago" is a phrase a reader rounds off; a dated timestamp in the
        exchange's own timezone is one they check against the session they
        meant to look at.
        """
        note = describe_stored_snapshot(ist(2026, 9, 4, 14, 2), self.SATURDAY, SCHEDULE)

        assert "Fri 4 Sep 2026, 14:02 IST" in note
        assert "ago" not in note


class TestStaleSpotNeverPricesTheBook:
    """The money path may not consume a warm-started spot.

    ``get_latest_spot`` feeds portfolio Greeks and moneyness. A previous
    session's underlying would relabel every moneyness and misstate every
    Greek while looking entirely normal — the same class of error as pricing
    a 24500 strike against a spot of a few thousand. Display may be stale;
    pricing may not.
    """

    def test_a_warm_started_spot_is_withheld(self, store: SnapshotStore) -> None:
        pipeline = build_pipeline(store)
        asyncio.run(pipeline.warm_start_from_store())

        snapshot = pipeline.get_latest_snapshot()
        assert snapshot is not None
        assert snapshot.spot > 0, "the payload really does carry a usable spot"
        assert pipeline.get_latest_spot() is None, "yet the pricing path must not see it"

    def test_a_live_capture_does_supply_the_spot(self, store: SnapshotStore) -> None:
        """The guard must not disable the live path it protects."""
        pipeline = build_pipeline(store)
        asyncio.run(pipeline.warm_start_from_store())
        assert pipeline.get_latest_spot() is None

        pipeline._last_payload = LiveDashboardPayload(spot=24500.0, is_live=True)

        assert pipeline.get_latest_spot() == pytest.approx(24500.0)


class TestWarmStartDoesNotArmTheBroadcast:
    def test_the_stale_chain_is_not_cached_for_on_capture(self, store: SnapshotStore) -> None:
        """``on_capture`` rebuilds from ``_last_chain``, not from its argument.

        Seeding that field during warm start would let a capture cycle that
        never called ``cache_chain`` rebroadcast this stale chain as live.
        """
        pipeline = build_pipeline(store)
        asyncio.run(pipeline.warm_start_from_store())

        assert pipeline._last_chain is None

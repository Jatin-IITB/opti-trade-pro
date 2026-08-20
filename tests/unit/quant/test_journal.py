"""Tests for the append-only JSONL event journal."""

from __future__ import annotations

import json
import uuid

import pytest

from optitrade.core.errors import JournalError
from optitrade.journal import Event, EventLog


class TestAppendReplay:
    def test_round_trip_preserves_every_field(self, tmp_path):
        log = EventLog(tmp_path, "run-a")
        first = log.append("order_submitted", {"symbol": "NIFTY", "qty": 10})
        second = log.append(
            "fill", {"px": 101.5, "nested": {"venue": "NSE"}}, correlation_id="cid-1"
        )

        replayed = list(log.replay())
        assert replayed == [first, second]
        assert replayed[1].data == {"px": 101.5, "nested": {"venue": "NSE"}}

    def test_sequences_are_monotonically_increasing_from_one(self, tmp_path):
        log = EventLog(tmp_path, "run-a")
        events = [log.append("tick", {"i": i}) for i in range(5)]
        assert [e.sequence for e in events] == [1, 2, 3, 4, 5]

    def test_auto_correlation_id_is_a_fresh_uuid(self, tmp_path):
        log = EventLog(tmp_path, "run-a")
        first = log.append("a", {})
        second = log.append("b", {})
        assert first.correlation_id != second.correlation_id
        uuid.UUID(first.correlation_id)  # parses as a UUID

    def test_explicit_correlation_id_is_preserved(self, tmp_path):
        log = EventLog(tmp_path, "run-a")
        event = log.append("a", {}, correlation_id="review-42")
        assert event.correlation_id == "review-42"

    def test_writes_to_run_id_named_file(self, tmp_path):
        log = EventLog(tmp_path, "run-xyz")
        log.append("a", {})
        assert (tmp_path / "run-xyz.jsonl").exists()

    def test_replay_of_missing_file_is_empty(self, tmp_path):
        log = EventLog(tmp_path, "never-written")
        assert list(log.replay()) == []

    def test_unserialisable_data_raises_journal_error_and_writes_nothing(self, tmp_path):
        log = EventLog(tmp_path, "run-a")
        with pytest.raises(JournalError):
            log.append("bad", {"payload": object()})
        assert list(log.replay()) == []
        assert log.append("good", {}).sequence == 1  # sequence not burned

    def test_frozen_event_round_trips_via_json_line(self):
        event = Event(
            sequence=7, event_type="x", timestamp=123.456, correlation_id="c", data={"k": 1}
        )
        assert Event.from_json_line(event.to_json_line()) == event


class TestSequenceRecovery:
    def test_reopening_resumes_after_max_existing_sequence(self, tmp_path):
        first_session = EventLog(tmp_path, "run-a")
        for i in range(3):
            first_session.append("tick", {"i": i})

        reopened = EventLog(tmp_path, "run-a")
        event = reopened.append("tick", {"i": 3})
        assert event.sequence == 4
        assert [e.sequence for e in reopened.replay()] == [1, 2, 3, 4]

    def test_recovery_tolerates_a_torn_final_line(self, tmp_path):
        log = EventLog(tmp_path, "run-a")
        log.append("tick", {})
        log.append("tick", {})
        # Simulate a crash mid-write: a partial line with no closing brace.
        with (tmp_path / "run-a.jsonl").open("a", encoding="utf-8") as fh:
            fh.write('{"sequence": 3, "event_type": "tick"')

        recovered = EventLog(tmp_path, "run-a")
        assert recovered.append("tick", {}).sequence == 3


class TestReplayCorruption:
    def test_corrupt_line_raises_journal_error_naming_the_line(self, tmp_path):
        log = EventLog(tmp_path, "run-a")
        log.append("tick", {})
        log.append("tick", {})
        with (tmp_path / "run-a.jsonl").open("a", encoding="utf-8") as fh:
            fh.write("this is not json\n")

        with pytest.raises(JournalError, match="line 3"):
            list(log.replay())

    def test_corrupt_middle_line_reports_its_own_number(self, tmp_path):
        log = EventLog(tmp_path, "run-a")
        good = log.append("tick", {}).to_json_line()
        path = tmp_path / "run-a.jsonl"
        path.write_text(good + "\n{{{garbage\n" + good + "\n", encoding="utf-8")

        with pytest.raises(JournalError, match="line 2"):
            list(log.replay())

    def test_valid_json_missing_fields_is_corrupt(self, tmp_path):
        path = tmp_path / "run-a.jsonl"
        path.write_text(json.dumps({"sequence": 1}) + "\n", encoding="utf-8")
        log = EventLog(tmp_path, "run-a")
        with pytest.raises(JournalError, match="line 1"):
            list(log.replay())


class TestCorrelationFiltering:
    def test_events_by_correlation_returns_only_matching_in_order(self, tmp_path):
        log = EventLog(tmp_path, "run-a")
        a1 = log.append("start", {"n": 1}, correlation_id="cid-a")
        log.append("start", {"n": 2}, correlation_id="cid-b")
        a2 = log.append("end", {"n": 3}, correlation_id="cid-a")

        assert log.events_by_correlation("cid-a") == [a1, a2]
        assert log.events_by_correlation("cid-missing") == []

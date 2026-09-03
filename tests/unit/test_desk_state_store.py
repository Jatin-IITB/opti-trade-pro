"""Tests for the paper desk's persisted state.

The desk is the only stateful panel in the app: its fills mutate a book the
next cycle trades against. Two properties matter more than any display
concern, and both are asserted here — a book survives a restart byte for
byte, and state that cannot be parsed is reported rather than replaced with
an empty book that would read as a desk holding nothing.
"""

from __future__ import annotations

import json

import pytest

from options_trading.services.desk_state_store import (
    SCHEMA_VERSION,
    CycleRecord,
    DeskState,
    DeskStateError,
    DeskStateStore,
)
from optitrade.core.types import OptionContract, OptionType, Portfolio, Position

pytestmark = pytest.mark.unit


def make_position(symbol: str = "NIFTY-C-23900", quantity: float = -2.0) -> Position:
    return Position(
        contract=OptionContract(
            symbol=symbol,
            strike=23_900.0,
            expiry=0.0821917808,
            option_type=OptionType.CALL,
            lot_size=75,
        ),
        quantity=quantity,
        entry_price=142.35,
    )


def make_record(date: str = "2026-09-03", correlation_id: str = "abc-123") -> CycleRecord:
    return CycleRecord(
        date=date,
        timestamp=1_772_000_000.0,
        action="enter",
        action_taken="enter: 2 filled, 0 rejected; hedge rebalance",
        n_fills=2,
        n_rejected=0,
        equity=999_863.37,
        cash=1_054_539.61,
        drawdown=0.000136,
        delta=-5.58,
        gamma=-22.6,
        vega=-4606.7,
        theta=6813.4,
        hedge_action="rebalance",
        halted=False,
        correlation_id=correlation_id,
        fills=({"symbol": "NIFTY-C-23900", "quantity": -2.0, "price": 142.35},),
        rejected=(),
    )


def make_state() -> DeskState:
    position = make_position()
    return DeskState(
        journal_run_id="desk",
        book=(position,),
        portfolio=Portfolio(
            positions=(position,),
            cash=1_054_539.61,
            equity=999_863.37,
            high_water_mark=1_000_000.0,
            margin_available=1_000_000.0,
        ),
        processed_dates=("2026-09-02", "2026-09-03"),
        cycles=(make_record(),),
    )


class TestRoundTrip:
    def test_absent_file_is_a_fresh_desk_not_an_error(self, tmp_path):
        """No file means "never run", which is a legitimate state."""
        store = DeskStateStore(tmp_path / "desk_state.json")

        assert store.exists() is False
        assert store.load() is None

    def test_book_survives_a_save_and_load(self, tmp_path):
        store = DeskStateStore(tmp_path / "desk_state.json")
        store.save(make_state())

        loaded = store.load()

        assert loaded is not None
        assert loaded.book == make_state().book
        assert loaded.portfolio == make_state().portfolio
        assert loaded.processed_dates == ("2026-09-02", "2026-09-03")

    def test_contract_identity_is_preserved_exactly(self, tmp_path):
        """A lot size or option type that drifts silently misprices the book."""
        store = DeskStateStore(tmp_path / "desk_state.json")
        store.save(make_state())

        contract = store.load().book[0].contract

        assert contract.symbol == "NIFTY-C-23900"
        assert contract.strike == pytest.approx(23_900.0)
        assert contract.expiry == pytest.approx(0.0821917808)
        assert contract.option_type is OptionType.CALL
        assert contract.lot_size == 75

    def test_cycle_records_survive_with_their_correlation_ids(self, tmp_path):
        """The correlation id is the only join back to the decision trail."""
        store = DeskStateStore(tmp_path / "desk_state.json")
        store.save(make_state())

        cycles = store.load().cycles

        assert len(cycles) == 1
        assert cycles[0].correlation_id == "abc-123"
        assert cycles[0].action_taken.startswith("enter:")
        assert cycles[0].fills[0]["symbol"] == "NIFTY-C-23900"

    def test_save_is_atomic_and_leaves_no_temporary(self, tmp_path):
        path = tmp_path / "desk_state.json"
        DeskStateStore(path).save(make_state())

        assert path.exists()
        assert list(tmp_path.glob("*.tmp")) == []

    def test_an_empty_book_round_trips_as_an_empty_book(self, tmp_path):
        """A measured flat book is real data and must persist as itself."""
        store = DeskStateStore(tmp_path / "desk_state.json")
        store.save(DeskState(journal_run_id="desk", processed_dates=("2026-09-03",)))

        loaded = store.load()

        assert loaded is not None
        assert loaded.book == ()
        assert loaded.processed_dates == ("2026-09-03",)


class TestFailsClosed:
    """Unreadable state must never present as an empty desk."""

    def test_unparseable_json_raises_rather_than_resetting(self, tmp_path):
        path = tmp_path / "desk_state.json"
        path.write_text("{not json", encoding="utf-8")

        with pytest.raises(DeskStateError, match="unreadable"):
            DeskStateStore(path).load()

    def test_a_json_scalar_is_rejected(self, tmp_path):
        path = tmp_path / "desk_state.json"
        path.write_text("42", encoding="utf-8")

        with pytest.raises(DeskStateError, match="not a JSON object"):
            DeskStateStore(path).load()

    def test_an_unknown_schema_version_is_rejected(self, tmp_path):
        """A future build's file must not be read with this build's meaning."""
        path = tmp_path / "desk_state.json"
        path.write_text(json.dumps({"schema_version": SCHEMA_VERSION + 1}), encoding="utf-8")

        with pytest.raises(DeskStateError, match="schema_version"):
            DeskStateStore(path).load()

    def test_a_truncated_object_is_rejected(self, tmp_path):
        """Valid JSON missing the book is still not a desk state."""
        path = tmp_path / "desk_state.json"
        path.write_text(
            json.dumps({"schema_version": SCHEMA_VERSION, "journal_run_id": "desk"}),
            encoding="utf-8",
        )

        with pytest.raises(DeskStateError, match="malformed"):
            DeskStateStore(path).load()

    def test_a_position_missing_its_contract_is_rejected(self, tmp_path):
        path = tmp_path / "desk_state.json"
        path.write_text(
            json.dumps(
                {
                    "schema_version": SCHEMA_VERSION,
                    "journal_run_id": "desk",
                    "processed_dates": [],
                    "portfolio": {
                        "cash": 1.0,
                        "equity": 1.0,
                        "high_water_mark": 1.0,
                        "margin_available": 1.0,
                    },
                    "book": [{"quantity": -2.0, "entry_price": 1.0}],
                    "cycles": [],
                }
            ),
            encoding="utf-8",
        )

        with pytest.raises(DeskStateError, match="malformed"):
            DeskStateStore(path).load()

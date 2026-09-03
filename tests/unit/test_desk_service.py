"""Tests for the paper-desk service.

Deterministic throughout: the market is built from ``SyntheticSource`` with a
fixed seed and written to a real Parquet store, so these exercise the same
``StoreReplay`` path the deployed desk uses without touching a network or a
clock.

The properties under test are the ones that make a *stateful* panel safe:
advancing is idempotent, the book survives a restart, an engaged kill switch
stops the desk dead, and every failure reports itself rather than showing an
empty book.
"""

from __future__ import annotations

import pathlib
from typing import ClassVar

import pytest

from options_trading.services.desk_service import (
    MIN_DAYS_DESK,
    DeskService,
    DeskServiceConfig,
    kill_switch_wire,
    unavailable_desk_wire,
)
from options_trading.services.desk_state_store import DeskStateStore
from optitrade.data import SnapshotStore, SyntheticSource
from optitrade.data.models import RawChain
from optitrade.desk import KillSwitch

pytestmark = pytest.mark.unit

UNDERLYING = "NIFTY"
BASE_TIMESTAMP = 1_700_000_000.0
SECONDS_PER_DAY = 86_400.0
# Keeps realized vol far under implied so VRP clears the entry gate on the
# days after the realized-vol estimator has enough spots to be defined.
RICH_DRIFT = 0.0002


def build_store(tmp_path: pathlib.Path, n_days: int) -> SnapshotStore:
    """A snapshot store holding ``n_days`` consecutive end-of-day chains."""
    store = SnapshotStore(tmp_path / "snapshots")
    source = SyntheticSource(seed=1)
    for i in range(n_days):
        chain = source.fetch_chain(UNDERLYING)
        store.write(
            RawChain(
                underlying=chain.underlying,
                spot=chain.spot * (1 + RICH_DRIFT * ((i % 3) - 1)),
                rate=chain.rate,
                timestamp=BASE_TIMESTAMP + i * SECONDS_PER_DAY,
                quotes=chain.quotes,
                dividend_yield=chain.dividend_yield,
            )
        )
    return store


def make_service(
    tmp_path: pathlib.Path,
    n_days: int = 8,
    config: DeskServiceConfig | None = None,
) -> DeskService:
    return DeskService(
        build_store(tmp_path, n_days),
        DeskStateStore(tmp_path / "desk_state.json"),
        tmp_path / "journal",
        KillSwitch(tmp_path / "HALT"),
        config if config is not None else DeskServiceConfig(underlying=UNDERLYING, lot_size=75),
    )


class TestNeverRun:
    """A desk that has not run says so; it does not show a flat book."""

    def test_empty_store_reports_no_market_days(self, tmp_path):
        wire = make_service(tmp_path, n_days=0).build().to_wire_dict()

        assert wire["history"]["hasHistory"] is False
        assert "no captured market days" in wire["history"]["reason"]
        assert wire["history"]["daysAvailable"] == 0
        assert wire["cycles"] == []
        assert wire["book"] == []

    def test_captured_days_but_no_cycle_says_exactly_that(self, tmp_path):
        """Distinguishes "nothing to trade" from "has not traded yet"."""
        wire = make_service(tmp_path, n_days=5).build().to_wire_dict()

        assert wire["history"]["hasHistory"] is False
        assert wire["history"]["reason"] == "the paper desk has not run a cycle yet"
        assert wire["history"]["daysAvailable"] == 5

    def test_days_required_is_one(self, tmp_path):
        """One captured day is genuinely enough to run one cycle."""
        wire = make_service(tmp_path, n_days=0).build().to_wire_dict()

        assert wire["history"]["daysRequired"] == MIN_DAYS_DESK == 1

    def test_account_is_absent_before_the_first_run(self, tmp_path):
        """No state file means no account to report, not a zeroed one."""
        assert make_service(tmp_path, n_days=3).build().to_wire_dict()["account"] is None


class TestAdvance:
    def test_advance_runs_one_cycle_per_captured_day(self, tmp_path):
        service = make_service(tmp_path, n_days=6)

        wire = service.advance().to_wire_dict()

        assert wire["history"]["hasHistory"] is True
        assert len(wire["cycles"]) == 6
        assert [c["date"] for c in wire["cycles"]] == sorted(c["date"] for c in wire["cycles"])

    def test_every_cycle_carries_a_correlation_id(self, tmp_path):
        """Without it the decision trail is unreachable."""
        wire = make_service(tmp_path, n_days=4).advance().to_wire_dict()

        ids = [c["correlation_id"] for c in wire["cycles"]]
        assert all(ids)
        assert len(set(ids)) == len(ids)

    def test_advance_is_idempotent(self, tmp_path):
        """Re-running over the same history must not double the fills."""
        service = make_service(tmp_path, n_days=6)
        first = service.advance().to_wire_dict()

        second = service.advance().to_wire_dict()

        assert len(second["cycles"]) == len(first["cycles"])
        assert second["book"] == first["book"]
        assert second["account"] == first["account"]
        assert any("already been processed" in w for w in second["warnings"])

    def test_advance_reports_how_many_days_it_ran(self, tmp_path):
        wire = make_service(tmp_path, n_days=3).advance().to_wire_dict()

        assert any("advanced 3 of 3 pending day(s)" in w for w in wire["warnings"])

    def test_only_new_days_are_processed(self, tmp_path):
        """The desk advances on the increment, not the whole history."""
        service = make_service(tmp_path, n_days=4)
        service.advance()

        # A fifth stored day appears; the desk should run exactly one cycle.
        source = SyntheticSource(seed=1)
        chain = source.fetch_chain(UNDERLYING)
        SnapshotStore(tmp_path / "snapshots").write(
            RawChain(
                underlying=chain.underlying,
                spot=chain.spot,
                rate=chain.rate,
                timestamp=BASE_TIMESTAMP + 4 * SECONDS_PER_DAY,
                quotes=chain.quotes,
                dividend_yield=chain.dividend_yield,
            )
        )
        # A fresh service avoids the replay cache, as a restart would.
        wire = make_service(tmp_path, n_days=0).advance().to_wire_dict()

        assert len(wire["cycles"]) == 5
        assert any("advanced 1 of 1 pending day(s)" in w for w in wire["warnings"])

    def test_the_book_reflects_real_fills(self, tmp_path):
        """Positions come from the core's paper fills, not a placeholder."""
        wire = make_service(tmp_path, n_days=8).advance().to_wire_dict()

        filled = [c for c in wire["cycles"] if c["n_fills"] > 0]
        assert filled, "the seeded market should let the strategy enter at least once"
        for position in wire["book"]:
            assert position["lotSize"] == 75
            assert position["optionType"] in {"call", "put"}
            assert position["entryPrice"] > 0

    def test_equity_moves_away_from_the_starting_figure(self, tmp_path):
        """A desk that traded and reports untouched equity computed nothing."""
        config = DeskServiceConfig(underlying=UNDERLYING, lot_size=75, initial_equity=1_000_000.0)
        wire = make_service(tmp_path, n_days=8, config=config).advance().to_wire_dict()

        assert wire["account"]["equity"] != pytest.approx(1_000_000.0)


class TestRestartSafety:
    def test_a_new_service_resumes_the_persisted_book(self, tmp_path):
        before = make_service(tmp_path, n_days=6).advance().to_wire_dict()

        after = make_service(tmp_path, n_days=0).build().to_wire_dict()

        assert after["book"] == before["book"]
        assert after["account"] == before["account"]
        assert len(after["cycles"]) == len(before["cycles"])

    def test_state_is_written_per_cycle_not_once_at_the_end(self, tmp_path):
        """A crash mid-backlog must resume, so each cycle is durable."""
        service = make_service(tmp_path, n_days=5)
        service.advance()

        store = DeskStateStore(tmp_path / "desk_state.json")
        assert len(store.load().processed_dates) == 5

    def test_cycle_retention_is_bounded(self, tmp_path):
        config = DeskServiceConfig(underlying=UNDERLYING, lot_size=75, max_cycles_retained=3)
        wire = make_service(tmp_path, n_days=8, config=config).advance().to_wire_dict()

        assert len(wire["cycles"]) == 3
        # The journal keeps everything; only the tabulated summary is capped.
        assert len(DeskStateStore(tmp_path / "desk_state.json").load().processed_dates) == 8

    def test_unreadable_state_blocks_the_advance(self, tmp_path):
        """Never trade from a fresh book while the real one sits unparsed."""
        service = make_service(tmp_path, n_days=5)
        service.advance()
        (tmp_path / "desk_state.json").write_text("{corrupt", encoding="utf-8")

        wire = make_service(tmp_path, n_days=0).advance().to_wire_dict()

        assert wire["history"]["hasHistory"] is False
        assert wire["book"] == []
        assert any("did not advance" in w for w in wire["warnings"])

    def test_unreadable_state_is_not_shown_as_an_empty_desk(self, tmp_path):
        (tmp_path / "desk_state.json").write_text("{corrupt", encoding="utf-8")

        wire = make_service(tmp_path, n_days=5).build().to_wire_dict()

        assert wire["history"]["hasHistory"] is False
        assert "could not be read" in wire["history"]["reason"]
        assert wire["account"] is None
        # Fails closed: an undescribable desk must not look ready to trade.
        assert wire["killSwitch"]["engaged"] is True


class TestKillSwitch:
    def test_a_clear_switch_reports_clear(self, tmp_path):
        state = make_service(tmp_path, n_days=1).kill_switch_state()

        assert state["engaged"] is False
        assert state["reason"] is None
        assert state["readable"] is True

    def test_engaging_is_immediate_and_states_the_reason(self, tmp_path):
        service = make_service(tmp_path, n_days=1)

        state = service.engage_kill_switch("vega breach on the open")

        assert state["engaged"] is True
        assert "vega breach on the open" in state["reason"]

    def test_an_engaged_switch_stops_the_advance(self, tmp_path):
        service = make_service(tmp_path, n_days=6)
        service.engage_kill_switch("halted by the operator")

        wire = service.advance().to_wire_dict()

        assert wire["cycles"] == []
        assert any("halted and did not advance" in w for w in wire["warnings"])

    def test_a_halt_survives_a_restart(self, tmp_path):
        make_service(tmp_path, n_days=3).engage_kill_switch("overnight halt")

        assert make_service(tmp_path, n_days=0).kill_switch_state()["engaged"] is True

    def test_reset_clears_the_halt_and_the_desk_runs_again(self, tmp_path):
        service = make_service(tmp_path, n_days=5)
        service.engage_kill_switch("halted")
        service.reset_kill_switch("cause understood and fixed")

        wire = service.advance().to_wire_dict()

        assert service.kill_switch_state()["engaged"] is False
        assert len(wire["cycles"]) == 5

    def test_a_bare_touch_is_engaged_with_no_stated_reason(self, tmp_path):
        """Any process may halt the desk, including with plain ``touch``."""
        path = tmp_path / "HALT"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()

        state = kill_switch_wire(KillSwitch(path))

        assert state["engaged"] is True
        assert state["reason"] == "engaged with no stated reason"

    def test_engage_and_reset_are_journaled(self, tmp_path):
        """A resume must be auditable, so both transitions are recorded."""
        from optitrade.journal import EventLog

        service = make_service(tmp_path, n_days=1)
        service.engage_kill_switch("breach")
        service.reset_kill_switch("fixed")

        events = list(EventLog(tmp_path / "journal", "desk").replay())
        types = [e.event_type for e in events]

        assert "kill_switch_engaged" in types
        assert "kill_switch_reset" in types
        reset = next(e for e in events if e.event_type == "kill_switch_reset")
        assert reset.data["reason"] == "fixed"
        assert "breach" in (reset.data["previous"] or "")


class TestDecisionTrail:
    def test_a_cycle_trail_reconstructs_the_whole_day(self, tmp_path):
        service = make_service(tmp_path, n_days=8)
        wire = service.advance().to_wire_dict()
        entering = next(c for c in wire["cycles"] if c["n_fills"] > 0)

        trail = service.decision_trail(entering["correlation_id"])

        kinds = [step["kind"] for step in trail["steps"]]
        assert trail["found"] is True
        assert "market" in kinds
        # The linkage fix: the panel's and engine's own records join the cycle.
        assert "debate" in kinds
        assert "risk" in kinds
        assert "hedge" in kinds
        assert trail["summary"]["action"] == "enter"

    def test_the_trail_carries_every_risk_check_not_just_the_binding_one(self, tmp_path):
        service = make_service(tmp_path, n_days=8)
        wire = service.advance().to_wire_dict()
        entering = next(c for c in wire["cycles"] if c["n_fills"] > 0)

        trail = service.decision_trail(entering["correlation_id"])
        risk = next(s for s in trail["steps"] if s["kind"] == "risk")

        assert {c["check"] for c in risk["checks"]} == {
            "greeks_limit",
            "margin_sufficiency",
            "drawdown",
            "concentration",
        }

    def test_the_trail_carries_per_expert_opinions(self, tmp_path):
        """ "What the panel concluded" means the opinions, not just the verdict."""
        service = make_service(tmp_path, n_days=8)
        wire = service.advance().to_wire_dict()
        entering = next(c for c in wire["cycles"] if c["n_fills"] > 0)

        trail = service.decision_trail(entering["correlation_id"])
        debate = next(s for s in trail["steps"] if s["kind"] == "debate")

        assert {op["expert"] for op in debate["opinions"]} == {
            "risk_officer",
            "strategy_expert",
            "execution_expert",
        }
        assert debate["rationale"]
        for opinion in debate["opinions"]:
            assert opinion["assessment"]
            assert 0.0 <= opinion["confidence"] <= 1.0

    def test_an_unknown_correlation_id_is_reported_not_faked(self, tmp_path):
        service = make_service(tmp_path, n_days=2)
        service.advance()

        trail = service.decision_trail("00000000-0000-0000-0000-000000000000")

        assert trail["found"] is False
        assert trail["reason"]
        assert trail["steps"] == []
        assert trail["summary"] is None


class TestWireContract:
    """The frontend merges only the keys it receives, so keys must persist."""

    EXPECTED_KEYS: ClassVar[set[str]] = {
        "isPaper",
        "mode",
        "underlying",
        "computedAt",
        "killSwitch",
        "history",
        "cycles",
        "book",
        "account",
        "warnings",
    }

    def test_a_never_run_desk_emits_every_key(self, tmp_path):
        assert set(make_service(tmp_path, n_days=0).build().to_wire_dict()) == self.EXPECTED_KEYS

    def test_an_advanced_desk_emits_every_key(self, tmp_path):
        assert set(make_service(tmp_path, n_days=4).advance().to_wire_dict()) == self.EXPECTED_KEYS

    def test_the_unavailable_payload_emits_every_key(self, tmp_path):
        assert set(unavailable_desk_wire("boom")) == self.EXPECTED_KEYS

    def test_the_unavailable_payload_fails_closed(self):
        """An undescribable desk reports halted, not ready to trade."""
        wire = unavailable_desk_wire("the state file is corrupt")

        assert wire["killSwitch"]["engaged"] is True
        assert wire["killSwitch"]["readable"] is False
        assert wire["history"]["hasHistory"] is False
        assert wire["account"] is None
        assert wire["cycles"] == []

    def test_paper_is_declared_on_every_payload(self, tmp_path):
        """A consumer must be able to prove the fills are simulated."""
        for wire in (
            make_service(tmp_path, n_days=0).build().to_wire_dict(),
            unavailable_desk_wire("boom"),
        ):
            assert wire["isPaper"] is True
            assert wire["mode"] == "paper"


class TestConfig:
    def test_zero_retention_is_rejected(self):
        with pytest.raises(ValueError, match="max_cycles_retained"):
            DeskServiceConfig(max_cycles_retained=0)

    def test_non_positive_equity_is_rejected(self):
        with pytest.raises(ValueError, match="initial_equity"):
            DeskServiceConfig(initial_equity=0.0)

    def test_config_from_settings_matches_the_deployed_values(self):
        """Deployed and tested values must not diverge (CLAUDE.md rule 2)."""
        from options_trading.config.settings import settings
        from options_trading.services.desk_service import desk_config_from_settings

        config = desk_config_from_settings()

        assert config.initial_equity == settings.desk_initial_equity
        assert config.lot_size == settings.desk_lot_size
        assert config.spread_frac == settings.desk_spread_frac
        assert config.require_debate == settings.desk_require_debate
        assert config.underlying == settings.capture_autostart_underlying

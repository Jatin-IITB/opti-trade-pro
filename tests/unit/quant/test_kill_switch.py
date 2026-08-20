"""Tests for the file-based desk kill switch (paper-loop halt latch)."""

from __future__ import annotations

from optitrade.core.types import Greeks, Portfolio
from optitrade.desk import DeskConfig, KillSwitch, run_daily_cycle
from optitrade.hedging import BandParams
from optitrade.journal import EventLog
from optitrade.risk import RiskLimits
from optitrade.strategy import MarketDay


class BoomStrategy:
    """A strategy that must never be consulted; an engaged switch blocks the cycle."""

    @property
    def name(self) -> str:
        return "boom"

    def decide(self, day, open_positions):
        raise AssertionError("strategy consulted while the kill switch was engaged")


def make_config() -> DeskConfig:
    return DeskConfig(
        limits=RiskLimits(
            max_abs_delta=1000.0,
            max_abs_gamma=100.0,
            max_abs_vega=10_000.0,
            max_drawdown=0.2,
            max_concentration=1.0,
        ),
        band=BandParams(
            proportional_cost=5e-4, risk_aversion=1.0, min_half_width=0.01, max_half_width=0.5
        ),
        underlying_symbol="NIFTY",
    )


class TestKillSwitchFile:
    def test_engage_reason_reset_round_trip(self, tmp_path):
        switch = KillSwitch(tmp_path / "HALT")
        assert not switch.is_engaged()
        assert switch.reason() is None

        switch.engage("drawdown 30.00% breached the 20.00% limit")
        assert switch.is_engaged()
        assert (tmp_path / "HALT").exists()
        assert "drawdown 30.00% breached the 20.00% limit" in switch.reason()
        assert "engaged" in switch.reason()  # timestamp line prefix

        switch.reset()
        assert not switch.is_engaged()
        assert switch.reason() is None
        assert not (tmp_path / "HALT").exists()

    def test_reset_without_file_is_a_no_op(self, tmp_path):
        switch = KillSwitch(tmp_path / "HALT")
        switch.reset()
        assert not switch.is_engaged()

    def test_human_touch_engages_the_switch(self, tmp_path):
        # The file-based design means `touch runtime_data/HALT` halts the desk.
        path = tmp_path / "HALT"
        path.touch()
        switch = KillSwitch(path)
        assert switch.is_engaged()
        assert switch.reason() == ""

    def test_engage_creates_parent_directories(self, tmp_path):
        switch = KillSwitch(tmp_path / "runtime_data" / "HALT")
        switch.engage("manual halt")
        assert switch.is_engaged()


class TestEngagedSwitchBlocksCycle:
    def test_cycle_is_skipped_with_no_decisions(self, tmp_path):
        journal = EventLog(tmp_path, "halted-run")
        switch = KillSwitch(tmp_path / "HALT")
        switch.engage("manual halt for the test")
        day = MarketDay(timestamp=1_700_000_000.0, spot=100.0, rate=0.05, realized_vol=0.2)
        portfolio = Portfolio(
            cash=100_000.0, equity=100_000.0, high_water_mark=100_000.0, margin_available=1e9
        )

        result, book_after, portfolio_after = run_daily_cycle(
            day=day,
            portfolio=portfolio,
            book=(),
            strategy=BoomStrategy(),
            config=make_config(),
            journal=journal,
            kill_switch=switch,
        )

        assert result.halted
        assert result.fills == ()
        assert result.rejected == ()
        assert result.hedge is None
        assert result.book_greeks == Greeks()
        assert book_after == ()
        assert portfolio_after == portfolio
        events = list(journal.replay())
        assert [e.event_type for e in events] == ["cycle_skipped"]
        assert "manual halt for the test" in events[0].data["reason"]
        assert events[0].correlation_id == result.correlation_id

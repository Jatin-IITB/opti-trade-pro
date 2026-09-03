"""One desk cycle must be one correlation group (ADR-009).

Before this, ``RiskEngine.review`` and ``DebatePanel.deliberate`` each minted
their own correlation id unconditionally, so the two richest records the
system produces — why an order was blocked, and what the experts argued —
were journaled under ids nothing else shared. ``events_by_correlation`` on a
cycle returned only ``market_features``, ``hedge_decision`` and
``daily_cycle``, which is a trail with the reasoning cut out of it.

Both now accept an optional correlation id and mint one only when it is
absent, so standalone callers are unaffected.
"""

from __future__ import annotations

import pytest

from optitrade.core.types import Greeks, OptionContract, OptionType, Order, Portfolio
from optitrade.governance.debate import DebatePanel
from optitrade.governance.experts import ExecutionExpert, RiskOfficer, StrategyExpert, TradeProposal
from optitrade.journal.event_log import EventLog
from optitrade.risk.checks import RiskContext
from optitrade.risk.engine import RiskEngine
from optitrade.risk.limits import RiskLimits

pytestmark = pytest.mark.unit

CYCLE_ID = "cycle-correlation-id"


def make_limits() -> RiskLimits:
    return RiskLimits(
        max_abs_delta=500.0,
        max_abs_gamma=50.0,
        max_abs_vega=5_000.0,
        max_drawdown=0.15,
        max_concentration=1.0,
    )


def make_order() -> Order:
    return Order(
        symbol="NIFTY-C-23900",
        quantity=-1.0,
        price=140.0,
        contract=OptionContract(
            symbol="NIFTY-C-23900",
            strike=23_900.0,
            expiry=0.0821917808,
            option_type=OptionType.CALL,
            lot_size=75,
        ),
    )


def make_ctx() -> RiskContext:
    equity = 1_000_000.0
    return RiskContext(
        portfolio=Portfolio(
            cash=equity, equity=equity, high_water_mark=equity, margin_available=equity
        ),
        portfolio_greeks=Greeks(),
        order_greeks=Greeks(delta=-0.5, gamma=0.001, vega=10.0, theta=-5.0),
        margin_required=10_500.0,
        spot=23_900.0,
    )


def make_proposal() -> TradeProposal:
    return TradeProposal(
        order=make_order(),
        thesis="implied is rich to realized",
        expected_edge=5_000.0,
        estimated_cost=200.0,
        implied_vol=0.18,
        realized_vol=0.12,
        ctx=make_ctx(),
    )


class TestRiskEngineCorrelation:
    def test_a_supplied_id_is_used_for_the_decision(self):
        decision = RiskEngine(make_limits()).review(make_order(), make_ctx(), CYCLE_ID)

        assert decision.correlation_id == CYCLE_ID

    def test_a_supplied_id_is_used_for_the_journal_entry(self, tmp_path):
        journal = EventLog(tmp_path, "run")
        RiskEngine(make_limits(), journal=journal).review(make_order(), make_ctx(), CYCLE_ID)

        events = journal.events_by_correlation(CYCLE_ID)

        assert [e.event_type for e in events] == ["risk_decision"]

    def test_omitting_the_id_still_mints_a_fresh_one(self):
        """Standalone callers (MCP, the analytics route) are unchanged."""
        first = RiskEngine(make_limits()).review(make_order(), make_ctx())
        second = RiskEngine(make_limits()).review(make_order(), make_ctx())

        assert first.correlation_id
        assert first.correlation_id != second.correlation_id


class TestDebatePanelCorrelation:
    def _panel(self, journal: EventLog | None = None) -> DebatePanel:
        return DebatePanel(
            experts=(RiskOfficer(make_limits()), StrategyExpert(), ExecutionExpert()),
            journal=journal,
        )

    def test_a_supplied_id_is_used_for_the_record(self):
        record = self._panel().deliberate(make_proposal(), correlation_id=CYCLE_ID)

        assert record.correlation_id == CYCLE_ID

    def test_a_supplied_id_is_used_for_the_journal_entry(self, tmp_path):
        journal = EventLog(tmp_path, "run")
        self._panel(journal).deliberate(make_proposal(), correlation_id=CYCLE_ID)

        events = journal.events_by_correlation(CYCLE_ID)

        assert [e.event_type for e in events] == ["debate_decision"]

    def test_omitting_the_id_still_mints_a_fresh_one(self):
        panel = self._panel()

        first = panel.deliberate(make_proposal())
        second = panel.deliberate(make_proposal())

        assert first.correlation_id
        assert first.correlation_id != second.correlation_id


class TestCycleGroupsEveryDecision:
    """The end-to-end property: one cycle, one retrievable trail."""

    def test_a_cycle_journals_debate_and_risk_under_its_own_id(self, tmp_path):
        from optitrade.backtest.market_replay import SyntheticVRPMarket
        from optitrade.desk.cycle import DeskConfig, run_daily_cycle
        from optitrade.desk.kill_switch import KillSwitch
        from optitrade.hedging.band import BandParams
        from optitrade.strategy import VRPConfig, VRPStrategy

        journal = EventLog(tmp_path, "cycle")
        limits = make_limits()
        market = SyntheticVRPMarket(
            n_days=2, spot=100.0, rate=0.05, realized_vol=0.18, vrp=0.06, seed=7
        )
        equity = 1_000_000.0
        result, _, _ = run_daily_cycle(
            next(iter(market)),
            Portfolio(
                cash=equity, equity=equity, high_water_mark=equity, margin_available=equity / 2
            ),
            (),
            VRPStrategy(VRPConfig(quantity=4.0), lot_size=50),
            DeskConfig(
                limits=limits,
                band=BandParams(proportional_cost=5e-4, risk_aversion=1.0),
                underlying_symbol="SYNTH",
            ),
            journal,
            KillSwitch(tmp_path / "HALT"),
            DebatePanel(
                experts=(RiskOfficer(limits), StrategyExpert(), ExecutionExpert()), journal=journal
            ),
        )

        grouped = [e.event_type for e in journal.events_by_correlation(result.correlation_id)]

        assert "market_features" in grouped
        assert "debate_decision" in grouped, "the deliberation is orphaned from the cycle"
        assert "risk_decision" in grouped, "the risk report is orphaned from the cycle"
        assert "daily_cycle" in grouped

    def test_no_event_of_the_cycle_is_left_ungrouped(self, tmp_path):
        """Every event written during a cycle shares that cycle's id."""
        from optitrade.backtest.market_replay import SyntheticVRPMarket
        from optitrade.desk.cycle import DeskConfig, run_daily_cycle
        from optitrade.desk.kill_switch import KillSwitch
        from optitrade.hedging.band import BandParams
        from optitrade.strategy import VRPConfig, VRPStrategy

        journal = EventLog(tmp_path, "cycle")
        limits = make_limits()
        market = SyntheticVRPMarket(
            n_days=2, spot=100.0, rate=0.05, realized_vol=0.18, vrp=0.06, seed=7
        )
        equity = 1_000_000.0
        result, _, _ = run_daily_cycle(
            next(iter(market)),
            Portfolio(
                cash=equity, equity=equity, high_water_mark=equity, margin_available=equity / 2
            ),
            (),
            VRPStrategy(VRPConfig(quantity=4.0), lot_size=50),
            DeskConfig(
                limits=limits,
                band=BandParams(proportional_cost=5e-4, risk_aversion=1.0),
                underlying_symbol="SYNTH",
            ),
            journal,
            KillSwitch(tmp_path / "HALT"),
            DebatePanel(
                experts=(RiskOfficer(limits), StrategyExpert(), ExecutionExpert()), journal=journal
            ),
        )

        ids = {e.correlation_id for e in journal.replay()}

        assert ids == {result.correlation_id}

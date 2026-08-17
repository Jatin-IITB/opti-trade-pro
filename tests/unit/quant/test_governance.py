"""Tests for the expert debate panel and rule-based experts."""

from __future__ import annotations

import importlib

import pytest

from optitrade.core.types import Greeks, Order, Portfolio
from optitrade.governance import (
    DebatePanel,
    ExecutionExpert,
    ExpertOpinion,
    RiskOfficer,
    Stance,
    StrategyExpert,
    TradeProposal,
)
from optitrade.journal import EventLog
from optitrade.risk import RiskContext, RiskLimits


def make_limits(delta: float = 100.0) -> RiskLimits:
    return RiskLimits(
        max_abs_delta=delta,
        max_abs_gamma=50.0,
        max_abs_vega=200.0,
        max_drawdown=0.20,
        max_concentration=1.0,
    )


def make_proposal(
    expected_edge: float = 300.0,
    estimated_cost: float = 100.0,
    implied_vol: float = 0.18,
    realized_vol: float = 0.24,
    quantity: float = 10.0,
    unit_vega: float = 2.0,
    portfolio_delta: float = 10.0,
    equity: float = 100.0,
    high_water_mark: float = 100.0,
) -> TradeProposal:
    ctx = RiskContext(
        portfolio=Portfolio(equity=equity, high_water_mark=high_water_mark, margin_available=1e9),
        portfolio_greeks=Greeks(delta=portfolio_delta),
        order_greeks=Greeks(delta=1.0, vega=unit_vega),
        margin_required=0.0,
        spot=100.0,
    )
    return TradeProposal(
        order=Order(symbol="NIFTY", quantity=quantity, price=100.0),
        thesis="vol looks cheap into the event",
        expected_edge=expected_edge,
        estimated_cost=estimated_cost,
        implied_vol=implied_vol,
        realized_vol=realized_vol,
        ctx=ctx,
    )


class TestRiskOfficer:
    def test_approves_inside_limits_with_headroom_scaled_confidence(self):
        officer = RiskOfficer(make_limits(delta=100.0))
        opinion = officer.evaluate(make_proposal())  # post delta 20 -> 20% utilisation
        assert opinion.stance is Stance.APPROVE
        assert 0.0 < opinion.confidence <= 1.0
        assert any(ch.isdigit() for ch in opinion.assessment)  # numbers in the audit trail

    def test_confidence_shrinks_as_headroom_shrinks(self):
        officer = RiskOfficer(make_limits(delta=100.0))
        roomy = officer.evaluate(make_proposal(portfolio_delta=10.0))  # 20% used
        tight = officer.evaluate(make_proposal(portfolio_delta=85.0))  # 95% used
        assert tight.confidence < roomy.confidence
        assert tight.concerns  # near-limit approval carries a concern

    def test_would_be_breach_is_a_veto_grade_reject(self):
        officer = RiskOfficer(make_limits(delta=25.0))
        opinion = officer.evaluate(make_proposal(portfolio_delta=20.0))  # post delta 30 > 25
        assert opinion.stance is Stance.REJECT
        assert opinion.confidence >= 0.9  # engages the panel veto rule
        assert opinion.concerns

    def test_drawdown_breach_also_rejects(self):
        officer = RiskOfficer(make_limits())
        opinion = officer.evaluate(make_proposal(equity=75.0, high_water_mark=100.0))
        assert opinion.stance is Stance.REJECT  # 25% drawdown >= 20% limit


class TestStrategyExpert:
    def test_approves_good_edge_with_consistent_long_vol_view(self):
        opinion = StrategyExpert().evaluate(
            make_proposal(
                expected_edge=300.0, estimated_cost=100.0, implied_vol=0.18, realized_vol=0.24
            )
        )
        assert opinion.stance is Stance.APPROVE  # ratio 3.0, long vol with realized > implied

    def test_rejects_thin_edge_with_the_ratio_spelled_out(self):
        opinion = StrategyExpert(min_edge_ratio=1.5).evaluate(
            make_proposal(expected_edge=100.0, estimated_cost=100.0)
        )
        assert opinion.stance is Stance.REJECT
        assert "1.00" in opinion.assessment and "1.50" in opinion.assessment

    def test_rejects_long_vol_thesis_contradicted_by_vols(self):
        opinion = StrategyExpert().evaluate(
            make_proposal(implied_vol=0.25, realized_vol=0.15, quantity=10.0, unit_vega=2.0)
        )
        assert opinion.stance is Stance.REJECT
        assert "long vol" in opinion.assessment
        assert any("implied" in c for c in opinion.concerns)

    def test_short_vol_needs_implied_above_realized(self):
        consistent = StrategyExpert().evaluate(
            make_proposal(implied_vol=0.25, realized_vol=0.15, quantity=-10.0, unit_vega=2.0)
        )
        assert consistent.stance is Stance.APPROVE
        contradicted = StrategyExpert().evaluate(
            make_proposal(implied_vol=0.15, realized_vol=0.25, quantity=-10.0, unit_vega=2.0)
        )
        assert contradicted.stance is Stance.REJECT

    def test_zero_vega_order_has_no_vol_view_to_contradict(self):
        opinion = StrategyExpert().evaluate(
            make_proposal(implied_vol=0.25, realized_vol=0.15, unit_vega=0.0)
        )
        assert opinion.stance is Stance.APPROVE


class TestExecutionExpert:
    def test_approves_when_cost_is_a_small_share_of_edge(self):
        opinion = ExecutionExpert().evaluate(
            make_proposal(expected_edge=100.0, estimated_cost=20.0)
        )
        assert opinion.stance is Stance.APPROVE

    def test_rejects_when_cost_exceeds_the_budget(self):
        opinion = ExecutionExpert(max_cost_ratio=0.5).evaluate(
            make_proposal(expected_edge=100.0, estimated_cost=60.0)
        )
        assert opinion.stance is Stance.REJECT
        assert "60" in opinion.assessment and "50%" in opinion.assessment

    def test_abstains_with_low_confidence_when_there_is_no_edge(self):
        opinion = ExecutionExpert().evaluate(make_proposal(expected_edge=-5.0, estimated_cost=10.0))
        assert opinion.stance is Stance.ABSTAIN
        assert opinion.confidence <= 0.3


class _StubExpert:
    def __init__(self, name: str, stance: Stance, confidence: float):
        self._name = name
        self._stance = stance
        self._confidence = confidence

    @property
    def name(self) -> str:
        return self._name

    def evaluate(self, proposal: TradeProposal) -> ExpertOpinion:
        return ExpertOpinion(
            self._name, self._stance, f"stub view of {self._name}", (), self._confidence
        )


class _RaisingExpert:
    @property
    def name(self) -> str:
        return "flaky"

    def evaluate(self, proposal: TradeProposal) -> ExpertOpinion:
        raise RuntimeError("model endpoint down")


class TestDebatePanel:
    def test_confident_majority_approves(self):
        panel = DebatePanel(
            [_StubExpert("a", Stance.APPROVE, 0.9), _StubExpert("b", Stance.APPROVE, 0.8)]
        )
        record = panel.deliberate(make_proposal())
        assert record.consensus is Stance.APPROVE
        assert record.approval_score == pytest.approx(1.0)
        assert record.dissents == ()

    def test_approval_score_is_confidence_weighted_and_bounded(self):
        panel = DebatePanel(
            [
                _StubExpert("a", Stance.APPROVE, 0.9),
                _StubExpert("b", Stance.REJECT, 0.3),
                _StubExpert("c", Stance.ABSTAIN, 0.8),
            ]
        )
        record = panel.deliberate(make_proposal())
        assert record.approval_score == pytest.approx((0.9 - 0.3) / 2.0)
        assert -1.0 <= record.approval_score <= 1.0

    def test_all_reject_scores_minus_one(self):
        panel = DebatePanel(
            [_StubExpert("a", Stance.REJECT, 0.4), _StubExpert("b", Stance.REJECT, 0.6)]
        )
        record = panel.deliberate(make_proposal())
        assert record.approval_score == pytest.approx(-1.0)
        assert record.consensus is Stance.REJECT

    def test_low_confidence_dissent_is_recorded_but_not_blocking(self):
        panel = DebatePanel(
            [
                _StubExpert("a", Stance.APPROVE, 0.9),
                _StubExpert("b", Stance.APPROVE, 0.9),
                _StubExpert("c", Stance.REJECT, 0.5),
            ]
        )
        record = panel.deliberate(make_proposal())
        assert record.consensus is Stance.APPROVE
        assert [op.expert_name for op in record.dissents] == ["c"]

    def test_confident_veto_blocks_despite_winning_score(self):
        panel = DebatePanel(
            [
                _StubExpert("a", Stance.APPROVE, 0.95),
                _StubExpert("b", Stance.APPROVE, 0.95),
                _StubExpert("veto", Stance.REJECT, 0.92),
            ]
        )
        record = panel.deliberate(make_proposal())
        # Score (1.9 - 0.92) / 2.82 = +0.35 clears the 0.25 threshold, yet
        # the confident rejection wins.
        assert record.approval_score > 0.25
        assert record.consensus is Stance.REJECT
        assert "veto" in record.rationale.lower()

    def test_score_below_threshold_rejects(self):
        panel = DebatePanel(
            [_StubExpert("a", Stance.APPROVE, 0.5), _StubExpert("b", Stance.REJECT, 0.4)],
            approval_threshold=0.25,
        )
        record = panel.deliberate(make_proposal())
        assert record.approval_score < 0.25
        assert record.consensus is Stance.REJECT

    def test_raising_expert_becomes_full_confidence_reject(self):
        panel = DebatePanel([_StubExpert("a", Stance.APPROVE, 0.95), _RaisingExpert()])
        record = panel.deliberate(make_proposal())
        flaky = next(op for op in record.opinions if op.expert_name == "flaky")
        assert flaky.stance is Stance.REJECT
        assert flaky.confidence == 1.0
        assert "model endpoint down" in flaky.assessment
        assert record.consensus is Stance.REJECT  # a failure is a veto

    def test_journal_records_the_deliberation(self, tmp_path):
        journal = EventLog(tmp_path, "debate-run")
        panel = DebatePanel([_StubExpert("a", Stance.APPROVE, 0.9)], journal=journal)
        record = panel.deliberate(make_proposal())
        events = list(journal.replay())
        assert len(events) == 1
        assert events[0].event_type == "debate_decision"
        assert events[0].correlation_id == record.correlation_id
        assert events[0].data["consensus"] == "approve"
        assert events[0].data["opinions"][0]["expert"] == "a"

    def test_end_to_end_with_rule_based_experts(self):
        limits = make_limits()
        panel = DebatePanel([RiskOfficer(limits), StrategyExpert(), ExecutionExpert()])
        record = panel.deliberate(make_proposal())
        assert record.consensus is Stance.APPROVE
        assert len(record.opinions) == 3


class TestDspyAdapter:
    def test_module_imports_cleanly_without_dspy(self):
        module = importlib.import_module("optitrade.governance.dspy_adapter")
        assert hasattr(module, "LLMExpert")

    def test_llm_expert_raises_helpful_import_error_when_dspy_missing(self):
        try:
            import dspy  # noqa: F401
        except ImportError:
            pass
        else:
            pytest.skip("dspy is installed; the ImportError path is not reachable")
        from optitrade.governance.dspy_adapter import LLMExpert

        with pytest.raises(ImportError, match=r"optitrade-pro\[agentic\]"):
            LLMExpert("risk officer")

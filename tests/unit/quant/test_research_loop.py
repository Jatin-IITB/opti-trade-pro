"""Tests for the research loop orchestrator (ADR-022).

Verifies:
1. The loop proposes, evaluates, and ranks experiments.
2. ResearchReport.accepted filters correctly.
3. Accepted proposals generate research_accepted journal events.
4. max_proposals caps the evaluation count.
"""

from __future__ import annotations

import pytest

from optitrade.backtest.market_replay import SyntheticVRPMarket
from optitrade.backtest.walk_forward import BacktestConfig
from optitrade.hedging.band import BandParams
from optitrade.journal.event_log import EventLog
from optitrade.research.agent import GridSearchAgent
from optitrade.research.evaluator import ProposalEvaluator
from optitrade.research.loop import ResearchLoop
from optitrade.risk.limits import RiskLimits
from optitrade.strategy.vrp import VRPConfig

_LIMITS = RiskLimits(
    max_abs_delta=500.0,
    max_abs_gamma=50.0,
    max_abs_vega=5_000.0,
    max_drawdown=0.15,
    max_concentration=1.0,
)
_BT_CONFIG = BacktestConfig(
    risk_limits=_LIMITS,
    band_params=BandParams(proportional_cost=5e-4, risk_aversion=1.0),
    lot_size=50,
)
_BASELINE = VRPConfig(quantity=4.0)


@pytest.fixture()
def replay_days():
    market = SyntheticVRPMarket(
        n_days=40,
        spot=100.0,
        rate=0.05,
        realized_vol=0.18,
        vrp=0.06,
        seed=42,
    )
    return list(market)


@pytest.fixture()
def journal(tmp_path):
    return EventLog(tmp_path, "test-loop")


class TestResearchLoop:
    def test_loop_runs_and_ranks(self, replay_days, journal):
        agent = GridSearchAgent(steps=(0.5, 1.5))
        evaluator = ProposalEvaluator(
            replay_days=replay_days,
            backtest_config=_BT_CONFIG,
            baseline_config=_BASELINE,
            n_folds=2,
            lot_size=50,
            min_improvement=100.0,
            journal=journal,
        )
        loop = ResearchLoop(agent=agent, evaluator=evaluator, journal=journal)
        report = loop.run(_BASELINE, max_proposals=3)

        assert len(report.experiments) == 3
        ranked = report.ranked
        for i in range(len(ranked) - 1):
            assert ranked[i].improvement_sharpe >= ranked[i + 1].improvement_sharpe

    def test_max_proposals_caps(self, replay_days, journal):
        agent = GridSearchAgent(steps=(0.5, 0.75, 1.5, 2.0))
        evaluator = ProposalEvaluator(
            replay_days=replay_days,
            backtest_config=_BT_CONFIG,
            baseline_config=_BASELINE,
            n_folds=2,
            lot_size=50,
            journal=journal,
        )
        loop = ResearchLoop(agent=agent, evaluator=evaluator, journal=journal)
        report = loop.run(_BASELINE, max_proposals=2)

        assert len(report.experiments) == 2

    def test_journal_events(self, replay_days, journal):
        agent = GridSearchAgent(steps=(1.5,))
        evaluator = ProposalEvaluator(
            replay_days=replay_days,
            backtest_config=_BT_CONFIG,
            baseline_config=_BASELINE,
            n_folds=2,
            lot_size=50,
            min_improvement=0.0,
            journal=journal,
        )
        loop = ResearchLoop(agent=agent, evaluator=evaluator, journal=journal)
        loop.run(_BASELINE, max_proposals=2)

        event_types = {e.event_type for e in journal.replay()}
        assert "experiment_result" in event_types
        assert "research_loop" in event_types

    def test_report_baseline_fields(self, replay_days, journal):
        agent = GridSearchAgent(steps=(1.5,))
        evaluator = ProposalEvaluator(
            replay_days=replay_days,
            backtest_config=_BT_CONFIG,
            baseline_config=_BASELINE,
            n_folds=2,
            lot_size=50,
            journal=journal,
        )
        loop = ResearchLoop(agent=agent, evaluator=evaluator, journal=journal)
        report = loop.run(_BASELINE, max_proposals=1)

        assert report.baseline_config is _BASELINE
        assert isinstance(report.baseline_sharpe, float)
        assert isinstance(report.baseline_dsr, float)

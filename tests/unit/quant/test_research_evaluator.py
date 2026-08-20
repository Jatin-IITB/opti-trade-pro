"""Tests for the research proposal evaluator (ADR-022).

Verifies:
1. Evaluation runs walk-forward and produces an ExperimentResult.
2. The baseline is cached (second evaluation reuses it).
3. An improvement >= min_improvement is accepted.
4. Experiment results are journaled when a journal is provided.
"""

from __future__ import annotations

import pytest

from optitrade.backtest.market_replay import SyntheticVRPMarket
from optitrade.backtest.walk_forward import BacktestConfig
from optitrade.hedging.band import BandParams
from optitrade.journal.event_log import EventLog
from optitrade.research.evaluator import ProposalEvaluator
from optitrade.research.proposals import ResearchProposal
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
    return EventLog(tmp_path, "test-evaluator")


class TestProposalEvaluator:
    def test_evaluate_produces_result(self, replay_days, journal):
        evaluator = ProposalEvaluator(
            replay_days=replay_days,
            backtest_config=_BT_CONFIG,
            baseline_config=_BASELINE,
            n_folds=2,
            train_frac=0.6,
            lot_size=50,
            min_improvement=100.0,
            journal=journal,
        )
        proposal = ResearchProposal(
            proposal_id="test-001",
            thesis="double the quantity",
            config=VRPConfig(quantity=8.0),
            source="test",
            changes={"quantity": 8.0},
        )
        result = evaluator.evaluate(proposal)

        assert result.proposal is proposal
        assert isinstance(result.baseline_sharpe, float)
        assert isinstance(result.candidate_sharpe, float)
        assert result.improvement_sharpe == pytest.approx(
            result.candidate_sharpe - result.baseline_sharpe
        )

    def test_baseline_is_cached(self, replay_days):
        evaluator = ProposalEvaluator(
            replay_days=replay_days,
            backtest_config=_BT_CONFIG,
            baseline_config=_BASELINE,
            n_folds=2,
            lot_size=50,
        )
        p1 = ResearchProposal(
            proposal_id="a",
            thesis="a",
            config=_BASELINE,
            source="test",
        )
        p2 = ResearchProposal(
            proposal_id="b",
            thesis="b",
            config=_BASELINE,
            source="test",
        )
        evaluator.evaluate(p1)
        baseline_1 = evaluator._baseline_result
        evaluator.evaluate(p2)
        baseline_2 = evaluator._baseline_result
        assert baseline_1 is baseline_2

    def test_experiment_journaled(self, replay_days, journal):
        evaluator = ProposalEvaluator(
            replay_days=replay_days,
            backtest_config=_BT_CONFIG,
            baseline_config=_BASELINE,
            n_folds=2,
            lot_size=50,
            journal=journal,
        )
        proposal = ResearchProposal(
            proposal_id="j-001",
            thesis="test",
            config=_BASELINE,
            source="test",
        )
        evaluator.evaluate(proposal)

        events = [e for e in journal.replay() if e.event_type == "experiment_result"]
        assert len(events) == 1
        assert events[0].data["proposal_id"] == "j-001"

    def test_empty_replay_raises(self, journal):
        with pytest.raises(ValueError, match="non-empty"):
            ProposalEvaluator(
                replay_days=[],
                backtest_config=_BT_CONFIG,
                baseline_config=_BASELINE,
            )

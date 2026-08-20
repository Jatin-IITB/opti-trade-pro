"""Proposal evaluation via walk-forward backtesting (ADR-022).

The evaluator is pure math: run walk-forward on the proposed config, run it
on the baseline config (cached across proposals), and compare. No LLM
output reaches this path. The evaluator journals each experiment result so
downstream agents can cite the comparison.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

from optitrade.backtest.walk_forward import BacktestConfig, WalkForwardResult, run_walk_forward
from optitrade.journal.event_log import EventLog
from optitrade.research.proposals import ExperimentResult, ResearchProposal
from optitrade.strategy.base import MarketDay, Strategy
from optitrade.strategy.vrp import VRPConfig, VRPStrategy


def _strategy_factory(config: VRPConfig, lot_size: int) -> Callable[[VRPConfig], Strategy]:
    """Curried factory for walk-forward: given a config, return a Strategy."""

    def factory(cfg: VRPConfig) -> Strategy:
        return VRPStrategy(cfg, lot_size=lot_size)

    return factory


class ProposalEvaluator:
    """Evaluate proposals by running walk-forward against a baseline.

    The baseline result is computed once and cached; each candidate is
    evaluated independently. ``min_improvement`` is the minimum Sharpe
    improvement (absolute, annualised) required for acceptance.
    """

    def __init__(
        self,
        replay_days: Sequence[MarketDay],
        backtest_config: BacktestConfig,
        baseline_config: VRPConfig,
        *,
        n_folds: int = 4,
        train_frac: float = 0.6,
        lot_size: int = 50,
        min_improvement: float = 0.5,
        journal: EventLog | None = None,
    ) -> None:
        if not replay_days:
            raise ValueError("replay_days must be non-empty")
        self._days = list(replay_days)
        self._bt_config = backtest_config
        self._baseline_config = baseline_config
        self._n_folds = n_folds
        self._train_frac = train_frac
        self._lot_size = lot_size
        self._min_improvement = min_improvement
        self._journal = journal
        self._baseline_result: WalkForwardResult[VRPConfig] | None = None

    def _baseline(self) -> WalkForwardResult[VRPConfig]:
        if self._baseline_result is not None:
            return self._baseline_result
        factory = _strategy_factory(self._baseline_config, self._lot_size)
        self._baseline_result = run_walk_forward(
            strategy_factory=factory,
            param_grid=[self._baseline_config],
            replay=self._days,
            config=self._bt_config,
            n_folds=self._n_folds,
            train_frac=self._train_frac,
        )
        return self._baseline_result

    def evaluate(self, proposal: ResearchProposal) -> ExperimentResult:
        """Run walk-forward on the proposal and compare against baseline."""
        baseline = self._baseline()
        factory = _strategy_factory(proposal.config, self._lot_size)
        candidate = run_walk_forward(
            strategy_factory=factory,
            param_grid=[proposal.config],
            replay=self._days,
            config=self._bt_config,
            n_folds=self._n_folds,
            train_frac=self._train_frac,
        )
        improvement_sharpe = candidate.oos_sharpe - baseline.oos_sharpe
        improvement_dsr = candidate.deflated_sharpe - baseline.deflated_sharpe
        accepted = improvement_sharpe >= self._min_improvement

        result = ExperimentResult(
            proposal=proposal,
            baseline_sharpe=baseline.oos_sharpe,
            candidate_sharpe=candidate.oos_sharpe,
            baseline_dsr=baseline.deflated_sharpe,
            candidate_dsr=candidate.deflated_sharpe,
            improvement_sharpe=improvement_sharpe,
            improvement_dsr=improvement_dsr,
            candidate_max_drawdown=float(candidate.oos_equity.min())
            / float(candidate.oos_equity.max())
            if candidate.oos_equity.max() > 0
            else 0.0,
            candidate_n_trades=sum(f.test_n_trades for f in candidate.folds),
            accepted=accepted,
        )

        if self._journal is not None:
            self._journal.append(
                "experiment_result",
                {
                    "proposal_id": proposal.proposal_id,
                    "source": proposal.source,
                    "thesis": proposal.thesis,
                    "changes": proposal.changes,
                    "baseline_sharpe": baseline.oos_sharpe,
                    "candidate_sharpe": candidate.oos_sharpe,
                    "baseline_dsr": baseline.deflated_sharpe,
                    "candidate_dsr": candidate.deflated_sharpe,
                    "improvement_sharpe": improvement_sharpe,
                    "improvement_dsr": improvement_dsr,
                    "accepted": accepted,
                },
            )
        return result


__all__ = ["ProposalEvaluator"]

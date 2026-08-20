"""Research loop orchestrator: propose → evaluate → rank → journal (ADR-022).

The loop is deterministic at its core: the LLM proposes, but evaluation is
pure math (walk-forward with deflated Sharpe). Every experiment result is
journaled; accepted proposals generate a ``research_accepted`` event that
feeds the governance pipeline (debate record → ADR → config update).

Deliberate design: the loop itself never applies a proposed change. It
presents the ranked results; a human reviews and approves. Accepted changes
land as ADRs through the standard governance process (docs/governance.md).
"""

from __future__ import annotations

from optitrade.journal.event_log import EventLog
from optitrade.research.agent import ResearchAgent
from optitrade.research.evaluator import ProposalEvaluator
from optitrade.research.proposals import ExperimentResult, ResearchReport
from optitrade.strategy.vrp import VRPConfig


class ResearchLoop:
    """Orchestrate one research cycle: propose → evaluate → rank.

    The loop runs synchronously: each proposal is evaluated via walk-forward
    (the expensive part), and results are collected into a
    :class:`ResearchReport`. A ``research_loop`` event summarises the run.
    """

    def __init__(
        self,
        agent: ResearchAgent,
        evaluator: ProposalEvaluator,
        journal: EventLog | None = None,
    ) -> None:
        self._agent = agent
        self._evaluator = evaluator
        self._journal = journal

    def run(
        self,
        baseline: VRPConfig,
        max_proposals: int | None = None,
    ) -> ResearchReport:
        """Run one research cycle; returns the full report.

        ``max_proposals`` caps the number of proposals evaluated (the agent
        may generate more; only the first ``max_proposals`` are tested).
        """
        proposals = list(self._agent.propose(baseline))
        if max_proposals is not None:
            proposals = proposals[:max_proposals]

        experiments: list[ExperimentResult] = []
        for proposal in proposals:
            result = self._evaluator.evaluate(proposal)
            experiments.append(result)

        baseline_result = self._evaluator._baseline()
        report = ResearchReport(
            experiments=tuple(experiments),
            baseline_config=baseline,
            baseline_sharpe=baseline_result.oos_sharpe,
            baseline_dsr=baseline_result.deflated_sharpe,
        )

        if self._journal is not None:
            accepted_ids = [e.proposal.proposal_id for e in report.accepted]
            self._journal.append(
                "research_loop",
                {
                    "agent": self._agent.name,
                    "n_proposals": len(proposals),
                    "n_evaluated": len(experiments),
                    "n_accepted": len(accepted_ids),
                    "accepted_ids": accepted_ids,
                    "baseline_sharpe": report.baseline_sharpe,
                    "baseline_dsr": report.baseline_dsr,
                    "best_improvement": max(
                        (e.improvement_sharpe for e in experiments), default=0.0
                    ),
                },
            )
            for experiment in report.accepted:
                self._journal.append(
                    "research_accepted",
                    {
                        "proposal_id": experiment.proposal.proposal_id,
                        "thesis": experiment.proposal.thesis,
                        "source": experiment.proposal.source,
                        "changes": experiment.proposal.changes,
                        "candidate_sharpe": experiment.candidate_sharpe,
                        "candidate_dsr": experiment.candidate_dsr,
                        "improvement_sharpe": experiment.improvement_sharpe,
                    },
                )

        return report


__all__ = ["ResearchLoop"]

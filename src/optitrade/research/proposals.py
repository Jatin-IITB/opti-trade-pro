"""Research proposal and experiment result types (ADR-022).

A :class:`ResearchProposal` captures a thesis about why changing strategy
parameters should improve performance. A :class:`ExperimentResult` records the
walk-forward evaluation of that thesis, comparing against a baseline config.
Every proposal is journaled; accepted proposals feed the governance pipeline
(debate record → ADR → config update).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from optitrade.strategy.vrp import VRPConfig


@dataclass(frozen=True, slots=True)
class ResearchProposal:
    """One proposed parameter change with a numbered thesis.

    ``changes`` describes which config fields were modified and how; the
    ``config`` is the complete proposed :class:`VRPConfig`. ``source``
    identifies the proposer (``"grid_search"``, ``"llm_agent"``, etc.).
    """

    proposal_id: str
    thesis: str
    config: VRPConfig
    source: str
    changes: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ExperimentResult:
    """Walk-forward evaluation of one :class:`ResearchProposal`.

    Metrics compare the candidate against the baseline config: positive
    ``improvement_*`` means the candidate is better. ``accepted`` is True
    when the candidate beats baseline on the primary metric (Sharpe) by at
    least ``min_improvement``.
    """

    proposal: ResearchProposal
    baseline_sharpe: float
    candidate_sharpe: float
    baseline_dsr: float
    candidate_dsr: float
    improvement_sharpe: float
    improvement_dsr: float
    candidate_max_drawdown: float
    candidate_n_trades: int
    accepted: bool


@dataclass(frozen=True)
class ResearchReport:
    """Summary of one research loop run.

    ``ranked`` lists experiments best-to-worst by improvement on the primary
    metric. ``accepted`` filters to the ones that cleared the bar.
    """

    experiments: tuple[ExperimentResult, ...]
    baseline_config: VRPConfig
    baseline_sharpe: float
    baseline_dsr: float

    @property
    def ranked(self) -> tuple[ExperimentResult, ...]:
        return tuple(sorted(self.experiments, key=lambda e: e.improvement_sharpe, reverse=True))

    @property
    def accepted(self) -> tuple[ExperimentResult, ...]:
        return tuple(e for e in self.ranked if e.accepted)


__all__ = ["ExperimentResult", "ResearchProposal", "ResearchReport"]

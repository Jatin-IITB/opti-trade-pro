"""Multi-expert debate with confidence-weighted consensus.

Ported from Prism's expert-debate pattern. Two rules decide the outcome:

1. Weighted score: ``approval_score = (sum of APPROVE confidence - sum of
   REJECT confidence) / total confidence`` — in [-1, 1]; abstainers dilute
   the score without taking a side. Consensus requires the score to reach
   ``approval_threshold``.
2. Confident veto: any single REJECT at confidence >= 0.9 blocks consensus
   regardless of the score. A near-certain objection (typically the risk
   officer) must never be outvoted by enthusiasm — the same fail-closed
   philosophy as the risk engine.

An expert that raises contributes a REJECT at confidence 1.0: an
unevaluated proposal is treated as vetoed, never waved through.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from optitrade.governance.experts import Expert, ExpertOpinion, Stance, TradeProposal
from optitrade.journal.event_log import EventLog

_VETO_CONFIDENCE = 0.9


@dataclass(frozen=True)
class DecisionRecord:
    """Full audit record of one deliberation."""

    proposal_summary: str  # symbol, quantity, thesis
    opinions: tuple[ExpertOpinion, ...]
    consensus: Stance
    approval_score: float  # in [-1, 1]
    dissents: tuple[ExpertOpinion, ...]  # definite stances disagreeing with consensus
    rationale: str
    correlation_id: str


class DebatePanel:
    """Convenes the experts and synthesises a consensus decision."""

    def __init__(
        self,
        experts: Sequence[Expert],
        approval_threshold: float = 0.25,
        journal: EventLog | None = None,
    ) -> None:
        self._experts = tuple(experts)
        self._approval_threshold = approval_threshold
        self._journal = journal

    def deliberate(
        self, proposal: TradeProposal, correlation_id: str | None = None
    ) -> DecisionRecord:
        """Convene the panel; journals the record under ``correlation_id``.

        Passed in by the daily desk cycle so the deliberation joins that
        cycle's causal group, and minted fresh for standalone callers. See
        the matching note on :meth:`optitrade.risk.engine.RiskEngine.review`
        (ADR-009).
        """
        opinions = tuple(self._opinion_of(expert, proposal) for expert in self._experts)
        total = sum(op.confidence for op in opinions)
        approve_weight = sum(op.confidence for op in opinions if op.stance is Stance.APPROVE)
        reject_weight = sum(op.confidence for op in opinions if op.stance is Stance.REJECT)
        score = (approve_weight - reject_weight) / total if total > 0 else 0.0
        vetoes = tuple(
            op
            for op in opinions
            if op.stance is Stance.REJECT and op.confidence >= _VETO_CONFIDENCE
        )
        approved = score >= self._approval_threshold and not vetoes
        consensus = Stance.APPROVE if approved else Stance.REJECT
        # An abstention is neither agreement nor dissent; only definite
        # opposite stances are recorded as dissents.
        dissents = tuple(
            op for op in opinions if op.stance is not Stance.ABSTAIN and op.stance is not consensus
        )
        rationale = self._rationale(opinions, score, vetoes, consensus)
        order = proposal.order
        summary = f"{order.symbol} qty {order.quantity:+g} @ {order.price:g}: {proposal.thesis}"
        correlation_id = correlation_id or str(uuid.uuid4())
        record = DecisionRecord(
            proposal_summary=summary,
            opinions=opinions,
            consensus=consensus,
            approval_score=score,
            dissents=dissents,
            rationale=rationale,
            correlation_id=correlation_id,
        )
        if self._journal is not None:
            self._journal.append(
                "debate_decision", _record_payload(record), correlation_id=correlation_id
            )
        return record

    def _opinion_of(self, expert: Expert, proposal: TradeProposal) -> ExpertOpinion:
        try:
            name = expert.name
        except Exception:  # a broken name must not break the deliberation
            name = type(expert).__name__
        try:
            return expert.evaluate(proposal)
        except Exception as exc:  # fail closed: an error is a full-confidence veto
            return ExpertOpinion(
                expert_name=name,
                stance=Stance.REJECT,
                assessment=(
                    f"Expert '{name}' raised {type(exc).__name__}: {exc}. Failing closed: "
                    "an unevaluated proposal is treated as rejected."
                ),
                concerns=(f"{type(exc).__name__}: {exc}",),
                confidence=1.0,
            )

    def _rationale(
        self,
        opinions: tuple[ExpertOpinion, ...],
        score: float,
        vetoes: tuple[ExpertOpinion, ...],
        consensus: Stance,
    ) -> str:
        approvals = sum(1 for op in opinions if op.stance is Stance.APPROVE)
        rejections = sum(1 for op in opinions if op.stance is Stance.REJECT)
        abstentions = len(opinions) - approvals - rejections
        counts = f"{approvals} approve, {rejections} reject, {abstentions} abstain"
        if vetoes:
            veto = vetoes[0]
            return (
                f"Vetoed by {veto.expert_name} (REJECT at confidence {veto.confidence:.2f}); "
                f"a confident objection overrides the weighted score of {score:+.2f} ({counts})."
            )
        if consensus is Stance.APPROVE:
            return (
                f"Weighted score {score:+.2f} clears the {self._approval_threshold:+.2f} "
                f"threshold ({counts})."
            )
        return (
            f"Weighted score {score:+.2f} falls short of the {self._approval_threshold:+.2f} "
            f"threshold ({counts})."
        )


def _record_payload(record: DecisionRecord) -> dict[str, Any]:
    """JSON-serialisable snapshot of a deliberation for the journal."""
    return {
        "proposal": record.proposal_summary,
        "consensus": record.consensus.value,
        "approval_score": record.approval_score,
        "rationale": record.rationale,
        "dissenters": [op.expert_name for op in record.dissents],
        "opinions": [
            {
                "expert": op.expert_name,
                "stance": op.stance.value,
                "assessment": op.assessment,
                "concerns": list(op.concerns),
                "confidence": op.confidence,
            }
            for op in record.opinions
        ],
    }


__all__ = ["DebatePanel", "DecisionRecord"]

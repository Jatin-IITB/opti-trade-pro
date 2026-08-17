"""Optional LLM-backed debate expert built on dspy.

This module imports cleanly without dspy installed; the dependency is only
loaded inside ``LLMExpert.__init__``. The signature mirrors Prism's
``ExpertEvaluationSignature``: (task_description, proposed_approach,
expert_role) -> (assessment, concerns as JSON, confidence).
"""

from __future__ import annotations

import json
from typing import Any

from optitrade.governance.experts import ExpertOpinion, Stance, TradeProposal

_TASK = (
    "Decide whether a derivatives trading desk should send this order. "
    "Judge only from your professional role; cite the numbers you rely on."
)


class LLMExpert:
    """Debate-panel expert whose opinion comes from an LLM via dspy."""

    def __init__(self, role: str) -> None:
        try:
            import dspy  # type: ignore[import-not-found,import-untyped]
        except ImportError as exc:
            raise ImportError(
                "LLMExpert requires the optional 'dspy' dependency: "
                "pip install 'optitrade-pro[agentic]'"
            ) from exc
        self._role = role

        class ExpertEvaluationSignature(dspy.Signature):
            """Evaluate a proposed trade from the standpoint of a named expert role."""

            task_description: str = dspy.InputField(desc="the decision to be made")
            proposed_approach: str = dspy.InputField(desc="the trade proposal, with its numbers")
            expert_role: str = dspy.InputField(desc="professional lens, e.g. 'risk officer'")
            assessment: str = dspy.OutputField(desc="2-3 sentence professional assessment")
            concerns: str = dspy.OutputField(desc="JSON array of concern strings")
            confidence: float = dspy.OutputField(desc="confidence in the assessment, 0.0-1.0")

        self._predict = dspy.Predict(ExpertEvaluationSignature)

    @property
    def name(self) -> str:
        return self._role

    def evaluate(self, proposal: TradeProposal) -> ExpertOpinion:
        order = proposal.order
        result: Any = self._predict(
            task_description=_TASK,
            proposed_approach=(
                f"{order.symbol} qty {order.quantity:+g} @ {order.price:g}; "
                f"thesis: {proposal.thesis}; expected edge {proposal.expected_edge:.2f} vs "
                f"cost {proposal.estimated_cost:.2f}; implied vol {proposal.implied_vol:.2%} "
                f"vs realized {proposal.realized_vol:.2%}"
            ),
            expert_role=self._role,
        )
        assessment = str(result.assessment)
        confidence = min(1.0, max(0.0, float(result.confidence)))
        # The Prism signature has no explicit stance field; infer it. An
        # explicit rejection wins; low confidence reads as an abstention.
        if "reject" in assessment.lower():
            stance = Stance.REJECT
        elif confidence < 0.5:
            stance = Stance.ABSTAIN
        else:
            stance = Stance.APPROVE
        return ExpertOpinion(
            expert_name=self._role,
            stance=stance,
            assessment=assessment,
            concerns=_parse_concerns(str(result.concerns)),
            confidence=confidence,
        )


def _parse_concerns(raw: str) -> tuple[str, ...]:
    """Parse the concerns field, tolerating non-JSON model output."""
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return (raw,) if raw.strip() else ()
    if isinstance(parsed, list):
        return tuple(str(item) for item in parsed)
    return (str(parsed),)


__all__ = ["LLMExpert"]

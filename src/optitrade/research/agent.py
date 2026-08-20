"""Research agents: deterministic grid search + LLM-backed proposer (ADR-022).

A :class:`ResearchAgent` proposes parameter changes to be evaluated by
:class:`~optitrade.research.evaluator.ProposalEvaluator`. Two implementations:

- :class:`GridSearchAgent` — deterministic: varies one parameter at a time
  from the baseline, producing a finite set of proposals. The reference
  implementation; always available.
- :class:`LLMResearchAgent` — reads regime data and backtest history from
  the journal and asks an LLM to propose parameter changes with a thesis.
  Optional (requires ``[agentic]`` extra).
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import replace
from typing import Protocol, runtime_checkable

from optitrade.agents.base import LLMBackend, events_to_context, extract_events
from optitrade.journal.event_log import EventLog
from optitrade.research.proposals import ResearchProposal
from optitrade.strategy.vrp import VRPConfig


@runtime_checkable
class ResearchAgent(Protocol):
    """Interface every research agent implements."""

    @property
    def name(self) -> str: ...

    def propose(self, baseline: VRPConfig) -> Sequence[ResearchProposal]: ...


class GridSearchAgent:
    """Deterministic grid search: vary one parameter at a time.

    Generates proposals by scaling each tunable parameter by the configured
    ``steps``. For example, ``steps=(0.5, 0.75, 1.5, 2.0)`` produces four
    proposals per parameter, each holding all other params at baseline.
    """

    def __init__(
        self,
        steps: tuple[float, ...] = (0.5, 0.75, 1.5, 2.0),
    ) -> None:
        self._steps = steps

    @property
    def name(self) -> str:
        return "grid_search"

    def propose(self, baseline: VRPConfig) -> Sequence[ResearchProposal]:
        proposals: list[ResearchProposal] = []
        counter = 0

        for scale in self._steps:
            new_entry = baseline.entry_vrp_min * scale
            if new_entry > baseline.exit_vrp_max:
                counter += 1
                proposals.append(
                    ResearchProposal(
                        proposal_id=f"grid-{counter:03d}",
                        thesis=(
                            f"entry_vrp_min {baseline.entry_vrp_min:.4f} → "
                            f"{new_entry:.4f} ({scale:.0%} of baseline)"
                        ),
                        config=replace(baseline, entry_vrp_min=new_entry),
                        source=self.name,
                        changes={"entry_vrp_min": new_entry, "scale": scale},
                    )
                )

        for qty_scale in self._steps:
            new_qty = max(1.0, baseline.quantity * qty_scale)
            counter += 1
            proposals.append(
                ResearchProposal(
                    proposal_id=f"grid-{counter:03d}",
                    thesis=(
                        f"quantity {baseline.quantity:g} → {new_qty:g} "
                        f"({qty_scale:.0%} of baseline)"
                    ),
                    config=replace(baseline, quantity=new_qty),
                    source=self.name,
                    changes={"quantity": new_qty, "scale": qty_scale},
                )
            )

        for structure in ("straddle", "strangle"):
            if structure != baseline.structure:
                counter += 1
                proposals.append(
                    ResearchProposal(
                        proposal_id=f"grid-{counter:03d}",
                        thesis=f"switch structure from {baseline.structure} to {structure}",
                        config=replace(baseline, structure=structure),
                        source=self.name,
                        changes={"structure": structure},
                    )
                )

        for max_days in (15, 20, 25, 30, 45):
            if max_days != baseline.max_days_in_trade:
                counter += 1
                proposals.append(
                    ResearchProposal(
                        proposal_id=f"grid-{counter:03d}",
                        thesis=f"max_days_in_trade → {max_days}",
                        config=replace(baseline, max_days_in_trade=max_days),
                        source=self.name,
                        changes={"max_days_in_trade": max_days},
                    )
                )

        return proposals


_RESEARCH_SYSTEM = (
    "You are a quantitative researcher on a derivatives desk. You propose "
    "parameter changes to a variance-risk-premium (VRP) strategy. Each "
    "proposal must be a concrete parameter change with a thesis explaining "
    "why it should improve risk-adjusted returns. Respond with a JSON array "
    "of objects, each with keys: 'thesis' (string), 'changes' (dict of "
    "param name → new value). Valid parameters: entry_vrp_min (float, must "
    "exceed exit_vrp_max), exit_vrp_max (float), tenor_days (int >=1), "
    "structure ('straddle' or 'strangle'), quantity (float >0), "
    "max_days_in_trade (int >=1 or null). Produce 3-5 proposals."
)


class LLMResearchAgent:
    """LLM-backed research agent: reads regime data, proposes changes.

    The agent receives the current baseline config, recent market features
    and backtest results from the journal, and asks the LLM for parameter
    proposals. The LLM's structured output is parsed into
    :class:`ResearchProposal` objects; invalid proposals are silently
    dropped (the evaluator will catch inconsistent configs).
    """

    def __init__(self, backend: LLMBackend, journal: EventLog | None = None) -> None:
        self._backend = backend
        self._journal = journal

    @property
    def name(self) -> str:
        return "llm_research"

    def propose(self, baseline: VRPConfig) -> Sequence[ResearchProposal]:
        context_parts = [
            f"Current baseline config: entry_vrp_min={baseline.entry_vrp_min}, "
            f"exit_vrp_max={baseline.exit_vrp_max}, tenor_days={baseline.tenor_days}, "
            f"structure={baseline.structure}, quantity={baseline.quantity}, "
            f"max_days_in_trade={baseline.max_days_in_trade}."
        ]

        if self._journal is not None:
            features = extract_events(self._journal, "market_features")[-5:]
            if features:
                context_parts.append(f"Recent market features:\n{events_to_context(features)}")
            backtests = extract_events(self._journal, "backtest_result")[-3:]
            if backtests:
                context_parts.append(f"Recent backtest results:\n{events_to_context(backtests)}")

        prompt = "\n\n".join(context_parts)
        response = self._backend.complete(_RESEARCH_SYSTEM, prompt)
        return _parse_proposals(response.text, baseline)


def _parse_proposals(text: str, baseline: VRPConfig) -> list[ResearchProposal]:
    """Parse LLM response into proposals; invalid entries silently dropped."""
    start = text.find("[")
    end = text.rfind("]")
    if start == -1 or end == -1:
        return []
    try:
        items = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return []
    if not isinstance(items, list):
        return []

    proposals: list[ResearchProposal] = []
    for i, item in enumerate(items):
        if not isinstance(item, dict):
            continue
        thesis = str(item.get("thesis", ""))
        changes = item.get("changes", {})
        if not isinstance(changes, dict) or not thesis:
            continue
        try:
            kwargs = {}
            for key, value in changes.items():
                if hasattr(baseline, key):
                    kwargs[key] = value
            config = replace(baseline, **kwargs)
            config.__post_init__()
            proposals.append(
                ResearchProposal(
                    proposal_id=f"llm-{i + 1:03d}",
                    thesis=thesis,
                    config=config,
                    source="llm_research",
                    changes=changes,
                )
            )
        except (ValueError, TypeError):
            continue
    return proposals


__all__ = ["GridSearchAgent", "LLMResearchAgent", "ResearchAgent"]

"""Deterministic groundedness auditing of agent claims (the Prism auditor pattern).

Agents in OptiTrade observe, explain and propose — they never execute. The
counterpart discipline is that agent *text* is only trusted insofar as it
cites journaled engine output: every numeric assertion an agent makes must be
traceable to an event the deterministic engines actually wrote. This module
scores that property with no LLM anywhere in the loop.

An :class:`AgentClaim` carries the journal sequence numbers it cites and the
named numeric values it asserts; the :class:`GroundednessAuditor` replays the
journal and checks that every asserted number is reachable (via a recursive
walk of nested dicts/lists/tuples) inside the cited events' ``data`` payloads,
comparing with :func:`math.isclose`. A claim with no citations is ungrounded
by definition — unverifiable prose scores zero.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass

from optitrade.journal.event_log import EventLog
from optitrade.journal.events import Event


@dataclass(frozen=True, slots=True)
class AgentClaim:
    """One agent assertion to be checked against the journal.

    ``citations`` are journal sequence numbers; ``values`` are the named
    numeric assertions extracted from ``statement``, e.g.
    ``(("delta", 7.35),)``.
    """

    claim_id: str
    statement: str
    citations: tuple[int, ...]
    values: tuple[tuple[str, float], ...]


@dataclass(frozen=True)
class ClaimVerdict:
    """Audit outcome for one claim; ``reasons`` spells out every failure."""

    claim_id: str
    grounded: bool
    reasons: tuple[str, ...]
    matched: tuple[tuple[str, int], ...]  # value name -> sequence where found


@dataclass(frozen=True)
class GroundednessReport:
    """Verdicts over one audited batch of claims."""

    verdicts: tuple[ClaimVerdict, ...]

    @property
    def grounded_rate(self) -> float:
        """Fraction of claims grounded; 1.0 for an empty batch (nothing failed)."""
        if not self.verdicts:
            return 1.0
        return sum(1 for v in self.verdicts if v.grounded) / len(self.verdicts)

    def summary(self) -> str:
        """One-paragraph plain-text account of the audit."""
        total = len(self.verdicts)
        if total == 0:
            return "No claims were audited."
        grounded = sum(1 for v in self.verdicts if v.grounded)
        text = (
            f"Audited {total} claim{'s' if total != 1 else ''}: "
            f"{grounded} grounded, {total - grounded} ungrounded "
            f"(grounded rate {self.grounded_rate:.1%})."
        )
        failures = [v for v in self.verdicts if not v.grounded]
        if failures:
            detail = "; ".join(f"{v.claim_id} ({', '.join(v.reasons)})" for v in failures)
            text += f" Ungrounded: {detail}."
        return text


def _iter_numbers(node: object) -> Iterator[float]:
    """Yield every number reachable in ``node`` (nested dicts/lists/tuples).

    ``bool`` is excluded: ``True == 1`` in Python, but a flag is not a numeric
    engine output and must not ground a numeric claim.
    """
    if isinstance(node, bool):
        return
    if isinstance(node, int | float):
        yield float(node)
    elif isinstance(node, dict):
        for value in node.values():
            yield from _iter_numbers(value)
    elif isinstance(node, list | tuple):
        for item in node:
            yield from _iter_numbers(item)


def _audit_claim(
    claim: AgentClaim,
    events: dict[int, Event],
    rtol: float,
    atol: float,
) -> ClaimVerdict:
    if not claim.citations:
        return ClaimVerdict(
            claim_id=claim.claim_id, grounded=False, reasons=("no citations",), matched=()
        )
    reasons: list[str] = []
    cited: list[Event] = []
    for sequence in claim.citations:
        event = events.get(sequence)
        if event is None:
            reasons.append(f"citation {sequence} does not exist")
        else:
            cited.append(event)
    matched: list[tuple[str, int]] = []
    for name, value in claim.values:
        source = next(
            (
                event.sequence
                for event in cited
                if any(
                    math.isclose(value, candidate, rel_tol=rtol, abs_tol=atol)
                    for candidate in _iter_numbers(event.data)
                )
            ),
            None,
        )
        if source is None:
            reasons.append(f"value {name}={value:g} not found within rtol in cited events")
        else:
            matched.append((name, source))
    return ClaimVerdict(
        claim_id=claim.claim_id,
        grounded=not reasons,
        reasons=tuple(reasons),
        matched=tuple(matched),
    )


class GroundednessAuditor:
    """Scores agent claims against journaled engine facts; fully deterministic.

    Accepts either a live :class:`EventLog` — replayed afresh on every
    ``audit`` call, so events appended after construction are visible — or a
    pre-materialised sequence of :class:`Event`.
    """

    def __init__(self, journal: EventLog | Sequence[Event]) -> None:
        self._journal: EventLog | None
        if isinstance(journal, EventLog):
            self._journal = journal
            self._events: dict[int, Event] = {}
        else:
            self._journal = None
            self._events = {event.sequence: event for event in journal}

    def _events_by_sequence(self) -> dict[int, Event]:
        if self._journal is not None:
            return {event.sequence: event for event in self._journal.replay()}
        return self._events

    def audit(
        self,
        claims: Iterable[AgentClaim],
        rtol: float = 1e-6,
        atol: float = 1e-9,
    ) -> GroundednessReport:
        """Audit ``claims``: grounded iff at least one citation, all cited
        sequences exist, and every asserted value matches a number in the
        cited events' data within ``math.isclose(rtol, atol)``."""
        events = self._events_by_sequence()
        return GroundednessReport(
            verdicts=tuple(_audit_claim(claim, events, rtol, atol) for claim in claims)
        )


__all__ = ["AgentClaim", "ClaimVerdict", "GroundednessAuditor", "GroundednessReport"]

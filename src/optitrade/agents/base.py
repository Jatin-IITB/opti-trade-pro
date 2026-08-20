"""Base types for LLM-backed agents — the Prism agentic layer (ADR-021).

Agents observe, explain and propose; they never touch order flow (ADR-015).
The :class:`LLMBackend` protocol abstracts the language model so the analyst
layer is testable with deterministic mocks and pluggable across providers.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from optitrade.journal.event_log import EventLog
from optitrade.journal.events import Event


@dataclass(frozen=True, slots=True)
class LLMResponse:
    """Response from an LLM backend."""

    text: str
    raw: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class LLMBackend(Protocol):
    """Abstraction over the language model provider.

    Implementations: :class:`DspyBackend` (production), plain callables or
    lambdas (tests). The backend must be synchronous — async adapters wrap
    with ``asyncio.to_thread``.
    """

    def complete(self, system: str, user: str) -> LLMResponse: ...


class DspyBackend:
    """LLM backend wrapping dspy.ChainOfThought.

    Requires the ``[agentic]`` extra (``dspy >= 2.5``). The dspy language model
    must be configured globally (``dspy.configure(lm=...)`` or ``dspy.settings.
    configure(lm=...)``) before any ``complete`` call.
    """

    def __init__(self) -> None:
        try:
            import dspy  # type: ignore[import-not-found,import-untyped]
        except ImportError as exc:
            raise ImportError(
                "DspyBackend requires the optional 'dspy' dependency: "
                "pip install 'optitrade-pro[agentic]'"
            ) from exc

        class _ChatSignature(dspy.Signature):
            """System + user prompt → response."""

            system_prompt: str = dspy.InputField(desc="system instructions")
            user_prompt: str = dspy.InputField(desc="user message with context")
            response: str = dspy.OutputField(desc="the assistant response")

        self._predict = dspy.ChainOfThought(_ChatSignature)

    def complete(self, system: str, user: str) -> LLMResponse:
        result: Any = self._predict(system_prompt=system, user_prompt=user)
        text = str(result.response)
        return LLMResponse(text=text, raw={"rationale": str(getattr(result, "rationale", ""))})


def events_to_context(events: list[Event]) -> str:
    """Format journal events as JSON context for an LLM prompt."""
    return json.dumps(
        [
            {
                "sequence": e.sequence,
                "event_type": e.event_type,
                "timestamp": e.timestamp,
                "data": e.data,
            }
            for e in events
        ],
        indent=2,
        default=str,
    )


def extract_events(journal: EventLog, event_type: str) -> list[Event]:
    """All events of ``event_type`` from the journal, chronological."""
    return [e for e in journal.replay() if e.event_type == event_type]


def latest_event(journal: EventLog, event_type: str) -> Event:
    """Most recent event of ``event_type``; raises when absent."""
    latest: Event | None = None
    for event in journal.replay():
        if event.event_type == event_type:
            latest = event
    if latest is None:
        raise ValueError(
            f"journal contains no '{event_type}' event; the analyst has nothing to report on"
        )
    return latest


__all__ = [
    "DspyBackend",
    "LLMBackend",
    "LLMResponse",
    "events_to_context",
    "extract_events",
    "latest_event",
]

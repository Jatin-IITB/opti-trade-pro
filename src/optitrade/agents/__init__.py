"""LLM-backed agents: the Prism agentic layer over deterministic rails (ADR-021).

Agents observe, explain and propose — they never execute. Each LLM analyst
mirrors a deterministic reference implementation, replacing template text
with LLM-generated narrative while keeping deterministic, journal-grounded
claims. The :class:`AnalystOrchestrator` runs both tiers and merges reports.
"""

from optitrade.agents.base import (
    DspyBackend,
    LLMBackend,
    LLMResponse,
    events_to_context,
    extract_events,
    latest_event,
)
from optitrade.agents.llm_analyst import (
    LLMPostMortemAnalyst,
    LLMRegimeAnalyst,
    LLMSurfaceAnalyst,
)
from optitrade.agents.orchestrator import (
    AnalystFailure,
    AnalystOrchestrator,
    OrchestratorReport,
)

__all__ = [
    "AnalystFailure",
    "AnalystOrchestrator",
    "DspyBackend",
    "LLMBackend",
    "LLMPostMortemAnalyst",
    "LLMRegimeAnalyst",
    "LLMResponse",
    "LLMSurfaceAnalyst",
    "OrchestratorReport",
    "events_to_context",
    "extract_events",
    "latest_event",
]

# ADR-021: LLM agent adapters over deterministic rails

## Status
Accepted

## Context
Phase 5 of the flagship plan calls for LLM adapters on the analyst layer. The
four deterministic analysts (Surface Auditor, Regime Analyst, Risk Officer,
Post-Mortem) are complete and self-auditing (ADR-018/020). The question is how
to add LLM-generated narrative without breaking the groundedness invariant.

## Decision
- **Hybrid architecture**: LLM analysts mirror the deterministic reference
  implementations, replacing template text with LLM-generated narrative.
  Claims are built from journal event data (deterministic), not from LLM
  output. The LLM provides richer prose; the groundedness auditor still
  checks the machine-built claims.
- **`LLMBackend` protocol** (`optitrade.agents.base`): abstracts the LLM
  provider. `DspyBackend` wraps dspy.ChainOfThought; tests use a plain mock.
  The backend is synchronous; async adapters wrap with `asyncio.to_thread`.
- **Three LLM analysts** (`optitrade.agents.llm_analyst`): `LLMSurfaceAnalyst`,
  `LLMRegimeAnalyst`, `LLMPostMortemAnalyst`. Each reads the same journal
  event as its deterministic counterpart, builds identical claims, and sends
  the event data to the LLM for narrative.
- **`AnalystOrchestrator`** (`optitrade.agents.orchestrator`): runs both
  deterministic and LLM analysts, merges reports. LLM failures are captured
  as `AnalystFailure`, never propagated (fail open for the analyst layer;
  the risk engine is the fail-closed boundary).
- **Optional dependency**: LLM analysts require `[agentic]` extra (dspy).
  Without it, only deterministic analysts are available.

Enforcing tests: `test_llm_analyst.py` (100% groundedness with mock backend),
`test_orchestrator.py` (failure capture, mixed-tier merging).

## Consequences
### Positive
- LLM narrative enriches reports without compromising groundedness.
- The deterministic tier is always available as a fallback.
- Mock backends make the entire agent layer deterministically testable.
### Negative
- LLM analysts cannot currently make claims beyond what the deterministic
  extraction produces (no cross-event reasoning yet).
### Risks
- LLM latency: the orchestrator runs analysts sequentially; async
  parallelisation is future work.

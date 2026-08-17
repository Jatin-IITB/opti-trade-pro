# ADR-010: Trade-decision governance — deterministic expert debate with confident veto; LLM experts optional

## Status
Accepted

## Context
Prism's hero pipeline reviews consequential actions with a multi-expert debate
(ExpertEvaluation → DebateConsensus). A trading engine wants the same shape — risk officer,
strategy, and execution perspectives on every proposal — but the core must stay
deterministic, testable, and runnable without LLM credentials.

## Decision
- `optitrade/governance`: `Expert` protocol; three rule-based experts by default
  (`RiskOfficer` reusing the risk-check arithmetic, `StrategyExpert` on edge/cost ratio and
  vol-view consistency, `ExecutionExpert` on cost share of edge).
- `DebatePanel.deliberate(proposal)` → confidence-weighted approval score in [−1, 1];
  APPROVE requires score ≥ threshold **and** no REJECT with confidence ≥ 0.9 — a confident
  risk veto beats any majority (game-theoretically: the veto player prices the tail risk the
  majority averages away).
- Dissents are preserved verbatim in the `DecisionRecord`; the record is journaled.
- Experts fail closed: an expert that raises contributes REJECT at confidence 1.0.
- LLM-backed experts are an optional adapter (`dspy_adapter.LLMExpert`, extra
  `optitrade-pro[agentic]`, mirrors Prism's ExpertEvaluationSignature). The default panel
  never needs a network.

## Consequences
### Positive
- Deterministic, unit-testable governance; the LLM path is additive, not load-bearing.
### Negative
- Rule-based experts encode simple heuristics; nuance requires either better rules or the
  LLM adapter.
### Risks
- Veto threshold (0.9) is a policy constant; recorded here so changing it requires a
  superseding ADR.

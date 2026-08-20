# Debate: Should the research loop auto-apply accepted proposals?

- **Date**: 2026-08-20
- **Drivers**: Phase 6 design: agents propose parameter changes, backtests
  evaluate; the question is whether accepted proposals should auto-update the
  strategy config or require human approval.
- **Options**:
  A) Auto-apply: accepted proposals update the live config immediately,
     journaled as a config_change event.
  B) Human gate: accepted proposals are presented for review; the operator
     approves through the governance pipeline (debate → ADR → config update).
  C) Staged: auto-apply to a shadow desk, human approves promotion to the
     primary desk.
- **Outcome**: B (human gate) → ADR-022

## Expert opinions

### Risk Officer (confidence 0.95)
**Assessment** — Auto-application of parameter changes to a live desk is a
control failure. The deflated Sharpe on a 40-day synthetic window is evidence
of local improvement, not proof of robustness. Walk-forward overfitting to
the specific synthetic seed is the known failure mode (Bailey & López de
Prado 2014); a human must review the improvement in context of the current
regime and accumulated real history before committing.
**Concerns**
- Auto-apply bypasses the governance pipeline that governs all other design
  decisions.
- Synthetic-data improvements may not transfer to real markets.
**Position** — B.

### Strategy Expert (confidence 0.85)
**Assessment** — Option C (shadow desk) is elegant but premature: we do not
yet have the infrastructure for parallel desk execution and drift tracking
between primary and shadow. Option B is the minimal viable control that
preserves the governance invariant. The research loop's value is in
*surfacing* candidates, not in *applying* them.
**Concerns**
- Option B adds human latency to the feedback loop; a daily research cycle
  cannot iterate faster than the human review cadence.
**Position** — B.

### Execution Expert (confidence 0.80)
**Assessment** — The MCP `run_experiment` tool makes walk-forward available
to agents, but the tool returns results, never mutates config. This is the
correct separation: compute in the tool, decisions in the governance
pipeline. Option A blurs the boundary.
**Concerns**
- Walk-forward cost: each proposal costs O(|grid| × folds × days) backtest
  steps. Grid-search with 15 proposals and 4 folds on 60 days ≈ 3,600
  backtest-days. Manageable but not free.
**Position** — B.

## Consensus
Option B: human approval gate. Accepted proposals generate
`research_accepted` journal events; the operator reviews and, if approved,
commits the change through the standard debate → ADR → config update
pipeline. The research loop surfaces candidates; humans decide.

## Dissents
None — all experts agreed that auto-application of strategy parameters is a
governance failure.

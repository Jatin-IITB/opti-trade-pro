# ADR-018: Daily desk cycle — deterministic orchestration, file-based kill switch, self-auditing analysts

## Status
Accepted

## Context
Phase 4 needs the closed loop: snapshot → decide → review → (paper) fill → hedge →
journal, runnable unattended, with a stop mechanism that does not depend on the process
being healthy. Phase 5 needs analyst agents whose output can be trusted mechanically.

## Decision
- `optitrade/desk/cycle.py`: `run_daily_cycle` — one pure orchestration function per
  trading day: mark the book, ask the `Strategy` protocol for a decision, route ENTER/EXIT
  orders through the debate panel (optional) and the fail-closed risk engine, paper-fill
  approved orders paying the configured spread, take the Whalley–Wilmott hedge decision,
  and journal a `daily_cycle` event. A HALT verdict (drawdown breach) engages the kill
  switch mid-cycle and cancels remaining orders.
- `optitrade/desk/kill_switch.py`: file-based halt (`runtime_data/HALT` with reason and
  timestamp). Deliberately primitive: any process — or a human with `touch` — can stop the
  desk, and an engaged switch survives crashes and restarts. Every subsequent cycle
  short-circuits to `cycle_skipped` until a human `reset()`.
- `optitrade/desk/analysts.py`: the first analyst agents (Surface Auditor, Post-Mortem
  Analyst) are **deterministic** — they read journal events, write plain-English reports
  with the numbers, attach `AgentClaim` citations, and run the groundedness auditor on
  **their own report** before returning it. A report that fails its own audit is a bug,
  not an output. LLM-backed analysts (ADR-015) plug in behind the same report shape.

Enforcing tests: `tests/unit/quant/test_daily_cycle.py`, `test_kill_switch.py`,
`test_analysts.py` (analyst self-audit must ground at 100%).

## Consequences
### Positive
- The money path is one auditable function; halting is possible from outside the process;
  analyst output is mechanically verifiable before anyone reads it.
### Negative
- Paper fills at spread-adjusted mid ignore queue position and impact — stated limitation
  until the broker adapter lands.
### Risks
- Kill-switch file deleted by accident — mitigated by journaling every engage/reset with
  reasons, and by the drawdown check re-engaging on the next cycle if the breach persists.

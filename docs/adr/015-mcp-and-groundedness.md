# ADR-015: Agent layer — MCP server over the engines; deterministic groundedness audit; no LLM in the money path

## Status
Accepted

## Context
The defensible agentic design (from Prism, operated at production scale there) is a
deterministic engine exposed as tools, with agents above it doing analyst work — surface
QC, regime commentary, P&L forensics — every claim grounded in computed numbers. "LLM
picks trades" is explicitly rejected.

## Decision
- **Hard rule**: nothing an LLM produces reaches order flow. Agents observe, explain, and
  propose; the deterministic debate panel + risk engine (ADR-008/010) own execution
  decisions.
- `optitrade/mcp_server.py`: MCP stdio server (optional `optitrade-pro[mcp]` extra, lazy
  import) exposing `price_option`, `book_greeks`, `run_scenarios`, `review_order`,
  `journal_tail`. Every tool call is journaled as a `tool_call` event — this journaling is
  load-bearing: it is what agent citations point at.
- `optitrade/audit/groundedness.py`: the Prism auditor pattern, deterministic — an
  `AgentClaim` carries citations (journal sequence numbers) and named numeric assertions;
  a claim is grounded iff all citations exist and every number matches the cited events'
  data within tolerance. `GroundednessReport.grounded_rate` is the agent-quality metric.
- Analyst agents themselves (Surface Auditor, Regime Analyst, Risk Officer, Post-Mortem
  Analyst) are roadmap phase 5, built on these two primitives.

Enforcing tests: `tests/unit/quant/test_groundedness.py`, `test_mcp_server.py`.

## Consequences
### Positive
- Any MCP-capable agent framework can sit on the desk; agent output is scoreable against
  ground truth without trusting the agent.
### Negative
- Tool surface must be kept in sync with engine capabilities; thin-adapter discipline
  applies (no math in the server).
### Risks
- Journal bloat from chatty agents — one journal per run and rotation-by-run_id contain it
  (ADR-009).

# ADR-022: Research loop — backtest-as-tool with human approval gate

## Status
Accepted

## Context
Phase 6 of the flagship plan: agents propose parameter/strategy changes,
walk-forward evaluation ranks them, and humans approve. The goal is to close
the loop between observation (analysts) and adaptation (strategy tuning)
while keeping every decision auditable and every metric honest (deflated
Sharpe with trial accounting from ADR-016).

## Decision
- **`ResearchProposal`** (`optitrade.research.proposals`): a proposed
  parameter change with a thesis, the complete `VRPConfig`, and provenance
  (`source`). The proposal is a value object, not a command — it never
  mutates state.
- **`ProposalEvaluator`** (`optitrade.research.evaluator`): wraps
  `run_walk_forward` to compare a candidate config against a baseline. The
  baseline result is cached across proposals. Each experiment is journaled as
  `experiment_result`.
- **`ResearchAgent` protocol** (`optitrade.research.agent`): produces
  proposals from a baseline config. Two implementations:
  - `GridSearchAgent` (deterministic): varies one parameter at a time from
    the baseline — the reference implementation, always available.
  - `LLMResearchAgent` (optional): reads regime data and backtest results
    from the journal, asks the LLM for proposals in structured JSON. Invalid
    proposals are silently dropped; the evaluator catches inconsistent configs.
- **`ResearchLoop`** (`optitrade.research.loop`): orchestrates propose →
  evaluate → rank → journal. Accepted proposals generate `research_accepted`
  events. The loop **never applies** a proposed change; a human reviews and
  approves through the standard governance pipeline (debate → ADR → config
  update).
- **MCP tool `run_experiment`** (`optitrade.mcp_server`): backtest-as-tool
  for agents. Takes a VRPConfig dict, runs walk-forward over a synthetic
  market, journals the result, and returns OOS Sharpe and DSR.
- **CLI `optitrade research`**: runs the grid-search research loop over a
  synthetic market, prints ranked results.

Enforcing tests: `test_research_agent.py` (proposal generation and parsing),
`test_research_evaluator.py` (walk-forward evaluation and caching),
`test_research_loop.py` (end-to-end loop and journaling).

## Consequences
### Positive
- The research cycle is auditable end-to-end: every proposal, evaluation,
  and acceptance is journaled with sequence numbers.
- Grid search and LLM proposals use the same evaluation pipeline — no
  special path for any proposer.
- Human approval gate prevents automated strategy drift; the governance
  pipeline is the same one used for engineering decisions.
### Negative
- Walk-forward evaluation is expensive (one full backtest per proposal per
  fold); real-time research requires async execution or job queuing.
- The grid-search agent explores a fixed parameter space; creative proposals
  require the LLM agent (optional dependency).
### Risks
- Overfitting to synthetic data: the research loop must eventually run on
  real captured history (StoreReplay from ADR-019) to be decision-relevant.

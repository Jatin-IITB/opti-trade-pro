# Roadmap — from pricing engine to autonomous volatility desk

v3.0 (this rebuild) ships the deterministic foundation: the four engines, governance, and
the journal. The flagship direction — settled 2026-08-17 — evolves it into an autonomous
volatility desk with a strict rule: **deterministic money path; agents observe, explain,
and propose only.**

## Shipped in v3.0 (foundation)

- Vol surface: spline + per-expiry SABR (Hagan), no-arb validation (ADR-005)
- Greeks: analytic / finite-diff / from-scratch adjoint AD, scenario grids (ADR-006)
- Hedging: Whalley–Wilmott bands + RV/IV gamma scalping + GBM sim (ADR-007)
- Risk: fail-closed pre-trade engine (ADR-008); journal (ADR-009); debate panel (ADR-010)

## Phase status (2026-08-22)

| Phase | State |
|---|---|
| 0 Data pipeline | **Complete** — filters + Parquet store + capture API + unattended IST-window scheduler (ADR-019); accumulate real days by starting `/capture/schedule/start` |
| 1 Joint surface | **Built** — eSSVI joint fit, Durrleman + RND gates, SABR benchmark (ADR-012) |
| 2 AAD + P&L explain | **Complete** — P&L explain + PCA factors + bucket reports; JAX AAD with exact higher-order Greeks and `vmap` book vectorisation (ADR-023) |
| 3 Strategy + backtest | **Built** — VRP strategy, Indian cost model, walk-forward + deflated Sharpe (ADR-016/017); `StoreReplay` runs it on real history as it accumulates |
| 4 Paper loop | **Built** — daily cycle, kill switch, daily report, backtest-vs-desk drift metric (ADR-018/019/020); remaining: wire cycle to live captures on a schedule, drive drift toward zero |
| 5 Agent layer | **Complete** — MCP server, groundedness auditor, all four deterministic analysts, LLM adapters over the same rails (`LLMBackend` protocol, `DspyBackend`, `AnalystOrchestrator`), MCP `run_experiment` tool (ADR-021) |
| 6 Research loop | **Built** — `GridSearchAgent` + `LLMResearchAgent` propose, `ProposalEvaluator` runs walk-forward, `ResearchLoop` orchestrates → rank → journal; human approval gate before config changes land as ADRs; `optitrade research` CLI (ADR-022) |

## Phase detail

0. **Data pipeline** — chain-snapshot capture from Upstox with quote filtering → Parquet;
   replayable market history.
1. **Joint surface calibration** — eSSVI across expiries with butterfly/calendar
   constraints imposed at fit time (upgrade over per-expiry SABR, which stays as the
   benchmark); Breeden–Litzenberger risk-neutral-density sanity checks.
2. **AAD at scale** — ADR-006's revisit trigger resolved (ADR-023): `jax.grad` nesting
   gives exact all-order Greeks (charm/veta/speed/color/ultima/zomma); `jax.vmap` fuses
   the book into one XLA kernel; daily P&L explain (theta / gamma-vs-RV / vega-vs-surface
   factors / residual) on top of `hedging.pnl`.
3. **Strategy layer** — variance-risk-premium harvesting (IV–RV gate with term/skew regime
   filters), walk-forward backtests with real Indian cost model (charges), deflated Sharpe
   reporting.
4. **Live paper loop** — scheduled snapshot → decide (debate + risk) → paper-execute →
   journal; monitoring dashboards and a kill switch wired to `DrawdownCheck`'s HALT.
5. **Agentic layer (Prism patterns)** — expose the engines as an MCP server; narrow analyst
   agents (Surface Auditor, Regime Analyst, Risk Officer, Post-Mortem Analyst) consuming
   the journal; groundedness audit harness scoring agent claims against journaled facts.
6. **Research loop** — agents propose parameter/strategy changes with backtest-as-tool;
   humans approve; every accepted change lands as an ADR.

Each phase lands through the governance pipeline: debate record → ADR → implementation →
enforcing tests (docs/governance.md).

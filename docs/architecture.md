# Architecture

Two packages, one-way dependency (ADR-002):

```
┌────────────────────────────────────────────────────────────────┐
│  options_trading  (platform layer)                             │
│  FastAPI app · Upstox OAuth/market data · dashboards · WS      │
│  api/routes/analytics.py  ──  thin adapter over the core       │
└───────────────────────────┬────────────────────────────────────┘
                            │ imports (never the reverse)
┌───────────────────────────▼────────────────────────────────────┐
│  optitrade  (quant core — numpy/scipy, strict types, no I/O)   │
│                                                                │
│  core        types (Greeks, Order, Portfolio…) · errors        │
│  pricing     BS-Merton · analytic Greeks · implied vol         │
│  vol         spline smiles · SABR (Hagan) · no-arb checks      │
│  greeks      finite-diff · adjoint AD tape · scenario grids    │
│  hedging     WW band · gamma scalper · P&L attribution         │
│  risk        fail-closed pre-trade checks (ADR-008)            │
│  governance  expert debate panel (ADR-010)                     │
│  journal     append-only JSONL event log (ADR-009)             │
│  attribution Shapley P&L credit                                │
│  backtest    GBM paths · hedging simulation                    │
└────────────────────────────────────────────────────────────────┘
```

## Decision flow for a trade

```
TradeProposal
   │
   ▼
DebatePanel.deliberate()          RiskOfficer / StrategyExpert / ExecutionExpert
   │  DecisionRecord ──────────►  journal: debate_decision
   ▼ (approved)
RiskEngine.review(order, ctx)     GreeksLimit / Margin / Drawdown / Concentration
   │  RiskDecision ────────────►  journal: risk_decision
   ▼ (approved / resized)
execution (platform layer)
   │
   ▼
DeltaHedger.decide()  per rebalance tick
      HedgeDecision ───────────►  journal: hedge_decision
```

Every arrow into the journal carries a correlation ID, so one order's debate → risk review →
hedge chain replays as a unit (`EventLog.events_by_correlation`).

## Engines and their enforcing tests

| Engine | Headline behaviour | Enforced by |
|---|---|---|
| Vol surface | SABR round-trip RMSE < 0.3 vol-pt | `tests/unit/quant/test_sabr.py` |
| Vol surface | static no-arb detection (butterfly/calendar) | `tests/unit/quant/test_arbitrage.py` |
| Greeks | analytic ≡ finite-diff ≡ adjoint AD | `tests/unit/quant/test_greeks_cross.py` |
| Greeks | ≥500-cell ΔS×Δσ×Δt grid, 50 positions, <200 ms | `tests/unit/quant/test_scenario.py` |
| Hedging | hedged P&L tracks theoretical theta in GBM sim | `tests/unit/quant/test_hedging_sim.py` |
| Risk | 100% of out-of-bound orders blocked (property test) | `tests/unit/quant/test_risk.py` |
| Journal | replay + sequence recovery | `tests/unit/quant/test_journal.py` |
| Governance | confident-veto consensus | `tests/unit/quant/test_governance.py` |

## Layering rules

- `optitrade` never imports `options_trading`, FastAPI, or broker SDKs.
- The platform maps broker/market payloads into `optitrade.core` types at the boundary
  (`MarketSnapshot`, `BookPosition`) and maps results back out.
- All tunables are typed config dataclasses; no magic numbers inside flows (CLAUDE.md rule 2).

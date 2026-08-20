# ADR-017: Transaction costs — typed Indian F&O cost model in the core

## Status
Accepted

## Context
A short-vol strategy's edge is a few vol points; Indian F&O costs (STT on sell premium,
exchange transaction charges, GST, SEBI fees, stamp duty, flat brokerage) are large enough
to flip the sign. The platform's `charges.py` queries Upstox's brokerage API — correct for
live checks, unusable inside a backtest loop (network, rate limits, no history).

## Decision
`optitrade/strategy/costs.py`: `IndianCostRates` — a frozen dataclass holding every rate
(flat brokerage per order, STT fraction on sell-side premium, NSE exchange transaction
fraction, GST on the fee subtotal, SEBI turnover fee, buy-side stamp duty), each field
documenting its rate-card source and dated "as of 2025". `IndianOptionsCostModel` computes
per-fill `CostBreakdown`s (never a single opaque number) plus a proportional cost for
underlying hedge fills. No rate appears anywhere outside the dataclass (CLAUDE.md rule 2).
The live Upstox charges tool remains the pre-trade margin/brokerage check at the platform
boundary; the core model is the backtest/paper truth.

Enforcing test: `tests/unit/quant/test_costs.py` (hand-checked against the documented
rate card, exact).

## Consequences
### Positive
- Backtests and the paper desk price costs identically and offline; rate changes are a
  one-line config edit with a visible diff.
### Negative
- Two cost sources (core model, broker API) can drift; reconciling them on live fills is
  a phase-4 monitoring task.
### Risks
- Regulatory rate changes (STT has moved twice since 2023) — dated fields and the
  breakdown structure make audits trivial.

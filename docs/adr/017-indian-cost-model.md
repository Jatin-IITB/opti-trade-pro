# ADR-017: Transaction costs — typed Indian F&O cost model in the core

## Status
Accepted

## Context
A short-vol strategy's edge is a few vol points; Indian F&O costs (STT on sell premium,
exchange transaction charges, GST, SEBI fees, stamp duty, flat brokerage) are large enough
to flip the sign. A broker charges API is correct for live pre-trade checks but unusable
inside a backtest loop (network, rate limits, no history).

## Decision
`optitrade/strategy/costs.py`: `IndianCostRates` — a frozen dataclass holding every rate
(flat brokerage per order, STT fraction on sell-side premium, NSE exchange transaction
fraction, GST on the fee subtotal, SEBI turnover fee, buy-side stamp duty), each field
documenting its rate-card source and dated "as of 2025". `IndianOptionsCostModel` computes
per-fill `CostBreakdown`s (never a single opaque number) plus a proportional cost for
underlying hedge fills. No rate appears anywhere outside the dataclass (CLAUDE.md rule 2).
The core model is the single cost authority for backtest and paper trading.

Enforcing test: `tests/unit/quant/test_costs.py` (hand-checked against the documented
rate card, exact).

## Consequences
### Positive
- Backtests and the paper desk price costs identically and offline; rate changes are a
  one-line config edit with a visible diff.
### Negative
- Costs are modelled, not quoted: with no broker-side charges call in the platform there is
  nothing to reconcile the model against on live fills. Adding that check back means
  building it against the core model's `CostBreakdown`, not a second parallel calculator.
### Risks
- Regulatory rate changes (STT has moved twice since 2023) — dated fields and the
  breakdown structure make audits trivial.

## Amendment (2026-09-03)
The original text described the platform's `api/tools/charges.py` as the live pre-trade
brokerage check. That was never true in practice: the module was imported nowhere, and it
called FastAPI's `Query(...)` as a value, so every rate, timeout and retry count it used
was a `Query` object rather than a number. It was deleted rather than repaired — the core
cost model above already covers the calculation, and a broker-quoted pre-trade check is a
new feature to be specified when it is actually needed. This ADR's decision is unchanged;
only the false claim about an existing platform-side tool is withdrawn.

# ADR-008: Fail-closed pre-trade risk engine; rejections are decisions, not exceptions

## Status
Accepted

## Context
The report's headline claim — "blocking 100% of out-of-bound orders" — is only achievable if
no code path lets an order through by accident, including when the risk code itself fails.

## Decision
- Every order passes `RiskEngine.review(order, ctx)` before execution. Checks are small
  objects behind a `PreTradeCheck` protocol (open/closed: add checks without touching the
  engine): Greeks caps, margin sufficiency, drawdown halt, concentration resize.
- All checks always run (full report, no short-circuit). Verdict precedence
  HALT > REJECT > RESIZE > APPROVE; multiple resizes take the smallest allowed quantity.
- **Fail closed**: an exception inside any check becomes a REJECT result carrying the error
  message. There is no code path from check-failure to approval.
- A rejection is a normal `RiskDecision` value with plain-English, number-bearing reasons —
  not an exception — and is journaled (ADR-009).
- Enforcement is property-tested: randomized orders/limits/greeks, asserting no
  limit-breaching order is ever approved (`tests/unit/quant/test_risk.py`).

## Consequences
### Positive
- The 100%-blocked claim is a tested invariant, not a slogan; the audit trail explains every
  block with numbers.
### Negative
- Fail-closed means a buggy check can block legitimate trading — the correct failure mode
  for a risk system.
### Risks
- Context staleness (greeks computed before a market jump); mitigated by journaled
  timestamps and, later, revalidation on execution.

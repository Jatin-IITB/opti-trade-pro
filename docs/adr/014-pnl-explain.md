# ADR-014: Daily P&L explain — theta / gamma-vs-realized-variance / vega-by-surface-factor / residual

## Status
Accepted

## Context
A desk that cannot decompose its P&L cannot distinguish edge from luck. The flagship's
headline metric is "% of daily P&L explained"; the residual is where model error, unhedged
risk, and bugs hide.

## Decision
`optitrade/explain`:
- `factors.py`: PCA (numpy SVD) on daily IV-surface moves over a (moneyness × expiry)
  grid → level/term/skew factors with explained-variance ratios; heuristic naming,
  documented as heuristic.
- `pnl_explain.py`: `explain_pnl` decomposes a period's P&L into theta carry (θ·dt),
  delta (Δ·dS), gamma against **realized variance** (½ΓS²·σ_R²·dt — gamma P&L realises
  against variance, not one squared move), vega attributed **per surface factor** (book
  vega profile · reconstructed factor move), vanna/volga cross-terms, and residual.
  `explained_fraction` is the headline number; results journal as `pnl_explain` events.
- `bucket_report.py`: vega/gamma/theta exposures bucketed by expiry (0–7d, 7–30d, 30–90d,
  90d+) — the shape hedging decisions are actually made in; bucket sums must equal
  whole-book totals.

Enforcing tests: `tests/unit/quant/test_factors.py`, `test_pnl_explain.py`,
`test_bucket_report.py`.

## Consequences
### Positive
- The residual percentage turns "the hedging works" into a measured daily number; factor
  vega attribution says *which part of the surface* paid or charged.
### Negative
- Factor attribution quality depends on the factor model's history window; early days will
  attribute mostly to "level".
### Risks
- Explained-fraction can flatter when total P&L is near zero; the metric definition clamps
  and documents this.

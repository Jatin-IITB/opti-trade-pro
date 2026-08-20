# Debate: How to evaluate the VRP strategy — single optimized backtest, cross-validation, or walk-forward with deflated Sharpe?

- **Date**: 2026-08-18
- **Drivers**: Phase 3 (strategy + backtester). The flagship's credibility rests on the
  track-record numbers being un-flattered; "backtest Sharpe" is the most gamed number in
  quant hiring.
- **Options**: A) One backtest with parameters tuned on the full history. B) K-fold CV
  over shuffled days. C) Rolling walk-forward: tune on train window, evaluate untouched
  test window, stitch out-of-sample P&L, report **deflated** Sharpe accounting for every
  trial burned.
- **Outcome**: C → ADR-016

## Expert opinions

### Overfitting Skeptic (confidence 0.95)
**Assessment** — Option A's Sharpe is an in-sample maximum over the parameter grid — an
order statistic, not an estimate. Bailey & López de Prado (2014) quantify exactly this:
the expected max SR of n unskilled trials grows like √(2·ln n / n_obs). The deflated
Sharpe ratio inverts that: it reports the probability the observed OOS SR exceeds the
best-of-n-trials null, using the P&L's own skew and kurtosis (short-vol P&L is left-skewed
and fat-tailed — a plain t-stat flatters it twice).
**Concerns** — DSR needs an honest n_trials count; the harness must count every config ×
fold evaluated, not just the winners.
**Position** — C, with n_trials accounting built into the harness so it cannot be forgotten.

### Time-Series Methodologist (confidence 0.9)
**Assessment** — Option B (shuffled K-fold) leaks: vol regimes are autocorrelated, so
training on Wednesday and testing on Tuesday of the same regime is contaminated. Options
markets add a second leak through overlapping position lifetimes. Walk-forward with
contiguous, chronologically ordered train→test windows is the only split that respects
the arrow of time.
**Concerns** — Few folds ⇒ noisy OOS estimate; report per-fold tables, not just the stitch.
**Position** — C.

### Pragmatist (confidence 0.7)
**Assessment** — Walk-forward machinery is more code than one backtest, and with no real
history accumulated yet (data spine just landed), the first runs are on synthetic markets
where we *know* the answer. But that is precisely the argument for building the honest
harness now: validate it where ground truth exists, so when real snapshots accumulate the
methodology is already trustworthy.
**Concerns** — Synthetic markets flatter any strategy that matches their generating
process; label all synthetic results as such.
**Position** — C, with synthetic results clearly labelled and economic ground-truth tests
(positive VRP ⇒ profit, zero VRP ⇒ no trading) as the harness's own unit tests.

## Consensus
Walk-forward with per-fold parameter selection, stitched out-of-sample P&L, and deflated
Sharpe with full trial accounting (n_trials = |grid| × folds). Synthetic-market results
labelled synthetic; the same harness runs unchanged on stored real snapshots.

## Dissents
None on methodology; the Pragmatist's caveat about synthetic flattery is recorded as a
labelling requirement, not a disagreement.

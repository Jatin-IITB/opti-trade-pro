# ADR-016: First strategy — VRP harvesting; evaluation — walk-forward with deflated Sharpe

## Status
Accepted

## Context
The desk needs a first strategy that exercises the whole stack (surface → signal → debate
→ risk → hedge → P&L explain) and an evaluation methodology whose numbers survive
scrutiny. Debate: `docs/debates/2026-08-18-backtest-methodology.md`.

## Decision
- **Strategy** (`optitrade/strategy/vrp.py`): variance-risk-premium harvesting —
  delta-hedged short straddles/strangles gated on IV − RV ≥ threshold with optional
  term-slope/skew regime filters. All thresholds live in `VRPConfig`; theses carry the
  numbers. RV estimators (close-to-close, Parkinson 1980, Garman–Klass 1980) live in
  `optitrade/vol/realized.py`.
- **Interface** (`optitrade/strategy/base.py`): strategies are pure
  `decide(MarketDay, positions) → StrategyDecision` functions behind a `Strategy`
  protocol; the backtester and the live desk cycle consume the same protocol, so backtest
  code *is* production decision code.
- **Evaluation** (`optitrade/backtest/walk_forward.py`): rolling walk-forward — tune on
  the train window, evaluate untouched test windows, stitch out-of-sample P&L; report
  annualised OOS Sharpe **and** deflated Sharpe (Bailey & López de Prado 2014) with
  n_trials = |param grid| × folds counted by the harness itself, using OOS skew/kurtosis.
- **Ground truth first**: the harness's own tests run on synthetic VRP markets where the
  answer is known (positive premium ⇒ profit; zero premium ⇒ no trading; costs reduce
  P&L). All synthetic results are labelled synthetic.

Enforcing tests: `tests/unit/quant/test_vrp_strategy.py`, `test_walk_forward.py`,
`test_dsr.py`, `test_realized_vol.py`.

## Consequences
### Positive
- The headline Sharpe is out-of-sample and overfitting-discounted by construction; the
  strategy layer is swappable behind a protocol.
### Negative
- Walk-forward on short histories is noisy; per-fold tables must accompany the stitch.
### Risks
- Synthetic-market flattery — contained by labelling and by running the identical harness
  on stored real snapshots as they accumulate (ADR-013).

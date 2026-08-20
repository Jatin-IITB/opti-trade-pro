# ADR-020: Daily report artifact; Regime Analyst; structured-query Risk Officer

## Status
Accepted

## Context
An unattended desk needs one artifact a human reads each day, and the analyst roster from
the flagship plan (Surface Auditor, Regime Analyst, Risk Officer, Post-Mortem) needs its
remaining two members — under the standing rule that analyst output is journal-cited and
self-audited (ADR-015/018).

## Decision
- **Daily report** (`optitrade/desk/report.py`): one markdown artifact per run assembled
  from journal events — desk summary (from `daily_cycle`), then a section per analyst
  whose source event exists. Analysts whose events are missing are **listed as skipped**;
  a report must say what it could not cover (silent gaps read as coverage). Each section
  ends with its groundedness score; the report journals itself (`daily_report` event).
- **Regime Analyst**: narrates the day's regime (VRP size/sign, term slope, skew, RV vs
  IV) from a `market_features` event the cycle now journals before every strategy
  decision. Thresholds are init-config, not constants.
- **Risk Officer Analyst**: answers **structured** `ScenarioQuery`s (spot/vol/time
  shifts). It computes via the scenario engine, journals a `scenario_query` event, then
  cites the event it just wrote — the same compute→journal→cite pattern as the MCP tools.
  Natural-language parsing is deliberately out of the deterministic core: an optional LLM
  adapter may translate NL into `ScenarioQuery`, but the answer path stays deterministic
  and grounded.

Enforcing tests: `tests/unit/quant/test_daily_report.py`, `test_regime_analyst.py`,
`test_risk_officer_analyst.py` (all reports must ground at 100%).

## Consequences
### Positive
- The "42 days unattended" story has a daily, human-readable, mechanically verified
  artifact; the four-analyst roster from the flagship plan is complete in deterministic
  form.
### Negative
- Markdown is the only output format for now; dashboards come with the monitoring phase.
### Risks
- Report treating a skipped analyst as normal forever — the skipped list is printed at
  the bottom of every report precisely so the gap stays visible.

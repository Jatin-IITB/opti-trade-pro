# Changelog

Format: [Keep a Changelog](https://keepachangelog.com/); versioning: SemVer.

## [3.0.0] — Unreleased (feature/production-rebuild)

### Added (wave 5 — LLM agent adapters, research loop)
- `agents/`: `LLMBackend` protocol + `DspyBackend` wrapping dspy.ChainOfThought;
  three LLM analysts (`LLMSurfaceAnalyst`, `LLMRegimeAnalyst`,
  `LLMPostMortemAnalyst`) — hybrid design: deterministic claims, LLM-generated
  narrative, 100% groundedness invariant preserved; `AnalystOrchestrator` runs
  both deterministic and LLM tiers, captures failures (ADR-021).
- `research/`: `ResearchProposal`, `ExperimentResult`, `ResearchReport` types;
  `ProposalEvaluator` wraps walk-forward for baseline-vs-candidate comparison
  (cached baseline, journaled results); `GridSearchAgent` (deterministic:
  varies one parameter at a time) and `LLMResearchAgent` (parses structured
  JSON from LLM); `ResearchLoop` orchestrates propose → evaluate → rank →
  journal; human approval gate — loop surfaces candidates, never applies them
  (ADR-022).
- MCP `run_experiment` tool: backtest-as-tool for agents; takes a VRPConfig
  dict, runs walk-forward, journals and returns OOS Sharpe + DSR.
- CLI `optitrade research`: grid-search proposals evaluated via walk-forward
  over synthetic market, prints ranked results.
- Governance: ADR-021/022, debate records for LLM agent architecture and
  research loop design.

### Added (wave 4 — unattended capture, real-history replay, drift, daily report)
- `CaptureScheduler` + `/api/v1/capture/schedule/*`: unattended chain capture inside the
  IST market window; injected clock/sleeper, failure-tolerant loop, operator-started
  (ADR-019).
- `StoreReplay`: captured Parquet history → `MarketDay`s (EOD snapshot per date, filter →
  surface fit → trailing RV → features); walk-forward runs on real data unchanged.
- `desk/reconcile.py`: backtest-vs-desk drift metric (bps of equity, per-day table,
  correlation) — phase 4's exit criterion, isolating execution-model differences.
- `RegimeAnalyst` + `RiskOfficerAnalyst` (structured `ScenarioQuery`s, compute → journal
  → cite) completing the four-analyst roster; `market_features` journaled every cycle.
- `desk/report.py`: markdown daily report with per-section groundedness scores and an
  explicit coverage-gap list; wired into `optitrade cycle` (ADR-020).

### Added (wave 3 — capture, strategy, walk-forward, paper desk)
- `options_trading` capture: `UpstoxCaptureSource` (CaptureSource protocol over live
  chains), `/api/v1/capture/run` + `/capture/history`, clean-only Parquet persistence,
  `snapshot_store_path` setting.
- `strategy/`: `Strategy` protocol shared by backtester and desk; `VRPStrategy`
  (IV−RV gate, regime filters, numbered theses); `IndianOptionsCostModel` with per-fill
  `CostBreakdown` (ADR-016/017); `vol/realized.py` (Garman–Klass, Parkinson, close-close).
- `backtest/`: `SyntheticVRPMarket` replay, `run_backtest` (fail-closed risk in the loop,
  spread + cost fills, daily WW hedging), `run_walk_forward` with stitched OOS P&L and
  deflated Sharpe (Bailey–López de Prado 2014, n_trials counted by the harness).
- `desk/`: `run_daily_cycle` (strategy → debate → risk → paper fill → hedge → journal),
  file-based `KillSwitch` auto-engaged by drawdown HALT, self-auditing `SurfaceAuditor` +
  `PostMortemAnalyst` (ADR-018); `optitrade cycle` CLI paper-desk command.
- Governance: ADR-016..018, backtest-methodology debate record.

### Added (wave 2 — flagship phases 0/1/2/5 seeds)
- `vol/essvi.py`: SSVI joint calibration across expiries (Gatheral–Jacquier 2014) with
  structural θ monotonicity and in-fit butterfly penalties; `vol/density.py`
  Breeden–Litzenberger risk-neutral density gate; `check_durrleman` validation (ADR-012).
- `data/`: NSE-reality quote filters with per-reason audit stats; schema-versioned Parquet
  `SnapshotStore`; `CaptureSource` protocol + seeded `SyntheticSource` (ADR-013).
- `explain/`: PCA surface factors (level/term/skew), daily P&L explain with
  `explained_fraction` headline metric, expiry-bucketed exposure reports (ADR-014).
- `audit/`: deterministic groundedness auditor scoring agent claims against journaled
  engine facts; `mcp_server.py`: engines as journaling MCP tools, optional `[mcp]` extra
  (ADR-015).
- Analytics `/surface` endpoint now returns spline + SABR + eSSVI with fit diagnostics;
  demo CLI runs eSSVI + density gate + a groundedness audit in the journaled run.

### Added
- `optitrade` quant core package (numpy/scipy, strictly typed, standalone):
  - `pricing`: vectorised Black-Scholes-Merton + analytic Greeks; Newton–Raphson/Brent
    implied vol; chain stripping.
  - `vol`: cubic-spline smile slices in log-moneyness, total-variance time interpolation;
    per-expiry SABR (Hagan 2002) with fixed beta and seeded multi-start calibration;
    Breeden–Litzenberger butterfly and calendar no-arbitrage validation.
  - `greeks`: model-agnostic finite differences; from-scratch tape-based adjoint AD;
    fully vectorised ΔS×Δσ×Δt scenario-grid engine.
  - `hedging`: Whalley–Wilmott no-transaction band, RV/IV-modulated gamma scalping,
    Taylor P&L attribution, theta tracking-error metric.
  - `risk`: fail-closed pre-trade engine (Greeks caps, margin, drawdown halt,
    concentration resize) with property-tested 100% blocking of out-of-bound orders.
  - `journal`: append-only JSONL event log with sequence recovery and correlation IDs.
  - `governance`: deterministic expert debate panel (risk/strategy/execution) with
    confident-veto consensus; optional dspy LLM expert adapter.
  - `attribution`: exact Shapley-value P&L credit assignment.
  - `backtest`: GBM simulation and delta-hedging backtest harness.
- Governance mechanisms: `docs/adr/` (ADR-001…011), `docs/debates/` with template and three
  worked debates, `docs/governance.md`, `CLAUDE.md` engineering standards, this changelog.

### Changed
- Packaging consolidated on `pyproject.toml` (ruff + tiered mypy + pytest markers); honest
  dependency list.
- README rewritten to state only test-enforced claims.

### Removed
- Legacy duplicate tree (`/api`, `/utils`, `constants.py`), `main_v0.py`, `legacy_main.py`,
  `market_data_service_v0.py`, `setup-instructions.py`, `_v0` static/templates, committed
  runtime state (`.job_registry.json`, `plots/`), misspelled `requirments.txt` (ADR-011).

### Fixed
- `.pre-commit-config.yaml` and `.env.example` misnames (tooling silently disabled before).

## [2.0.0] — 2025 (historical)
FastAPI platform with Upstox OAuth, market-data services, basic Black-Scholes Greeks and
realized-vol estimators.

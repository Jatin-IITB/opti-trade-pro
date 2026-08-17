# Changelog

Format: [Keep a Changelog](https://keepachangelog.com/); versioning: SemVer.

## [3.0.0] — Unreleased (feature/production-rebuild)

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

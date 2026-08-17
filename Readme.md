# OptiTrade Pro — Derivatives Pricing & Risk Engine

A production-grade options analytics stack in two layers:

- **`optitrade`** — a standalone quant core (numpy/scipy only, strictly typed, event-journaled):
  vol surfaces, Greeks, hedging, and pre-trade risk.
- **`options_trading`** — a FastAPI platform (Upstox OAuth, market data, dashboards) that
  consumes the core through thin adapters.

Every quantitative claim below names the test that enforces it (CLAUDE.md rule 8 — docs
state only test-enforced facts).

## The four engines

| Engine | What it does | Verified behaviour |
|---|---|---|
| **Vol surface** | Newton–Raphson/Brent IV stripping; natural cubic-spline smiles in log-moneyness; per-expiry SABR (Hagan 2002, fixed β, seeded multi-start least-squares); calendar interpolation in total variance; Breeden–Litzenberger butterfly + calendar no-arb validation | SABR round-trip RMSE **< 0.3 vol-pt** on noisy synthetic smiles — `tests/unit/quant/test_sabr.py` |
| **Greeks** | Three independent methods: vectorised analytic BS, model-agnostic central finite differences, and a from-scratch tape-based **adjoint AD** engine (one backward pass → all first-order Greeks); fully broadcast ΔS×Δσ×Δt scenario revaluation | Methods agree pairwise across a parameter sweep — `test_greeks_cross.py`; **539-cell grid × 50 positions < 200 ms** — `test_scenario.py` (benchmark marker) |
| **Hedging** | Whalley–Wilmott (1997) no-transaction band (stochastic-control optimal under proportional costs); gamma scalping modulates the band by the realized/implied vol ratio (EWMA RV); Taylor P&L attribution | GBM hedging sim: mean P&L ≈ 0 at realized = implied, hedged P&L tracks theoretical theta; long-gamma earns when RV > IV — `test_hedging_sim.py` |
| **Risk** | Fail-closed pre-trade engine: Greeks caps, margin sufficiency, drawdown **halt**, concentration **resize**; verdict precedence HALT > REJECT > RESIZE > APPROVE; every decision journaled with plain-English, number-bearing reasons | Property-tested: **no limit-breaching order is ever approved**, including when a check itself crashes — `test_risk.py` |

Plus the connective tissue the engines report through:

- **Event journal** (`optitrade.journal`) — append-only JSONL, monotonic sequences,
  correlation IDs; a run replays as evidence (`test_journal.py`).
- **Governance** (`optitrade.governance`) — every trade proposal is debated by a
  deterministic expert panel (risk officer / strategy / execution) with confidence-weighted
  consensus and a confident-veto rule; dissents preserved in the journaled decision record
  (`test_governance.py`). LLM experts are an optional extra (`pip install ".[agentic]"`).
- **Attribution** (`optitrade.attribution`) — exact Shapley values for fair P&L credit
  across strategies (`test_shapley.py`).

## Quick start

```bash
uv venv --python 3.12 && uv pip install -e ".[dev]"   # or: pip install -e ".[dev]"

optitrade demo         # end-to-end synthetic run: chain → surface → Greeks →
                       # debate → risk review → hedging sim, journaled to ./runtime_data

pytest -q              # full suite (deterministic, seeded)
pytest -q -m benchmark # latency targets (run locally; excluded on shared CI runners)
```

Run the platform (needs Upstox credentials in `.env`, see `.env.example`):

```bash
uvicorn options_trading.main:app --reload --port 8000
# docs at http://localhost:8000/docs — quant endpoints under /api/v1/analytics/*
```

Docker:

```bash
docker build -t optitrade-pro . && docker run -p 8000:8000 --env-file .env optitrade-pro
```

## Analytics API (platform → core adapters)

- `POST /api/v1/analytics/surface` — chain in, spline+SABR surface out, with per-expiry SABR
  params, fit RMSE, and any no-arbitrage violations
- `POST /api/v1/analytics/greeks` — book in, aggregate + per-position Greeks out
- `POST /api/v1/analytics/scenarios` — ΔS×Δσ×Δt P&L cube with worst/best cells and timing
- `POST /api/v1/analytics/hedge/decide` — Whalley–Wilmott band decision with rationale
- `POST /api/v1/analytics/risk/review` — fail-closed pre-trade verdict with per-check reasons

## How decisions are made (in the code and about the code)

The same debate → consensus → recorded-decision mechanism runs at two timescales:

- **Runtime**: `DebatePanel` + `RiskEngine` journal every trade decision with reasons and
  correlation IDs (ADR-008, ADR-009, ADR-010).
- **Engineering**: contested design choices get a debate record in `docs/debates/` and land
  as a numbered ADR in `docs/adr/` (ADR-001; process in `docs/governance.md`).

Start with [docs/architecture.md](docs/architecture.md), then the ADR index in
[docs/adr/](docs/adr/). Engineering standards: [CLAUDE.md](CLAUDE.md).

## Project structure

```
src/optitrade/            quant core (numpy/scipy, mypy-strict, no web/broker deps)
  core/ pricing/ vol/ greeks/ hedging/ risk/ journal/ governance/ attribution/ backtest/
src/options_trading/      FastAPI platform: auth, market data, dashboards, analytics routes
tests/unit/quant/         the enforcing tests referenced throughout this README
docs/adr/                 architecture decision records (ADR-001…)
docs/debates/             expert-debate records behind the contested ADRs
```

## Conventions

Year-fraction time (ACT/365), continuously compounded rates, decimal vols, vega per unit
vol, theta per year, signed quantities (ADR-003). Toolchain: ruff + tiered mypy + pytest
with deterministic seeds (ADR-004). Conventional Commits; branch from `main`.

## License & disclaimer

MIT. This software is for research and education. Options trading involves substantial
risk; nothing here is investment advice.

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
| **Surface v2 (eSSVI)** | SSVI with power-law φ calibrated **jointly across all expiries** (Gatheral–Jacquier 2014); θ monotone by construction, butterfly conditions as in-fit penalties; Durrleman g(k) ≥ 0 validation; **risk-neutral density gate** (pdf ≥ 0, ∫≈1, mean ≈ forward); SABR reported as benchmark on every fit | Joint round-trip RMSE **0.076 vol-pt** with **0 Durrleman violations** — `test_essvi.py`; density gate — `test_density.py` |
| **Greeks** | Three independent methods: vectorised analytic BS, model-agnostic central finite differences, and a from-scratch tape-based **adjoint AD** engine (one backward pass → all first-order Greeks); fully broadcast ΔS×Δσ×Δt scenario revaluation | Methods agree pairwise across a parameter sweep — `test_greeks_cross.py`; **539-cell grid × 50 positions < 200 ms** — `test_scenario.py` (benchmark marker) |
| **Hedging** | Whalley–Wilmott (1997) no-transaction band (stochastic-control optimal under proportional costs); gamma scalping modulates the band by the realized/implied vol ratio (EWMA RV); Taylor P&L attribution | GBM hedging sim: mean P&L ≈ 0 at realized = implied, hedged P&L tracks theoretical theta; long-gamma earns when RV > IV — `test_hedging_sim.py` |
| **Risk** | Fail-closed pre-trade engine: Greeks caps, margin sufficiency, drawdown **halt**, concentration **resize**; verdict precedence HALT > REJECT > RESIZE > APPROVE; every decision journaled with plain-English, number-bearing reasons | Property-tested: **no limit-breaching order is ever approved**, including when a check itself crashes — `test_risk.py` |

Plus the flagship layers on top (the [autonomous-volatility-desk roadmap](docs/roadmap.md)):

- **Data spine** (`optitrade.data`) — NSE-reality quote filters (crossed books, stale
  quotes, wide spreads, zero-bid wings) with per-reason audit stats, and a
  schema-versioned Parquet snapshot store with lossless round-trips
  (`test_quote_filters.py`, `test_snapshot_store.py`, ADR-013).
- **P&L explain** (`optitrade.explain`) — daily decomposition into theta carry, gamma vs
  realized variance, vega per PCA surface factor (level/term/skew), vanna/volga, and
  residual; `explained_fraction` is the headline metric; expiry-bucketed exposure reports
  (`test_pnl_explain.py`, `test_factors.py`, `test_bucket_report.py`, ADR-014).
- **MCP server** (`optitrade.mcp_server`, extra `[mcp]`) — the engines exposed as agent
  tools (`price_option`, `book_greeks`, `run_scenarios`, `review_order`, `journal_tail`);
  every tool call journaled so agent claims have something to cite (ADR-015).
- **Groundedness audit** (`optitrade.audit`) — deterministic auditor: an agent claim is
  trusted only if every number it states matches the journal events it cites
  (`test_groundedness.py`). No LLM in the money path, ever.
- **Strategy + backtest** (`optitrade.strategy`, `optitrade.backtest`) — VRP harvesting
  behind a `Strategy` protocol shared by backtester and live desk (backtested code *is*
  production decision code); typed Indian cost model (STT/exchange/GST/SEBI/stamp/
  brokerage, per-fill breakdowns); walk-forward evaluation reporting out-of-sample **and
  deflated** Sharpe (Bailey–López de Prado) with honest trial accounting. Economic ground
  truths enforced: positive synthetic VRP ⇒ profit, zero VRP ⇒ zero trades, a tight vega
  cap blocks 100% of entries (`test_walk_forward.py`, `test_dsr.py`, `test_costs.py`,
  ADR-016/017).
- **Paper desk** (`optitrade.desk`) — `run_daily_cycle`: mark → strategy → debate →
  fail-closed risk → paper fill → WW hedge → journal, with a file-based **kill switch**
  a drawdown HALT engages automatically; self-auditing analyst agents (Surface Auditor,
  Post-Mortem) that must ground at 100% against the journal before reporting
  (`test_daily_cycle.py`, `test_analysts.py`, ADR-018).
- **Live capture** (`options_trading` → `/api/v1/capture/*`) — Upstox chains through the
  quote filters into the Parquet store; clean history accumulates per run
  (`tests/unit/test_capture_service.py`). The **scheduler**
  (`/capture/schedule/start|stop|status`) runs it unattended inside the IST market window
  — injected-clock tested, one bad capture never kills the loop, operator-started by
  design (`test_capture_scheduler.py`, ADR-019).
- **Real-history replay + drift** — `StoreReplay` turns captured Parquet into the same
  `MarketDay`s the synthetic replay emits, so the walk-forward harness runs on real data
  unchanged; `backtest_vs_desk_drift` runs backtester and paper desk over identical days
  with the identical strategy and reports the execution-model gap in bps
  (`test_store_replay.py`, `test_reconcile.py`, ADR-019).
- **Daily report** (`optitrade.desk.report`) — one markdown artifact per run: desk
  summary + every analyst whose source events exist, each section groundedness-scored,
  skipped analysts listed rather than hidden (`test_daily_report.py`, ADR-020). Emitted
  automatically by `optitrade cycle`.

And the connective tissue the engines report through:

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
optitrade cycle        # paper desk over a synthetic market: strategy → debate →
                       # fail-closed risk → paper fills → WW hedging → kill switch

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
  core/ pricing/ vol/ greeks/ hedging/ risk/ journal/ governance/
  attribution/ backtest/ data/ explain/ audit/ strategy/ desk/ mcp_server.py
src/options_trading/      FastAPI platform: auth, market data, dashboards, analytics routes
tests/unit/quant/         the enforcing tests referenced throughout this README
docs/adr/                 architecture decision records (ADR-001…015)
docs/debates/             expert-debate records behind the contested ADRs
```

## Conventions

Year-fraction time (ACT/365), continuously compounded rates, decimal vols, vega per unit
vol, theta per year, signed quantities (ADR-003). Toolchain: ruff + tiered mypy + pytest
with deterministic seeds (ADR-004). Conventional Commits; branch from `main`.

## License & disclaimer

MIT. This software is for research and education. Options trading involves substantial
risk; nothing here is investment advice.

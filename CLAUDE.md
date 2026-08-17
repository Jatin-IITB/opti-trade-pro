# OptiTrade Pro — Engineering Standards

Operational principles for anyone (human or agent) working in this repo. Ported from the
Prism engineering handbook and binding for all changes.

## Architecture rules

1. **One-way dependency**: `options_trading` (platform: FastAPI, Upstox) may import
   `optitrade` (quant core: numpy/scipy). Never the reverse. The quant core stays free of
   web frameworks, broker SDKs, and network I/O (ADR-002).
2. **No hardcoding**: no magic limits, bumps, thresholds, or model parameters baked into
   flows — they live in typed config dataclasses (`RiskLimits`, `FDBumps`, `BandParams`, …).
3. **Fail closed**: any error inside a risk check or governance expert converts to a
   rejection, never a pass-through (ADR-008).
4. **Every decision is journaled**: hedge decisions, risk verdicts, and debate outcomes are
   appended to the event journal with sequence numbers and correlation IDs (ADR-009).

## Process rules

5. **Diagnose first**: trace a failing test or run with evidence before editing; prefer the
   smallest fix.
6. **Done = verified at the boundary**: a change is done when the test suite passes and the
   claimed behaviour is observed (a real run, a real number), not when the code compiles.
7. **Cleanup sweep**: every refactor removes the dead code, stale tests, and orphaned files
   it obsoletes. No `_v0` / `legacy_` files may be reintroduced (ADR-011).
8. **Evidence-based claims**: README and docs state only what tests enforce. Performance and
   accuracy numbers cite the test that checks them.
9. **Decisions get records**: significant design choices go through a debate record
   (`docs/debates/`) and land as an ADR (`docs/adr/`). See `docs/governance.md`.

## Mechanics

- Quality gates (all must pass before commit):
  `.venv/bin/ruff check src tests && .venv/bin/ruff format --check src tests`
  `.venv/bin/mypy src/optitrade`
  `.venv/bin/python -m pytest -q`
- Conventional Commits (`feat:`, `fix:`, `docs:`, `refactor:`, `test:`, `chore:`).
- Branches: `feature/…`, `fix/…`, `docs/…`; never commit straight to `main`.
- Never stage: `.env`, `runtime_data/`, `.job_registry.json`, generated plots.
- Never echo secret values; check presence/length only.
- Tests are deterministic: seeded RNGs only, no wall-clock or network dependence.

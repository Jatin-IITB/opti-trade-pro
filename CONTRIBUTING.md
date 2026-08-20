# Contributing

## Ground rules
Read `CLAUDE.md` (engineering standards) and `docs/governance.md` (how decisions are made)
first. Significant design choices need a debate record and an ADR before the code lands.

## Setup

```bash
uv venv --python 3.12
uv pip install -e ".[dev]"
pre-commit install
```

## Branches & commits
- Branches: `feature/<name>`, `fix/<name>`, `docs/<name>`, `refactor/<name>`, `test/<name>`.
- [Conventional Commits](https://www.conventionalcommits.org/): `type(scope): description`
  with types `feat`, `fix`, `docs`, `style`, `refactor`, `test`, `chore`.
- Never commit directly to `main`.

## Quality gates (all must pass)

```bash
.venv/bin/ruff check src tests
.venv/bin/ruff format --check src tests
.venv/bin/mypy src/optitrade        # strict on the quant core
.venv/bin/python -m pytest -q       # deterministic; seeded RNGs only
```

## PR checklist
- [ ] Tests added/updated; the suite passes locally
- [ ] ruff + mypy clean
- [ ] New design decision? → debate record + ADR
- [ ] Measurable claim in docs? → named enforcing test
- [ ] Dead code and stale tests swept (CLAUDE.md rule 7)
- [ ] CHANGELOG.md updated under `Unreleased`

## Where code goes
- Pricing/vol/Greeks/hedging/risk math → `src/optitrade/` (numpy/scipy only, strict types)
- FastAPI routes, Upstox broker calls, dashboards → `src/options_trading/`
- The platform imports the core; never the reverse (ADR-002)

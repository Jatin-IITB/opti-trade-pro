# ADR-011: Purge the legacy tree; documentation states only test-enforced facts

## Status
Accepted

## Context
The repo carried a full duplicate legacy tree (`/api`, `/utils`, `constants.py`), three
abandoned entry points (`main_v0.py`, `legacy_main.py`, `market_data_service_v0.py`),
committed runtime state (`.job_registry.json`, `plots/*.png`), misnamed config files
(`pre-commit-config.yaml`, `.env.example.env`, `requirments.txt`), and a README claiming
"90%+ test coverage" against a single 378-line test file.

## Decision
- Delete the legacy tree and all `_v0`/`legacy_` variants (~1,100 lines). Git history is the
  archive; no graveyard directories.
- Runtime state is gitignored, never committed.
- `.pre-commit-config.yaml` and `.env.example` correctly named; packaging consolidated on
  `pyproject.toml` (the misspelled requirements file removed).
- Documentation policy: quantitative claims (coverage, RMSE, latency, blocking rate) appear
  in the README only with the name of the test that enforces them (CLAUDE.md rule 8).

## Consequences
### Positive
- One authoritative tree; claims and reality reconciled.
### Negative
- Anyone with muscle memory for the old paths loses them (recoverable from git history).
### Risks
- None material; the deleted code had modern equivalents in `src/options_trading`.

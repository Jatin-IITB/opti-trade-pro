# ADR-004: Tooling — ruff (format+lint), tiered mypy, pytest markers

## Status
Accepted

## Context
The repo declared black + isort + flake8 + mypy but the pre-commit file was misnamed
(`pre-commit-config.yaml`, no leading dot) so none of it ran. The Prism standard is
ruff + mypy. Full-strict mypy on the legacy platform would stall the rebuild.

## Decision
- ruff replaces black + isort + flake8 (one tool, `E,F,W,I,UP,B,SIM,RUF`, line 100).
- mypy tiered: `optitrade.*` strict (`disallow_untyped_defs`); `options_trading.*`
  temporarily `ignore_errors = true`, to be tightened module-by-module as the platform is
  refactored.
- pytest markers: `unit`, `integration`, `slow`, `benchmark`; deterministic seeds mandatory.
- `.pre-commit-config.yaml` correctly named and aligned with CI.

## Consequences
### Positive
- One fast linter; the new core cannot regress on typing.
### Negative
- The platform's typing debt is explicitly parked, visible in `pyproject.toml`.
### Risks
- "Temporarily" becoming permanent — tracked as roadmap item; new platform modules must not
  add to the exemption.

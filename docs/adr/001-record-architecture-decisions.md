# ADR-001: Record architecture decisions

## Status
Accepted

## Context
The repo previously accumulated `_v0` files, duplicate trees, and README claims with no
paper trail for why anything was the way it was. Rebuilding to production grade needs a
mechanism that keeps decisions auditable, the same standard the Prism platform uses.

## Decision
Adopt ADRs in `docs/adr/` (numbered, immutable, superseded-not-edited) and debate records in
`docs/debates/` for contested choices. Process defined in `docs/governance.md`. Every ADR
with a measurable claim names the enforcing test.

## Consequences
### Positive
- Decisions survive contributor turnover and context loss; reviews argue with the record,
  not with memory.
### Negative
- Small writing overhead per significant change.
### Risks
- ADRs rot if not treated as part of the change; CLAUDE.md rule 9 makes them mandatory.

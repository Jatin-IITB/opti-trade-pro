# Decision Governance

How design decisions are made and recorded in this repo. The mechanism mirrors the one the
engine itself uses for trades (`optitrade.governance`): expert debate → consensus → recorded
decision. Both exist for the same reason — decisions without recorded reasoning cannot be
audited, revisited, or trusted.

## The pipeline

```
Problem → Debate record (docs/debates/) → ADR (docs/adr/) → Implementation → Tests enforce it
```

1. **Debate** — for consequential or contested choices, write a debate record: the question,
   2–4 expert perspectives (each with assessment, concerns, confidence), and the consensus
   with dissents preserved. Template: `docs/debates/TEMPLATE.md`. Cheap decisions skip
   straight to an ADR.
2. **ADR** — the accepted outcome becomes a numbered ADR using the template below. ADRs are
   immutable history: to change one, write a new ADR that supersedes it and update the old
   one's Status line.
3. **Enforcement** — every ADR that makes a measurable claim names the test that enforces it.

## ADR template

```markdown
# ADR-NNN: Title

## Status
Accepted | Proposed | Rejected | Superseded by ADR-MMM

## Context
The problem and the forces at play.

## Decision
What we chose, concretely.

## Consequences
### Positive
### Negative
### Risks
```

## Runtime twin

Trade-level decisions go through the same shape at runtime: `DebatePanel` gathers
`ExpertOpinion`s (risk officer, strategy, execution), forms a confidence-weighted consensus
with a confident-veto rule, and journals a `DecisionRecord` to the event log. The docs
process and the runtime process are the same idea at two timescales.

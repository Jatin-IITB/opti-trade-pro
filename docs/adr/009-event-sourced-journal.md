# ADR-009: Event-sourced journal — append-only JSONL with sequences and correlation IDs

## Status
Accepted

## Context
A hedging/risk engine that cannot answer "why did we trade at 14:32?" is not production
grade. Prism's aroha runtime solved the same problem with append-only JSONL event logs,
monotonic sequence numbers, and correlation IDs tying request/response chains together; the
pattern is proven and directly portable.

## Decision
`optitrade/journal/EventLog`: one JSONL file per run (`{run_id}.jsonl`), each line an
`Event{sequence, event_type, timestamp, correlation_id, data}`.
- Append-only, flushed per write (crash safety over throughput — this is an audit log, not
  a tick store).
- On reopen, the next sequence is recovered by scanning the existing file (aroha pattern).
- `replay()` streams events back; corrupt lines raise `JournalError` with the line number.
- Risk decisions (`risk_decision`), debate outcomes (`debate_decision`), and hedge decisions
  share one journal per run; correlation IDs link an order's debate → risk review → hedge
  chain.

## Consequences
### Positive
- Full forensic replay of any run; the audit trail is the same artifact the tests assert on.
### Negative
- Per-write flush costs throughput; irrelevant at decision frequency (Hz, not kHz).
### Risks
- Unbounded file growth on long runs — one file per run_id keeps it partitioned; rotation
  can be added without format changes.

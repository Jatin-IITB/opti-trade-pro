# Debate: Paper desk — what triggers a cycle, and what happens to the book

- **Date**: 2026-09-03
- **Drivers**: `run_daily_cycle` is a complete, tested desk day with no opinion on cadence
  or persistence; the desk is the first *stateful* panel, so a cycle has side effects the
  other panels do not (paper fills that mutate a book the next cycle trades against)
- **Options**: A) Advance on the capture tick, alongside the history panels
  B) Advance on a daily scheduler inside the app
  C) Advance only on an explicit request, with a cheap read on the broadcast
- **Outcome**: C (on-demand advance, read on the tick) → ADR-027

## Expert opinions

### Risk officer (confidence 0.95)
**Assessment** — A/B both make "the desk took a position" something that happened because
a timer fired. Every other panel in this app is a pure function of stored data: re-running
it produces the same numbers and harms nothing. An advance takes fills, mutates a
persisted book, and appends to the journal. That is an action, and an action needs an
actor. Option C makes every cycle attributable to a request, which is also what makes the
kill switch meaningful — a switch that only stops a background timer you cannot see is
theatre.
**Concerns** — On-demand means a desk can sit un-advanced for weeks and then process a
long backlog in one call against days that are no longer current. Real, but strictly
better than the alternative, and the state file records exactly which dates were cycled.
**Position** — C.

### Data engineer (confidence 0.85)
**Assessment** — `StoreReplay` refits a vol surface per stored day and materialises
eagerly in `__init__`. That is the exact cost that already keeps the history panels off
the 60-second capture tick (`history_analytics.py`), and the desk pays it too. Option A
would run that replay on every tick *and* take fills from it. The captured history gains
at most one end-of-day snapshot per date, so there is nothing a per-tick advance could
discover that a per-day advance would miss.
**Concerns** — The read path still lists stored date directories, which is hundreds of
milliseconds on a year of history; it must go off the event loop (`build_async`).
**Position** — C.

### Platform engineer (confidence 0.80)
**Assessment** — Idempotence is what makes C safe rather than merely cautious. Keying on
`processed_dates` means a second advance is a no-op, so a double-click, a retry, or an
external scheduler calling the endpoint hourly cannot double the book. Without that record
any trigger — timer or button — is one retry away from re-entering a position. Persisting
after each cycle rather than at the end of the loop is the same argument applied to
crashes.
**Concerns** — Two "books" now exist in the UI (real read-only positions, simulated desk
book) and the distinction rests on labelling. Mitigated by a permanent paper badge and by
`isPaper` being asserted in the panel rather than assumed.
**Position** — C.

### Frontend engineer (confidence 0.75)
**Assessment** — An explicit "Run pending days" button is honest about what it does, and
pairs naturally with the kill switch already on the panel. It also solves a display
problem: with a background advance, a user watching the tab would see the book change with
no explanation. The cycle-history table with a per-row decision trail explains each change
because each change was requested.
**Concerns** — The kill-switch badge must not lag the latch it describes, or the control
invites a misclick. Handled by polling the dedicated endpoint rather than relying on the
capture-cadence broadcast.
**Position** — C.

## Consensus
Option C. `LivePipelineService` calls only the desk's cheap read, so a dashboard tick can
never trade; `POST /desk/advance` is the sole path that runs a cycle, and it is idempotent
via `processed_dates`. State is persisted after every cycle, and state that exists but does
not parse blocks the advance rather than resetting the book.

Dissent worth recording: the risk officer's backlog concern is unresolved by design. A
reset after a long halt will process every intervening captured day in one call. That is
acceptable while the data source is end-of-day replay and would be wrong for a desk
trading live — it is named as the first thing to revisit in ADR-027's risks if the cadence
ever changes.

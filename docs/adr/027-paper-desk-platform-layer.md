# ADR-027: Paper desk — platform layer, cadence and the paper/real boundary

## Status
Accepted

## Context
`optitrade.desk.cycle.run_daily_cycle` is a complete, tested desk day: mark the book,
ask the strategy, optional debate consensus, fail-closed risk review, paper fill,
delta-hedge decision, journal the lot under one correlation id. It has zero platform
imports and no notion of where its `MarketDay` comes from, when it should run, or what
happens to the book afterwards. Those three questions are platform work, and each has a
wrong answer that is easy to reach:

- **Where the days come from.** The captured Parquet history via
  `optitrade.backtest.market_replay.StoreReplay` is the only real source in this app, and
  is what the VRP and backtest panels already use.
- **When it runs.** The desk is the only *stateful* panel: a cycle takes paper fills that
  mutate a book the next cycle trades against. A background timer that advanced it would
  make "the desk traded" something nobody chose.
- **What survives.** A restart that forgot the book would re-enter positions the desk
  already holds the next time the same stored day came round.

Separately, the user's Upstox account is real money and this application is read-only
against it. There is no order-placement path anywhere in `options_trading` and this
feature must not create one.

## Decision

**1. Cadence: one cycle per captured trading day, advanced on demand.**
`run_daily_cycle` is by construction one desk day. The captured history gains at most one
end-of-day snapshot per date, so a day is the natural tick. `DeskState.processed_dates`
is the authoritative record of which dates have been cycled, making `advance()` idempotent
— a second pass over the same history does nothing and says so.

Advancing is deliberately **not** wired to the capture tick. A replay refits a surface per
stored day (the cost that already keeps the history panels off that tick) and, unlike those
panels, an advance has side effects. `LivePipelineService` therefore calls only the desk's
cheap read; `POST /desk/advance` is the sole path that runs a cycle.

**2. State: versioned JSON, written per cycle, unreadable ≠ empty.**
`DeskStateStore` persists the book, the notional account, `processed_dates` and display
summaries as atomically-renamed JSON under gitignored `runtime_data`. Written after *every*
cycle, not once at the end, so a crash halfway through a backlog resumes rather than
replaying fills. `load()` distinguishes "nothing stored" (returns `None`; a fresh desk is
correct) from "stored and unparseable" (raises `DeskStateError`). The latter reports an
unavailable desk and **refuses to advance**: trading from a fresh book while the real one
sat unparsed on disk is the worst available outcome, and displaying it would render a flat
book that reads as a desk holding nothing (ADR-008).

**3. Journal surfacing: a rendered trail, not a dump.**
`desk_journal.build_decision_trail` reads one cycle's correlation group and orders it into
a narrative — market seen, debate consensus with every expert opinion and confidence, risk
verdict with all four checks and the binding reasons promoted, rejections in the engine's
own wording, hedge decision. Nothing is recomputed; if a number is shown it was read from
the journal.

**4. Kill switch: asymmetric by design.**
`POST /desk/kill-switch/engage` takes an optional reason and acts immediately — anything
that delays a halt is a defect. `POST /desk/kill-switch/reset` requires the caller to echo
`RESET` *and* state a reason, which is journaled. The UI mirrors this: one button to halt,
a typed phrase plus a reason to resume. An unreadable latch reports as **engaged**.

**5. Paper-only is asserted, not assumed.**
Every desk payload carries `isPaper: true` and `mode: "paper"` as constants — there is no
live mode to switch to. The panel renders a permanent badge, and if a payload ever arrives
without `isPaper` the panel says it cannot vouch for the fills.

### Supporting core change (ADR-009 compliance)
`RiskEngine.review` and `DebatePanel.deliberate` minted their own correlation id
unconditionally, so the two richest records in the system were journaled under ids nothing
else shared: `events_by_correlation(cycle_id)` returned only `market_features`,
`hedge_decision` and `daily_cycle`. Both now accept an optional `correlation_id` and mint
one only when absent, and `run_daily_cycle` passes its own. Standalone callers (MCP, the
analytics route, the CLI) are unaffected.

Enforcing tests: `tests/unit/test_desk_service.py`, `tests/unit/test_desk_state_store.py`,
`tests/unit/test_desk_routes.py` (including `TestPaperOnlyInvariant`, which greps the
source tree for order-placement symbols), `tests/unit/quant/test_decision_correlation.py`.

## Consequences

### Positive
- The desk drives the real, tested core loop over real captured data; the platform layer
  adds no strategy, risk or pricing logic of its own.
- One cycle is one retrievable correlation group, so "why was this order blocked" is
  answerable from the journal alone.
- A restart resumes the book; a corrupt state file stops the desk instead of silently
  resetting it.
- The paper/real boundary is enforced by a test over the source tree, not by convention.

### Negative
- The desk does not advance on its own. An operator (or an external scheduler calling
  `POST /desk/advance`) must trigger it. This is a deliberate trade of convenience for
  attributability.
- `processed_dates` grows without bound, and `max_cycles_retained` caps only the tabulated
  summaries. The journal is the complete record; the state file is not a time series.
- The desk's book is unrelated to the user's real positions. Two "books" exist in the UI
  (Positions tab: real, read-only; Paper Desk tab: simulated) and the distinction rests on
  labelling.

### Risks
- **Backlog after a halt.** A HALT stops the advance and leaves later captured days
  unprocessed; a reset then processes that backlog against days that are no longer current.
  Acceptable while the source is end-of-day replay, but it is the wrong behaviour for a
  desk trading live, and is the first thing to revisit if the cadence changes.
- **Marking uses each day's fitted surface**, so the paper P&L inherits every simplification
  documented in `cycle.py` (no book aging, premium-notional margin proxy, hedge decisions
  journaled not booked). These are honest simplifications, not a live-trading model.

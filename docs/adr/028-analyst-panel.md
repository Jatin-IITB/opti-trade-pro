# ADR-028: Analyst panel — provenance rendered per claim

- **Status**: Accepted
- **Date**: 2026-09-03
- **Debate**: [2026-09-03-analyst-panel-roster.md](../debates/2026-09-03-analyst-panel-roster.md)
- **Supersedes**: nothing. Extends ADR-015 (analyst/journal citation contract),
  ADR-021 (agent layer), ADR-027 (paper desk platform layer).

## Context

`optitrade.desk.analysts` and `optitrade.audit.groundedness` were fully
implemented and unit-tested but had **zero `options_trading` imports**: the
product surfaced none of it. The deterministic analysts read journaled engine
facts, write plain-English reports, attach `AgentClaim` records naming the
journal sequences their numbers came from, and audit themselves with
`GroundednessAuditor` before returning.

That audit is the reason this is worth surfacing at all. Analyst output is the
one place in this product where a real measurement and an invented one are
typographically identical — a paragraph of confident prose reads the same
either way. Phases 1–3 were spent deleting fabricated series precisely because
a plausible number is indistinguishable from a measured one; analyst text is
that problem in its most acute form, because prose does not even have a chart
axis to look wrong on.

## Decision

### 1. The groundedness verdict is rendered per claim, not per panel

Every claim reaches the wire with `grounded`, the sequence numbers it
`cites`, and — when ungrounded — the auditor's `reasons`. The panel draws a
badge on each one.

An aggregate score was the obvious cheaper option and is rejected: "94%
grounded" tells a reader that something is wrong but not which sentence to
distrust, which is the only actionable form of the information. The header
still shows the overall rate, taken from `OrchestratorReport
.grounded_rate_overall` (a re-audit of the union of claims) rather than a mean
of per-analyst rates, because analysts contribute different claim counts and
averaging rates would weight a one-claim analyst like a five-claim one.

Claims are paired to verdicts **by `claim_id`, not by list position**. The
auditor returns verdicts in claim order today, so position would work; it is
not used because the failure it permits is a green badge on an unproven
number, which is the exact thing this panel exists to prevent. A claim with no
verdict at all renders as ungrounded — fail closed (ADR-008): no verdict means
unproven, not proven.

### 2. Partial coverage is the normal case, designed for from the start

Each analyst reads one journal event type and raises `ValueError` when the
journal has none. On the desk's current history — one cycle, three events
(`market_features`, `hedge_decision`, `daily_cycle`) — exactly one of three
roster analysts can report. That is not an edge case to be handled later; it
is the steady state until the surface fitter and the P&L explain have run.

So failures are first-class payload, not a swallowed exception: each is listed
with the event type it needed (`surface_fit`, `pnl_explain`) and the engine's
own error text. "Needs a `surface_fit` event" tells a user the fitter has not
run, which is actionable; hiding the analyst entirely would misrepresent the
roster as complete.

`AnalystOrchestrator` is deliberately fail-**open** — an analyst that raises
becomes an `AnalystFailure` rather than taking down the others. That is left
exactly as it is. Analysts observe and explain; the fail-closed boundary is
the risk engine (ADR-008), and a missing surface fit must not blank the regime
report. Fail-open in the orchestrator and honest reporting in the panel are
complementary, not in tension.

### 3. `RiskOfficerAnalyst` is excluded, and the UI says so

It is not on the roster, for two independent reasons:

- It exposes `answer(query, book, spot, rate, journal)`, not
  `report(journal)`, so it does not satisfy the orchestrator's analyst
  protocol.
- More decisively, `answer` **appends** a `scenario_query` event to the desk
  journal before citing it. That is correct for a deliberate what-if, but this
  panel is read on every dashboard tick. A read that writes would inflate the
  desk's audit trail with synthetic queries nobody asked for, and shift the
  sequence numbers every other citation is checked against.

It is therefore named in an `excluded` list with that reason, rendered in the
panel. Silently dropping it would let the UI imply the desk has three analysts
when the code has four. Wiring it properly needs a user-supplied
`ScenarioQuery` against the current book on an explicit request, which is a
different endpoint from this one.

### 4. The `analysts` key is always emitted

`build_wire_dict` emits `analysts` on every path, including when the service
raises, using `unavailable_analysts_wire(reason)`. Omitting it fails *open*:
the frontend merges only the keys it receives, so an absent key leaves the
previous reports on screen — stale sentences asserting numbers from a journal
this process can no longer read, each still wearing its grounded badge from
the previous audit. Same contract as the history panels (ADR-024) and the desk
(ADR-027); the consequence is worse here because the stale content is prose.

`groundedRate` is `None` rather than `0.0` or `1.0` when nothing was audited.
Both numbers read as the result of an audit.

The panel gates on `hasJournal === true`, not `!== false`, so a null payload,
a missing key or a schema change renders the empty state rather than prose.

### 5. Read-only, and tested as such

The service replays the journal and appends nothing. `POST` is not exposed on
the router, no broker client or HTTP library is imported, and
`test_analyst_routes.py::TestPaperOnlyInvariant` asserts all three against the
module source. The app remains read-only against a real Upstox account.

### 6. Caching mirrors `HistoryAnalytics` exactly

`threading.Lock` for the cache, `asyncio.Lock` for task creation, and one
shared shielded in-flight task. Not a new scheme: `asyncio.to_thread` cancels
the future, not the thread, so a dashboard client disconnecting mid-build
would otherwise release the lock while its worker still ran and let the next
caller start a second full replay. That cancellation race was a real bug fixed
once already in the history path; a second implementation would be a second
chance to get it wrong.

The cache key is a `stat` of the journal file (size + mtime), not a replay:
reading every event to decide whether to read every event defeats the point.

## Consequences

**Good.** The most defensible property in the codebase — that agent prose is
checkable against journaled engine output — is now visible in the product
rather than only in unit tests. A reader can see which sentence is sourced and
which is not, and which analysts have not run.

**Cost.** The analyst thresholds now exist in two places: the analysts'
constructor defaults and `settings.analyst_*`. The config dataclass is the
single source used by the flow (no literals), but a core default change will
not propagate to a deployment that has set the setting. Accepted for the same
reason `HistoryAnalyticsConfig` accepts it.

**Known gap.** The deterministic analysts ground at 100% by construction —
they cite what they just read — so the ungrounded rendering path is exercised
by tests using the real auditor over hand-written claims rather than by any
analyst in the current roster. It becomes load-bearing when the LLM tier lands
on these rails, which is what it is built for.

**Not done.** The LLM analysts (`optitrade.agents.llm_analyst`) are not wired:
`dspy` is not installed and the `[agentic]` extra has never been run. The
orchestrator's `llm` tier is left empty rather than populated with analysts
that would report an unavailable backend as a failure — noise, not
information.

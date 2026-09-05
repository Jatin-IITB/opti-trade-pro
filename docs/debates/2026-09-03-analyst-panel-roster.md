# Debate: what the analyst panel shows, and what it admits it does not

- **Date**: 2026-09-03
- **Outcome**: [ADR-028](../adr/028-analyst-panel.md)
- **Question**: `optitrade.desk.analysts` has four analysts, three of which fit
  the orchestrator protocol, and on the desk's current journal exactly one can
  report. What does the panel render, and how does it account for the rest?

## Position A — aggregate score, hide the gaps

Show each report that succeeded and one overall groundedness percentage. Omit
analysts that raised: they produced nothing, so there is nothing to show.

**For.** Simplest payload and the cleanest-looking panel. A reader sees three
paragraphs and a 100% badge.

**Against.** Two failures, both severe:

- A panel showing one report and "100% grounded" is indistinguishable from a
  panel where all four analysts ran and agreed. The reader cannot tell that
  three quarters of the desk's analytical coverage is missing, and the number
  they *can* see is the reassuring one.
- An aggregate score identifies that something is unsourced without saying
  which sentence. The only actionable form of "this claim is ungrounded" is
  attached to the claim.

This is the failure mode of the demo-data era in a new costume: a plausible
surface with nothing behind part of it, and no way to tell which part.

## Position B — per-claim badges, failures as payload, exclusions named

Render the auditor's verdict beside every claim with its cited sequence
numbers. List analysts that could not report, with the event type each needed.
Name the analyst that is deliberately off the roster and why.

**For.** Each of the three things a reader needs is present and separable:
what was claimed, whether it is sourced, and what is missing. "Needs a
`surface_fit` event" is actionable — it says the surface fitter has not run.

**Against.** A busier panel, and it makes the product look less complete than
Position A. Accepted: it *is* less complete, and one captured day is the
honest reason.

## Consensus

Position B, on the precedent already set. `HistoryGate` reports "3 of 11 days"
rather than drawing a curve from nothing; this is the same decision applied to
prose instead of a chart, and prose needs it more — a chart from no data at
least looks suspicious, whereas a confident paragraph does not.

## Sub-question: the fourth analyst

`RiskOfficerAnalyst` cannot simply be added. It takes `answer(query, book,
spot, rate, journal)` rather than `report(journal)`, so it does not satisfy
the orchestrator's protocol — but that alone would only be an argument for
special-casing it.

The decisive objection is that `answer` **journals** the `scenario_query`
event it then cites. That is right for a deliberate what-if and wrong for a
panel read on every dashboard tick: the desk's audit trail would fill with
synthetic queries nobody asked for, and every other claim's citations are
checked against sequence numbers those writes would shift. A read path that
mutates the artifact the audit is performed against is not a read path.

Three options were considered:

1. **Force it in** with a hardcoded default scenario (say, spot −5%). Rejected
   twice over: it writes on read, and the scenario would be a magic parameter
   in the flow, which rule 2 forbids.
2. **Drop it silently.** Rejected — the UI would imply the roster is three
   analysts when the code has four, which is the same
   completeness-by-omission that Position A was rejected for.
3. **Name it as excluded, with the reason, in the payload and the panel.**
   Chosen. Wiring it properly needs a user-supplied `ScenarioQuery` on an
   explicit request, which is a different endpoint.

## Sub-question: should the orchestrator be made fail-closed?

Raised because ADR-008 makes fail-closed binding for risk paths, and
`AnalystOrchestrator` catches every analyst exception.

Resolved: **no.** ADR-008 governs risk checks and governance experts, where an
error must become a rejection. An analyst is neither — it observes and
explains, and it cannot approve anything. Making it fail-closed would mean a
missing surface fit blanks the regime report, which loses real information to
protect nothing; the risk engine remains the boundary where an error rejects.

The panel carries the honesty obligation instead. Fail-open orchestration and
a panel that reports every failure are complementary: the first keeps one
missing event from silencing the others, the second stops the survivors from
being read as the whole picture.

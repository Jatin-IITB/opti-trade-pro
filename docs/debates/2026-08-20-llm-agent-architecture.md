# Debate: How should LLM agents integrate with the deterministic analyst layer?

- **Date**: 2026-08-20
- **Drivers**: Phase 5 of the flagship plan requires LLM adapters on the
  analyst layer; the groundedness invariant (ADR-015) must be preserved.
- **Options**:
  A) Pure LLM: agents generate both text and claims from scratch; groundedness
     auditor catches hallucinated numbers.
  B) Hybrid: agents get LLM narrative, but claims are deterministic (built
     from journal event data, identical to the reference analysts).
  C) Enhancement: LLM agents extend the deterministic analysts, adding
     cross-event reasoning claims on top of the reference claim set.
- **Outcome**: B (hybrid) for v1 → ADR-021, with C as the documented
  extension path.

## Expert opinions

### Risk Officer (confidence 0.95)
**Assessment** — Option A is unacceptable: an LLM that generates claim values
can hallucinate numbers that are not in the journal, creating plausible but
ungrounded assertions. Even with the auditor catching them, a 70% grounded
rate on an analyst report degrades trust. The groundedness rate must stay at
100% for the reference claim set.
**Concerns**
- Hallucinated numbers in claims bypass the deterministic-first discipline.
- Debugging grounded-vs-ungrounded claims requires LLM-output inspection.
**Position** — B.

### Strategy Expert (confidence 0.80)
**Assessment** — Option C is the richest output, but it adds complexity: the
cross-event claims need a new extraction pipeline with multi-event citation
tracking. Option B delivers the 80% value (richer narrative) at 20% of the
complexity, and the deterministic claim pipeline is already tested.
**Concerns**
- Option B limits LLM analysts to narrative enhancement only; they cannot
  surface patterns the deterministic extraction misses.
**Position** — B, with C as documented next step.

### Execution Expert (confidence 0.85)
**Assessment** — LLM latency matters: the orchestrator runs analysts
sequentially, so adding three LLM analysts triples the report-generation
time. Option B is correct for v1; parallelisation and async adapters come
later. The mock-backend pattern makes the test suite deterministic.
**Concerns**
- Sequential execution with real LLMs may take 15-30s per report.
**Position** — B.

## Consensus
Option B: hybrid architecture with deterministic claims and LLM-generated
narrative. The test suite uses mock backends and asserts 100% groundedness.
Option C (cross-event LLM claims) is the documented extension path,
gated on a multi-event citation tracking pipeline.

## Dissents
None — all experts agreed on B. The risk officer's near-veto on option A
(hallucinated claim values) was the deciding argument.

"""Analyst orchestrator: runs deterministic + LLM analysts, merges reports (ADR-021).

The orchestrator manages two tiers of analyst:

1. **Deterministic** — the reference implementations from :mod:`optitrade.desk.
   analysts`. Always run; their claims are the ground truth.
2. **LLM-backed** — from :mod:`optitrade.agents.llm_analyst`. Optional; each
   runs only when its source event type exists in the journal. An LLM analyst
   that raises is caught and reported as a failure (fail open for analyst
   layer, unlike the risk engine which fails closed).

The orchestrator produces an :class:`OrchestratorReport` that merges all
analyst outputs, re-audits the full claim set, and reports coverage.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from optitrade.audit.groundedness import AgentClaim, GroundednessAuditor
from optitrade.desk.analysts import AnalystReport
from optitrade.journal.event_log import EventLog


class _Analyst(Protocol):
    @property
    def name(self) -> str: ...

    def report(self, journal: EventLog) -> AnalystReport: ...


@dataclass(frozen=True)
class AnalystFailure:
    """An analyst that raised during its report; captured, not propagated."""

    analyst_name: str
    error: str


@dataclass(frozen=True)
class OrchestratorReport:
    """Merged output of all analyst tiers."""

    deterministic_reports: tuple[AnalystReport, ...]
    llm_reports: tuple[AnalystReport, ...]
    failures: tuple[AnalystFailure, ...]
    grounded_rate_overall: float

    @property
    def all_reports(self) -> tuple[AnalystReport, ...]:
        return self.deterministic_reports + self.llm_reports


class AnalystOrchestrator:
    """Runs all registered analysts and merges their reports.

    Deterministic analysts always run (they raise ``ValueError`` if their
    source event is missing, caught and recorded as a failure). LLM analysts
    are optional: any exception during their report is caught and recorded as
    a failure — the orchestrator never propagates analyst errors.

    Usage::

        orchestrator = AnalystOrchestrator(
            deterministic=(RegimeAnalyst(), SurfaceAuditor()),
            llm=(LLMRegimeAnalyst(backend), LLMSurfaceAnalyst(backend)),
        )
        report = orchestrator.run_all(journal)
    """

    def __init__(
        self,
        deterministic: tuple[_Analyst, ...] = (),
        llm: tuple[_Analyst, ...] = (),
    ) -> None:
        self._deterministic = deterministic
        self._llm = llm

    def run_all(self, journal: EventLog) -> OrchestratorReport:
        det_reports: list[AnalystReport] = []
        llm_reports: list[AnalystReport] = []
        failures: list[AnalystFailure] = []

        for analyst in self._deterministic:
            try:
                det_reports.append(analyst.report(journal))
            except Exception as exc:
                failures.append(
                    AnalystFailure(
                        analyst_name=analyst.name,
                        error=f"{type(exc).__name__}: {exc}",
                    )
                )

        for analyst in self._llm:
            try:
                llm_reports.append(analyst.report(journal))
            except Exception as exc:
                failures.append(
                    AnalystFailure(
                        analyst_name=analyst.name,
                        error=f"{type(exc).__name__}: {exc}",
                    )
                )

        all_claims: list[AgentClaim] = []
        for r in det_reports + llm_reports:
            all_claims.extend(r.claims)
        if all_claims:
            overall = GroundednessAuditor(journal).audit(all_claims)
            grounded_rate = overall.grounded_rate
        else:
            grounded_rate = 1.0

        return OrchestratorReport(
            deterministic_reports=tuple(det_reports),
            llm_reports=tuple(llm_reports),
            failures=tuple(failures),
            grounded_rate_overall=grounded_rate,
        )


__all__ = ["AnalystFailure", "AnalystOrchestrator", "OrchestratorReport"]

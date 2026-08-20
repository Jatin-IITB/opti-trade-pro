"""Tests for the analyst orchestrator (ADR-021).

Verifies:
1. Deterministic analysts run and their reports are collected.
2. LLM analysts run when their events exist; failures are captured.
3. Overall grounded rate is computed across all tiers.
4. A failing analyst produces an AnalystFailure, not a crash.
"""

from __future__ import annotations

import pytest

from optitrade.agents.base import LLMResponse
from optitrade.agents.llm_analyst import LLMRegimeAnalyst, LLMSurfaceAnalyst
from optitrade.agents.orchestrator import AnalystOrchestrator
from optitrade.desk.analysts import RegimeAnalyst, SurfaceAuditor
from optitrade.journal.event_log import EventLog


class MockBackend:
    def complete(self, system: str, user: str) -> LLMResponse:
        return LLMResponse(text="Mock analysis from LLM.")


class BrokenBackend:
    def complete(self, system: str, user: str) -> LLMResponse:
        raise RuntimeError("LLM provider unavailable")


@pytest.fixture()
def journal(tmp_path):
    j = EventLog(tmp_path, "test-orchestrator")
    j.append(
        "market_features",
        {"ts": 1000.0, "spot": 100.0, "realized_vol": 0.18, "vrp": 0.06},
    )
    j.append(
        "surface_fit",
        {
            "quotes": 27,
            "essvi_rmse_vol_points": 0.08,
            "worst_rmse_vol_points": 0.14,
            "durrleman_violations": 0,
            "density_violations": 0,
            "spline_arb_violations": 0,
            "sabr_arb_violations": 0,
        },
    )
    return j


class TestAnalystOrchestrator:
    def test_deterministic_only(self, journal):
        orch = AnalystOrchestrator(
            deterministic=(RegimeAnalyst(), SurfaceAuditor()),
        )
        report = orch.run_all(journal)

        assert len(report.deterministic_reports) == 2
        assert len(report.llm_reports) == 0
        assert len(report.failures) == 0
        assert report.grounded_rate_overall == 1.0

    def test_mixed_tiers(self, journal):
        backend = MockBackend()
        orch = AnalystOrchestrator(
            deterministic=(RegimeAnalyst(),),
            llm=(LLMSurfaceAnalyst(backend),),
        )
        report = orch.run_all(journal)

        assert len(report.deterministic_reports) == 1
        assert len(report.llm_reports) == 1
        assert len(report.failures) == 0
        assert report.grounded_rate_overall == 1.0
        assert len(report.all_reports) == 2

    def test_broken_llm_captured_as_failure(self, journal):
        broken = BrokenBackend()
        orch = AnalystOrchestrator(
            deterministic=(RegimeAnalyst(),),
            llm=(LLMRegimeAnalyst(broken),),
        )
        report = orch.run_all(journal)

        assert len(report.deterministic_reports) == 1
        assert len(report.llm_reports) == 0
        assert len(report.failures) == 1
        assert "LLM provider unavailable" in report.failures[0].error

    def test_missing_event_captured_as_failure(self, tmp_path):
        empty_journal = EventLog(tmp_path, "test-empty")
        orch = AnalystOrchestrator(
            deterministic=(SurfaceAuditor(),),
        )
        report = orch.run_all(empty_journal)

        assert len(report.deterministic_reports) == 0
        assert len(report.failures) == 1
        assert "surface_fit" in report.failures[0].error

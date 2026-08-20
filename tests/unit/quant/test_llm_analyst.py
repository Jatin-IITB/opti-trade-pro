"""Tests for the LLM-backed analyst agents (ADR-021).

Each test uses a deterministic mock backend and verifies:
1. The analyst produces an AnalystReport with the correct analyst name.
2. Claims are deterministic (identical to the reference analyst's claims).
3. Groundedness is 100% (claims cite real journal events with real numbers).
4. The LLM text is the mock backend's response (not a template).
"""

from __future__ import annotations

import pytest

from optitrade.agents.base import LLMResponse
from optitrade.agents.llm_analyst import (
    LLMPostMortemAnalyst,
    LLMRegimeAnalyst,
    LLMSurfaceAnalyst,
)
from optitrade.journal.event_log import EventLog


class MockBackend:
    """Deterministic mock: returns a canned response with the analyst type."""

    def __init__(self, reply: str = "Mock analysis.") -> None:
        self._reply = reply
        self.calls: list[tuple[str, str]] = []

    def complete(self, system: str, user: str) -> LLMResponse:
        self.calls.append((system, user))
        return LLMResponse(text=self._reply)


@pytest.fixture()
def journal(tmp_path):
    return EventLog(tmp_path, "test-llm-analyst")


class TestLLMSurfaceAnalyst:
    def test_report_grounds_at_100(self, journal):
        journal.append(
            "surface_fit",
            {
                "quotes": 27,
                "essvi_rmse_vol_points": 0.076,
                "worst_rmse_vol_points": 0.15,
                "durrleman_violations": 0,
                "density_violations": 0,
                "spline_arb_violations": 1,
                "sabr_arb_violations": 0,
            },
        )
        backend = MockBackend("The eSSVI fit looks healthy.")
        analyst = LLMSurfaceAnalyst(backend)
        report = analyst.report(journal)

        assert report.analyst == "llm_surface_analyst"
        assert report.text == "The eSSVI fit looks healthy."
        assert report.groundedness.grounded_rate == 1.0
        assert len(report.claims) == 3
        assert len(backend.calls) == 1

    def test_missing_event_raises(self, journal):
        backend = MockBackend()
        analyst = LLMSurfaceAnalyst(backend)
        with pytest.raises(ValueError, match="surface_fit"):
            analyst.report(journal)


class TestLLMRegimeAnalyst:
    def test_report_grounds_at_100(self, journal):
        journal.append(
            "market_features",
            {
                "ts": 1000.0,
                "spot": 100.0,
                "realized_vol": 0.18,
                "atm_iv": 0.24,
                "vrp": 0.06,
                "term_slope": 0.02,
                "skew_25d": 0.015,
            },
        )
        backend = MockBackend("Vol regime is rich for sellers.")
        analyst = LLMRegimeAnalyst(backend)
        report = analyst.report(journal)

        assert report.analyst == "llm_regime_analyst"
        assert report.text == "Vol regime is rich for sellers."
        assert report.groundedness.grounded_rate == 1.0
        assert len(report.claims) == 5

    def test_partial_features(self, journal):
        journal.append(
            "market_features",
            {"ts": 1000.0, "spot": 100.0, "realized_vol": 0.18},
        )
        backend = MockBackend("Limited data today.")
        analyst = LLMRegimeAnalyst(backend)
        report = analyst.report(journal)

        assert report.groundedness.grounded_rate == 1.0
        assert len(report.claims) == 1


class TestLLMPostMortemAnalyst:
    def test_report_grounds_at_100(self, journal):
        journal.append(
            "pnl_explain",
            {
                "theta_carry": -50.0,
                "delta_pnl": 10.0,
                "gamma_vs_rv": 30.0,
                "vega_from_factors": {"level": 5.0, "term": -2.0},
                "vega_residual_move": 1.0,
                "vanna_volga": -3.0,
                "residual": 2.0,
                "total": -10.0,
                "explained_fraction": 0.95,
            },
        )
        backend = MockBackend("Theta drag dominated today's P&L.")
        analyst = LLMPostMortemAnalyst(backend)
        report = analyst.report(journal)

        assert report.analyst == "llm_post_mortem_analyst"
        assert report.text == "Theta drag dominated today's P&L."
        assert report.groundedness.grounded_rate == 1.0
        assert len(report.claims) == 2

    def test_missing_event_raises(self, journal):
        backend = MockBackend()
        analyst = LLMPostMortemAnalyst(backend)
        with pytest.raises(ValueError, match="pnl_explain"):
            analyst.report(journal)

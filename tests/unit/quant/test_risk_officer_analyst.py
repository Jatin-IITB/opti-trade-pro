"""Tests for the RiskOfficerAnalyst (structured scenario queries, compute -> journal -> cite)."""

from __future__ import annotations

import numpy as np
import pytest

from optitrade.core import OptionType
from optitrade.desk import RiskOfficerAnalyst, ScenarioQuery
from optitrade.greeks.scenario import BookPosition, ScenarioGrid, run_scenario_grid
from optitrade.journal import EventLog

SPOT = 100.0
RATE = 0.05

BOOK = (
    BookPosition(strike=100.0, expiry=0.25, option_type=OptionType.CALL, quantity=10.0, vol=0.20),
    BookPosition(strike=95.0, expiry=0.50, option_type=OptionType.PUT, quantity=-5.0, vol=0.22),
)


@pytest.fixture
def journal(tmp_path):
    return EventLog(tmp_path, "risk-officer-run")


def independent_pnl(spot_shift: float, vol_shift: float, time_shift_days: float) -> float:
    """Reference P&L from run_scenario_grid at exactly the queried shifts."""
    grid = ScenarioGrid(
        spot_shifts=np.array([spot_shift], dtype=np.float64),
        vol_shifts=np.array([vol_shift], dtype=np.float64),
        time_shifts=np.array([time_shift_days / 365.0], dtype=np.float64),
    )
    return float(run_scenario_grid(BOOK, SPOT, RATE, grid).pnl[0, 0, 0])


class TestRiskOfficerAnalyst:
    def test_answer_journals_the_scenario_query_event(self, journal):
        query = ScenarioQuery(
            spot_shift=-0.05, vol_shift=0.02, time_shift_days=5.0, label="gap down"
        )
        RiskOfficerAnalyst().answer(query, BOOK, SPOT, RATE, journal)

        [event] = [e for e in journal.replay() if e.event_type == "scenario_query"]
        assert event.data["label"] == "gap down"
        assert event.data["spot_shift"] == -0.05
        assert event.data["vol_shift"] == 0.02
        assert event.data["time_shift_days"] == 5.0
        assert "pnl" in event.data
        assert "base_value" in event.data

    def test_pnl_matches_an_independent_scenario_grid_run(self, journal):
        query = ScenarioQuery(
            spot_shift=-0.05, vol_shift=0.02, time_shift_days=5.0, label="gap down"
        )
        report = RiskOfficerAnalyst().answer(query, BOOK, SPOT, RATE, journal)

        expected = independent_pnl(-0.05, 0.02, 5.0)
        [event] = [e for e in journal.replay() if e.event_type == "scenario_query"]
        assert abs(event.data["pnl"] - expected) < 1e-9
        assert f"{expected:+.2f}" in report.text  # the number in the prose is the engine's

    def test_report_grounds_at_100_percent_and_cites_the_event_it_wrote(self, journal):
        journal.append("daily_cycle", {"date_ts": 0.0})  # unrelated earlier event
        query = ScenarioQuery(spot_shift=0.03, vol_shift=-0.01, label="rally, vol crush")
        report = RiskOfficerAnalyst().answer(query, BOOK, SPOT, RATE, journal)

        assert report.analyst == "risk_officer_analyst"
        assert report.groundedness.grounded_rate == 1.0
        assert all(v.grounded for v in report.groundedness.verdicts)
        [event] = [e for e in journal.replay() if e.event_type == "scenario_query"]
        assert all(claim.citations == (event.sequence,) for claim in report.claims)
        assert "+3.0%" in report.text  # spot shift
        assert "-1.0 vol-pt" in report.text  # vol shift
        assert "rally, vol crush" in report.text

    def test_zero_shift_query_returns_approximately_zero_pnl(self, journal):
        query = ScenarioQuery(spot_shift=0.0, vol_shift=0.0, label="base")
        report = RiskOfficerAnalyst().answer(query, BOOK, SPOT, RATE, journal)

        [event] = [e for e in journal.replay() if e.event_type == "scenario_query"]
        assert event.data["pnl"] == pytest.approx(0.0, abs=1e-9)
        assert report.groundedness.grounded_rate == 1.0

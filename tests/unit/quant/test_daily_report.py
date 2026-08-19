"""Tests for the daily desk report (journal-grounded markdown artifact)."""

from __future__ import annotations

import pytest

from optitrade.desk import build_daily_report
from optitrade.journal import EventLog


def daily_cycle_data(**overrides):
    """Field names mirror the daily_cycle event written by desk.cycle."""
    data = {
        "date_ts": 1_700_000_000.0,
        "action": "enter",
        "action_taken": "enter: 1 filled, 0 rejected; hedge none",
        "fills": [{"symbol": "NIFTY-100-CE", "quantity": 2.0, "price": 4.01, "notional": 8.02}],
        "rejected": [],
        "cash": 99_991.98,
        "equity": 100_000.45,
        "drawdown": 0.0,
        "book_delta": 1.1,
        "book_gamma": 0.05,
        "book_vega": 19.5,
        "book_theta": -4.2,
        "hedge_action": "none",
        "halted": False,
    }
    data.update(overrides)
    return data


def market_features_data(**overrides):
    """Field names mirror the market_features event written by desk.cycle."""
    data = {
        "ts": 1_700_000_000.0,
        "spot": 100.0,
        "realized_vol": 0.18,
        "atm_iv": 0.24,
        "term_slope": 0.01,
        "skew_25d": 0.02,
        "vrp": 0.06,
    }
    data.update(overrides)
    return data


def surface_fit_data(**overrides):
    """Field names mirror the surface_fit event written by optitrade.cli."""
    data = {
        "quotes": 66,
        "worst_rmse_vol_points": 0.31,
        "essvi_rmse_vol_points": 0.21,
        "spline_arb_violations": 0,
        "sabr_arb_violations": 0,
        "durrleman_violations": 0,
        "density_violations": 0,
    }
    data.update(overrides)
    return data


def pnl_explain_data(residual=-0.05, total=5.5, **overrides):
    """Shape mirrors PnLExplain.to_event_data (optitrade.explain.pnl_explain)."""
    data = {
        "theta_carry": 12.5,
        "delta_pnl": -0.4,
        "gamma_vs_rv": -8.25,
        "vega_from_factors": {"level": 3.0, "skew": -1.0},
        "vega_residual_move": 0.2,
        "vanna_volga": 0.05,
        "residual": residual,
        "total": total,
        "explained_fraction": 1.0 - abs(residual) / abs(total),
    }
    data.update(overrides)
    return data


@pytest.fixture
def journal(tmp_path):
    return EventLog(tmp_path / "journal", "report-run")


def seed_full_journal(journal: EventLog) -> None:
    journal.append("market_features", market_features_data())
    journal.append("surface_fit", surface_fit_data())
    journal.append("pnl_explain", pnl_explain_data())
    journal.append("daily_cycle", daily_cycle_data())


class TestFullReport:
    def test_all_sections_present_and_fully_grounded(self, journal):
        seed_full_journal(journal)
        report = build_daily_report(journal)

        md = report.markdown
        assert "# Daily desk report — run report-run" in md
        assert "## Desk summary" in md
        assert "## Regime analyst" in md
        assert "## Surface auditor" in md
        assert "## Post-mortem analyst" in md
        assert "## Coverage" in md
        assert md.count("groundedness:") == 3  # one per included analyst
        assert "groundedness: 5/5 claims" in md  # regime: 5 claims, all grounded
        assert "groundedness: 3/3 claims" in md  # surface auditor
        assert "groundedness: 2/2 claims" in md  # post-mortem
        assert "All analysts reported; no coverage gaps." in md

        assert report.grounded_rate_overall == 1.0
        assert report.path is None
        assert [r.analyst for r in report.analyst_reports] == [
            "regime_analyst",
            "surface_auditor",
            "post_mortem_analyst",
        ]
        assert all(r.groundedness.grounded_rate == 1.0 for r in report.analyst_reports)

    def test_desk_summary_quotes_the_latest_daily_cycle_event(self, journal):
        seed_full_journal(journal)
        md = build_daily_report(journal).markdown
        assert "enter: 1 filled, 0 rejected; hedge none" in md
        assert "fills: 1, rejected: 0" in md
        assert "delta +1.1000" in md
        assert "equity 100,000.45" in md
        assert "halted: False" in md


class TestPartialCoverage:
    def test_only_daily_cycle_lists_every_skipped_analyst(self, journal):
        journal.append("daily_cycle", daily_cycle_data())
        report = build_daily_report(journal)  # must not raise

        md = report.markdown
        assert "## Desk summary" in md
        assert "Skipped analysts (missing source events):" in md
        assert "- regime_analyst: no 'market_features' event in the journal" in md
        assert "- surface_auditor: no 'surface_fit' event in the journal" in md
        assert "- post_mortem_analyst: no 'pnl_explain' event in the journal" in md
        assert report.analyst_reports == ()
        assert report.grounded_rate_overall == 1.0  # no claims made, nothing failed

    def test_missing_daily_cycle_raises_naming_the_event_type(self, journal):
        journal.append("market_features", market_features_data())
        with pytest.raises(ValueError, match="daily_cycle"):
            build_daily_report(journal)


class TestWrittenArtifact:
    def test_out_dir_writes_the_file_and_journals_a_daily_report_event(self, journal, tmp_path):
        seed_full_journal(journal)
        out_dir = tmp_path / "reports"
        report = build_daily_report(journal, out_dir=out_dir)

        assert report.path == out_dir / "report-run-report.md"
        assert report.path.read_text(encoding="utf-8") == report.markdown

        [event] = [e for e in journal.replay() if e.event_type == "daily_report"]
        assert event.data["path"] == str(report.path)
        assert event.data["grounded_rate_overall"] == 1.0
        assert event.data["analysts_included"] == [
            "regime_analyst",
            "surface_auditor",
            "post_mortem_analyst",
        ]
        assert event.data["analysts_skipped"] == []

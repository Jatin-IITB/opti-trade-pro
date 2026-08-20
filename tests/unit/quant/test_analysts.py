"""Tests for the deterministic analyst agents (self-audited, journal-cited)."""

from __future__ import annotations

import pytest

from optitrade.desk import PostMortemAnalyst, SurfaceAuditor
from optitrade.journal import EventLog


def surface_event_data(**overrides):
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


def pnl_event_data(residual=-0.05, total=5.5, **overrides):
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
    return EventLog(tmp_path, "analyst-run")


class TestSurfaceAuditor:
    def test_clean_fit_grounds_at_100_percent_with_numbers_in_text(self, journal):
        journal.append("surface_fit", surface_event_data())
        report = SurfaceAuditor().report(journal)

        assert report.analyst == "surface_auditor"
        assert report.groundedness.grounded_rate == 1.0
        assert all(v.grounded for v in report.groundedness.verdicts)
        assert "0.2100" in report.text  # eSSVI RMSE
        assert "0.3100" in report.text  # SABR benchmark
        assert "66 quotes" in report.text
        assert "FLAG" not in report.text
        assert all(claim.citations == (1,) for claim in report.claims)

    def test_rmse_above_threshold_is_flagged(self, journal):
        journal.append("surface_fit", surface_event_data(essvi_rmse_vol_points=0.8))
        report = SurfaceAuditor(rmse_threshold_vol_points=0.5).report(journal)
        assert "FLAG" in report.text
        assert "exceeds" in report.text
        assert "0.8000" in report.text
        assert report.groundedness.grounded_rate == 1.0

    def test_rmse_threshold_is_configurable(self, journal):
        journal.append("surface_fit", surface_event_data(essvi_rmse_vol_points=0.8))
        report = SurfaceAuditor(rmse_threshold_vol_points=1.0).report(journal)
        assert "FLAG" not in report.text

    def test_arbitrage_violations_are_flagged(self, journal):
        journal.append("surface_fit", surface_event_data(durrleman_violations=3))
        report = SurfaceAuditor().report(journal)
        assert "FLAG" in report.text
        assert "Durrleman 3" in report.text
        assert report.groundedness.grounded_rate == 1.0

    def test_reads_the_latest_surface_fit_event(self, journal):
        journal.append("surface_fit", surface_event_data(essvi_rmse_vol_points=0.21))
        journal.append("surface_fit", surface_event_data(essvi_rmse_vol_points=0.35))
        report = SurfaceAuditor().report(journal)
        assert "0.3500" in report.text
        assert all(claim.citations == (2,) for claim in report.claims)
        assert report.groundedness.grounded_rate == 1.0

    def test_missing_event_raises_naming_the_event_type(self, journal):
        journal.append("pnl_explain", pnl_event_data())  # wrong type only
        with pytest.raises(ValueError, match="surface_fit"):
            SurfaceAuditor().report(journal)


class TestPostMortemAnalyst:
    def test_healthy_explain_grounds_at_100_percent_with_numbers_in_text(self, journal):
        journal.append("pnl_explain", pnl_event_data(residual=-0.05, total=5.5))
        report = PostMortemAnalyst().report(journal)

        assert report.analyst == "post_mortem_analyst"
        assert report.groundedness.grounded_rate == 1.0
        assert all(v.grounded for v in report.groundedness.verdicts)
        assert "+12.50" in report.text  # theta carry
        assert "-8.25" in report.text  # gamma vs realized variance
        assert "level +3.00" in report.text  # per-factor vega
        assert "99.1%" in report.text  # explained fraction
        assert "FLAG" not in report.text
        assert all(claim.citations == (1,) for claim in report.claims)

    def test_low_explained_fraction_is_flagged(self, journal):
        journal.append("pnl_explain", pnl_event_data(residual=-0.6, total=5.5))
        report = PostMortemAnalyst().report(journal)  # 89.1% < 90% default floor
        assert "FLAG" in report.text
        assert "below" in report.text
        assert "89.1%" in report.text
        assert report.groundedness.grounded_rate == 1.0

    def test_explained_floor_is_configurable(self, journal):
        journal.append("pnl_explain", pnl_event_data(residual=-0.6, total=5.5))
        report = PostMortemAnalyst(min_explained_fraction=0.8).report(journal)
        assert "FLAG" not in report.text

    def test_missing_event_raises_naming_the_event_type(self, journal):
        journal.append("surface_fit", surface_event_data())  # wrong type only
        with pytest.raises(ValueError, match="pnl_explain"):
            PostMortemAnalyst().report(journal)

    def test_empty_journal_raises(self, tmp_path):
        with pytest.raises(ValueError, match="pnl_explain"):
            PostMortemAnalyst().report(EventLog(tmp_path, "empty-run"))

"""Tests for the RegimeAnalyst (journal-cited market-regime narration)."""

from __future__ import annotations

import pytest

from optitrade.desk import RegimeAnalyst
from optitrade.journal import EventLog


def features_event_data(**overrides):
    """Field names mirror the market_features event written by desk.cycle:
    {ts, spot, realized_vol, **day.features} with the synthetic market's
    feature keys (backtest.market_replay)."""
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


@pytest.fixture
def journal(tmp_path):
    return EventLog(tmp_path, "regime-run")


class TestRegimeAnalyst:
    def test_report_grounds_at_100_percent_with_numbers_in_text(self, journal):
        journal.append("market_features", features_event_data())
        report = RegimeAnalyst().report(journal)

        assert report.analyst == "regime_analyst"
        assert report.groundedness.grounded_rate == 1.0
        assert all(v.grounded for v in report.groundedness.verdicts)
        assert "100.00" in report.text  # spot
        assert "0.1800" in report.text  # realized vol
        assert "0.2400" in report.text  # ATM implied vol
        assert "+0.0600" in report.text  # VRP
        assert "+0.0100" in report.text  # term slope
        assert "+0.0200" in report.text  # 25d skew
        assert "above" in report.text  # implied trades above realized
        assert all(claim.citations == (1,) for claim in report.claims)

    def test_high_vrp_flag_fires_above_the_threshold(self, journal):
        journal.append("market_features", features_event_data(vrp=0.06))
        report = RegimeAnalyst(high_vrp=0.04).report(journal)
        assert "FLAG" in report.text
        assert "high-VRP" in report.text
        assert "0.0400" in report.text  # the threshold itself is stated
        assert report.groundedness.grounded_rate == 1.0

    def test_no_flags_when_everything_is_inside_the_thresholds(self, journal):
        journal.append(
            "market_features", features_event_data(vrp=0.02, term_slope=0.01, skew_25d=0.02)
        )
        report = RegimeAnalyst(high_vrp=0.04, steep_term=0.05, deep_skew=0.03).report(journal)
        assert "FLAG" not in report.text
        assert report.groundedness.grounded_rate == 1.0

    def test_steep_term_flag_fires_in_magnitude(self, journal):
        journal.append("market_features", features_event_data(term_slope=-0.08))
        report = RegimeAnalyst(steep_term=0.05).report(journal)
        assert "FLAG" in report.text
        assert "steep-term" in report.text
        assert "inverted" in report.text  # negative slope direction

    def test_deep_skew_flag_fires_in_magnitude(self, journal):
        journal.append("market_features", features_event_data(skew_25d=-0.05))
        report = RegimeAnalyst(deep_skew=0.03).report(journal)
        assert "FLAG" in report.text
        assert "deep-skew" in report.text
        assert "calls over puts" in report.text  # negative skew shape

    def test_thresholds_are_configurable(self, journal):
        journal.append("market_features", features_event_data(vrp=0.06))
        report = RegimeAnalyst(high_vrp=0.10).report(journal)
        assert "FLAG" not in report.text

    def test_reads_the_latest_market_features_event(self, journal):
        journal.append("market_features", features_event_data(vrp=0.02))
        journal.append("market_features", features_event_data(vrp=0.07))
        report = RegimeAnalyst().report(journal)
        assert "+0.0700" in report.text
        assert all(claim.citations == (2,) for claim in report.claims)
        assert report.groundedness.grounded_rate == 1.0

    def test_missing_features_are_listed_not_invented(self, journal):
        # A day journaled without derived features (bare MarketDay): the
        # analyst reports what exists and names what it cannot cover.
        journal.append(
            "market_features", {"ts": 1_700_000_000.0, "spot": 100.0, "realized_vol": 0.18}
        )
        report = RegimeAnalyst().report(journal)
        assert report.groundedness.grounded_rate == 1.0
        assert "Not journaled" in report.text
        for feature in ("atm_iv", "vrp", "term_slope", "skew_25d"):
            assert feature in report.text
        assert [claim.claim_id for claim in report.claims] == ["regime_market"]

    def test_missing_event_raises_naming_the_event_type(self, journal):
        journal.append("daily_cycle", {"date_ts": 0.0})  # wrong type only
        with pytest.raises(ValueError, match="market_features"):
            RegimeAnalyst().report(journal)

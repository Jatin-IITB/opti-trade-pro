"""Tests for the deterministic groundedness auditor (Prism auditor pattern)."""

from __future__ import annotations

import pytest

from optitrade.audit import AgentClaim, GroundednessAuditor
from optitrade.journal import EventLog


@pytest.fixture
def log(tmp_path):
    """A real journal with known events (sequences 1..3)."""
    log = EventLog(tmp_path, "audit-run")
    log.append(  # sequence 1: nested dicts, the canonical tool_call shape
        "tool_call",
        {
            "tool": "price_option",
            "args": {"spot": 100.0, "strike": 100.0},
            "result": {"price": 4.615, "greeks": {"delta": 7.35379, "gamma": 0.021}},
        },
    )
    log.append(  # sequence 2: numbers nested inside lists, plus a bool decoy
        "tool_call",
        {
            "tool": "run_scenarios",
            "args": {},
            "result": {"worst": {"pnl": -1234.5}, "pnl_row": [0.5, [12.25, -3.5]]},
            "approved": True,
        },
    )
    log.append(  # sequence 3: None values must not break the walk
        "risk_decision",
        {"verdict": "approve", "results": [{"allowed_quantity": None}]},
    )
    return log


def claim(claim_id="c1", statement="a claim", citations=(1,), values=()):
    return AgentClaim(
        claim_id=claim_id,
        statement=statement,
        citations=tuple(citations),
        values=tuple(values),
    )


class TestSingleClaims:
    def test_grounded_claim_passes_and_reports_match(self, log):
        report = GroundednessAuditor(log).audit(
            [claim(citations=(1,), values=(("delta", 7.353791),))]  # within rtol=1e-6
        )
        [verdict] = report.verdicts
        assert verdict.grounded
        assert verdict.reasons == ()
        assert verdict.matched == (("delta", 1),)
        assert report.grounded_rate == 1.0

    def test_wrong_value_fails_naming_the_value(self, log):
        report = GroundednessAuditor(log).audit([claim(citations=(1,), values=(("delta", 7.4),))])
        [verdict] = report.verdicts
        assert not verdict.grounded
        assert verdict.reasons == ("value delta=7.4 not found within rtol in cited events",)

    def test_nonexistent_citation_fails_naming_the_sequence(self, log):
        report = GroundednessAuditor(log).audit([claim(citations=(99,), values=())])
        [verdict] = report.verdicts
        assert not verdict.grounded
        assert "citation 99 does not exist" in verdict.reasons

    def test_no_citations_is_ungrounded(self, log):
        report = GroundednessAuditor(log).audit([claim(citations=(), values=(("delta", 7.35379),))])
        [verdict] = report.verdicts
        assert not verdict.grounded
        assert verdict.reasons == ("no citations",)

    def test_value_in_uncited_event_does_not_ground(self, log):
        # 12.25 lives in event 2; citing only event 1 must fail.
        report = GroundednessAuditor(log).audit([claim(citations=(1,), values=(("pnl", 12.25),))])
        assert not report.verdicts[0].grounded

    def test_one_missing_citation_ungrounds_even_if_values_match(self, log):
        report = GroundednessAuditor(log).audit(
            [claim(citations=(1, 99), values=(("delta", 7.35379),))]
        )
        [verdict] = report.verdicts
        assert not verdict.grounded
        assert verdict.reasons == ("citation 99 does not exist",)
        assert verdict.matched == (("delta", 1),)  # the value itself did match


class TestValueMatching:
    def test_values_found_in_nested_lists(self, log):
        report = GroundednessAuditor(log).audit(
            [claim(citations=(2,), values=(("cell_pnl", 12.25), ("worst_pnl", -1234.5)))]
        )
        [verdict] = report.verdicts
        assert verdict.grounded
        assert verdict.matched == (("cell_pnl", 2), ("worst_pnl", 2))

    def test_bool_true_does_not_ground_numeric_one(self, log):
        report = GroundednessAuditor(log).audit(
            [claim(citations=(2,), values=(("approved", 1.0),))]
        )
        assert not report.verdicts[0].grounded

    def test_none_values_in_data_are_skipped_not_fatal(self, log):
        report = GroundednessAuditor(log).audit([claim(citations=(3,), values=())])
        assert report.verdicts[0].grounded

    def test_custom_rtol_widens_the_match(self, log):
        auditor = GroundednessAuditor(log)
        loose = auditor.audit([claim(citations=(1,), values=(("delta", 7.0),))], rtol=0.1)
        strict = auditor.audit([claim(citations=(1,), values=(("delta", 7.0),))])
        assert loose.verdicts[0].grounded
        assert not strict.verdicts[0].grounded

    def test_match_reports_the_citation_that_contains_the_value(self, log):
        report = GroundednessAuditor(log).audit(
            [claim(citations=(1, 2), values=(("pnl", -1234.5),))]
        )
        [verdict] = report.verdicts
        assert verdict.grounded
        assert verdict.matched == (("pnl", 2),)


class TestReport:
    def test_mixed_report_grounded_rate(self, log):
        claims = [
            claim("good-1", citations=(1,), values=(("delta", 7.35379),)),
            claim("good-2", citations=(2,), values=(("pnl", -1234.5),)),
            claim("bad-value", citations=(1,), values=(("delta", 99.9),)),
            claim("bad-cite", citations=(), values=()),
        ]
        report = GroundednessAuditor(log).audit(claims)
        assert report.grounded_rate == pytest.approx(0.5)
        assert [v.grounded for v in report.verdicts] == [True, True, False, False]

    def test_summary_names_the_ungrounded_claims(self, log):
        report = GroundednessAuditor(log).audit(
            [
                claim("good", citations=(1,), values=(("delta", 7.35379),)),
                claim("bad", citations=(), values=()),
            ]
        )
        text = report.summary()
        assert "2 claim" in text
        assert "1 grounded" in text
        assert "bad" in text
        assert "no citations" in text

    def test_empty_batch_rate_and_summary(self, log):
        report = GroundednessAuditor(log).audit([])
        assert report.grounded_rate == 1.0
        assert report.summary() == "No claims were audited."


class TestAuditorConstruction:
    def test_accepts_a_plain_sequence_of_events(self, log):
        events = list(log.replay())
        report = GroundednessAuditor(events).audit(
            [claim(citations=(1,), values=(("delta", 7.35379),))]
        )
        assert report.verdicts[0].grounded

    def test_event_log_is_replayed_afresh_so_new_events_are_citable(self, log):
        auditor = GroundednessAuditor(log)
        log.append("tool_call", {"tool": "price_option", "result": {"price": 55.5}})  # seq 4
        report = auditor.audit([claim(citations=(4,), values=(("price", 55.5),))])
        assert report.verdicts[0].grounded

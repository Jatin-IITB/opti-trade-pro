"""Tests for the analyst panel service.

The load-bearing properties, in order of how much damage their absence does:

1. A claim's badge reflects the *auditor's* verdict, not the analyst's
   confidence. An ungrounded claim must reach the wire marked ungrounded, with
   its reason, because the panel's whole purpose is to make unsourced prose
   visible.
2. Partial coverage is reported, never hidden. An analyst with no facts to
   cite appears in ``failures`` with the event type it needed.
3. The ``analysts`` key is always emitted, including on failure. An absent key
   leaves the previous reports on screen (the frontend merges only what it
   receives), which is stale prose still wearing its old grounded badges.
4. Nothing here writes to the journal.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from options_trading.services.analyst_service import (
    AnalystService,
    AnalystServiceConfig,
    unavailable_analysts_wire,
)
from optitrade.journal import EventLog

# A market_features payload with every derived feature present, so
# RegimeAnalyst produces its full five claims.
FULL_FEATURES = {
    "ts": "2026-09-03",
    "spot": 23_873.45,
    "realized_vol": 0.0966,
    "atm_iv": 0.1400,
    "vrp": 0.0434,
    "term_slope": 0.0120,
    "skew_25d": -0.0077,
}


@pytest.fixture()
def config() -> AnalystServiceConfig:
    return AnalystServiceConfig(journal_run_id="desk", refresh_seconds=600.0)


def _journal(tmp_path, run_id: str = "desk") -> EventLog:
    directory = tmp_path / "journal"
    directory.mkdir(exist_ok=True)
    return EventLog(directory, run_id)


def _service(tmp_path, config: AnalystServiceConfig, clock=None) -> AnalystService:
    return AnalystService(
        tmp_path / "journal",
        config,
        **({"clock": clock} if clock is not None else {}),
    )


def _claim(wire: dict, claim_id: str) -> dict:
    """One claim from the first analyst's report, by id."""
    return next(c for c in wire["analysts"][0]["claims"] if c["claimId"] == claim_id)


class TestNoJournal:
    """A desk that has never run says so; it does not render a sample report."""

    def test_missing_journal_reports_no_journal(self, tmp_path, config):
        wire = _service(tmp_path, config).build().to_wire_dict()
        assert wire["hasJournal"] is False
        assert wire["analysts"] == []
        assert wire["reason"]

    def test_missing_journal_leaves_grounded_rate_unmeasured(self, tmp_path, config):
        """Not 0.0 and not 1.0: both read as the result of an audit."""
        wire = _service(tmp_path, config).build().to_wire_dict()
        assert wire["groundedRate"] is None
        assert wire["claimsTotal"] == 0

    def test_an_empty_journal_file_is_not_a_journal_to_analyse(self, tmp_path, config):
        directory = tmp_path / "journal"
        directory.mkdir()
        (directory / "desk.jsonl").write_text("", encoding="utf-8")
        wire = _service(tmp_path, config).build().to_wire_dict()
        assert wire["hasJournal"] is False
        assert wire["eventsSeen"] == 0

    def test_the_excluded_analyst_is_named_even_with_no_journal(self, tmp_path, config):
        """The roster must be honest before there is anything to report."""
        wire = _service(tmp_path, config).build().to_wire_dict()
        assert [e["name"] for e in wire["excluded"]] == ["risk_officer_analyst"]

    def test_reason_explains_what_to_do(self, tmp_path, config):
        wire = _service(tmp_path, config).build().to_wire_dict()
        assert "desk" in wire["reason"].lower()


class TestRunIdMismatchIsDistinguishable:
    """An idle desk and a misconfigured panel must not read the same.

    Both leave the configured journal absent, so before this the payload was
    identical and the message — "run a desk cycle from the Desk tab and this
    panel fills in" — was actively wrong for the second: every cycle writes to
    the journal the configured id does not read. A panel whose purpose is
    making unfounded prose visible must not emit any.
    """

    @pytest.fixture()
    def mismatched(self, tmp_path):
        journal = _journal(tmp_path, "desk")
        for _ in range(3):
            journal.append("market_features", dict(FULL_FEATURES))
        return AnalystService(
            tmp_path / "journal", AnalystServiceConfig(journal_run_id="desk-prod")
        ).build()

    @pytest.fixture()
    def idle(self, tmp_path):
        # Its own directory: sharing tmp_path with the `mismatched` fixture
        # would let this one find that one's journal and report a running desk.
        directory = tmp_path / "idle-journal"
        directory.mkdir()
        return AnalystService(directory, AnalystServiceConfig(journal_run_id="desk")).build()

    def test_the_mismatch_is_flagged(self, mismatched):
        assert mismatched.run_id_mismatch is True

    def test_an_idle_desk_is_not_flagged_as_a_mismatch(self, idle):
        assert idle.run_id_mismatch is False
        assert idle.available_run_ids == ()

    def test_the_two_states_do_not_share_a_reason(self, mismatched, idle):
        assert mismatched.reason != idle.reason

    def test_the_mismatch_does_not_prescribe_running_a_cycle(self, mismatched):
        """The remedy that can never work must not be offered."""
        assert "Run a desk cycle from the Desk tab" not in (mismatched.reason or "")
        assert "will not fill this panel" in mismatched.reason

    def test_the_mismatch_names_the_journals_it_found(self, mismatched):
        assert mismatched.available_run_ids == ("desk",)
        assert "'desk'" in mismatched.reason

    def test_the_mismatch_names_the_setting_to_change(self, mismatched):
        assert "DESK_JOURNAL_RUN_ID" in mismatched.reason

    def test_the_mismatch_warns(self, mismatched, idle):
        assert mismatched.warnings, "a misconfiguration is worth a warning"
        assert idle.warnings == (), "an idle desk is not a problem to warn about"

    def test_events_seen_stays_zero_because_none_were_read(self, mismatched):
        """The three events on disk are real, but not in this journal."""
        assert mismatched.events_seen == 0
        assert mismatched.has_journal is False

    def test_several_other_run_ids_are_all_named(self, tmp_path):
        for run_id in ("desk-prod", "desk-staging", "desk"):
            _journal(tmp_path, run_id).append("market_features", dict(FULL_FEATURES))
        payload = AnalystService(
            tmp_path / "journal", AnalystServiceConfig(journal_run_id="absent")
        ).build()
        assert payload.available_run_ids == ("desk", "desk-prod", "desk-staging")

    def test_an_empty_configured_journal_beside_a_populated_one_is_flagged(self, tmp_path):
        """The empty-file branch prescribes a cycle too, so it needs the check."""
        _journal(tmp_path, "desk").append("market_features", dict(FULL_FEATURES))
        (tmp_path / "journal" / "desk-prod.jsonl").write_text("", encoding="utf-8")
        payload = AnalystService(
            tmp_path / "journal", AnalystServiceConfig(journal_run_id="desk-prod")
        ).build()
        assert payload.run_id_mismatch is True
        assert "desk" in payload.reason

    def test_the_matching_run_id_reads_the_events(self, tmp_path):
        """The control: the same directory, the right id, three events."""
        journal = _journal(tmp_path, "desk")
        for _ in range(3):
            journal.append("market_features", dict(FULL_FEATURES))
        payload = AnalystService(
            tmp_path / "journal", AnalystServiceConfig(journal_run_id="desk")
        ).build()
        assert payload.has_journal is True
        assert payload.events_seen == 3
        assert payload.run_id_mismatch is False

    def test_the_flag_reaches_the_wire(self, mismatched):
        wire = mismatched.to_wire_dict()
        assert wire["runIdMismatch"] is True
        assert wire["availableRunIds"] == ["desk"]


class TestZeroClaimReportIsNotAllGrounded:
    """A report that audited nothing is unaudited, not perfect.

    Currently unreachable — every roster analyst builds at least one claim
    unconditionally — so this pins the intent against a hand-built report. The
    trap it closes: ``claimsGrounded == claimsTotal`` is trivially true at 0,
    which drew a green "all grounded" badge over prose citing nothing.
    """

    @pytest.fixture()
    def wire(self, tmp_path):
        from options_trading.services.analyst_service import _report_wire
        from optitrade.audit.groundedness import GroundednessReport
        from optitrade.desk.analysts import AnalystReport

        return _report_wire(
            AnalystReport(
                analyst="regime_analyst",
                text="a paragraph asserting nothing checkable",
                claims=(),
                groundedness=GroundednessReport(verdicts=()),
            )
        )

    def test_the_rate_is_unmeasured_not_perfect(self, wire):
        """Consistent with unavailable_analysts_wire: None, not 0.0 or 1.0."""
        assert wire["groundedRate"] is None

    def test_the_counts_are_zero(self, wire):
        assert wire["claimsTotal"] == 0
        assert wire["claimsGrounded"] == 0

    def test_the_core_still_calls_an_empty_batch_grounded(self, tmp_path):
        """Documents the divergence, so it reads as deliberate.

        ``GroundednessReport.grounded_rate`` returns 1.0 for an empty batch
        ("nothing failed"), which is right for an auditor. It is wrong for a
        panel, where the number is displayed as a measurement.
        """
        from optitrade.audit.groundedness import GroundednessReport

        assert GroundednessReport(verdicts=()).grounded_rate == 1.0

    def test_every_roster_analyst_makes_at_least_one_claim(self, tmp_path, config):
        """Why this is a latent trap rather than a live bug."""
        journal = _journal(tmp_path)
        journal.append("market_features", dict(FULL_FEATURES))
        payload = _service(tmp_path, config).build()
        assert all(a["claimsTotal"] >= 1 for a in payload.analysts)
        assert all(a["groundedRate"] is not None for a in payload.analysts)


class TestPartialCoverage:
    """One event type present, two absent — the state on a real desk today."""

    @pytest.fixture()
    def wire(self, tmp_path, config):
        journal = _journal(tmp_path)
        journal.append("market_features", dict(FULL_FEATURES))
        journal.append("daily_cycle", {"n_orders": 0, "n_fills": 0})
        return _service(tmp_path, config).build().to_wire_dict()

    def test_the_analyst_with_facts_reports(self, wire):
        assert [a["name"] for a in wire["analysts"]] == ["regime_analyst"]
        assert wire["hasJournal"] is True

    def test_the_analysts_without_facts_are_listed_not_dropped(self, wire):
        failures = {f["name"]: f for f in wire["failures"]}
        assert set(failures) == {"surface_auditor", "post_mortem_analyst"}

    def test_each_failure_names_the_event_type_it_needed(self, wire):
        requires = {f["name"]: f["requires"] for f in wire["failures"]}
        assert requires["surface_auditor"] == "surface_fit"
        assert requires["post_mortem_analyst"] == "pnl_explain"

    def test_failure_reason_is_the_engine_message_not_a_paraphrase(self, wire):
        reasons = {f["name"]: f["reason"] for f in wire["failures"]}
        assert "surface_fit" in reasons["surface_auditor"]
        assert "ValueError" in reasons["surface_auditor"]

    def test_reported_plus_failed_covers_the_whole_roster(self, wire):
        assert len(wire["analysts"]) + len(wire["failures"]) == wire["rosterSize"]

    def test_partial_coverage_is_warned_about_not_silent(self, wire):
        assert any("analysts had no journaled facts" in w for w in wire["warnings"])

    def test_a_partial_run_is_still_hasJournal_true(self, wire):
        """Two of three failing is normal; the panel still renders the one."""
        assert wire["hasJournal"] is True
        assert wire["analysts"][0]["claimsTotal"] > 0


class TestGroundedness:
    """The badge must come from the auditor, on real and on tampered data."""

    def test_claims_from_a_real_event_are_grounded(self, tmp_path, config):
        journal = _journal(tmp_path)
        journal.append("market_features", dict(FULL_FEATURES))
        wire = _service(tmp_path, config).build().to_wire_dict()
        report = wire["analysts"][0]
        assert report["claimsTotal"] == 5, "every feature present should yield five claims"
        assert report["claimsGrounded"] == 5
        assert all(c["grounded"] for c in report["claims"])
        assert wire["groundedRate"] == pytest.approx(1.0)

    def test_every_claim_carries_the_sequences_it_cites(self, tmp_path, config):
        journal = _journal(tmp_path)
        event = journal.append("market_features", dict(FULL_FEATURES))
        wire = _service(tmp_path, config).build().to_wire_dict()
        for claim in wire["analysts"][0]["claims"]:
            assert claim["citations"] == [event.sequence]

    def test_a_claim_records_where_each_value_was_found(self, tmp_path, config):
        journal = _journal(tmp_path)
        journal.append("market_features", dict(FULL_FEATURES))
        wire = _service(tmp_path, config).build().to_wire_dict()
        matched = wire["analysts"][0]["claims"][0]["matched"]
        assert matched, "a grounded claim must say which event grounded it"
        assert all(m["sequence"] >= 1 for m in matched)

    def test_rewriting_the_journal_does_not_forge_a_grounded_claim(self, tmp_path, config):
        """The badge tracks the journal, not a remembered number.

        A deterministic analyst reads the journal, states what it read, and
        audits itself against the same journal, so it grounds by construction
        — the core's own tests assert ``grounded_rate == 1.0``. Rewriting the
        file therefore does not produce an ungrounded claim; it produces a
        claim about the *new* number. Asserting that is what stops this test
        from quietly passing on a stale cached payload, and the wire's
        handling of a genuinely ungrounded claim is covered by
        :class:`TestUngroundedClaimsReachTheWire` using the real auditor.
        """
        journal = _journal(tmp_path)
        journal.append("market_features", dict(FULL_FEATURES))
        path = tmp_path / "journal" / "desk.jsonl"

        clean = _service(tmp_path, config).build().to_wire_dict()
        assert clean["groundedRate"] == pytest.approx(1.0)
        assert "-0.0077" in _claim(clean, "regime_skew")["statement"]

        raw = json.loads(path.read_text(encoding="utf-8").strip())
        raw["data"]["skew_25d"] = -0.5
        path.write_text(json.dumps(raw) + "\n", encoding="utf-8")

        rebuilt = _service(tmp_path, config).build().to_wire_dict()
        assert "-0.5" in _claim(rebuilt, "regime_skew")["statement"]
        assert _claim(rebuilt, "regime_skew")["grounded"] is True

    def test_claim_totals_are_the_sum_over_analysts(self, tmp_path, config):
        journal = _journal(tmp_path)
        journal.append("market_features", dict(FULL_FEATURES))
        wire = _service(tmp_path, config).build().to_wire_dict()
        assert wire["claimsTotal"] == sum(a["claimsTotal"] for a in wire["analysts"])
        assert wire["claimsGrounded"] == sum(a["claimsGrounded"] for a in wire["analysts"])

    def test_features_absent_from_the_event_produce_no_claim(self, tmp_path, config):
        """No claim without an engine number to cite."""
        journal = _journal(tmp_path)
        journal.append(
            "market_features",
            {"ts": "2026-09-03", "spot": 23_873.45, "realized_vol": 0.0966},
        )
        wire = _service(tmp_path, config).build().to_wire_dict()
        report = wire["analysts"][0]
        assert report["claimsTotal"] == 1, "only spot/realized_vol were journaled"
        assert report["claimsGrounded"] == 1


class TestUngroundedClaimsReachTheWire:
    """The display contract for a claim that does not ground.

    The deterministic analysts cite what they just read, so they cannot
    produce this state — but the badge exists for prose that *can*, and the
    LLM tier lands on these same rails. The verdicts here come from the real
    :class:`GroundednessAuditor` against a real journal; only the claims are
    hand-written, because a fabricated claim is what is being rendered.
    """

    @pytest.fixture()
    def wire(self, tmp_path):
        from options_trading.services.analyst_service import _report_wire
        from optitrade.audit.groundedness import AgentClaim, GroundednessAuditor
        from optitrade.desk.analysts import AnalystReport

        journal = _journal(tmp_path)
        journal.append("market_features", dict(FULL_FEATURES))

        claims = (
            AgentClaim(
                claim_id="honest",
                statement="spot is 23873.45 (seq 1)",
                citations=(1,),
                values=(("spot", 23_873.45),),
            ),
            AgentClaim(
                claim_id="fabricated_number",
                statement="spot is 99999 (seq 1)",
                citations=(1,),
                values=(("spot", 99_999.0),),
            ),
            AgentClaim(
                claim_id="uncited",
                statement="vol looks likely to rise",
                citations=(),
                values=(),
            ),
            AgentClaim(
                claim_id="citation_does_not_exist",
                statement="spot is 23873.45 (seq 42)",
                citations=(42,),
                values=(("spot", 23_873.45),),
            ),
        )
        report = AnalystReport(
            analyst="regime_analyst",
            text="a mix of sourced and unsourced sentences",
            claims=claims,
            groundedness=GroundednessAuditor(journal).audit(claims),
        )
        return _report_wire(report)

    def test_a_sourced_claim_is_grounded(self, wire):
        assert wire["claims"][0]["grounded"] is True
        assert wire["claims"][0]["matched"]

    def test_a_number_absent_from_the_cited_event_is_ungrounded(self, wire):
        claim = wire["claims"][1]
        assert claim["grounded"] is False
        assert "spot" in " ".join(claim["reasons"])

    def test_a_claim_citing_nothing_is_ungrounded(self, wire):
        claim = wire["claims"][2]
        assert claim["grounded"] is False
        assert claim["reasons"] == ["no citations"]

    def test_a_claim_citing_a_missing_sequence_is_ungrounded(self, wire):
        claim = wire["claims"][3]
        assert claim["grounded"] is False
        assert "42" in " ".join(claim["reasons"])

    def test_ungrounded_claims_are_rendered_not_dropped(self, wire):
        """Hiding them would leave the paragraph looking fully sourced."""
        assert wire["claimsTotal"] == 4
        assert wire["claimsGrounded"] == 1
        assert wire["groundedRate"] == pytest.approx(0.25)

    def test_every_claim_keeps_its_statement_and_citations(self, wire):
        ids = [c["claimId"] for c in wire["claims"]]
        assert ids == [
            "honest",
            "fabricated_number",
            "uncited",
            "citation_does_not_exist",
        ]
        assert wire["claims"][3]["citations"] == [42]

    def test_a_missing_verdict_is_not_rendered_as_a_pass(self):
        """Fail closed: no verdict means unproven, not proven."""
        from options_trading.services.analyst_service import _claim_wire
        from optitrade.audit.groundedness import AgentClaim

        claim = AgentClaim(
            claim_id="orphan",
            statement="spot is 1 (seq 1)",
            citations=(1,),
            values=(("spot", 1.0),),
        )
        rendered = _claim_wire(claim, None)
        assert rendered["grounded"] is False
        assert rendered["reasons"]

    def test_verdicts_are_paired_to_claims_by_id_not_position(self, tmp_path):
        """A green badge on the wrong claim is the failure this prevents."""
        from options_trading.services.analyst_service import _report_wire
        from optitrade.audit.groundedness import (
            AgentClaim,
            ClaimVerdict,
            GroundednessReport,
        )
        from optitrade.desk.analysts import AnalystReport

        claims = (
            AgentClaim(claim_id="first", statement="a", citations=(1,), values=()),
            AgentClaim(claim_id="second", statement="b", citations=(1,), values=()),
        )
        # Verdicts deliberately in the opposite order to the claims.
        report = AnalystReport(
            analyst="regime_analyst",
            text="",
            claims=claims,
            groundedness=GroundednessReport(
                verdicts=(
                    ClaimVerdict(claim_id="second", grounded=False, reasons=("nope",), matched=()),
                    ClaimVerdict(claim_id="first", grounded=True, reasons=(), matched=()),
                )
            ),
        )
        rendered = {c["claimId"]: c for c in _report_wire(report)["claims"]}
        assert rendered["first"]["grounded"] is True
        assert rendered["second"]["grounded"] is False


class TestConfigDrivesTheAnalysts:
    """Thresholds come from the config, not from literals in the flow."""

    def test_a_low_vrp_threshold_flags_a_regime_a_high_one_does_not(self, tmp_path, config):
        journal = _journal(tmp_path)
        journal.append("market_features", dict(FULL_FEATURES))

        flagged = AnalystService(tmp_path / "journal", AnalystServiceConfig(high_vrp=0.01)).build()
        quiet = AnalystService(tmp_path / "journal", AnalystServiceConfig(high_vrp=0.99)).build()

        assert "FLAG" in flagged.analysts[0]["summary"]
        assert "FLAG" not in quiet.analysts[0]["summary"]

    def test_rejects_a_non_positive_refresh_interval(self):
        with pytest.raises(ValueError, match="refresh_seconds"):
            AnalystServiceConfig(refresh_seconds=0.0)

    def test_rejects_an_out_of_range_explained_fraction(self):
        with pytest.raises(ValueError, match="min_explained_fraction"):
            AnalystServiceConfig(min_explained_fraction=1.5)

    def test_config_from_settings_matches_the_deployed_values(self):
        from options_trading.config.settings import settings
        from options_trading.services.analyst_service import analyst_config_from_settings

        cfg = analyst_config_from_settings()
        assert cfg.journal_run_id == settings.desk_journal_run_id
        assert cfg.high_vrp == settings.analyst_high_vrp
        assert cfg.refresh_seconds == settings.analyst_refresh_seconds

    def test_the_run_id_selects_the_journal(self, tmp_path):
        """A mismatched run id must not be reported as an idle desk.

        This previously asserted only ``has_journal is False``, which is what
        a legitimately-empty desk returns too — so it passed while the panel
        told users to run a desk cycle that could never fill it. The point of
        the run id is the *distinction*, so that is what is asserted.
        """
        _journal(tmp_path, "desk").append("market_features", dict(FULL_FEATURES))
        other = AnalystService(
            tmp_path / "journal", AnalystServiceConfig(journal_run_id="not-the-desk")
        ).build()

        assert other.has_journal is False
        assert other.run_id_mismatch is True, "a mismatch must be distinguishable"
        assert other.available_run_ids == ("desk",), "and must name what it found"
        assert "not-the-desk" in other.reason
        assert "'desk'" in other.reason

    def test_the_settings_factory_pins_both_services_to_one_run_id(self):
        """The two configs cannot diverge, because there is one setting.

        The mismatch above was reachable in deployment: the analyst run id was
        settings-driven while ``DeskServiceConfig.journal_run_id`` was a
        hardcoded default its factory never set, so a user could move the
        panel but never the desk.
        """
        from options_trading.config.settings import settings
        from options_trading.services.analyst_service import analyst_config_from_settings
        from options_trading.services.desk_service import desk_config_from_settings

        assert (
            analyst_config_from_settings().journal_run_id
            == desk_config_from_settings().journal_run_id
            == settings.desk_journal_run_id
        )

    def test_there_is_no_analyst_specific_run_id_setting(self):
        """A second setting would restore the ability to drift apart."""
        from options_trading.config.settings import settings

        assert not hasattr(settings, "analyst_journal_run_id")


class TestCaching:
    """Mirrors HistoryAnalytics: keyed on the journal, bounded by an interval."""

    def test_second_build_is_served_from_cache(self, tmp_path, config):
        journal = _journal(tmp_path)
        journal.append("market_features", dict(FULL_FEATURES))
        service = _service(tmp_path, config)
        first = service.build()
        assert service.build() is first

    def test_a_new_event_invalidates_the_cache(self, tmp_path, config):
        journal = _journal(tmp_path)
        journal.append("market_features", dict(FULL_FEATURES))
        service = _service(tmp_path, config)
        first = service.build()
        journal.append("market_features", {**FULL_FEATURES, "spot": 24_000.0})
        second = service.build()
        assert second is not first
        assert second.events_seen == 2

    def test_cache_survives_until_the_refresh_interval_elapses(self, tmp_path):
        now = [1_000.0]
        journal = _journal(tmp_path)
        journal.append("market_features", dict(FULL_FEATURES))
        service = AnalystService(
            tmp_path / "journal",
            AnalystServiceConfig(refresh_seconds=60.0),
            clock=lambda: now[0],
        )
        first = service.build()
        now[0] += 59.0
        assert service.build() is first
        now[0] += 2.0
        assert service.build() is not first

    def test_a_journal_appearing_later_is_picked_up(self, tmp_path, config):
        """The empty-store payload must not be cached past the first write."""
        service = _service(tmp_path, config)
        assert service.build().has_journal is False
        _journal(tmp_path).append("market_features", dict(FULL_FEATURES))
        assert service.build().has_journal is True

    def test_build_async_returns_the_same_payload(self, tmp_path, config):
        _journal(tmp_path).append("market_features", dict(FULL_FEATURES))
        service = _service(tmp_path, config)

        async def two_concurrent():
            return await asyncio.gather(service.build_async(), service.build_async())

        first, second = asyncio.run(two_concurrent())
        assert first is second, "concurrent callers share one in-flight build"

    def test_a_cancelled_caller_does_not_abandon_the_work(self, tmp_path, config):
        """The cancellation race this shape exists to prevent.

        ``asyncio.to_thread`` cancels the future, not the thread. A caller
        that goes away must not leave the next caller to restart the replay.
        """
        _journal(tmp_path).append("market_features", dict(FULL_FEATURES))
        service = _service(tmp_path, config)

        async def cancel_then_await():
            doomed = asyncio.ensure_future(service.build_async())
            await asyncio.sleep(0)
            doomed.cancel()
            with pytest.raises(asyncio.CancelledError):
                await doomed
            return await service.build_async()

        assert asyncio.run(cancel_then_await()).has_journal is True


class TestFailClosed:
    """An error must not become a pass-through (ADR-008)."""

    def test_a_corrupt_journal_reports_unavailable_rather_than_raising(self, tmp_path, config):
        directory = tmp_path / "journal"
        directory.mkdir()
        (directory / "desk.jsonl").write_text("{not json at all\n", encoding="utf-8")
        payload = _service(tmp_path, config).build()
        assert payload.has_journal is False
        assert payload.reason
        assert payload.warnings

    def test_a_failing_build_still_emits_every_key(self, tmp_path, config):
        directory = tmp_path / "journal"
        directory.mkdir()
        (directory / "desk.jsonl").write_text("{not json at all\n", encoding="utf-8")
        wire = _service(tmp_path, config).build().to_wire_dict()
        assert set(wire) == set(unavailable_analysts_wire("x"))

    def test_unavailable_wire_has_the_same_keys_as_a_real_payload(self, tmp_path, config):
        """A consumer must not have to branch on which shape it received."""
        _journal(tmp_path).append("market_features", dict(FULL_FEATURES))
        real = _service(tmp_path, config).build().to_wire_dict()
        assert set(real) == set(unavailable_analysts_wire("boom"))

    def test_unavailable_wire_declares_no_journal_and_no_rate(self):
        wire = unavailable_analysts_wire("the journal is unreadable")
        assert wire["hasJournal"] is False
        assert wire["groundedRate"] is None
        assert wire["analysts"] == []
        assert wire["reason"] == "the journal is unreadable"
        assert wire["warnings"] == ["the journal is unreadable"]

    def test_unavailable_wire_still_names_the_excluded_analyst(self):
        assert unavailable_analysts_wire("x")["excluded"]


class TestReadOnly:
    """The panel is read on every dashboard tick; it must not write."""

    def test_building_does_not_append_to_the_journal(self, tmp_path, config):
        journal = _journal(tmp_path)
        journal.append("market_features", dict(FULL_FEATURES))
        path = tmp_path / "journal" / "desk.jsonl"
        before = path.read_bytes()

        service = _service(tmp_path, config)
        service.build()
        service._cached = None  # force a second real build
        service.build()

        assert path.read_bytes() == before, "the analyst panel journaled something"

    def test_no_new_journal_files_are_created(self, tmp_path, config):
        directory = tmp_path / "journal"
        directory.mkdir()
        _service(tmp_path, config).build()
        assert list(directory.iterdir()) == [], "a read created a journal file"

    def test_the_risk_officer_is_excluded_with_a_stated_reason(self, tmp_path, config):
        """It journals the query it cites, so it cannot run on a read path."""
        wire = _service(tmp_path, config).build().to_wire_dict()
        excluded = wire["excluded"][0]
        assert excluded["name"] == "risk_officer_analyst"
        assert excluded["reason"].strip(), "an excluded analyst needs a stated reason"

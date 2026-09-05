"""Analyst panel: deterministic analyst reports, each claim audited for provenance.

:mod:`optitrade.desk.analysts` holds four analysts that read journaled engine
facts and write plain-English reports. Each report carries machine-checkable
:class:`~optitrade.audit.groundedness.AgentClaim` records naming the journal
sequences its numbers came from, and
:class:`~optitrade.audit.groundedness.GroundednessAuditor` replays the journal
to confirm every asserted number actually appears in a cited event.

That audit is the whole point of surfacing this. Analyst prose is the one
place in the product where a sentence and a fabrication are typographically
identical, so the panel renders the verdict beside every claim rather than
reporting a single aggregate score the reader has to trust. A claim whose
number is not in its cited events is shown as ungrounded with the auditor's
reason.

**Partial failure is the normal case, not an error path.** Each analyst reads
one event type and raises ``ValueError`` when the journal has none, so on a
desk that has run one cycle most of the roster legitimately has nothing to
say. :class:`~optitrade.agents.orchestrator.AnalystOrchestrator` is fail-open
by design — an analyst that raises becomes an ``AnalystFailure`` rather than
taking the others down — which is correct here: analysts observe and explain,
and the fail-closed boundary is the risk engine (ADR-008). The panel's job is
to be honest about which of them ran, so failures are reported with their
reason rather than hidden.

Read-only throughout: nothing here appends to the journal or touches order
flow. See :data:`_EXCLUDED` for why the fourth analyst is not in the roster.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from optitrade.agents.orchestrator import AnalystOrchestrator, OrchestratorReport
from optitrade.audit.groundedness import ClaimVerdict
from optitrade.desk.analysts import (
    AnalystReport,
    PostMortemAnalyst,
    RegimeAnalyst,
    SurfaceAuditor,
)
from optitrade.journal import EventLog

logger = logging.getLogger(__name__)

#: Human-readable titles and the journal event each analyst reads. The event
#: type is surfaced so a failure can say *what is missing* rather than only
#: that something was: "needs a surface_fit event" tells a user the surface
#: fitter has not run, which is actionable.
_ROSTER_META: dict[str, tuple[str, str]] = {
    "regime_analyst": ("Regime Analyst", "market_features"),
    "surface_auditor": ("Surface Auditor", "surface_fit"),
    "post_mortem_analyst": ("Post-Mortem Analyst", "pnl_explain"),
}

#: ``RiskOfficerAnalyst`` is deliberately absent from the roster, and the panel
#: says so rather than implying the desk has three analysts.
#:
#: Two reasons, either sufficient. It exposes ``answer(query, book, spot, rate,
#: journal)`` rather than ``report(journal)``, so it does not satisfy the
#: orchestrator's analyst protocol and would have to be special-cased. More
#: decisively, ``answer`` *appends* a ``scenario_query`` event to the desk
#: journal before citing it — that is correct for a deliberate what-if, but
#: this panel is read on every dashboard tick, and a read that writes would
#: inflate the desk's audit trail with synthetic queries nobody asked for and
#: shift the sequence numbers every other citation is checked against.
#:
#: Wiring it needs a user-supplied :class:`~optitrade.desk.analysts.
#: ScenarioQuery` on an explicit request against the current book, which is a
#: different endpoint from this one.
_EXCLUDED: tuple[dict[str, str], ...] = (
    {
        "name": "risk_officer_analyst",
        "title": "Risk Officer",
        "reason": (
            "Not run: it answers a specific what-if scenario against the book and journals "
            "the query it cites, so it needs an explicit request rather than a dashboard "
            "refresh. It is listed here so this panel is not read as the whole roster."
        ),
    },
)


@dataclass(frozen=True)
class AnalystServiceConfig:
    """Typed configuration for the analyst panel — no literals in the flow.

    The four thresholds are the analysts' own constructor arguments, lifted
    into settings so a deployment can retune what counts as a flag without
    editing the quant core. Construct via :func:`analyst_config_from_settings`
    rather than by hand so deployed and tested values cannot diverge.
    """

    #: Journal run id. Must match ``DeskServiceConfig.journal_run_id`` or the
    #: panel audits a different (probably empty) journal than the desk writes.
    journal_run_id: str = "desk"
    refresh_seconds: float = 60.0
    #: eSSVI RMSE above this many vol points is flagged by the surface auditor.
    rmse_threshold_vol_points: float = 0.5
    #: A P&L decomposition explaining less than this fraction is flagged.
    min_explained_fraction: float = 0.9
    #: Variance risk premium above this is flagged as a rich-premium regime.
    high_vrp: float = 0.04
    #: Term slope whose magnitude exceeds this is flagged as steep/inverted.
    steep_term: float = 0.05
    #: 25-delta skew whose magnitude exceeds this is flagged as pronounced.
    deep_skew: float = 0.03

    def __post_init__(self) -> None:
        if self.refresh_seconds <= 0:
            raise ValueError(f"refresh_seconds must be positive, got {self.refresh_seconds}")
        if not 0.0 < self.min_explained_fraction <= 1.0:
            raise ValueError(
                "min_explained_fraction must be a fraction in (0, 1], got "
                f"{self.min_explained_fraction}"
            )
        if self.rmse_threshold_vol_points <= 0:
            raise ValueError(
                f"rmse_threshold_vol_points must be positive, got {self.rmse_threshold_vol_points}"
            )


def analyst_config_from_settings() -> AnalystServiceConfig:
    """Build the config from deployed settings (single source of truth)."""
    from options_trading.config.settings import settings

    return AnalystServiceConfig(
        # The desk's run id, not an analyst-specific one. The panel audits what
        # the desk wrote, so the two are pinned to a single setting: a config
        # that let them differ could only ever be a misconfiguration, and it
        # produced a panel that reported an empty desk that had run (ADR-028).
        journal_run_id=settings.desk_journal_run_id,
        refresh_seconds=settings.analyst_refresh_seconds,
        rmse_threshold_vol_points=settings.analyst_surface_rmse_threshold,
        min_explained_fraction=settings.analyst_min_explained_fraction,
        high_vrp=settings.analyst_high_vrp,
        steep_term=settings.analyst_steep_term,
        deep_skew=settings.analyst_deep_skew,
    )


def _quoted(names: Sequence[str]) -> str:
    """``('a', 'b')`` -> ``"'a', 'b'"``, for naming what was found on disk."""
    return ", ".join(repr(name) for name in names)


def _claim_wire(claim: Any, verdict: ClaimVerdict | None) -> dict[str, Any]:
    """One claim plus its audit verdict.

    ``grounded`` is ``False`` when no verdict was produced. A claim whose
    provenance could not be established is not a grounded claim, and the panel
    must not render a missing verdict as a pass.
    """
    return {
        "claimId": claim.claim_id,
        "statement": claim.statement,
        "citations": list(claim.citations),
        "values": [{"name": name, "value": value} for name, value in claim.values],
        "grounded": bool(verdict.grounded) if verdict is not None else False,
        "reasons": list(verdict.reasons) if verdict is not None else ["no audit verdict produced"],
        "matched": (
            [{"name": name, "sequence": seq} for name, seq in verdict.matched]
            if verdict is not None
            else []
        ),
    }


def _report_wire(report: AnalystReport) -> dict[str, Any]:
    """One analyst's report: summary, claims, and its own grounded rate."""
    title, requires = _ROSTER_META.get(report.analyst, (report.analyst, ""))
    # Pair claims to verdicts by id rather than by position: the auditor
    # returns one verdict per claim in order today, but a claim rendered
    # against the wrong verdict would put a green badge on an unproven number,
    # which is precisely the failure this panel exists to make visible.
    verdicts = {v.claim_id: v for v in report.groundedness.verdicts}
    claims = [_claim_wire(c, verdicts.get(c.claim_id)) for c in report.claims]
    grounded = sum(1 for c in claims if c["grounded"])
    return {
        "name": report.analyst,
        "title": title,
        "requires": requires,
        "summary": report.text,
        "claims": claims,
        "claimsTotal": len(claims),
        "claimsGrounded": grounded,
        # ``None``, not 1.0, when an analyst made no numeric claims — the same
        # reasoning as ``unavailable_analysts_wire``: nothing was audited, and
        # both 0.0 and 1.0 read as the result of an audit. 1.0 was worse than
        # merely wrong here, because ``claimsGrounded === claimsTotal`` is
        # trivially true at 0 and drew a green "all grounded" badge over prose
        # that had cited nothing at all.
        "groundedRate": (grounded / len(claims)) if claims else None,
        "auditSummary": report.groundedness.summary(),
    }


def _failure_wire(name: str, error: str) -> dict[str, Any]:
    title, requires = _ROSTER_META.get(name, (name, ""))
    return {"name": name, "title": title, "requires": requires, "reason": error}


def unavailable_analysts_wire(reason: str) -> dict[str, Any]:
    """Wire payload declaring the analyst panel unavailable, every key present.

    The key must be *present* and explicitly empty. The frontend merges only
    the keys it receives, so omitting ``analysts`` on a failure leaves the last
    good reports on screen — stale analyst prose beside a live spot, asserting
    numbers from a journal nobody can now read. That is the fail-open mode this
    contract exists to prevent (ADR-008).

    ``groundedRate`` is ``None`` rather than 0.0 or 1.0: no claims were
    audited, and both numbers would be read as a measurement.
    """
    return {
        "hasJournal": False,
        "reason": reason,
        "runId": "",
        "eventsSeen": 0,
        # Unknown, not diagnosed: the failure that produced this payload may be
        # the very thing that stopped the directory being readable.
        "runIdMismatch": False,
        "availableRunIds": [],
        "groundedRate": None,
        "claimsTotal": 0,
        "claimsGrounded": 0,
        "computedAt": 0.0,
        "analysts": [],
        "failures": [],
        "excluded": [dict(e) for e in _EXCLUDED],
        "rosterSize": len(_ROSTER_META),
        "warnings": [reason],
    }


@dataclass(frozen=True)
class AnalystPayload:
    """The analyst panel's data plus the state that explains it."""

    has_journal: bool
    run_id: str
    events_seen: int
    reason: str | None = None
    grounded_rate: float | None = None
    claims_total: int = 0
    claims_grounded: int = 0
    analysts: tuple[dict[str, Any], ...] = ()
    failures: tuple[dict[str, Any], ...] = ()
    #: True when the configured run id has no journal but others exist here.
    #: Carried as its own flag rather than left implicit in ``reason``: a
    #: misconfiguration and an idle desk are different states, and only one of
    #: them is fixed by waiting.
    run_id_mismatch: bool = False
    available_run_ids: tuple[str, ...] = field(default_factory=tuple)
    computed_at: float = 0.0
    warnings: tuple[str, ...] = ()

    def to_wire_dict(self) -> dict[str, Any]:
        """camelCase keys the frontend reads; see live_analytics.to_wire_dict."""
        return {
            "hasJournal": self.has_journal,
            "reason": self.reason,
            "runId": self.run_id,
            "eventsSeen": self.events_seen,
            "runIdMismatch": self.run_id_mismatch,
            "availableRunIds": list(self.available_run_ids),
            "groundedRate": self.grounded_rate,
            "claimsTotal": self.claims_total,
            "claimsGrounded": self.claims_grounded,
            "computedAt": self.computed_at,
            "analysts": [dict(a) for a in self.analysts],
            "failures": [dict(f) for f in self.failures],
            "excluded": [dict(e) for e in _EXCLUDED],
            "rosterSize": len(_ROSTER_META),
            "warnings": list(self.warnings),
        }


class AnalystService:
    """Runs the deterministic analysts over the desk journal, with caching.

    ``build()`` is safe to call on every dashboard tick: it returns the cached
    payload unless the journal file has changed *or* the refresh interval has
    elapsed. Use :meth:`build_async` from async code — replaying the journal
    and re-auditing every claim is proportional to the journal's length, which
    grows for the life of the desk.

    **On replay count.** One report costs several full replays, not one: each
    analyst's ``_latest_event`` scans the journal, each self-audit scans it
    again, the orchestrator's overall audit scans it once more, and this class
    adds one as a corruption gate (see :meth:`_build_uncached`). Only the last
    is ours to remove, and it is load-bearing. Collapsing the rest means
    changing ``optitrade.desk.analysts`` and
    ``optitrade.agents.orchestrator`` to accept materialised events, which is
    rebuilding the core rather than wiring it — and the cache is what makes
    the cost bounded in the meantime: the work happens once per journal
    append, not once per dashboard tick.

    Read-only: this service never appends to the journal.
    """

    def __init__(
        self,
        journal_dir: Path,
        config: AnalystServiceConfig | None = None,
        *,
        clock: Any = time.monotonic,
    ) -> None:
        self._journal_dir = Path(journal_dir)
        self._config = config if config is not None else AnalystServiceConfig()
        self._clock = clock
        self._cached: AnalystPayload | None = None
        self._cached_key: tuple[Any, ...] | None = None
        self._cached_at: float = float("-inf")
        # Async lock guards task creation; the threading lock guards the cache
        # itself, which is written from worker threads that an asyncio lock
        # cannot see. Mirrors HistoryAnalytics deliberately: the cancellation
        # race this shape avoids was a real bug there.
        self._lock = asyncio.Lock()
        self._cache_lock = threading.Lock()
        self._inflight: asyncio.Task[AnalystPayload] | None = None

    @property
    def config(self) -> AnalystServiceConfig:
        return self._config

    def _orchestrator(self) -> AnalystOrchestrator:
        """The deterministic roster only.

        ``llm`` is left empty: the LLM analysts need the ``[agentic]`` extra
        and would report an unavailable backend as an analyst failure, which
        is noise rather than information.
        """
        cfg = self._config
        return AnalystOrchestrator(
            deterministic=(
                RegimeAnalyst(
                    high_vrp=cfg.high_vrp,
                    steep_term=cfg.steep_term,
                    deep_skew=cfg.deep_skew,
                ),
                SurfaceAuditor(rmse_threshold_vol_points=cfg.rmse_threshold_vol_points),
                PostMortemAnalyst(min_explained_fraction=cfg.min_explained_fraction),
            )
        )

    # -- cache ------------------------------------------------------------

    def _journal_path(self) -> Path:
        return self._journal_dir / f"{self._config.journal_run_id}.jsonl"

    def _other_run_ids(self) -> tuple[str, ...]:
        """Run ids with a journal in this directory other than the configured one.

        The difference between "the desk has not run" and "the desk has run
        under a name this panel is not reading" is invisible in the payload
        without this: both leave the configured journal absent, and the first
        message prescribes running a desk cycle, which for the second is
        advice that can never work.
        """
        try:
            return tuple(
                sorted(
                    path.stem
                    for path in self._journal_dir.glob("*.jsonl")
                    if path.stem != self._config.journal_run_id
                )
            )
        except OSError:
            return ()

    def _journal_key(self) -> tuple[Any, ...]:
        """Cheap fingerprint of the journal file: appends change size and mtime.

        Stat rather than a replay: the replay is the work being cached, so
        reading every event merely to decide whether to read every event would
        defeat the point.
        """
        path = self._journal_path()
        try:
            stat = path.stat()
        except OSError:
            return (str(path), False, 0, 0)
        return (str(path), True, stat.st_size, stat.st_mtime_ns)

    def _cache_is_fresh(self, key: tuple[Any, ...]) -> bool:
        if self._cached is None or self._cached_key != key:
            return False
        return (self._clock() - self._cached_at) < self._config.refresh_seconds

    def build(self) -> AnalystPayload:
        """Cached analyst payload; recomputes when stale. Blocking.

        Never raises. The orchestrator absorbs per-analyst failures, but
        everything around it can still fail — a corrupt journal line, an
        unreadable directory, a config error — and an escaped exception would
        strip the ``analysts`` key from the broadcast and leave the previous
        reports on screen. The catch-all is the fail-closed boundary
        (ADR-008), not defensive padding.
        """
        with self._cache_lock:
            key = self._journal_key()
            if self._cache_is_fresh(key):
                assert self._cached is not None  # guaranteed by _cache_is_fresh
                return self._cached
            try:
                payload = self._build_uncached()
            except Exception as exc:
                logger.exception(
                    "Analyst report build failed for run %s", self._config.journal_run_id
                )
                payload = self._unavailable(f"the analyst report failed: {type(exc).__name__}")
            # Published under the lock: the three fields are one logical value,
            # and an interleaved writer could otherwise pin a stale payload
            # under a fresh timestamp for the whole refresh interval.
            self._cached = payload
            self._cached_key = key
            self._cached_at = self._clock()
            return payload

    def _unavailable(self, reason: str) -> AnalystPayload:
        return AnalystPayload(
            has_journal=False,
            run_id=self._config.journal_run_id,
            events_seen=0,
            reason=reason,
            grounded_rate=None,
            computed_at=time.time(),
            warnings=(reason,),
        )

    async def build_async(self) -> AnalystPayload:
        """:meth:`build` off the event loop, with concurrent callers coalesced.

        Callers share one shielded in-flight task rather than each awaiting
        their own ``to_thread``. Cancellation is why: ``asyncio.to_thread``
        cancels the *future*, not the thread behind it, so a dashboard client
        that disconnects mid-build would release the async lock while its
        worker still runs, and the next caller would start a second full
        replay. Shielding a shared task means a disconnect abandons the
        result, not the work.
        """
        async with self._lock:
            if self._inflight is None or self._inflight.done():
                self._inflight = asyncio.create_task(asyncio.to_thread(self.build))
            task = self._inflight
        return await asyncio.shield(task)

    # -- build ------------------------------------------------------------

    def _build_uncached(self) -> AnalystPayload:
        cfg = self._config
        path = self._journal_path()
        others = self._other_run_ids()
        if not path.exists():
            # Two different facts share this branch, and telling a user the
            # wrong one is worse than saying nothing: "run a desk cycle" is
            # correct advice for an empty desk and unfollowable advice for a
            # run-id mismatch, because every new cycle writes to the file this
            # config does not read. The panel exists to make unfounded prose
            # visible, so it must not emit any.
            if others:
                return AnalystPayload(
                    has_journal=False,
                    run_id=cfg.journal_run_id,
                    events_seen=0,
                    reason=(
                        f"No journal named {cfg.journal_run_id!r} exists, but this directory "
                        f"holds one for {_quoted(others)}. The desk has journaled under a "
                        f"different run id than this panel is configured to read, so running "
                        f"more desk cycles will not fill this panel. Set "
                        f"DESK_JOURNAL_RUN_ID to the id the desk writes."
                    ),
                    grounded_rate=None,
                    run_id_mismatch=True,
                    available_run_ids=others,
                    computed_at=time.time(),
                    warnings=("The analyst panel and the desk are reading different journals.",),
                )
            return AnalystPayload(
                has_journal=False,
                run_id=cfg.journal_run_id,
                events_seen=0,
                reason=(
                    "The desk has not journaled anything yet, so there is nothing for an "
                    "analyst to report on. Run a desk cycle from the Desk tab and this "
                    "panel fills in."
                ),
                grounded_rate=None,
                computed_at=time.time(),
            )

        journal = EventLog(self._journal_dir, cfg.journal_run_id)
        # This replay is not redundant with the orchestrator's, despite looking
        # it. ``EventLog.replay`` is strict about corruption, and the
        # orchestrator is deliberately fail-OPEN — so without this gate a
        # malformed journal would surface as "three analysts failed" with
        # ``hasJournal: true``, rather than as an unreadable journal. Doing it
        # here keeps the fail-closed boundary (ADR-008) in front of the
        # fail-open layer. See the note in the class docstring on replay cost.
        events_seen = sum(1 for _ in journal.replay())
        if events_seen == 0:
            empty_reason = (
                "The desk journal exists but holds no events yet, so no analyst has a "
                "fact to cite. Run a desk cycle and this panel fills in."
            )
            if others:
                empty_reason = (
                    f"The journal for run id {cfg.journal_run_id!r} exists but is empty, "
                    f"while this directory also holds one for {_quoted(others)}. If the "
                    f"desk is journaling under one of those, this panel is reading the "
                    f"wrong file and more cycles will not fill it."
                )
            return AnalystPayload(
                has_journal=False,
                run_id=cfg.journal_run_id,
                events_seen=0,
                reason=empty_reason,
                grounded_rate=None,
                run_id_mismatch=bool(others),
                available_run_ids=others,
                computed_at=time.time(),
                warnings=(
                    ("The analyst panel may be reading a different journal than the desk.",)
                    if others
                    else ()
                ),
            )

        report: OrchestratorReport = self._orchestrator().run_all(journal)
        analysts = tuple(_report_wire(r) for r in report.deterministic_reports)
        failures = tuple(_failure_wire(f.analyst_name, f.error) for f in report.failures)

        claims_total = sum(int(a["claimsTotal"]) for a in analysts)
        claims_grounded = sum(int(a["claimsGrounded"]) for a in analysts)

        warnings: list[str] = []
        if failures:
            # Not a defect: each analyst reads one event type, and a desk that
            # has run one cycle legitimately has nothing for most of them.
            warnings.append(
                f"{len(failures)} of {len(_ROSTER_META)} analysts had no journaled facts to "
                "report on; each is listed with the event type it needs."
            )
        if claims_total and claims_grounded < claims_total:
            warnings.append(
                f"{claims_total - claims_grounded} of {claims_total} claims did not ground "
                "against the journal; treat their numbers as unverified."
            )
        if not analysts:
            warnings.append(
                "No analyst could report on this journal, so the panel shows only what "
                "each of them was missing."
            )

        return AnalystPayload(
            has_journal=True,
            run_id=cfg.journal_run_id,
            events_seen=events_seen,
            reason=None,
            # The orchestrator's own figure, computed by re-auditing the union
            # of claims against the journal, rather than a mean of per-analyst
            # rates: analysts contribute different claim counts, so averaging
            # the rates would weight a one-claim analyst like a six-claim one.
            grounded_rate=report.grounded_rate_overall if claims_total else None,
            claims_total=claims_total,
            claims_grounded=claims_grounded,
            analysts=analysts,
            failures=failures,
            computed_at=time.time(),
            warnings=tuple(warnings),
        )


__all__ = [
    "AnalystPayload",
    "AnalystService",
    "AnalystServiceConfig",
    "analyst_config_from_settings",
    "unavailable_analysts_wire",
]

"""Daily desk report: a journal-grounded markdown artifact (ADR-015/018).

The report is assembled only from journaled engine facts. The desk summary
quotes the latest ``daily_cycle`` event verbatim; every analyst section is a
self-audited :class:`~optitrade.desk.analysts.AnalystReport` whose claims cite
journal sequences and are re-checked by the groundedness auditor before the
report is returned. An analyst whose source event is missing is skipped and
*listed* under coverage — a report must say what it could not cover, never
leave a silent gap.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from optitrade.desk.analysts import (
    AnalystReport,
    PostMortemAnalyst,
    RegimeAnalyst,
    SurfaceAuditor,
)
from optitrade.journal.event_log import EventLog
from optitrade.journal.events import Event


class _Analyst(Protocol):
    """Structural contract for report-contributing analysts."""

    @property
    def name(self) -> str: ...

    def report(self, journal: EventLog) -> AnalystReport: ...


@dataclass(frozen=True)
class DailyReport:
    """The daily desk artifact plus its provenance.

    ``grounded_rate_overall`` is the grounded fraction over every claim made
    by every included analyst (1.0 when no claims were made — nothing
    failed); ``path`` is set only when the markdown was written to disk.
    """

    markdown: str
    analyst_reports: tuple[AnalystReport, ...]
    grounded_rate_overall: float
    path: Path | None


def _latest(events: list[Event], event_type: str) -> Event | None:
    latest: Event | None = None
    for event in events:
        if event.event_type == event_type:
            latest = event
    return latest


def _desk_summary_lines(cycle_event: Event) -> list[str]:
    data = cycle_event.data
    seq = cycle_event.sequence
    hedge = data["hedge_action"]
    return [
        "## Desk summary",
        "",
        f"- action: {data['action']} — {data['action_taken']}",
        f"- fills: {len(data['fills'])}, rejected: {len(data['rejected'])}",
        (
            f"- book greeks: delta {float(data['book_delta']):+.4f}, "
            f"gamma {float(data['book_gamma']):+.4f}, vega {float(data['book_vega']):+.4f}, "
            f"theta {float(data['book_theta']):+.4f}"
        ),
        (
            f"- cash {float(data['cash']):,.2f}, equity {float(data['equity']):,.2f}, "
            f"drawdown {float(data['drawdown']):.2%}"
        ),
        f"- hedge: {hedge if hedge is not None else 'none (halted or skipped)'}",
        f"- halted: {data['halted']}",
        "",
        f"All desk-summary numbers quote journal seq {seq} verbatim.",
    ]


def build_daily_report(
    journal: EventLog,
    *,
    out_dir: Path | None = None,
    surface_auditor: SurfaceAuditor | None = None,
    regime_analyst: RegimeAnalyst | None = None,
    post_mortem: PostMortemAnalyst | None = None,
) -> DailyReport:
    """Assemble the daily markdown report from the run journal.

    Analysts default to their reference configurations when not injected.
    Each analyst runs only when its source event type exists in the journal;
    skipped analysts and their missing event types are listed in the coverage
    section. A journal with no ``daily_cycle`` event has no desk day to
    report on and raises :class:`ValueError`.

    When ``out_dir`` is given the markdown is written to
    ``{run_id}-report.md`` and a ``daily_report`` event is journaled with the
    path, overall grounded rate and analyst coverage.
    """
    events = list(journal.replay())
    present = {event.event_type for event in events}
    cycle_event = _latest(events, "daily_cycle")
    if cycle_event is None:
        raise ValueError(
            f"journal '{journal.path.name}' contains no 'daily_cycle' event; "
            "there is no desk day to report on"
        )

    roster: tuple[tuple[str, _Analyst, str], ...] = (
        ("Regime analyst", regime_analyst or RegimeAnalyst(), "market_features"),
        ("Surface auditor", surface_auditor or SurfaceAuditor(), "surface_fit"),
        ("Post-mortem analyst", post_mortem or PostMortemAnalyst(), "pnl_explain"),
    )

    lines: list[str] = [
        f"# Daily desk report — run {journal.run_id}",
        "",
        (
            f"Latest daily cycle: journal seq {cycle_event.sequence}, market date_ts "
            f"{float(cycle_event.data['date_ts']):g}, journaled at "
            f"{cycle_event.timestamp:.0f}."
        ),
        "",
        *_desk_summary_lines(cycle_event),
    ]

    included: list[AnalystReport] = []
    included_names: list[str] = []
    skipped: list[tuple[str, str]] = []
    grounded_claims = 0
    total_claims = 0
    for title, analyst, event_type in roster:
        if event_type not in present:
            skipped.append((analyst.name, event_type))
            continue
        analyst_report = analyst.report(journal)
        verdicts = analyst_report.groundedness.verdicts
        grounded = sum(1 for v in verdicts if v.grounded)
        grounded_claims += grounded
        total_claims += len(verdicts)
        included.append(analyst_report)
        included_names.append(analyst_report.analyst)
        lines += [
            "",
            f"## {title}",
            "",
            analyst_report.text,
            "",
            f"groundedness: {grounded}/{len(verdicts)} claims",
        ]

    grounded_rate_overall = grounded_claims / total_claims if total_claims else 1.0

    lines += ["", "## Coverage", ""]
    if skipped:
        lines.append("Skipped analysts (missing source events):")
        lines.append("")
        lines += [
            f"- {name}: no '{event_type}' event in the journal" for name, event_type in skipped
        ]
    else:
        lines.append("All analysts reported; no coverage gaps.")
    lines += [
        "",
        (
            f"Overall grounded rate: {grounded_rate_overall:.1%} "
            f"({grounded_claims}/{total_claims} claims)."
        ),
    ]
    markdown = "\n".join(lines) + "\n"

    path: Path | None = None
    if out_dir is not None:
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / f"{journal.run_id}-report.md"
        path.write_text(markdown, encoding="utf-8")
        journal.append(
            "daily_report",
            {
                "path": str(path),
                "grounded_rate_overall": grounded_rate_overall,
                "analysts_included": included_names,
                "analysts_skipped": [name for name, _ in skipped],
            },
        )

    return DailyReport(
        markdown=markdown,
        analyst_reports=tuple(included),
        grounded_rate_overall=grounded_rate_overall,
        path=path,
    )


__all__ = ["DailyReport", "build_daily_report"]

"""Deterministic analyst agents: journal-cited, self-audited reports (ADR-015).

Analysts observe and explain; they never touch order flow. Each ``report``
reads engine facts from the run journal, writes a few plain-English
sentences carrying the actual numbers, attaches machine-checkable
:class:`~optitrade.audit.groundedness.AgentClaim` records citing the journal
sequences those numbers came from, and audits itself with the
:class:`~optitrade.audit.groundedness.GroundednessAuditor` before returning.
A report whose own claims do not ground at 100% indicates a bug in the
analyst, not a rhetorical shortfall — the enforcing tests assert
``grounded_rate == 1.0``.

No LLM is involved anywhere: these are the deterministic reference
implementations of the observe/explain layer, so the groundedness contract
is testable before any stochastic agent sits on the desk.
"""

from __future__ import annotations

from dataclasses import dataclass

from optitrade.audit.groundedness import AgentClaim, GroundednessAuditor, GroundednessReport
from optitrade.journal.event_log import EventLog
from optitrade.journal.events import Event


@dataclass(frozen=True)
class AnalystReport:
    """A self-audited analyst statement over journaled engine facts."""

    analyst: str
    text: str
    claims: tuple[AgentClaim, ...]
    groundedness: GroundednessReport


def _latest_event(journal: EventLog, event_type: str) -> Event:
    """Most recent event of ``event_type``; loud failure when absent."""
    latest: Event | None = None
    for event in journal.replay():
        if event.event_type == event_type:
            latest = event
    if latest is None:
        raise ValueError(
            f"journal '{journal.path.name}' contains no '{event_type}' event; "
            "the analyst has nothing to report on"
        )
    return latest


def _self_audit(journal: EventLog, claims: tuple[AgentClaim, ...]) -> GroundednessReport:
    return GroundednessAuditor(journal).audit(claims)


class SurfaceAuditor:
    """Reads the latest ``surface_fit`` event and passes judgement on the fit.

    Flags an eSSVI RMSE above ``rmse_threshold_vol_points`` and any non-zero
    arbitrage-violation count (Durrleman, density gate, spline/SABR static
    checks); comments on the quote count the fit consumed.
    """

    def __init__(self, rmse_threshold_vol_points: float = 0.5) -> None:
        self._rmse_threshold = rmse_threshold_vol_points

    @property
    def name(self) -> str:
        return "surface_auditor"

    def report(self, journal: EventLog) -> AnalystReport:
        event = _latest_event(journal, "surface_fit")
        data = event.data
        seq = event.sequence
        quotes = int(data["quotes"])
        essvi_rmse = float(data["essvi_rmse_vol_points"])
        sabr_rmse = float(data["worst_rmse_vol_points"])
        durrleman = int(data["durrleman_violations"])
        density = int(data["density_violations"])
        spline_arb = int(data["spline_arb_violations"])
        sabr_arb = int(data["sabr_arb_violations"])

        sentences = [
            f"Surface fit (journal seq {seq}) calibrated on {quotes} quotes: eSSVI joint-fit "
            f"RMSE {essvi_rmse:.4f} vol-pt against the per-expiry SABR benchmark "
            f"{sabr_rmse:.4f} vol-pt."
        ]
        if essvi_rmse > self._rmse_threshold:
            sentences.append(
                f"FLAG: eSSVI RMSE {essvi_rmse:.4f} vol-pt exceeds the "
                f"{self._rmse_threshold:.4f} vol-pt threshold; do not quote from this surface "
                "until it is refit."
            )
        else:
            sentences.append(
                f"Fit quality is within the {self._rmse_threshold:.4f} vol-pt RMSE threshold."
            )
        if durrleman + density + spline_arb + sabr_arb > 0:
            sentences.append(
                f"FLAG: arbitrage checks report violations — Durrleman {durrleman}, "
                f"density gate {density}, spline checker {spline_arb}, SABR checker {sabr_arb}."
            )
        else:
            sentences.append(
                "All arbitrage gates are clean: 0 Durrleman, 0 density and 0 static-arbitrage "
                "violations."
            )

        claims = (
            AgentClaim(
                claim_id="surface_rmse",
                statement=(
                    f"eSSVI joint-fit RMSE is {essvi_rmse:g} vol-pt vs SABR benchmark "
                    f"{sabr_rmse:g} vol-pt (seq {seq})"
                ),
                citations=(seq,),
                values=(
                    ("essvi_rmse_vol_points", essvi_rmse),
                    ("worst_rmse_vol_points", sabr_rmse),
                ),
            ),
            AgentClaim(
                claim_id="surface_quotes",
                statement=f"the fit consumed {quotes} quotes (seq {seq})",
                citations=(seq,),
                values=(("quotes", float(quotes)),),
            ),
            AgentClaim(
                claim_id="surface_arbitrage",
                statement=(
                    f"violation counts: durrleman {durrleman}, density {density}, "
                    f"spline {spline_arb}, sabr {sabr_arb} (seq {seq})"
                ),
                citations=(seq,),
                values=(
                    ("durrleman_violations", float(durrleman)),
                    ("density_violations", float(density)),
                    ("spline_arb_violations", float(spline_arb)),
                    ("sabr_arb_violations", float(sabr_arb)),
                ),
            ),
        )
        return AnalystReport(
            analyst=self.name,
            text=" ".join(sentences),
            claims=claims,
            groundedness=_self_audit(journal, claims),
        )


class PostMortemAnalyst:
    """Reads the latest ``pnl_explain`` event and narrates the decomposition.

    The event shape is :meth:`optitrade.explain.pnl_explain.PnLExplain.to_event_data`.
    Flags an ``explained_fraction`` below ``min_explained_fraction``.
    """

    def __init__(self, min_explained_fraction: float = 0.9) -> None:
        self._min_explained = min_explained_fraction

    @property
    def name(self) -> str:
        return "post_mortem_analyst"

    def report(self, journal: EventLog) -> AnalystReport:
        event = _latest_event(journal, "pnl_explain")
        data = event.data
        seq = event.sequence
        theta_carry = float(data["theta_carry"])
        delta_pnl = float(data["delta_pnl"])
        gamma_vs_rv = float(data["gamma_vs_rv"])
        vega_from_factors = {str(k): float(v) for k, v in dict(data["vega_from_factors"]).items()}
        vega_residual_move = float(data["vega_residual_move"])
        vanna_volga = float(data["vanna_volga"])
        residual = float(data["residual"])
        total = float(data["total"])
        explained_fraction = float(data["explained_fraction"])

        vega_bits = ", ".join(
            f"{name} {value:+.2f}" for name, value in sorted(vega_from_factors.items())
        )
        sentences = [
            f"P&L explain (journal seq {seq}): total {total:+.2f} decomposes into theta carry "
            f"{theta_carry:+.2f}, delta {delta_pnl:+.2f}, gamma-vs-realized-variance "
            f"{gamma_vs_rv:+.2f}, vega factors ({vega_bits or 'none'}), vega residual move "
            f"{vega_residual_move:+.2f} and vanna/volga {vanna_volga:+.2f}, leaving a residual "
            f"of {residual:+.2f}.",
            f"The decomposition explains {explained_fraction:.1%} of the day's P&L.",
        ]
        if explained_fraction < self._min_explained:
            sentences.append(
                f"FLAG: explained fraction {explained_fraction:.1%} is below the "
                f"{self._min_explained:.1%} floor; investigate the residual before trusting "
                "the book's risk picture."
            )
        else:
            sentences.append(
                f"That clears the {self._min_explained:.1%} floor; the residual is noise-sized."
            )

        claims = (
            AgentClaim(
                claim_id="pnl_totals",
                statement=(
                    f"total P&L is {total:g} with residual {residual:g}, explained fraction "
                    f"{explained_fraction:g} (seq {seq})"
                ),
                citations=(seq,),
                values=(
                    ("total", total),
                    ("residual", residual),
                    ("explained_fraction", explained_fraction),
                ),
            ),
            AgentClaim(
                claim_id="pnl_buckets",
                statement=(
                    f"theta carry {theta_carry:g}, delta {delta_pnl:g}, gamma-vs-rv "
                    f"{gamma_vs_rv:g}, vega residual move {vega_residual_move:g}, "
                    f"vanna/volga {vanna_volga:g} (seq {seq})"
                ),
                citations=(seq,),
                values=(
                    ("theta_carry", theta_carry),
                    ("delta_pnl", delta_pnl),
                    ("gamma_vs_rv", gamma_vs_rv),
                    ("vega_residual_move", vega_residual_move),
                    ("vanna_volga", vanna_volga),
                ),
            ),
        )
        return AnalystReport(
            analyst=self.name,
            text=" ".join(sentences),
            claims=claims,
            groundedness=_self_audit(journal, claims),
        )


__all__ = ["AnalystReport", "PostMortemAnalyst", "SurfaceAuditor"]

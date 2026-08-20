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

from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np

from optitrade.audit.groundedness import AgentClaim, GroundednessAuditor, GroundednessReport
from optitrade.greeks.scenario import BookPosition, ScenarioGrid, run_scenario_grid
from optitrade.journal.event_log import EventLog
from optitrade.journal.events import Event

# Calendar-day convention for scenario time shifts (ACT/365), matching
# optitrade.greeks.scenario.
_DAYS_PER_YEAR = 365.0


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


class RegimeAnalyst:
    """Reads the latest ``market_features`` event and narrates the vol regime.

    The event is written by :func:`~optitrade.desk.cycle.run_daily_cycle`
    before the strategy decision and carries ``ts``, ``spot``,
    ``realized_vol`` plus whatever derived features the market producer
    populated (``atm_iv``, ``vrp``, ``term_slope``, ``skew_25d``, ...).
    Features absent from the event are listed as not covered — no claim is
    made without an engine number to cite.

    Thresholds (plain config, all in decimal vol units):

    - ``high_vrp``: a variance risk premium above this is flagged as a rich
      premium regime for vol sellers.
    - ``steep_term``: a term slope whose magnitude exceeds this is flagged as
      a steep (or deeply inverted) term structure.
    - ``deep_skew``: a 25-delta skew whose magnitude exceeds this is flagged
      as pronounced skew.
    """

    def __init__(
        self,
        high_vrp: float = 0.04,
        steep_term: float = 0.05,
        deep_skew: float = 0.03,
    ) -> None:
        self._high_vrp = high_vrp
        self._steep_term = steep_term
        self._deep_skew = deep_skew

    @property
    def name(self) -> str:
        return "regime_analyst"

    def report(self, journal: EventLog) -> AnalystReport:
        event = _latest_event(journal, "market_features")
        data = event.data
        seq = event.sequence
        spot = float(data["spot"])
        realized_vol = float(data["realized_vol"])

        sentences = [
            f"Market regime (journal seq {seq}): spot {spot:.2f} with realized vol "
            f"{realized_vol:.4f}."
        ]
        claims: list[AgentClaim] = [
            AgentClaim(
                claim_id="regime_market",
                statement=f"spot is {spot:g} and realized vol is {realized_vol:g} (seq {seq})",
                citations=(seq,),
                values=(("spot", spot), ("realized_vol", realized_vol)),
            )
        ]
        missing: list[str] = []

        if data.get("atm_iv") is None:
            missing.append("atm_iv")
        else:
            atm_iv = float(data["atm_iv"])
            if atm_iv > realized_vol:
                relation = "above"
            elif atm_iv < realized_vol:
                relation = "below"
            else:
                relation = "level with"
            sentences.append(
                f"Implied vol {atm_iv:.4f} trades {relation} realized {realized_vol:.4f}."
            )
            claims.append(
                AgentClaim(
                    claim_id="regime_vol_level",
                    statement=(
                        f"ATM implied vol is {atm_iv:g} vs realized {realized_vol:g} (seq {seq})"
                    ),
                    citations=(seq,),
                    values=(("atm_iv", atm_iv), ("realized_vol", realized_vol)),
                )
            )

        if data.get("vrp") is None:
            missing.append("vrp")
        else:
            vrp = float(data["vrp"])
            if vrp >= 0:
                side = "positive (implied over realized)"
            else:
                side = "negative (implied under realized)"
            sentences.append(f"The variance risk premium is {side} at {vrp:+.4f}.")
            if vrp > self._high_vrp:
                sentences.append(
                    f"FLAG: VRP {vrp:+.4f} exceeds the {self._high_vrp:.4f} high-VRP threshold — "
                    "a rich premium regime for vol sellers."
                )
            claims.append(
                AgentClaim(
                    claim_id="regime_vrp",
                    statement=f"the variance risk premium is {vrp:g} (seq {seq})",
                    citations=(seq,),
                    values=(("vrp", vrp),),
                )
            )

        if data.get("term_slope") is None:
            missing.append("term_slope")
        else:
            term_slope = float(data["term_slope"])
            if term_slope > 0:
                direction = "upward-sloping"
            elif term_slope < 0:
                direction = "inverted"
            else:
                direction = "flat"
            sentences.append(
                f"The term structure is {direction} at {term_slope:+.4f} between tenors."
            )
            if abs(term_slope) > self._steep_term:
                sentences.append(
                    f"FLAG: term slope {term_slope:+.4f} exceeds the {self._steep_term:.4f} "
                    "steep-term threshold in magnitude."
                )
            claims.append(
                AgentClaim(
                    claim_id="regime_term_slope",
                    statement=f"the term slope is {term_slope:g} (seq {seq})",
                    citations=(seq,),
                    values=(("term_slope", term_slope),),
                )
            )

        if data.get("skew_25d") is None:
            missing.append("skew_25d")
        else:
            skew = float(data["skew_25d"])
            if skew > 0:
                shape = "puts over calls"
            elif skew < 0:
                shape = "calls over puts"
            else:
                shape = "symmetric"
            sentences.append(f"25-delta skew is {skew:+.4f} ({shape}).")
            if abs(skew) > self._deep_skew:
                sentences.append(
                    f"FLAG: skew {skew:+.4f} exceeds the {self._deep_skew:.4f} deep-skew "
                    "threshold in magnitude."
                )
            claims.append(
                AgentClaim(
                    claim_id="regime_skew",
                    statement=f"the 25-delta skew is {skew:g} (seq {seq})",
                    citations=(seq,),
                    values=(("skew_25d", skew),),
                )
            )

        if missing:
            sentences.append(
                "Not journaled this cycle (no claim without an engine number): "
                + ", ".join(missing)
                + "."
            )

        frozen_claims = tuple(claims)
        return AnalystReport(
            analyst=self.name,
            text=" ".join(sentences),
            claims=frozen_claims,
            groundedness=_self_audit(journal, frozen_claims),
        )


@dataclass(frozen=True)
class ScenarioQuery:
    """A structured what-if against the book, in engine units.

    ``spot_shift`` is relative (-0.05 == spot down 5%), ``vol_shift`` is an
    absolute vol move as a decimal (0.02 == +2 vol-pt), ``time_shift_days``
    is calendar days forward (ACT/365). ``label`` names the scenario in the
    journal and the report text.
    """

    spot_shift: float
    vol_shift: float
    time_shift_days: float = 0.0
    label: str = ""


class RiskOfficerAnalyst:
    """Answers structured scenario queries against the live book.

    Deliberately takes no natural language: the query surface is the typed
    :class:`ScenarioQuery` dataclass, so the compute path stays deterministic
    and testable. An optional LLM adapter can translate desk-speak ("what if
    we gap down 5%?") into a :class:`ScenarioQuery` later without touching
    this class.

    Mirrors the MCP tool-call pattern (compute -> journal -> cite): ``answer``
    first revalues the book with :func:`run_scenario_grid` at exactly the
    queried shifts, appends a ``scenario_query`` event carrying the inputs and
    the resulting P&L, and only then writes prose whose claims cite the event
    it just journaled — the report is grounded in an engine fact by
    construction.
    """

    @property
    def name(self) -> str:
        return "risk_officer_analyst"

    def answer(
        self,
        query: ScenarioQuery,
        book: Sequence[BookPosition],
        spot: float,
        rate: float,
        journal: EventLog,
    ) -> AnalystReport:
        grid = ScenarioGrid(
            spot_shifts=np.array([query.spot_shift], dtype=np.float64),
            vol_shifts=np.array([query.vol_shift], dtype=np.float64),
            time_shifts=np.array([query.time_shift_days / _DAYS_PER_YEAR], dtype=np.float64),
        )
        result = run_scenario_grid(book, spot, rate, grid)
        pnl = float(result.pnl[0, 0, 0])
        base_value = float(result.base_value)
        event = journal.append(
            "scenario_query",
            {
                "label": query.label,
                "spot_shift": query.spot_shift,
                "vol_shift": query.vol_shift,
                "time_shift_days": query.time_shift_days,
                "pnl": pnl,
                "base_value": base_value,
            },
        )
        seq = event.sequence

        label_note = f" [{query.label}]" if query.label else ""
        time_note = (
            f" and {query.time_shift_days:g} calendar days pass" if query.time_shift_days else ""
        )
        text = (
            f"Scenario{label_note} (journal seq {seq}): if spot moves {query.spot_shift:+.1%} "
            f"and vol moves {query.vol_shift * 100.0:+.1f} vol-pt{time_note}, the book P&L is "
            f"{pnl:+.2f} against a base value of {base_value:.2f} (full revaluation, not a "
            "Taylor expansion)."
        )
        claims = (
            AgentClaim(
                claim_id="scenario_pnl",
                statement=f"scenario P&L is {pnl:g} on base value {base_value:g} (seq {seq})",
                citations=(seq,),
                values=(("pnl", pnl), ("base_value", base_value)),
            ),
            AgentClaim(
                claim_id="scenario_shifts",
                statement=(
                    f"the query shifted spot {query.spot_shift:g}, vol {query.vol_shift:g} and "
                    f"time {query.time_shift_days:g} days (seq {seq})"
                ),
                citations=(seq,),
                values=(
                    ("spot_shift", query.spot_shift),
                    ("vol_shift", query.vol_shift),
                    ("time_shift_days", query.time_shift_days),
                ),
            ),
        )
        return AnalystReport(
            analyst=self.name,
            text=text,
            claims=claims,
            groundedness=_self_audit(journal, claims),
        )


__all__ = [
    "AnalystReport",
    "PostMortemAnalyst",
    "RegimeAnalyst",
    "RiskOfficerAnalyst",
    "ScenarioQuery",
    "SurfaceAuditor",
]

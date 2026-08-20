"""LLM-backed analyst agents following the AnalystReport contract (ADR-021).

Each LLM analyst mirrors a deterministic analyst from :mod:`optitrade.desk.
analysts` but replaces the template text with LLM-generated narrative. The
invariant is preserved: **claims are constructed from the journal event data
(deterministic), not from the LLM output**. The LLM provides richer,
context-aware prose; the groundedness auditor still checks the machine-built
claims against the journal. An LLM that hallucinates a number cannot
introduce an ungrounded claim because it never touches the claim pipeline.

Each analyst follows the same three-step pattern:
1. Extract the relevant journal event(s) (deterministic, identical to the
   reference analyst).
2. Build claims from the event data (deterministic, identical values).
3. Send the event data to the LLM for a natural-language narrative.
4. Return ``AnalystReport(text=llm_text, claims=deterministic_claims)``.
"""

from __future__ import annotations

from optitrade.agents.base import LLMBackend, events_to_context, latest_event
from optitrade.audit.groundedness import AgentClaim, GroundednessAuditor, GroundednessReport
from optitrade.desk.analysts import AnalystReport
from optitrade.journal.event_log import EventLog

_ANALYST_SYSTEM = (
    "You are a quantitative analyst on a derivatives trading desk. You observe "
    "and explain — you never recommend trades or modify order flow. Your "
    "analysis is grounded in the engine facts provided; cite the journal "
    "sequence numbers when referencing specific numbers. Be concise: 3-5 "
    "sentences. Write for a portfolio manager who understands options but has "
    "not seen today's data yet."
)


def _self_audit(journal: EventLog, claims: tuple[AgentClaim, ...]) -> GroundednessReport:
    return GroundednessAuditor(journal).audit(claims)


class LLMSurfaceAnalyst:
    """LLM-backed surface auditor: richer narrative, deterministic claims.

    Reads the latest ``surface_fit`` event (same as the reference
    :class:`~optitrade.desk.analysts.SurfaceAuditor`), builds identical
    claims, and sends the event data to the LLM for a professional analysis.
    """

    def __init__(self, backend: LLMBackend, rmse_threshold_vol_points: float = 0.5) -> None:
        self._backend = backend
        self._rmse_threshold = rmse_threshold_vol_points

    @property
    def name(self) -> str:
        return "llm_surface_analyst"

    def report(self, journal: EventLog) -> AnalystReport:
        event = latest_event(journal, "surface_fit")
        data = event.data
        seq = event.sequence
        quotes = int(data["quotes"])
        essvi_rmse = float(data["essvi_rmse_vol_points"])
        sabr_rmse = float(data["worst_rmse_vol_points"])
        durrleman = int(data["durrleman_violations"])
        density = int(data["density_violations"])
        spline_arb = int(data["spline_arb_violations"])
        sabr_arb = int(data["sabr_arb_violations"])

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

        prompt = (
            f"Analyze this volatility surface fit (journal seq {seq}). "
            f"RMSE threshold: {self._rmse_threshold} vol-pt. "
            f"Event data:\n{events_to_context([event])}"
        )
        response = self._backend.complete(_ANALYST_SYSTEM, prompt)
        return AnalystReport(
            analyst=self.name,
            text=response.text,
            claims=claims,
            groundedness=_self_audit(journal, claims),
        )


class LLMRegimeAnalyst:
    """LLM-backed regime analyst: richer narrative, deterministic claims.

    Reads the latest ``market_features`` event, builds claims for every
    numeric feature present, and sends the data to the LLM for a
    regime-characterisation narrative.
    """

    def __init__(
        self,
        backend: LLMBackend,
        high_vrp: float = 0.04,
        steep_term: float = 0.05,
        deep_skew: float = 0.03,
    ) -> None:
        self._backend = backend
        self._high_vrp = high_vrp
        self._steep_term = steep_term
        self._deep_skew = deep_skew

    @property
    def name(self) -> str:
        return "llm_regime_analyst"

    def report(self, journal: EventLog) -> AnalystReport:
        event = latest_event(journal, "market_features")
        data = event.data
        seq = event.sequence
        spot = float(data["spot"])
        realized_vol = float(data["realized_vol"])

        claims: list[AgentClaim] = [
            AgentClaim(
                claim_id="regime_market",
                statement=f"spot is {spot:g} and realized vol is {realized_vol:g} (seq {seq})",
                citations=(seq,),
                values=(("spot", spot), ("realized_vol", realized_vol)),
            )
        ]

        for key, claim_id in (
            ("atm_iv", "regime_vol_level"),
            ("vrp", "regime_vrp"),
            ("term_slope", "regime_term_slope"),
            ("skew_25d", "regime_skew"),
        ):
            if data.get(key) is not None:
                value = float(data[key])
                claims.append(
                    AgentClaim(
                        claim_id=claim_id,
                        statement=f"{key} is {value:g} (seq {seq})",
                        citations=(seq,),
                        values=((key, value),),
                    )
                )

        frozen_claims = tuple(claims)
        prompt = (
            f"Characterise today's volatility regime (journal seq {seq}). "
            f"Thresholds — high VRP: {self._high_vrp}, steep term: {self._steep_term}, "
            f"deep skew: {self._deep_skew}. "
            f"Event data:\n{events_to_context([event])}"
        )
        response = self._backend.complete(_ANALYST_SYSTEM, prompt)
        return AnalystReport(
            analyst=self.name,
            text=response.text,
            claims=frozen_claims,
            groundedness=_self_audit(journal, frozen_claims),
        )


class LLMPostMortemAnalyst:
    """LLM-backed post-mortem analyst: richer narrative, deterministic claims.

    Reads the latest ``pnl_explain`` event, builds claims for all P&L
    components, and sends the decomposition to the LLM for a professional
    post-mortem analysis.
    """

    def __init__(self, backend: LLMBackend, min_explained_fraction: float = 0.9) -> None:
        self._backend = backend
        self._min_explained = min_explained_fraction

    @property
    def name(self) -> str:
        return "llm_post_mortem_analyst"

    def report(self, journal: EventLog) -> AnalystReport:
        event = latest_event(journal, "pnl_explain")
        data = event.data
        seq = event.sequence
        theta_carry = float(data["theta_carry"])
        delta_pnl = float(data["delta_pnl"])
        gamma_vs_rv = float(data["gamma_vs_rv"])
        vega_residual_move = float(data["vega_residual_move"])
        vanna_volga = float(data["vanna_volga"])
        residual = float(data["residual"])
        total = float(data["total"])
        explained_fraction = float(data["explained_fraction"])

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

        prompt = (
            f"Analyze this P&L decomposition (journal seq {seq}). "
            f"Minimum explained fraction: {self._min_explained}. "
            f"Event data:\n{events_to_context([event])}"
        )
        response = self._backend.complete(_ANALYST_SYSTEM, prompt)
        return AnalystReport(
            analyst=self.name,
            text=response.text,
            claims=claims,
            groundedness=_self_audit(journal, claims),
        )


__all__ = [
    "LLMPostMortemAnalyst",
    "LLMRegimeAnalyst",
    "LLMSurfaceAnalyst",
]

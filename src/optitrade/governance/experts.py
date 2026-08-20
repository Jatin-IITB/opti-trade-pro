"""Rule-based debate experts.

Each expert examines a :class:`TradeProposal` from one professional angle
and returns an :class:`ExpertOpinion` whose assessment reads like a human
wrote it — with the actual numbers, because these strings are the audit
trail. All experts here are deterministic; the optional LLM-backed expert
lives in :mod:`optitrade.governance.dspy_adapter`.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Protocol

from optitrade.core.types import Order
from optitrade.risk.checks import (
    DrawdownCheck,
    GreeksLimitCheck,
    PreTradeCheck,
    RiskContext,
    Verdict,
    greek_utilisation,
)
from optitrade.risk.limits import RiskLimits

_EPSILON = 1e-9  # avoids division by zero for free-lunch (zero-cost) proposals


@dataclass(frozen=True, slots=True)
class TradeProposal:
    """A trade brought before the panel, with the numbers behind the thesis."""

    order: Order
    thesis: str
    expected_edge: float  # currency
    estimated_cost: float  # currency
    implied_vol: float
    realized_vol: float
    ctx: RiskContext


# (str, Enum) rather than StrEnum for parity with core.types.OptionType.
class Stance(str, Enum):  # noqa: UP042
    APPROVE = "approve"
    REJECT = "reject"
    ABSTAIN = "abstain"


@dataclass(frozen=True, slots=True)
class ExpertOpinion:
    expert_name: str
    stance: Stance
    assessment: str  # 2-3 sentences, includes the numbers
    concerns: tuple[str, ...]
    confidence: float  # 0-1


class Expert(Protocol):
    """Interface every debate expert implements."""

    @property
    def name(self) -> str: ...

    def evaluate(self, proposal: TradeProposal) -> ExpertOpinion: ...


class RiskOfficer:
    """Deterministic risk veto seat on the panel.

    Reuses the pre-trade check classes (no duplicated arithmetic): any
    would-be greeks or drawdown violation is a REJECT at confidence >= 0.9,
    which is deliberately veto-grade for the debate panel. On approval,
    confidence scales with the remaining headroom to the nearest limit.
    """

    def __init__(self, limits: RiskLimits) -> None:
        self._limits = limits
        self._checks: tuple[PreTradeCheck, ...] = (GreeksLimitCheck(), DrawdownCheck())

    @property
    def name(self) -> str:
        return "risk_officer"

    def evaluate(self, proposal: TradeProposal) -> ExpertOpinion:
        order, ctx = proposal.order, proposal.ctx
        results = [check.evaluate(order, ctx, self._limits) for check in self._checks]
        usage = greek_utilisation(order, ctx, self._limits)
        usage["drawdown"] = ctx.portfolio.drawdown / self._limits.max_drawdown
        worst_name, worst = max(usage.items(), key=lambda item: item[1])
        violations = [r for r in results if r.verdict is not Verdict.APPROVE]
        if violations:
            confidence = min(1.0, 0.9 + max(0.0, worst - 1.0))
            return ExpertOpinion(
                expert_name=self.name,
                stance=Stance.REJECT,
                assessment=(
                    f"Risk limits would be breached: worst utilisation is {worst_name} at "
                    f"{worst:.0%} of its cap. This trade cannot be booked as proposed."
                ),
                concerns=tuple(r.reason for r in violations),
                confidence=confidence,
            )
        headroom = 1.0 - min(worst, 1.0)
        concerns: tuple[str, ...] = ()
        if worst > 0.8:
            concerns = (f"{worst_name} utilisation {worst:.0%} is close to its cap",)
        return ExpertOpinion(
            expert_name=self.name,
            stance=Stance.APPROVE,
            assessment=(
                f"All greek and drawdown limits hold. Worst utilisation is {worst_name} at "
                f"{worst:.0%} of its cap, leaving {headroom:.0%} headroom."
            ),
            concerns=concerns,
            confidence=0.5 + 0.5 * headroom,
        )


class StrategyExpert:
    """Approves only when the edge clears cost and the vol view is coherent.

    A long-vol position (order vega x quantity > 0) should be backed by
    realized vol above implied; short-vol the reverse. Zero net vega carries
    no vol view and cannot mismatch.
    """

    def __init__(self, min_edge_ratio: float = 1.5) -> None:
        self._min_edge_ratio = min_edge_ratio

    @property
    def name(self) -> str:
        return "strategy_expert"

    def evaluate(self, proposal: TradeProposal) -> ExpertOpinion:
        edge, cost = proposal.expected_edge, proposal.estimated_cost
        ratio = edge / max(cost, _EPSILON)
        vega_exposure = proposal.ctx.order_greeks.vega * proposal.order.quantity
        if vega_exposure > 0:
            view, vol_consistent = "long vol", proposal.realized_vol > proposal.implied_vol
        elif vega_exposure < 0:
            view, vol_consistent = "short vol", proposal.realized_vol < proposal.implied_vol
        else:
            view, vol_consistent = "vol-neutral", True
        concerns: list[str] = []
        if ratio < self._min_edge_ratio:
            concerns.append(
                f"edge/cost ratio {ratio:.2f} is below the {self._min_edge_ratio:.2f} minimum "
                f"(edge {edge:.2f} vs cost {cost:.2f})"
            )
        if not vol_consistent:
            concerns.append(
                f"the position is {view} but realized vol {proposal.realized_vol:.2%} vs "
                f"implied {proposal.implied_vol:.2%} argues the opposite"
            )
        if concerns:
            return ExpertOpinion(
                expert_name=self.name,
                stance=Stance.REJECT,
                assessment="The thesis does not hold up: " + "; also, ".join(concerns) + ".",
                concerns=tuple(concerns),
                confidence=0.85 if not vol_consistent else 0.75,
            )
        return ExpertOpinion(
            expert_name=self.name,
            stance=Stance.APPROVE,
            assessment=(
                f"Expected edge {edge:.2f} covers cost {cost:.2f} by {ratio:.2f}x, above the "
                f"{self._min_edge_ratio:.2f}x bar. The {view} stance is consistent with realized "
                f"vol {proposal.realized_vol:.2%} against implied {proposal.implied_vol:.2%}."
            ),
            concerns=(),
            confidence=max(0.5, min(0.95, 0.5 + 0.1 * (ratio - self._min_edge_ratio))),
        )


class ExecutionExpert:
    """Judges whether transaction costs leave enough of the edge intact."""

    def __init__(self, max_cost_ratio: float = 0.5) -> None:
        self._max_cost_ratio = max_cost_ratio

    @property
    def name(self) -> str:
        return "execution_expert"

    def evaluate(self, proposal: TradeProposal) -> ExpertOpinion:
        edge, cost = proposal.expected_edge, proposal.estimated_cost
        if edge <= 0:
            return ExpertOpinion(
                expert_name=self.name,
                stance=Stance.ABSTAIN,
                assessment=(
                    f"Expected edge is {edge:.2f}, so there is nothing to execute against. "
                    "Execution quality is moot on a trade with no expected profit."
                ),
                concerns=(f"expected edge {edge:.2f} is not positive",),
                confidence=0.2,
            )
        cost_ratio = cost / edge
        if cost > self._max_cost_ratio * edge:
            return ExpertOpinion(
                expert_name=self.name,
                stance=Stance.REJECT,
                assessment=(
                    f"Estimated cost {cost:.2f} consumes {cost_ratio:.0%} of the {edge:.2f} "
                    f"edge, above the {self._max_cost_ratio:.0%} execution budget. "
                    "Slippage would eat the trade."
                ),
                concerns=(
                    f"cost {cost:.2f} exceeds {self._max_cost_ratio:.0%} of edge {edge:.2f}",
                ),
                confidence=0.85,
            )
        return ExpertOpinion(
            expert_name=self.name,
            stance=Stance.APPROVE,
            assessment=(
                f"Estimated cost {cost:.2f} is {cost_ratio:.0%} of the {edge:.2f} edge, "
                f"within the {self._max_cost_ratio:.0%} budget."
            ),
            concerns=(),
            confidence=max(0.5, min(0.95, 1.0 - cost_ratio)),
        )


__all__ = [
    "ExecutionExpert",
    "Expert",
    "ExpertOpinion",
    "RiskOfficer",
    "Stance",
    "StrategyExpert",
    "TradeProposal",
]

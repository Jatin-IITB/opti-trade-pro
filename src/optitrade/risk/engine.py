"""Fail-closed pre-trade risk engine.

Every check runs on every order — no short-circuiting — so a rejection comes
with the full report, not just the first breach. Any exception inside a
check becomes a REJECT result: an order is never let through because risk
logic errored. This is the mechanism behind "100% of out-of-bound orders
blocked": there is no code path from a breach (or a broken check) to an
APPROVE.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass, replace
from typing import Any

from optitrade.core.types import Order
from optitrade.journal.event_log import EventLog
from optitrade.risk.checks import (
    CheckResult,
    ConcentrationCheck,
    DrawdownCheck,
    GreeksLimitCheck,
    MarginSufficiencyCheck,
    PreTradeCheck,
    RiskContext,
    Verdict,
)
from optitrade.risk.limits import RiskLimits

_SEVERITY: dict[Verdict, int] = {
    Verdict.APPROVE: 0,
    Verdict.RESIZE: 1,
    Verdict.REJECT: 2,
    Verdict.HALT: 3,
}


def default_checks() -> tuple[PreTradeCheck, ...]:
    """The standard pre-trade battery, in evaluation order."""
    return (
        GreeksLimitCheck(),
        MarginSufficiencyCheck(),
        DrawdownCheck(),
        ConcentrationCheck(),
    )


@dataclass(frozen=True)
class RiskDecision:
    """Outcome of one risk review.

    ``verdict`` is the worst across all checks (HALT > REJECT > RESIZE >
    APPROVE). ``adjusted_order`` is the original order on APPROVE, a resized
    copy on RESIZE, and ``None`` otherwise.
    """

    verdict: Verdict
    adjusted_order: Order | None
    results: tuple[CheckResult, ...]
    correlation_id: str


class RiskEngine:
    """Runs the pre-trade checks and journals every decision."""

    def __init__(
        self,
        limits: RiskLimits,
        checks: Sequence[PreTradeCheck] | None = None,
        journal: EventLog | None = None,
    ) -> None:
        self._limits = limits
        self._checks = tuple(checks) if checks is not None else default_checks()
        self._journal = journal

    def review(
        self, order: Order, ctx: RiskContext, correlation_id: str | None = None
    ) -> RiskDecision:
        """Review one order; journals the decision under ``correlation_id``.

        A caller that is already running under a correlation id (the daily
        desk cycle) passes it so the risk report joins that causal group. Left
        as ``None`` — a standalone review, an MCP call, an API probe — a fresh
        id is minted. Without this the richest record the engine produces was
        journaled under an id nothing else shared, so
        ``events_by_correlation`` on a cycle could never reach the reason an
        order was blocked (ADR-009).
        """
        correlation_id = correlation_id or str(uuid.uuid4())
        results = tuple(self._run_check(check, order, ctx) for check in self._checks)
        verdict = max(
            (r.verdict for r in results),
            key=_SEVERITY.__getitem__,
            default=Verdict.APPROVE,
        )
        adjusted: Order | None
        if verdict is Verdict.APPROVE:
            adjusted = order
        elif verdict is Verdict.RESIZE:
            # Several checks may resize; the binding constraint is the
            # smallest surviving quantity.
            quantities = [
                r.allowed_quantity
                for r in results
                if r.verdict is Verdict.RESIZE and r.allowed_quantity is not None
            ]
            adjusted = replace(order, quantity=min(quantities, key=abs)) if quantities else None
        else:
            adjusted = None
        decision = RiskDecision(
            verdict=verdict,
            adjusted_order=adjusted,
            results=results,
            correlation_id=correlation_id,
        )
        if self._journal is not None:
            self._journal.append(
                "risk_decision",
                _decision_payload(order, decision),
                correlation_id=correlation_id,
            )
        return decision

    def _run_check(self, check: PreTradeCheck, order: Order, ctx: RiskContext) -> CheckResult:
        try:
            name = check.name
        except Exception:  # even a broken name property must not open the gate
            name = type(check).__name__
        try:
            return check.evaluate(order, ctx, self._limits)
        except Exception as exc:  # fail closed: a broken check is a REJECT
            return CheckResult(
                check_name=name,
                verdict=Verdict.REJECT,
                reason=(
                    f"risk check '{name}' raised {type(exc).__name__}: {exc} — "
                    "failing closed, order rejected"
                ),
            )


def _decision_payload(order: Order, decision: RiskDecision) -> dict[str, Any]:
    """JSON-serialisable snapshot of a decision for the journal."""
    return {
        "order": {
            "symbol": order.symbol,
            "quantity": order.quantity,
            "price": order.price,
            "notional": order.notional,
        },
        "verdict": decision.verdict.value,
        "adjusted_quantity": (
            None if decision.adjusted_order is None else decision.adjusted_order.quantity
        ),
        "results": [
            {
                "check": r.check_name,
                "verdict": r.verdict.value,
                "reason": r.reason,
                "allowed_quantity": r.allowed_quantity,
            }
            for r in decision.results
        ],
    }


__all__ = ["RiskDecision", "RiskEngine", "default_checks"]

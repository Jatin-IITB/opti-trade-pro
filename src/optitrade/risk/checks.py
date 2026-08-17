"""Pre-trade risk checks.

Each check is a small, independently testable rule with a plain-English
``reason`` carrying the actual numbers — those strings are the audit trail,
not decoration. Checks never raise for a bad order; they return a verdict.
(Exceptions are still possible on malformed inputs; the engine converts them
to REJECT — fail closed.)
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from math import copysign
from typing import Protocol

from optitrade.core.types import Greeks, Order, Portfolio
from optitrade.risk.limits import RiskLimits


@dataclass(frozen=True, slots=True)
class RiskContext:
    """Everything a check needs beyond the order itself.

    ``order_greeks`` are PER UNIT of the order; scale by the signed order
    quantity to get the order's aggregate contribution.
    """

    portfolio: Portfolio
    portfolio_greeks: Greeks  # current aggregate book greeks
    order_greeks: Greeks  # per unit of the proposed order
    margin_required: float  # margin the proposed order would consume
    spot: float


# (str, Enum) rather than StrEnum for parity with core.types.OptionType.
class Verdict(str, Enum):  # noqa: UP042
    APPROVE = "approve"
    RESIZE = "resize"
    REJECT = "reject"
    HALT = "halt"


@dataclass(frozen=True, slots=True)
class CheckResult:
    check_name: str
    verdict: Verdict
    reason: str
    allowed_quantity: float | None = None  # only set for RESIZE


class PreTradeCheck(Protocol):
    """Interface every pre-trade check implements."""

    @property
    def name(self) -> str: ...

    def evaluate(self, order: Order, ctx: RiskContext, limits: RiskLimits) -> CheckResult: ...


def post_trade_greeks(order: Order, ctx: RiskContext) -> Greeks:
    """Aggregate book greeks if the order fills at its full quantity."""
    return ctx.portfolio_greeks + ctx.order_greeks.scaled(order.quantity)


def greek_utilisation(order: Order, ctx: RiskContext, limits: RiskLimits) -> dict[str, float]:
    """Post-trade |greek| as a fraction of its cap, per greek (1.0 = at cap).

    Shared by :class:`GreeksLimitCheck` and the governance RiskOfficer so the
    arithmetic lives in exactly one place.
    """
    post = post_trade_greeks(order, ctx)
    return {
        "delta": abs(post.delta) / limits.max_abs_delta,
        "gamma": abs(post.gamma) / limits.max_abs_gamma,
        "vega": abs(post.vega) / limits.max_abs_vega,
    }


class GreeksLimitCheck:
    """Rejects any order whose post-trade aggregate greeks would breach a cap."""

    @property
    def name(self) -> str:
        return "greeks_limit"

    def evaluate(self, order: Order, ctx: RiskContext, limits: RiskLimits) -> CheckResult:
        post = post_trade_greeks(order, ctx)
        rows = (
            ("delta", post.delta, limits.max_abs_delta),
            ("gamma", post.gamma, limits.max_abs_gamma),
            ("vega", post.vega, limits.max_abs_vega),
        )
        breaches = [
            f"post-trade |{greek}| {abs(value):.6g} exceeds cap {cap:.6g}"
            for greek, value, cap in rows
            if abs(value) > cap
        ]
        if breaches:
            return CheckResult(self.name, Verdict.REJECT, "; ".join(breaches))
        detail = ", ".join(f"|{greek}| {abs(value):.6g} <= {cap:.6g}" for greek, value, cap in rows)
        return CheckResult(self.name, Verdict.APPROVE, f"post-trade greeks within limits: {detail}")


class MarginSufficiencyCheck:
    """Rejects when buffered margin for the order exceeds available margin."""

    @property
    def name(self) -> str:
        return "margin_sufficiency"

    def evaluate(self, order: Order, ctx: RiskContext, limits: RiskLimits) -> CheckResult:
        buffered = ctx.margin_required * limits.margin_buffer
        available = ctx.portfolio.margin_available
        if buffered > available:
            return CheckResult(
                self.name,
                Verdict.REJECT,
                f"margin required {ctx.margin_required:.2f} with buffer "
                f"{limits.margin_buffer:.2f}x = {buffered:.2f} exceeds available {available:.2f}",
            )
        return CheckResult(
            self.name,
            Verdict.APPROVE,
            f"buffered margin {buffered:.2f} within available {available:.2f}",
        )


class DrawdownCheck:
    """Halts all trading when the account drawdown reaches its limit."""

    @property
    def name(self) -> str:
        return "drawdown"

    def evaluate(self, order: Order, ctx: RiskContext, limits: RiskLimits) -> CheckResult:
        drawdown = ctx.portfolio.drawdown
        if drawdown >= limits.max_drawdown:
            return CheckResult(
                self.name,
                Verdict.HALT,
                f"drawdown {drawdown:.2%} has reached the {limits.max_drawdown:.2%} limit: "
                "halt trading and cancel all open orders",
            )
        return CheckResult(
            self.name,
            Verdict.APPROVE,
            f"drawdown {drawdown:.2%} within the {limits.max_drawdown:.2%} limit",
        )


class ConcentrationCheck:
    """Caps any single symbol at a fraction of post-trade gross notional.

    The ratio checked is ``(existing symbol notional + order notional) /
    (gross notional + order notional)``. Note that a first order into an
    empty book is by definition 100% of gross notional; a book that must
    bootstrap from empty needs ``max_concentration = 1.0``.
    """

    @property
    def name(self) -> str:
        return "concentration"

    def evaluate(self, order: Order, ctx: RiskContext, limits: RiskLimits) -> CheckResult:
        lot = order.contract.lot_size if order.contract is not None else 1
        unit_notional = abs(order.price) * lot
        existing = sum(
            abs(p.quantity) * p.entry_price * p.contract.lot_size
            for p in ctx.portfolio.positions
            if p.contract.symbol == order.symbol
        )
        gross = ctx.portfolio.gross_notional
        cap = limits.max_concentration
        denominator = gross + order.notional
        if denominator <= 0.0:
            return CheckResult(
                self.name,
                Verdict.APPROVE,
                "zero gross notional before and after the trade; concentration not applicable",
            )
        post = existing + order.notional
        ratio = post / denominator
        if ratio <= cap:
            return CheckResult(
                self.name,
                Verdict.APPROVE,
                f"post-trade {order.symbol} notional {post:.2f} is {ratio:.2%} of "
                f"gross {denominator:.2f}, within the {cap:.2%} cap",
            )
        # Largest q >= 0 with (existing + q*u) / (gross + q*u) <= cap, i.e.
        # q <= (cap*gross - existing) / (u * (1 - cap)). cap == 1.0 cannot
        # breach (ratio <= 1); guard anyway and fail closed on float dust.
        if unit_notional > 0.0 and cap < 1.0:
            max_quantity = (cap * gross - existing) / (unit_notional * (1.0 - cap))
        else:
            max_quantity = 0.0
        if max_quantity <= 0.0:
            return CheckResult(
                self.name,
                Verdict.REJECT,
                f"no quantity of {order.symbol} at price {order.price:.2f} satisfies the "
                f"{cap:.2%} concentration cap (existing symbol notional {existing:.2f}, "
                f"portfolio gross {gross:.2f})",
            )
        return CheckResult(
            self.name,
            Verdict.RESIZE,
            f"post-trade {order.symbol} notional {post:.2f} would be {ratio:.2%} of "
            f"gross {denominator:.2f}, above the {cap:.2%} cap; max quantity at price "
            f"{order.price:.2f} is {max_quantity:.6g}",
            allowed_quantity=copysign(max_quantity, order.quantity),
        )


__all__ = [
    "CheckResult",
    "ConcentrationCheck",
    "DrawdownCheck",
    "GreeksLimitCheck",
    "MarginSufficiencyCheck",
    "PreTradeCheck",
    "RiskContext",
    "Verdict",
    "greek_utilisation",
    "post_trade_greeks",
]

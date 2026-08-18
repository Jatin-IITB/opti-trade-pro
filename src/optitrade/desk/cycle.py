"""Deterministic daily desk cycle — the paper-loop money path (ADR-008/010/015).

One call to :func:`run_daily_cycle` is one desk day: mark the book, ask the
strategy, govern and risk-check every order (fail closed), paper-fill the
survivors, decide the delta hedge, and journal the lot under one correlation
id. No LLM output can reach this path; agents observe and explain via the
journal (ADR-015).

Deliberate paper-loop scope (each of these is a documented simplification,
not an oversight):

- **Marking vols**: positions carry no vol of their own. Each contract is
  marked at ``day.surface.vol(strike, expiry)`` when a surface is supplied,
  falling back to ``day.realized_vol`` when ``day.surface is None``.
- **Fills pay the spread**: an approved order fills at
  ``price * (1 + sign(quantity) * spread_frac / 2)`` — half the configured
  spread each way, so buys pay up and sells receive less.
- **Hedge decisions are journaled, not booked**: the paper loop tracks the
  option book plus hedge *decisions*; full underlying inventory (and hedge
  fills) arrive with the broker adapter in the platform layer.
- **Margin proxy**: ``margin_required`` for a proposed order is its premium
  notional (``order.notional``). A real SPAN-style margin model is
  platform-layer work.
- **No book aging**: contract expiries are the year fractions stored at
  construction; dating the book across days belongs to the data spine.
- **Debate is optional by construction**: governance review runs when a
  :class:`~optitrade.governance.debate.DebatePanel` is supplied *and*
  ``DeskConfig.require_debate`` is true. Passing ``panel=None`` (unit tests,
  bootstrap runs) skips deliberation; the fail-closed risk engine still
  reviews every order regardless.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, replace
from typing import Any, SupportsFloat, cast

from optitrade.core.types import Greeks, OptionContract, Order, Portfolio, Position
from optitrade.desk.kill_switch import KillSwitch
from optitrade.governance.debate import DebatePanel
from optitrade.governance.experts import Stance, TradeProposal
from optitrade.hedging.band import BandParams
from optitrade.hedging.delta_hedger import DeltaHedger, HedgeDecision
from optitrade.journal.event_log import EventLog
from optitrade.pricing.black_scholes import bs_greeks_at, bs_price
from optitrade.risk.checks import RiskContext, Verdict
from optitrade.risk.engine import RiskEngine
from optitrade.risk.limits import RiskLimits
from optitrade.strategy.base import MarketDay, Strategy


@dataclass(frozen=True)
class DeskConfig:
    """Typed configuration for one desk (no magic numbers in the flow).

    ``spread_frac`` is the full relative bid-ask spread assumed for paper
    fills; each fill pays half of it (buys above, sells below the reference
    price). ``require_debate`` gates governance review when a panel is
    supplied.
    """

    limits: RiskLimits
    band: BandParams
    underlying_symbol: str
    spread_frac: float = 0.005
    require_debate: bool = True


@dataclass(frozen=True)
class CycleResult:
    """Journal-ready summary of one daily cycle."""

    date_ts: float
    action_taken: str
    fills: tuple[Order, ...]
    rejected: tuple[tuple[Order, str], ...]
    portfolio_after: Portfolio
    book_greeks: Greeks
    hedge: HedgeDecision | None
    halted: bool
    correlation_id: str


def _vol_for(day: MarketDay, strike: float, expiry: float) -> float:
    """Marking vol for a contract: surface vol, else the day's realized vol."""
    if day.surface is None:
        return day.realized_vol
    return float(cast(SupportsFloat, day.surface.vol(strike, expiry)))


def _mark_book(day: MarketDay, book: tuple[Position, ...]) -> tuple[Greeks, float]:
    """Aggregate greeks and mark-to-model value of the option book."""
    greeks = Greeks()
    value = 0.0
    for position in book:
        contract = position.contract
        vol = _vol_for(day, contract.strike, contract.expiry)
        unit = bs_greeks_at(
            day.spot, contract.strike, contract.expiry, day.rate, vol, contract.option_type
        )
        scale = position.quantity * contract.lot_size
        greeks = greeks + unit.scaled(scale)
        price = float(
            bs_price(
                day.spot, contract.strike, contract.expiry, day.rate, vol, contract.option_type
            )
        )
        value += scale * price
    return greeks, value


def _apply_fill(
    book: tuple[Position, ...],
    contract: OptionContract,
    quantity: float,
    fill_price: float,
) -> tuple[Position, ...]:
    """Merge a fill into the book by contract symbol.

    Adding to the same side averages the entry price; a partial close keeps
    the original entry; closing through zero opens the residual at the fill
    price; closing exactly removes the position.
    """
    for index, position in enumerate(book):
        if position.contract.symbol != contract.symbol:
            continue
        new_quantity = position.quantity + quantity
        rest = book[:index] + book[index + 1 :]
        if new_quantity == 0.0:
            return rest
        if position.quantity * quantity > 0.0:  # adding to the same side
            entry = (
                position.entry_price * position.quantity + fill_price * quantity
            ) / new_quantity
        elif position.quantity * new_quantity > 0.0:  # partial close, same side remains
            entry = position.entry_price
        else:  # closed through zero; the residual was opened at the fill
            entry = fill_price
        return (*rest, Position(contract=contract, quantity=new_quantity, entry_price=entry))
    return (*book, Position(contract=contract, quantity=quantity, entry_price=fill_price))


def _order_payload(order: Order) -> dict[str, Any]:
    return {
        "symbol": order.symbol,
        "quantity": order.quantity,
        "price": order.price,
        "notional": order.notional,
    }


def _rejection_payload(order: Order, stage: str, reason: str) -> dict[str, Any]:
    return {"order": _order_payload(order), "stage": stage, "reason": reason}


def _hedge_payload(hedge: HedgeDecision) -> dict[str, Any]:
    return {
        "action": hedge.action,
        "order": None if hedge.order is None else _order_payload(hedge.order),
        "portfolio_delta": hedge.portfolio_delta,
        "band_half_width": hedge.band_half_width,
        "band_scale": hedge.band_scale,
        "rationale": hedge.rationale,
        "confidence": hedge.confidence,
    }


def run_daily_cycle(
    day: MarketDay,
    portfolio: Portfolio,
    book: tuple[Position, ...],
    strategy: Strategy,
    config: DeskConfig,
    journal: EventLog,
    kill_switch: KillSwitch,
    panel: DebatePanel | None = None,
) -> tuple[CycleResult, tuple[Position, ...], Portfolio]:
    """Run one desk day; returns ``(result, book_after, portfolio_after)``.

    Order pipeline per proposed order (fail closed at every stage): optional
    debate consensus -> risk engine review -> paper fill. A HALT verdict
    engages the kill switch, cancels every not-yet-filled order, and skips
    the hedge — after a HALT the desk places no orders of any kind until a
    human resets the switch.
    """
    correlation_id = str(uuid.uuid4())

    if kill_switch.is_engaged():
        reason = kill_switch.reason() or "kill switch file present"
        journal.append(
            "cycle_skipped",
            {"date_ts": day.timestamp, "reason": reason},
            correlation_id=correlation_id,
        )
        result = CycleResult(
            date_ts=day.timestamp,
            action_taken=f"skipped: {reason}",
            fills=(),
            rejected=(),
            portfolio_after=portfolio,
            book_greeks=Greeks(),
            hedge=None,
            halted=True,
            correlation_id=correlation_id,
        )
        return result, book, portfolio

    # (b) Mark the book: aggregate greeks and mark-to-model equity.
    book_greeks, mark_value = _mark_book(day, book)
    portfolio = replace(portfolio, positions=book)
    portfolio = portfolio.with_equity(portfolio.cash + mark_value)

    # (c) Strategy decision, then govern + risk-check each order.
    decision = strategy.decide(day, book)
    engine = RiskEngine(config.limits, journal=journal)
    fills: list[Order] = []
    rejected: list[tuple[Order, str]] = []
    halted = False
    halt_reason = ""

    orders = tuple(decision.orders) if decision.action != "hold" else ()
    for index, order in enumerate(orders):
        contract = order.contract
        if contract is None:
            reason = (
                "paper loop books option contracts only; underlying exposure "
                "arrives via the delta hedger"
            )
            rejected.append((order, reason))
            journal.append(
                "order_rejected",
                _rejection_payload(order, "structure", reason),
                correlation_id=correlation_id,
            )
            continue
        implied_vol = _vol_for(day, contract.strike, contract.expiry)
        unit_greeks = bs_greeks_at(
            day.spot, contract.strike, contract.expiry, day.rate, implied_vol, contract.option_type
        ).scaled(float(contract.lot_size))
        ctx = RiskContext(
            portfolio=portfolio,
            portfolio_greeks=book_greeks,
            order_greeks=unit_greeks,
            margin_required=order.notional,
            spot=day.spot,
        )

        if panel is not None and config.require_debate:
            record = panel.deliberate(
                TradeProposal(
                    order=order,
                    thesis=decision.thesis,
                    expected_edge=decision.expected_edge,
                    estimated_cost=decision.estimated_cost,
                    implied_vol=implied_vol,
                    realized_vol=day.realized_vol,
                    ctx=ctx,
                )
            )
            if record.consensus is Stance.REJECT:
                reason = f"debate consensus REJECT: {record.rationale}"
                rejected.append((order, reason))
                journal.append(
                    "order_rejected",
                    _rejection_payload(order, "debate", reason),
                    correlation_id=correlation_id,
                )
                continue

        risk = engine.review(order, ctx)
        if risk.verdict is Verdict.HALT:
            halt_reason = next(
                (r.reason for r in risk.results if r.verdict is Verdict.HALT),
                "risk engine returned HALT",
            )
            kill_switch.engage(halt_reason)
            cancelled = orders[index:]
            for pending in cancelled:
                rejected.append((pending, f"cancelled by HALT: {halt_reason}"))
            journal.append(
                "kill_switch_engaged",
                {"reason": halt_reason, "cancelled_orders": len(cancelled)},
                correlation_id=correlation_id,
            )
            halted = True
            break
        if risk.adjusted_order is None:  # REJECT, or RESIZE with no surviving quantity
            reason = "; ".join(r.reason for r in risk.results if r.verdict is not Verdict.APPROVE)
            rejected.append((order, reason or f"risk verdict {risk.verdict.value}"))
            journal.append(
                "order_rejected",
                _rejection_payload(order, "risk", reason),
                correlation_id=correlation_id,
            )
            continue

        # (d) Paper fill at the spread-adjusted price (RESIZE fills the
        # adjusted quantity), then refresh cash, book and marks.
        approved = risk.adjusted_order
        direction = 1.0 if approved.quantity > 0 else -1.0
        fill_price = approved.price * (1.0 + direction * config.spread_frac / 2.0)
        fills.append(
            Order(
                symbol=approved.symbol,
                quantity=approved.quantity,
                price=fill_price,
                contract=contract,
            )
        )
        book = _apply_fill(book, contract, approved.quantity, fill_price)
        cash = portfolio.cash - approved.quantity * fill_price * contract.lot_size
        book_greeks, mark_value = _mark_book(day, book)
        portfolio = replace(portfolio, positions=book, cash=cash)
        portfolio = portfolio.with_equity(cash + mark_value)

    # (e) Delta-hedge decision on the post-trade aggregate (skipped after a
    # HALT: a halted desk places no orders of any kind).
    hedge: HedgeDecision | None = None
    if not halted:
        hedger = DeltaHedger(config.underlying_symbol, config.band)
        hedge = hedger.decide(
            portfolio_delta=book_greeks.delta,
            gamma=book_greeks.gamma,
            spot=day.spot,
            realized_vol=day.realized_vol,
        )
        journal.append("hedge_decision", _hedge_payload(hedge), correlation_id=correlation_id)

    if halted:
        action_taken = (
            f"{decision.action}: HALT — {halt_reason} "
            f"({len(fills)} filled, {len(rejected)} rejected/cancelled)"
        )
    else:
        hedge_note = f"; hedge {hedge.action}" if hedge is not None else ""
        action_taken = (
            f"{decision.action}: {len(fills)} filled, {len(rejected)} rejected{hedge_note}"
        )

    # (f) One summarising event; sub-decisions journaled above share this
    # correlation id where the APIs allow (engine and panel mint their own).
    journal.append(
        "daily_cycle",
        {
            "date_ts": day.timestamp,
            "action": decision.action,
            "action_taken": action_taken,
            "fills": [_order_payload(o) for o in fills],
            "rejected": [{"order": _order_payload(o), "reason": r} for o, r in rejected],
            "cash": portfolio.cash,
            "equity": portfolio.equity,
            "drawdown": portfolio.drawdown,
            "book_delta": book_greeks.delta,
            "book_gamma": book_greeks.gamma,
            "book_vega": book_greeks.vega,
            "book_theta": book_greeks.theta,
            "hedge_action": None if hedge is None else hedge.action,
            "halted": halted,
        },
        correlation_id=correlation_id,
    )
    result = CycleResult(
        date_ts=day.timestamp,
        action_taken=action_taken,
        fills=tuple(fills),
        rejected=tuple(rejected),
        portfolio_after=portfolio,
        book_greeks=book_greeks,
        hedge=hedge,
        halted=halted,
        correlation_id=correlation_id,
    )
    return result, book, portfolio


__all__ = ["CycleResult", "DeskConfig", "run_daily_cycle"]

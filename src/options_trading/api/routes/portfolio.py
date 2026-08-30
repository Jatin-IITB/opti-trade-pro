"""Portfolio REST endpoints — read-only analytics over synced Upstox data."""

from __future__ import annotations

import logging
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Request
from starlette.status import HTTP_503_SERVICE_UNAVAILABLE

from optitrade.core.types import Greeks, Portfolio
from optitrade.pricing import bs_greeks_at
from optitrade.pricing.implied_vol import implied_vol

from ...models.portfolio import (
    HoldingOut,
    OrderOut,
    PortfolioGreeksOut,
    PortfolioPositionOut,
    PortfolioSignalOut,
    PortfolioSummaryOut,
    PortfolioSyncStatusOut,
)
from ...services.portfolio_client import UpstoxPosition, _expiry_str_to_date
from ...services.portfolio_sync_service import PortfolioSyncService
from ...utils.auth_dependencies import get_current_user

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/portfolio", tags=["portfolio"])


def _get_portfolio_sync(request: Request) -> PortfolioSyncService:
    svc = getattr(request.app.state, "portfolio_sync", None)
    if svc is None:
        raise HTTPException(
            status_code=HTTP_503_SERVICE_UNAVAILABLE,
            detail="Portfolio sync service not initialized — authenticate first",
        )
    return svc


def _position_to_out(pos: UpstoxPosition) -> PortfolioPositionOut:
    return PortfolioPositionOut(
        instrument_key=pos.instrument_key,
        trading_symbol=pos.trading_symbol,
        exchange=pos.exchange,
        product=pos.product,
        quantity=pos.quantity,
        buy_price=pos.buy_price,
        sell_price=pos.sell_price,
        last_price=pos.last_price,
        pnl=pos.pnl,
        option_type=pos.option_type,
        strike_price=pos.strike_price,
        expiry=pos.expiry,
    )


def _compute_aggregate_greeks(portfolio: Portfolio, spot: float, rate: float = 0.065) -> Greeks:
    agg = Greeks()
    for pos in portfolio.positions:
        c = pos.contract
        if c.expiry <= 0:
            continue
        try:
            iv = implied_vol(pos.entry_price, spot, c.strike, c.expiry, rate, c.option_type)
            if iv <= 0:
                iv = 0.20
        except Exception:
            iv = 0.20
        try:
            g = bs_greeks_at(spot, c.strike, c.expiry, rate, iv, c.option_type)
            agg = agg + g.scaled(pos.quantity)
        except Exception:
            logger.debug("Greeks computation failed for %s", c.symbol)
    return agg


def _greeks_to_out(g: Greeks) -> PortfolioGreeksOut:
    return PortfolioGreeksOut(
        delta=g.delta,
        gamma=g.gamma,
        vega=g.vega,
        theta=g.theta,
        rho=g.rho,
        vanna=g.vanna,
        volga=g.volga,
    )


@router.get("/positions", response_model=list[PortfolioPositionOut])
async def get_positions(
    request: Request,
    _current_user: dict = Depends(get_current_user),
) -> list[PortfolioPositionOut]:
    svc = _get_portfolio_sync(request)
    return [_position_to_out(p) for p in svc.get_latest_positions()]


@router.get("/holdings", response_model=list[HoldingOut])
async def get_holdings(
    request: Request,
    _current_user: dict = Depends(get_current_user),
) -> list[HoldingOut]:
    svc = _get_portfolio_sync(request)
    return [
        HoldingOut(
            instrument_key=h.instrument_key,
            trading_symbol=h.trading_symbol,
            exchange=h.exchange,
            quantity=h.quantity,
            average_price=h.average_price,
            last_price=h.last_price,
            pnl=h.pnl,
            day_change=h.day_change,
            day_change_percentage=h.day_change_percentage,
        )
        for h in svc.get_latest_holdings()
    ]


@router.get("/orders", response_model=list[OrderOut])
async def get_orders(
    request: Request,
    _current_user: dict = Depends(get_current_user),
) -> list[OrderOut]:
    svc = _get_portfolio_sync(request)
    return [
        OrderOut(
            order_id=o.order_id,
            trading_symbol=o.trading_symbol,
            exchange=o.exchange,
            order_type=o.order_type,
            transaction_type=o.transaction_type,
            quantity=o.quantity,
            price=o.price,
            trigger_price=o.trigger_price,
            status=o.status,
            filled_quantity=o.filled_quantity,
            average_price=o.average_price,
            placed_at=o.placed_at,
            product=o.product,
        )
        for o in svc.get_latest_orders()
    ]


@router.get("/summary", response_model=PortfolioSummaryOut)
async def get_portfolio_summary(
    request: Request,
    _current_user: dict = Depends(get_current_user),
) -> PortfolioSummaryOut:
    svc = _get_portfolio_sync(request)
    portfolio = svc.get_latest_portfolio()
    if portfolio is None:
        return PortfolioSummaryOut(synced=False)

    positions = svc.get_latest_positions()
    total_pnl = sum(p.pnl for p in positions)
    agg = _compute_aggregate_greeks(portfolio, spot=portfolio.equity or 0.0)

    return PortfolioSummaryOut(
        total_positions=len(positions),
        core_positions=len(portfolio.positions),
        total_pnl=total_pnl,
        equity=portfolio.equity,
        aggregate_greeks=_greeks_to_out(agg),
        synced=True,
    )


@router.post("/sync")
async def trigger_sync(
    request: Request,
    _current_user: dict = Depends(get_current_user),
) -> dict[str, str]:
    svc = _get_portfolio_sync(request)
    await svc.sync_once()
    return {"status": "synced"}


@router.get("/sync/status", response_model=PortfolioSyncStatusOut)
async def get_sync_status(
    request: Request,
    _current_user: dict = Depends(get_current_user),
) -> PortfolioSyncStatusOut:
    svc = _get_portfolio_sync(request)
    s = svc.status()
    return PortfolioSyncStatusOut(
        running=s.running,
        last_sync_ts=s.last_sync_ts,
        n_syncs=s.n_syncs,
        n_failures=s.n_failures,
        position_count=s.position_count,
    )


def _moneyness_label(strike: float, spot: float, option_type: str | None) -> str:
    if spot <= 0 or strike is None:
        return "unknown"
    ratio = strike / spot
    if option_type == "CE":
        if ratio < 0.97:
            return "ITM"
        if ratio > 1.03:
            return "OTM"
        return "ATM"
    if option_type == "PE":
        if ratio > 1.03:
            return "ITM"
        if ratio < 0.97:
            return "OTM"
        return "ATM"
    return "unknown"


@router.get("/signals", response_model=list[PortfolioSignalOut])
async def get_position_signals(
    request: Request,
    _current_user: dict = Depends(get_current_user),
) -> list[PortfolioSignalOut]:
    svc = _get_portfolio_sync(request)
    positions = svc.get_latest_positions()
    portfolio = svc.get_latest_portfolio()
    spot = portfolio.equity if portfolio else 0.0

    today = date.today()
    signals: list[PortfolioSignalOut] = []
    for pos in positions:
        entry = pos.buy_price if pos.quantity > 0 else pos.sell_price
        current = pos.last_price
        pnl_pct = ((current - entry) / entry * 100.0) if entry > 0 else 0.0
        if pos.quantity < 0 and entry > 0:
            pnl_pct = (entry - current) / entry * 100.0

        days_to_expiry = None
        if pos.expiry:
            exp_date = _expiry_str_to_date(pos.expiry)
            if exp_date:
                days_to_expiry = (exp_date - today).days

        moneyness = _moneyness_label(pos.strike_price or 0.0, spot, pos.option_type)

        signals.append(
            PortfolioSignalOut(
                trading_symbol=pos.trading_symbol,
                option_type=pos.option_type,
                strike_price=pos.strike_price,
                expiry=pos.expiry,
                quantity=pos.quantity,
                entry_price=entry,
                current_price=current,
                pnl=pos.pnl,
                pnl_pct=round(pnl_pct, 2),
                moneyness=moneyness,
                days_to_expiry=days_to_expiry,
            )
        )

    return signals

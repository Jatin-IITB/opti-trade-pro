"""Portfolio REST endpoints — read-only analytics over synced Upstox data."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Request
from starlette.status import HTTP_503_SERVICE_UNAVAILABLE

from optitrade.core.types import Greeks

from ...models.portfolio import (
    HoldingOut,
    OrderOut,
    PortfolioGreeksOut,
    PortfolioPositionOut,
    PortfolioSignalOut,
    PortfolioSummaryOut,
    PortfolioSyncStatusOut,
)
from ...services.book_pricing import price_book
from ...services.portfolio_client import UpstoxPortfolioClient, UpstoxPosition, _expiry_str_to_date
from ...services.portfolio_sync_service import (
    PortfolioSyncConfig,
    PortfolioSyncService,
)
from ...services.token_provider import get_token_provider
from ...utils.auth_dependencies import get_current_user

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/portfolio", tags=["portfolio"])


async def _get_portfolio_sync(request: Request) -> PortfolioSyncService:
    svc = getattr(request.app.state, "portfolio_sync", None)
    if svc is not None:
        return svc

    # Lazy init: start the sync service if a valid token is available
    from ...services.websocket_manager import WebSocketManager

    token_provider = get_token_provider(request.app)
    try:
        await token_provider.get()
    except Exception:
        raise HTTPException(
            status_code=HTTP_503_SERVICE_UNAVAILABLE,
            detail="Portfolio sync not available — authenticate via Upstox first",
        )

    ws_manager = getattr(request.app.state, "websocket_manager", None) or WebSocketManager()
    live_pipeline = getattr(request.app.state, "live_pipeline", None)
    client = UpstoxPortfolioClient(token_provider=token_provider)
    svc = PortfolioSyncService(
        client=client,
        ws_manager=ws_manager,
        config=PortfolioSyncConfig(),
        spot_fn=live_pipeline.get_latest_spot if live_pipeline is not None else None,
    )
    request.app.state.portfolio_sync = svc
    request.app.state._portfolio_sync_task = asyncio.create_task(svc.run())
    logger.info("Portfolio sync started on-demand")
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
    svc = await _get_portfolio_sync(request)
    return [_position_to_out(p) for p in svc.get_latest_positions()]


@router.get("/holdings", response_model=list[HoldingOut])
async def get_holdings(
    request: Request,
    _current_user: dict = Depends(get_current_user),
) -> list[HoldingOut]:
    svc = await _get_portfolio_sync(request)
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
    svc = await _get_portfolio_sync(request)
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
    svc = await _get_portfolio_sync(request)
    portfolio = svc.get_latest_portfolio()
    if portfolio is None:
        return PortfolioSummaryOut(synced=False)

    positions = svc.get_latest_positions()
    total_pnl = sum(p.pnl for p in positions)
    funds = svc.get_latest_funds()
    spot = svc.get_latest_spot()

    # Book Greeks need a live underlying level. Without one they are reported
    # as absent, not as zero — see PortfolioSummaryOut.
    greeks_out: PortfolioGreeksOut | None = None
    n_priced = 0
    if spot is not None:
        marks = {p.trading_symbol: p.last_price for p in positions}
        priced = price_book(portfolio, marks=marks, spot=spot)
        greeks_out = _greeks_to_out(priced.aggregate_greeks)
        n_priced = priced.n_priced
    else:
        logger.debug("No live spot available; book Greeks omitted from summary")

    return PortfolioSummaryOut(
        total_positions=len(positions),
        core_positions=len(portfolio.positions),
        total_pnl=total_pnl,
        equity=funds.total_equity if funds is not None else None,
        margin_used=funds.used_margin if funds is not None else None,
        margin_available=funds.available_margin if funds is not None else None,
        margin_utilization=funds.margin_utilization if funds is not None else None,
        spot=spot,
        aggregate_greeks=greeks_out,
        greeks_priced=n_priced,
        synced=True,
    )


@router.post("/sync")
async def trigger_sync(
    request: Request,
    _current_user: dict = Depends(get_current_user),
) -> dict[str, str]:
    svc = await _get_portfolio_sync(request)
    await svc.sync_once()
    return {"status": "synced"}


@router.get("/sync/status", response_model=PortfolioSyncStatusOut)
async def get_sync_status(
    request: Request,
    _current_user: dict = Depends(get_current_user),
) -> PortfolioSyncStatusOut:
    svc = await _get_portfolio_sync(request)
    s = svc.status()
    return PortfolioSyncStatusOut(
        running=s.running,
        last_sync_ts=s.last_sync_ts,
        n_syncs=s.n_syncs,
        n_failures=s.n_failures,
        position_count=s.position_count,
        spot=s.spot,
        auth_required=s.auth_required,
        last_error=s.last_error,
    )


@dataclass(frozen=True)
class MoneynessBands:
    """Strike/spot ratio bounds of the at-the-money bucket.

    Anything inside ``[atm_lower, atm_upper]`` is ATM; outside it the label
    depends on option type.
    """

    atm_lower: float = 0.97
    atm_upper: float = 1.03

    def __post_init__(self) -> None:
        if not 0 < self.atm_lower < 1 < self.atm_upper:
            raise ValueError(
                f"ATM band must straddle 1.0: got [{self.atm_lower}, {self.atm_upper}]"
            )


MONEYNESS_BANDS = MoneynessBands()


def _moneyness_label(
    strike: float | None,
    spot: float | None,
    option_type: str | None,
    bands: MoneynessBands = MONEYNESS_BANDS,
) -> str:
    if spot is None or spot <= 0 or strike is None or strike <= 0:
        return "unknown"
    ratio = strike / spot
    if option_type == "CE":
        if ratio < bands.atm_lower:
            return "ITM"
        if ratio > bands.atm_upper:
            return "OTM"
        return "ATM"
    if option_type == "PE":
        if ratio > bands.atm_upper:
            return "ITM"
        if ratio < bands.atm_lower:
            return "OTM"
        return "ATM"
    return "unknown"


@router.get("/signals", response_model=list[PortfolioSignalOut])
async def get_position_signals(
    request: Request,
    _current_user: dict = Depends(get_current_user),
) -> list[PortfolioSignalOut]:
    svc = await _get_portfolio_sync(request)
    positions = svc.get_latest_positions()
    spot = svc.get_latest_spot()

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

        moneyness = _moneyness_label(pos.strike_price, spot, pos.option_type)

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

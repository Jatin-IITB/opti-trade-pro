"""Analytics routes — thin adapter over the optitrade quant core (ADR-002).

This module owns exactly two jobs: map JSON payloads into ``optitrade.core``
types, and map engine results back to JSON. No math lives here.
"""

from __future__ import annotations

import logging
import time
from typing import Literal

import numpy as np
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from optitrade.core import (
    Greeks,
    MarketSnapshot,
    OptionQuote,
    OptionType,
    OptiTradeError,
    Order,
    Portfolio,
)
from optitrade.greeks.scenario import BookPosition, ScenarioGrid, run_scenario_grid
from optitrade.hedging import BandParams, DeltaHedger, ScalpingParams
from optitrade.pricing import bs_greeks_at
from optitrade.risk import RiskContext, RiskEngine, RiskLimits
from optitrade.vol.arbitrage import check_durrleman, validate_surface
from optitrade.vol.density import rnd_gate
from optitrade.vol.essvi import ESSVISurface
from optitrade.vol.surface import SABRSurface, VolSurface

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/analytics", tags=["Analytics"])


class QuoteIn(BaseModel):
    strike: float = Field(gt=0)
    expiry: float = Field(gt=0, description="Year fraction (ACT/365)")
    option_type: Literal["call", "put"]
    mid: float = Field(gt=0)


class ChainIn(BaseModel):
    spot: float = Field(gt=0)
    rate: float = 0.0
    dividend_yield: float = 0.0
    quotes: list[QuoteIn]


class PositionIn(BaseModel):
    strike: float = Field(gt=0)
    expiry: float = Field(gt=0)
    option_type: Literal["call", "put"]
    quantity: float
    vol: float = Field(gt=0)


class BookIn(BaseModel):
    spot: float = Field(gt=0)
    rate: float = 0.0
    dividend_yield: float = 0.0
    positions: list[PositionIn]


class ScenarioIn(BookIn):
    n_spot: int = 11
    spot_width: float = 0.10
    n_vol: int = 7
    vol_width: float = 0.05
    n_time: int = 7
    max_days: int = 30


class HedgeIn(BaseModel):
    underlying_symbol: str
    portfolio_delta: float
    gamma: float
    spot: float = Field(gt=0)
    proportional_cost: float = Field(default=5e-4, ge=0)
    risk_aversion: float = Field(default=1.0, gt=0)
    realized_vol: float | None = None
    implied_vol: float | None = None


class OrderIn(BaseModel):
    symbol: str
    quantity: float
    price: float = Field(gt=0)


class RiskReviewIn(BaseModel):
    order: OrderIn
    limits: dict[str, float]
    portfolio_greeks: dict[str, float] = Field(default_factory=dict)
    order_greeks: dict[str, float] = Field(default_factory=dict)
    equity: float = 0.0
    high_water_mark: float = 0.0
    margin_available: float = 0.0
    margin_required: float = 0.0
    spot: float = Field(gt=0)


def _snapshot(chain: ChainIn) -> MarketSnapshot:
    return MarketSnapshot(
        spot=chain.spot,
        rate=chain.rate,
        dividend_yield=chain.dividend_yield,
        timestamp=time.time(),
        quotes=tuple(
            OptionQuote(
                strike=q.strike,
                expiry=q.expiry,
                option_type=OptionType(q.option_type),
                mid=q.mid,
            )
            for q in chain.quotes
        ),
    )


@router.post("/surface")
async def build_surface(chain: ChainIn) -> dict:
    """Strip IVs, fit spline + SABR surfaces, and validate no-arbitrage."""
    try:
        snapshot = _snapshot(chain)
        spline = VolSurface.from_snapshot(snapshot)
        sabr = SABRSurface.from_snapshot(snapshot)
        essvi = ESSVISurface.from_snapshot(snapshot)
        violations = validate_surface(spline, spot=chain.spot, rate=chain.rate)
        durrleman = [
            v
            for t in essvi.expiries
            for v in check_durrleman(essvi, float(t), essvi.forward(float(t)))
        ]
        density = rnd_gate(essvi, [float(t) for t in essvi.expiries], chain.spot, chain.rate)
    except OptiTradeError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    expiries = sorted({q.expiry for q in chain.quotes})
    strikes = np.linspace(
        min(q.strike for q in chain.quotes), max(q.strike for q in chain.quotes), 25
    )
    grid = {
        f"{t:.6f}": {
            "spline": np.asarray(spline.vol(strikes, t)).tolist(),
            "sabr": np.asarray(sabr.vol(strikes, t)).tolist(),
            "essvi": np.asarray(essvi.vol(strikes, t)).tolist(),
        }
        for t in expiries
    }
    return {
        "strikes": strikes.tolist(),
        "expiries": expiries,
        "vols": grid,
        "sabr_fits": [
            {
                "expiry": fit.params.expiry,
                "alpha": fit.params.alpha,
                "beta": fit.params.beta,
                "rho": fit.params.rho,
                "nu": fit.params.nu,
                "rmse_vol_points": fit.rmse_vol_points,
            }
            for fit in sabr.slice_fits
        ],
        "worst_sabr_rmse_vol_points": sabr.worst_rmse_vol_points,
        "essvi_fit": {
            "rmse_vol_points": essvi.fit.rmse_vol_points if essvi.fit else None,
            "durrleman_violations": len(durrleman),
            "density_violations": len(density),
        },
        "arbitrage_violations": [
            {
                "kind": v.kind,
                "expiry": v.expiry,
                "strike": v.strike,
                "magnitude": v.magnitude,
                "detail": v.detail,
            }
            for v in violations
        ],
    }


def _greeks_dict(g: Greeks) -> dict[str, float]:
    return {
        "delta": g.delta,
        "gamma": g.gamma,
        "vega": g.vega,
        "theta": g.theta,
        "rho": g.rho,
        "vanna": g.vanna,
        "volga": g.volga,
    }


@router.post("/greeks")
async def portfolio_greeks(book: BookIn) -> dict:
    """Aggregate analytic Greeks for a book (per-unit conventions, ADR-003)."""
    total = Greeks()
    per_position = []
    for p in book.positions:
        g = bs_greeks_at(
            book.spot,
            p.strike,
            p.expiry,
            book.rate,
            p.vol,
            OptionType(p.option_type),
            book.dividend_yield,
        ).scaled(p.quantity)
        per_position.append(_greeks_dict(g))
        total = total + g
    return {"portfolio": _greeks_dict(total), "positions": per_position}


@router.post("/scenarios")
async def scenario_grid(req: ScenarioIn) -> dict:
    """Revalue the book across a ΔS × Δσ × Δt grid (vectorised, ADR-006)."""
    book = [
        BookPosition(
            strike=p.strike,
            expiry=p.expiry,
            option_type=OptionType(p.option_type),
            quantity=p.quantity,
            vol=p.vol,
        )
        for p in req.positions
    ]
    grid = ScenarioGrid.regular(
        n_spot=req.n_spot,
        spot_width=req.spot_width,
        n_vol=req.n_vol,
        vol_width=req.vol_width,
        n_time=req.n_time,
        max_days=req.max_days,
    )
    started = time.perf_counter()
    result = run_scenario_grid(book, req.spot, req.rate, grid, req.dividend_yield)
    elapsed_ms = (time.perf_counter() - started) * 1000.0
    worst_pnl, worst_ds, worst_dv, worst_dt = result.worst
    best_pnl, best_ds, best_dv, best_dt = result.best
    return {
        "n_cells": grid.size,
        "elapsed_ms": elapsed_ms,
        "base_value": result.base_value,
        "worst": {
            "pnl": worst_pnl,
            "spot_shift": worst_ds,
            "vol_shift": worst_dv,
            "time_shift": worst_dt,
        },
        "best": {
            "pnl": best_pnl,
            "spot_shift": best_ds,
            "vol_shift": best_dv,
            "time_shift": best_dt,
        },
        "pnl_cube": np.asarray(result.pnl).tolist(),
        "spot_shifts": np.asarray(grid.spot_shifts).tolist(),
        "vol_shifts": np.asarray(grid.vol_shifts).tolist(),
        "time_shifts": np.asarray(grid.time_shifts).tolist(),
    }


@router.post("/hedge/decide")
async def hedge_decide(req: HedgeIn) -> dict:
    """Whalley-Wilmott band hedge decision (ADR-007)."""
    hedger = DeltaHedger(
        underlying_symbol=req.underlying_symbol,
        band_params=BandParams(
            proportional_cost=req.proportional_cost, risk_aversion=req.risk_aversion
        ),
        scalping_params=ScalpingParams(
            band_params=BandParams(
                proportional_cost=req.proportional_cost, risk_aversion=req.risk_aversion
            )
        ),
    )
    decision = hedger.decide(
        portfolio_delta=req.portfolio_delta,
        gamma=req.gamma,
        spot=req.spot,
        realized_vol=req.realized_vol,
        implied_vol=req.implied_vol,
    )
    return {
        "action": decision.action,
        "order": None
        if decision.order is None
        else {
            "symbol": decision.order.symbol,
            "quantity": decision.order.quantity,
            "price": decision.order.price,
        },
        "portfolio_delta": decision.portfolio_delta,
        "band_half_width": decision.band_half_width,
        "band_scale": decision.band_scale,
        "rationale": decision.rationale,
        "confidence": decision.confidence,
    }


@router.post("/risk/review")
async def risk_review(req: RiskReviewIn) -> dict:
    """Fail-closed pre-trade review (ADR-008)."""
    try:
        limits = RiskLimits(**req.limits)
    except TypeError as exc:
        raise HTTPException(status_code=422, detail=f"bad limits: {exc}") from exc
    ctx = RiskContext(
        portfolio=Portfolio(
            equity=req.equity,
            high_water_mark=req.high_water_mark,
            margin_available=req.margin_available,
        ),
        portfolio_greeks=Greeks(**req.portfolio_greeks),
        order_greeks=Greeks(**req.order_greeks),
        margin_required=req.margin_required,
        spot=req.spot,
    )
    order = Order(symbol=req.order.symbol, quantity=req.order.quantity, price=req.order.price)
    decision = RiskEngine(limits).review(order, ctx)
    return {
        "verdict": decision.verdict,
        "adjusted_order": None
        if decision.adjusted_order is None
        else {
            "symbol": decision.adjusted_order.symbol,
            "quantity": decision.adjusted_order.quantity,
            "price": decision.adjusted_order.price,
        },
        "checks": [
            {
                "check": r.check_name,
                "verdict": r.verdict,
                "reason": r.reason,
                "allowed_quantity": r.allowed_quantity,
            }
            for r in decision.results
        ],
        "correlation_id": decision.correlation_id,
    }

"""Pydantic response models for portfolio REST endpoints."""

from __future__ import annotations

from pydantic import BaseModel, Field


class PortfolioGreeksOut(BaseModel):
    delta: float = 0.0
    gamma: float = 0.0
    vega: float = 0.0
    theta: float = 0.0
    rho: float = 0.0
    vanna: float = 0.0
    volga: float = 0.0


class PortfolioPositionOut(BaseModel):
    instrument_key: str = ""
    trading_symbol: str = ""
    exchange: str = ""
    product: str = ""
    quantity: int = 0
    buy_price: float = 0.0
    sell_price: float = 0.0
    last_price: float = 0.0
    pnl: float = 0.0
    option_type: str | None = None
    strike_price: float | None = None
    expiry: str | None = None
    greeks: PortfolioGreeksOut | None = None


class PortfolioSummaryOut(BaseModel):
    total_positions: int = 0
    core_positions: int = 0
    total_pnl: float = 0.0
    equity: float = 0.0
    aggregate_greeks: PortfolioGreeksOut = Field(default_factory=PortfolioGreeksOut)
    synced: bool = False


class PortfolioSyncStatusOut(BaseModel):
    running: bool = False
    last_sync_ts: float | None = None
    n_syncs: int = 0
    n_failures: int = 0
    position_count: int = 0


class PortfolioSignalOut(BaseModel):
    trading_symbol: str
    option_type: str | None = None
    strike_price: float | None = None
    expiry: str | None = None
    quantity: int = 0
    entry_price: float = 0.0
    current_price: float = 0.0
    pnl: float = 0.0
    pnl_pct: float = 0.0
    moneyness: str = "unknown"
    days_to_expiry: int | None = None
    greeks: PortfolioGreeksOut | None = None


class HoldingOut(BaseModel):
    instrument_key: str = ""
    trading_symbol: str = ""
    exchange: str = ""
    quantity: int = 0
    average_price: float = 0.0
    last_price: float = 0.0
    pnl: float = 0.0
    day_change: float = 0.0
    day_change_percentage: float = 0.0


class OrderOut(BaseModel):
    order_id: str = ""
    trading_symbol: str = ""
    exchange: str = ""
    order_type: str = ""
    transaction_type: str = ""
    quantity: int = 0
    price: float = 0.0
    trigger_price: float = 0.0
    status: str = ""
    filled_quantity: int = 0
    average_price: float = 0.0
    placed_at: str = ""
    product: str = ""

# src/options_trading/models/market_data.py
"""
Market data models for options trading platform.
Comprehensive data structures for options, Greeks, and volatility analysis.
"""

from datetime import datetime, date
from decimal import Decimal
from typing import Dict, List, Optional, Any
from pydantic import BaseModel, Field, validator

class GreeksSnapshot(BaseModel):
    """Greeks values for an option"""
    delta: Optional[Decimal] = None
    gamma: Optional[Decimal] = None  
    theta: Optional[Decimal] = None
    vega: Optional[Decimal] = None
    rho: Optional[Decimal] = None
    timestamp: datetime = Field(default_factory=datetime.now)

class OptionData(BaseModel):
    """Individual option contract data"""
    strike: Decimal
    option_type: str  # CE or PE
    last_price: Decimal
    bid: Decimal
    ask: Decimal
    volume: int = 0
    open_interest: int = 0
    implied_volatility: Optional[Decimal] = None
    greeks: Optional[GreeksSnapshot] = None
    last_updated: datetime = Field(default_factory=datetime.now)
    
    @validator('option_type')
    def validate_option_type(cls, v):
        if v not in ['CE', 'PE', 'CALL', 'PUT']:
            raise ValueError('option_type must be CE, PE, CALL, or PUT')
        return v

class OptionChain(BaseModel):
    """Complete option chain for a symbol"""
    symbol: str
    spot_price: Decimal
    expiry_date: str
    call_options: List[OptionData] = Field(default_factory=list)
    put_options: List[OptionData] = Field(default_factory=list)
    timestamp: datetime = Field(default_factory=datetime.now)
    
    @validator('expiry_date')
    def validate_expiry_date(cls, v):
        try:
            datetime.strptime(v, "%Y-%m-%d")
            return v
        except ValueError:
            raise ValueError('expiry_date must be in YYYY-MM-DD format')

class VolatilitySurfacePoint(BaseModel):
    """Single point on volatility surface"""
    strike: Decimal
    expiry: str
    implied_volatility: float
    time_to_expiry: float
    moneyness: float

class VolatilitySurface(BaseModel):
    """3D implied volatility surface"""
    symbol: str
    spot_price: Decimal
    surface_data: List[VolatilitySurfacePoint] = Field(default_factory=list)
    timestamp: datetime = Field(default_factory=datetime.now)

class MarketDataRequest(BaseModel):
    """Request for market data"""
    symbols: List[str]
    data_types: List[str] = Field(default_factory=lambda: ["price", "greeks", "iv"])
    expiry_dates: Optional[List[str]] = None
    strike_range: Optional[int] = 10
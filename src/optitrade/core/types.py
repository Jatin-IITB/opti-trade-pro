"""Shared domain types for the OptiTrade quant core.

Conventions (ADR-003):
- Time to expiry is a year fraction (ACT/365), never a date.
- Rates and dividend yields are continuously compounded.
- Volatility is annualised and expressed as a decimal (0.20 == 20%).
- Quantities are signed: positive = long, negative = short.
- Vega/rho are per unit vol / unit rate (NOT per 1%); theta is per year.
  Presentation layers rescale for display.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum


class OptionType(str, Enum):  # noqa: UP042 — (str, Enum) keeps plain-str JSON round-trips explicit
    CALL = "call"
    PUT = "put"


@dataclass(frozen=True, slots=True)
class OptionQuote:
    """A single market quote used for surface construction."""

    strike: float
    expiry: float  # year fraction
    option_type: OptionType
    mid: float
    bid: float | None = None
    ask: float | None = None

    def __post_init__(self) -> None:
        if self.strike <= 0:
            raise ValueError(f"strike must be positive, got {self.strike}")
        if self.expiry <= 0:
            raise ValueError(f"expiry must be positive, got {self.expiry}")
        if self.mid <= 0:
            raise ValueError(f"mid must be positive, got {self.mid}")


@dataclass(frozen=True, slots=True)
class MarketSnapshot:
    """Point-in-time market state consumed by the vol-surface engine."""

    spot: float
    rate: float  # continuously compounded risk-free rate
    timestamp: float  # unix epoch seconds
    quotes: tuple[OptionQuote, ...] = ()
    dividend_yield: float = 0.0

    def __post_init__(self) -> None:
        if self.spot <= 0:
            raise ValueError(f"spot must be positive, got {self.spot}")


@dataclass(frozen=True, slots=True)
class Greeks:
    """First- and second-order sensitivities of a position or portfolio.

    Values are per one unit of underlying exposure; scale by signed quantity
    (and lot size) to aggregate.
    """

    delta: float = 0.0
    gamma: float = 0.0
    vega: float = 0.0
    theta: float = 0.0
    rho: float = 0.0
    vanna: float = 0.0
    volga: float = 0.0

    def scaled(self, factor: float) -> Greeks:
        return Greeks(
            delta=self.delta * factor,
            gamma=self.gamma * factor,
            vega=self.vega * factor,
            theta=self.theta * factor,
            rho=self.rho * factor,
            vanna=self.vanna * factor,
            volga=self.volga * factor,
        )

    def __add__(self, other: Greeks) -> Greeks:
        return Greeks(
            delta=self.delta + other.delta,
            gamma=self.gamma + other.gamma,
            vega=self.vega + other.vega,
            theta=self.theta + other.theta,
            rho=self.rho + other.rho,
            vanna=self.vanna + other.vanna,
            volga=self.volga + other.volga,
        )


@dataclass(frozen=True, slots=True)
class OptionContract:
    """A tradeable option instrument."""

    symbol: str
    strike: float
    expiry: float  # year fraction at time of construction
    option_type: OptionType
    lot_size: int = 1


@dataclass(frozen=True, slots=True)
class Position:
    """Signed holding of a contract."""

    contract: OptionContract
    quantity: float  # signed number of contracts
    entry_price: float


@dataclass(frozen=True, slots=True)
class Order:
    """A proposed trade submitted to the risk engine before execution.

    ``contract is None`` means an order in the underlying itself (a hedge).
    """

    symbol: str
    quantity: float  # signed
    price: float  # reference price for notional/margin computation
    contract: OptionContract | None = None

    @property
    def notional(self) -> float:
        lot = self.contract.lot_size if self.contract is not None else 1
        return abs(self.quantity) * self.price * lot


@dataclass(frozen=True, slots=True)
class Portfolio:
    """Positions plus the account state the risk engine needs."""

    positions: tuple[Position, ...] = ()
    cash: float = 0.0
    equity: float = 0.0
    high_water_mark: float = 0.0
    margin_available: float = 0.0

    def with_equity(self, equity: float) -> Portfolio:
        return replace(self, equity=equity, high_water_mark=max(self.high_water_mark, equity))

    @property
    def drawdown(self) -> float:
        """Fractional drawdown from the high-water mark (0 when at the peak)."""
        if self.high_water_mark <= 0:
            return 0.0
        return max(0.0, 1.0 - self.equity / self.high_water_mark)

    @property
    def gross_notional(self) -> float:
        return sum(abs(p.quantity) * p.entry_price * p.contract.lot_size for p in self.positions)


__all__ = [
    "Greeks",
    "MarketSnapshot",
    "OptionContract",
    "OptionQuote",
    "OptionType",
    "Order",
    "Portfolio",
    "Position",
]

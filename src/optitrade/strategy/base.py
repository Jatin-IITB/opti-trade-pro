"""Strategy contract shared by the backtester and the daily desk cycle.

A strategy is a pure decision function over what the desk knows on a given
day. It emits orders and a numbered thesis; it never executes anything —
execution belongs to the debate panel + risk engine + (paper) fills
downstream (ADR-008/010/015).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Literal, Protocol, runtime_checkable

from optitrade.core import MarketSnapshot, Order, Position


@runtime_checkable
class VolLookup(Protocol):
    """Minimal surface interface a strategy may consult (duck-typed by
    VolSurface / SABRSurface / ESSVISurface)."""

    def vol(self, strike: object, expiry: object) -> object: ...


@dataclass(frozen=True)
class MarketDay:
    """Everything a strategy sees for one decision point.

    ``features`` carries derived signals (e.g. ``atm_iv``, ``term_slope``,
    ``skew``, ``vrp``) so strategies stay decoupled from how features are
    computed; producers document the keys they populate.
    """

    timestamp: float
    spot: float
    rate: float
    realized_vol: float
    snapshot: MarketSnapshot | None = None
    surface: VolLookup | None = None
    features: Mapping[str, float] = field(default_factory=dict)


@dataclass(frozen=True)
class StrategyDecision:
    action: Literal["enter", "exit", "hold"]
    orders: tuple[Order, ...] = ()
    thesis: str = ""
    expected_edge: float = 0.0  # currency, over the trade's life
    estimated_cost: float = 0.0  # currency, round-trip transaction cost
    diagnostics: Mapping[str, float] = field(default_factory=dict)


@runtime_checkable
class Strategy(Protocol):
    @property
    def name(self) -> str: ...

    def decide(self, day: MarketDay, open_positions: tuple[Position, ...]) -> StrategyDecision: ...


__all__ = ["MarketDay", "Strategy", "StrategyDecision", "VolLookup"]

"""Price the synced book against live market data.

Single source of truth for turning real Upstox positions into analytics
inputs. Every consumer (portfolio summary, scenario grid, risk dashboard)
prices the book the same way, so they cannot disagree about what the user's
delta is.

Two rules the whole module is built around:

- **Invert IV from the current mark, never the entry fill.** An entry-day IV
  describes a market that no longer exists; Greeks derived from it are wrong
  in a way that looks plausible.
- **Fail closed per leg.** A leg whose IV cannot be inverted is *excluded* and
  counted, never assigned a default vol. A fabricated Greek is worse than a
  missing one (ADR-008), and ``n_excluded`` lets the UI say how much of the
  book is actually represented.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from optitrade.core.types import Greeks, OptionContract, Portfolio
from optitrade.greeks.scenario import BookPosition
from optitrade.pricing import bs_greeks_at
from optitrade.pricing.implied_vol import implied_vol
from optitrade.risk import RiskLimits

from ..config.settings import settings

logger = logging.getLogger(__name__)


def risk_limits_from_settings() -> RiskLimits:
    """Build the book's risk limits from configuration.

    Exists so no flow hardcodes a limit: the dashboard, the pre-trade engine
    and any future desk loop all read the same numbers (CLAUDE.md rule 2).
    """
    return RiskLimits(
        max_abs_delta=settings.risk_max_abs_delta,
        max_abs_gamma=settings.risk_max_abs_gamma,
        max_abs_vega=settings.risk_max_abs_vega,
        max_drawdown=settings.risk_max_drawdown,
        max_concentration=settings.risk_max_concentration,
        margin_buffer=settings.risk_margin_buffer,
    )


@dataclass(frozen=True)
class PricedLeg:
    """One book leg with its inverted vol and Greeks at the live spot."""

    contract: OptionContract
    quantity: float
    mark: float
    iv: float
    greeks: Greeks


@dataclass(frozen=True)
class PricedBook:
    """The book as priced at one spot, plus what could not be priced."""

    legs: tuple[PricedLeg, ...]
    spot: float
    rate: float
    n_excluded: int = 0

    @property
    def aggregate_greeks(self) -> Greeks:
        """Position-weighted sum of leg Greeks."""
        agg = Greeks()
        for leg in self.legs:
            agg = agg + leg.greeks.scaled(leg.quantity)
        return agg

    @property
    def n_priced(self) -> int:
        return len(self.legs)

    def to_scenario_book(self) -> list[BookPosition]:
        """Flatten to the denormalised shape ``run_scenario_grid`` expects."""
        return [
            BookPosition(
                strike=leg.contract.strike,
                expiry=leg.contract.expiry,
                option_type=leg.contract.option_type,
                quantity=leg.quantity,
                vol=leg.iv,
            )
            for leg in self.legs
        ]


def price_book(
    portfolio: Portfolio,
    marks: dict[str, float],
    spot: float,
    rate: float | None = None,
) -> PricedBook:
    """Price ``portfolio`` at ``spot`` using each leg's current ``mark``.

    ``marks`` is keyed by ``OptionContract.symbol`` (which ``to_core_portfolio``
    sets to the Upstox trading symbol). A leg with no mark, an unusable mark, or
    an IV that will not invert is excluded and counted in ``n_excluded``.
    """
    if rate is None:
        rate = float(settings.risk_free_rate)
    if spot <= 0:
        raise ValueError(f"spot must be positive to price a book, got {spot}")

    legs: list[PricedLeg] = []
    n_excluded = 0

    for pos in portfolio.positions:
        contract = pos.contract
        if contract.expiry <= 0:
            n_excluded += 1
            logger.debug("Leg %s has expired; excluded from book", contract.symbol)
            continue

        mark = marks.get(contract.symbol)
        if mark is None or mark <= 0:
            n_excluded += 1
            logger.debug("No usable mark for %s; excluded from book", contract.symbol)
            continue

        try:
            iv = implied_vol(
                mark, spot, contract.strike, contract.expiry, rate, contract.option_type
            )
        except Exception:
            n_excluded += 1
            logger.debug("IV inversion failed for %s; excluded", contract.symbol, exc_info=True)
            continue

        if iv <= 0:
            n_excluded += 1
            logger.debug("Non-positive IV for %s; excluded", contract.symbol)
            continue

        try:
            greeks = bs_greeks_at(
                spot, contract.strike, contract.expiry, rate, iv, contract.option_type
            )
        except Exception:
            n_excluded += 1
            logger.debug("Greeks failed for %s; excluded", contract.symbol, exc_info=True)
            continue

        legs.append(
            PricedLeg(
                contract=contract,
                quantity=pos.quantity,
                mark=mark,
                iv=iv,
                greeks=greeks,
            )
        )

    if n_excluded:
        logger.info(
            "Book priced at spot %.1f: %d legs priced, %d excluded",
            spot,
            len(legs),
            n_excluded,
        )
    return PricedBook(legs=tuple(legs), spot=spot, rate=rate, n_excluded=n_excluded)


__all__ = ["PricedBook", "PricedLeg", "price_book", "risk_limits_from_settings"]

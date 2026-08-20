"""Variance-risk-premium harvesting strategy: short vol when IV runs rich.

The variance risk premium (VRP) is the persistent gap between option-implied
vol and subsequently realized vol; selling delta-hedged optionality collects
it, paying for the occasional realized-vol spike (Carr & Wu 2009). The
strategy is a pure decision function over :class:`~optitrade.strategy.base.
MarketDay` — it emits orders and a numbered thesis and never executes
anything (ADR-008/010/015). Delta hedging, risk review, fills and journaling
belong to the harness downstream.

Features consumed (all optional keys documented here):
- ``atm_iv``: ATM implied vol for the configured tenor. Fallback when absent:
  ``day.surface.vol(F, T)`` at the forward ``F = spot * exp(rate * T)``,
  ``T = tenor_days / 365``. With neither, the strategy holds (no signal,
  no trade — fail-safe).
- ``term_slope``: far-minus-near ATM vol; the entry is skipped when
  ``max_term_slope`` is configured and the feature exceeds it. Filter is
  bypassed when the threshold is ``None`` or the feature is absent.
- ``skew_25d``: 25-delta put vol minus 25-delta call vol; entry requires
  ``skew_25d >= min_skew`` when configured, same bypass rule.
- ``days_in_trade``: age of the open position in days, maintained by the
  harness; drives the ``max_days_in_trade`` time exit. Absent means the age
  is unknown and only the signal exit applies.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal, SupportsFloat, cast

from scipy.stats import norm

from optitrade.core import Order, Position
from optitrade.core.types import OptionContract, OptionType
from optitrade.pricing.black_scholes import bs_greeks_at, bs_price
from optitrade.strategy.base import MarketDay, StrategyDecision, VolLookup
from optitrade.strategy.costs import IndianOptionsCostModel

_DAYS_PER_YEAR = 365.0  # ACT/365 per ADR-003
_MIN_EXIT_EXPIRY = 1e-6  # year fraction floor when pricing near-expiry exits


@dataclass(frozen=True, slots=True)
class VRPConfig:
    """Typed configuration for :class:`VRPStrategy` — no magic numbers in flow.

    Attributes:
        entry_vrp_min: Minimum ``atm_iv - realized_vol`` (vol points, decimal)
            to open a short-vol structure.
        exit_vrp_max: Buy the structure back once VRP falls to this level.
        tenor_days: Target option tenor in calendar days (ACT/365).
        structure: ``"straddle"`` (both legs at the forward-ATM strike) or
            ``"strangle"`` (legs at ``strangle_delta`` strikes).
        strangle_delta: Absolute delta of each strangle leg, in (0, 0.5).
        quantity: Contracts sold per leg (positive; orders carry the minus).
        max_term_slope: Entry regime filter on the ``term_slope`` feature
            (skip entries in steep contango of vol); ``None`` disables.
        min_skew: Entry regime filter on the ``skew_25d`` feature (require
            downside skew to be paid for the crash risk); ``None`` disables.
        max_days_in_trade: Time-stop in days via the ``days_in_trade``
            feature; ``None`` disables.
    """

    entry_vrp_min: float = 0.03
    exit_vrp_max: float = 0.0
    tenor_days: int = 30
    structure: Literal["straddle", "strangle"] = "straddle"
    strangle_delta: float = 0.25
    quantity: float = 1.0
    max_term_slope: float | None = None
    min_skew: float | None = None
    max_days_in_trade: int | None = None

    def __post_init__(self) -> None:
        if self.entry_vrp_min <= self.exit_vrp_max:
            raise ValueError(
                f"entry_vrp_min {self.entry_vrp_min} must exceed exit_vrp_max "
                f"{self.exit_vrp_max} (otherwise the strategy churns)"
            )
        if self.tenor_days < 1:
            raise ValueError(f"tenor_days must be >= 1, got {self.tenor_days}")
        if self.structure not in ("straddle", "strangle"):
            raise ValueError(f"structure must be 'straddle' or 'strangle', got {self.structure}")
        if not 0.0 < self.strangle_delta < 0.5:
            raise ValueError(f"strangle_delta must be in (0, 0.5), got {self.strangle_delta}")
        if self.quantity <= 0.0:
            raise ValueError(f"quantity must be positive, got {self.quantity}")
        if self.max_days_in_trade is not None and self.max_days_in_trade < 1:
            raise ValueError(f"max_days_in_trade must be >= 1, got {self.max_days_in_trade}")


def strike_from_delta(
    forward: float,
    expiry: float,
    vol: float,
    delta: float,
    option_type: OptionType | str,
) -> float:
    """Strike whose Black-Scholes delta magnitude equals ``delta``.

    Analytic inversion of ``d1``: for a call ``d1 = Phi^{-1}(delta)`` and

        K = F * exp(0.5 * vol^2 * T - d1 * vol * sqrt(T));

    a put uses ``d1 = -Phi^{-1}(delta)``. Documented approximation: the
    inversion is done at one fixed vol (typically ATM) rather than iterating
    to the smile-consistent vol at the solved strike, and delta is the
    undiscounted spot delta (no rate/dividend discounting). Adequate for
    picking wing strikes; not for delta hedging.
    """
    if not 0.0 < delta < 1.0:
        raise ValueError(f"delta must be in (0, 1), got {delta}")
    if forward <= 0.0 or expiry <= 0.0 or vol <= 0.0:
        raise ValueError(
            f"forward, expiry and vol must be positive, got {forward}, {expiry}, {vol}"
        )
    d1 = float(norm.ppf(delta))
    if OptionType(option_type) is OptionType.PUT:
        d1 = -d1
    return forward * math.exp(0.5 * vol * vol * expiry - d1 * vol * math.sqrt(expiry))


def _lookup_vol(surface: VolLookup, strike: float, expiry: float) -> float:
    return float(cast("SupportsFloat", surface.vol(strike, expiry)))


class VRPStrategy:
    """Short-vol VRP harvester implementing the :class:`Strategy` protocol.

    Stateless by design: ``decide`` is a pure function of the day and the
    open positions, so one instance can serve backtests and the live desk.
    """

    def __init__(
        self,
        config: VRPConfig | None = None,
        cost_model: IndianOptionsCostModel | None = None,
        lot_size: int = 1,
    ) -> None:
        if lot_size < 1:
            raise ValueError(f"lot_size must be >= 1, got {lot_size}")
        self._config = config if config is not None else VRPConfig()
        self._cost_model = cost_model if cost_model is not None else IndianOptionsCostModel()
        self._lot_size = lot_size

    @property
    def name(self) -> str:
        return f"vrp_{self._config.structure}"

    @property
    def config(self) -> VRPConfig:
        return self._config

    def decide(self, day: MarketDay, open_positions: tuple[Position, ...]) -> StrategyDecision:
        tenor_years = self._config.tenor_days / _DAYS_PER_YEAR
        atm_iv = self._atm_iv(day, tenor_years)
        vrp = None if atm_iv is None else atm_iv - day.realized_vol
        if open_positions:
            return self._manage_open(day, open_positions, atm_iv, vrp)
        return self._consider_entry(day, atm_iv, vrp, tenor_years)

    # -- signal ----------------------------------------------------------

    def _atm_iv(self, day: MarketDay, tenor_years: float) -> float | None:
        """ATM IV from the ``atm_iv`` feature, else the surface at forward-ATM."""
        feature = day.features.get("atm_iv")
        if feature is not None:
            return float(feature)
        if day.surface is not None:
            forward = day.spot * math.exp(day.rate * tenor_years)
            return _lookup_vol(day.surface, forward, tenor_years)
        return None

    def _regime_block(self, day: MarketDay) -> str | None:
        """Reason string when a configured regime filter blocks the entry."""
        cfg = self._config
        if cfg.max_term_slope is not None:
            slope = day.features.get("term_slope")
            if slope is not None and slope > cfg.max_term_slope:
                return f"term_slope {slope:.4f} > max_term_slope {cfg.max_term_slope:.4f}"
        if cfg.min_skew is not None:
            skew = day.features.get("skew_25d")
            if skew is not None and skew < cfg.min_skew:
                return f"skew_25d {skew:.4f} < min_skew {cfg.min_skew:.4f}"
        return None

    # -- entry -----------------------------------------------------------

    def _consider_entry(
        self, day: MarketDay, atm_iv: float | None, vrp: float | None, tenor_years: float
    ) -> StrategyDecision:
        cfg = self._config
        if atm_iv is None or vrp is None:
            return StrategyDecision(
                action="hold",
                thesis="hold: no ATM IV available (no atm_iv feature and no surface); "
                "no signal, no trade",
            )
        if vrp < cfg.entry_vrp_min:
            return StrategyDecision(
                action="hold",
                thesis=(
                    f"hold: VRP {vrp:.4f} (atm_iv {atm_iv:.4f} - rv {day.realized_vol:.4f}) "
                    f"below entry threshold {cfg.entry_vrp_min:.4f}"
                ),
                diagnostics={"vrp": vrp, "atm_iv": atm_iv, "realized_vol": day.realized_vol},
            )
        block = self._regime_block(day)
        if block is not None:
            return StrategyDecision(
                action="hold",
                thesis=f"hold: VRP {vrp:.4f} clears entry but regime filter blocks: {block}",
                diagnostics={"vrp": vrp, "atm_iv": atm_iv, "realized_vol": day.realized_vol},
            )

        forward = day.spot * math.exp(day.rate * tenor_years)
        if cfg.structure == "straddle":
            legs = ((OptionType.CALL, forward), (OptionType.PUT, forward))
        else:
            legs = (
                (
                    OptionType.CALL,
                    strike_from_delta(
                        forward, tenor_years, atm_iv, cfg.strangle_delta, OptionType.CALL
                    ),
                ),
                (
                    OptionType.PUT,
                    strike_from_delta(
                        forward, tenor_years, atm_iv, cfg.strangle_delta, OptionType.PUT
                    ),
                ),
            )

        orders: list[Order] = []
        vega_structure = 0.0
        estimated_cost = 0.0
        for option_type, strike in legs:
            leg_vol = (
                _lookup_vol(day.surface, strike, tenor_years) if day.surface is not None else atm_iv
            )
            price = float(bs_price(day.spot, strike, tenor_years, day.rate, leg_vol, option_type))
            symbol = self._leg_symbol(option_type, strike)
            contract = OptionContract(
                symbol=symbol,
                strike=strike,
                expiry=tenor_years,
                option_type=option_type,
                lot_size=self._lot_size,
            )
            orders.append(
                Order(symbol=symbol, quantity=-cfg.quantity, price=price, contract=contract)
            )
            vega_structure += (
                bs_greeks_at(day.spot, strike, tenor_years, day.rate, leg_vol, option_type).vega
                * self._lot_size
            )
            # Round-trip cost estimate approximates the exit at the entry
            # price — a first-order estimate, consistent with expected_edge.
            estimated_cost += self._cost_model.round_trip(
                price, price, -cfg.quantity, self._lot_size
            ).total

        # First-order sizing of the premium richness: a short-vol structure
        # collects ~ vega * (IV - RV) if implied converges to realized over
        # the trade's life (dP/dsigma * dsigma; gamma path effects ignored).
        expected_edge = vega_structure * vrp * cfg.quantity
        strikes_txt = "/".join(f"{k:.2f}" for _, k in legs)
        return StrategyDecision(
            action="enter",
            orders=tuple(orders),
            thesis=(
                f"enter: VRP {vrp:.4f} (atm_iv {atm_iv:.4f} - rv {day.realized_vol:.4f}) "
                f">= entry threshold {cfg.entry_vrp_min:.4f}; selling {cfg.quantity:g}x "
                f"{cfg.structure} K={strikes_txt} T={cfg.tenor_days}d; expected edge "
                f"{expected_edge:.2f} vs estimated round-trip cost {estimated_cost:.2f}"
            ),
            expected_edge=expected_edge,
            estimated_cost=estimated_cost,
            diagnostics={
                "vrp": vrp,
                "atm_iv": atm_iv,
                "realized_vol": day.realized_vol,
                "forward": forward,
                "vega_structure": vega_structure,
            },
        )

    # -- exit / hold ------------------------------------------------------

    def _manage_open(
        self,
        day: MarketDay,
        open_positions: tuple[Position, ...],
        atm_iv: float | None,
        vrp: float | None,
    ) -> StrategyDecision:
        cfg = self._config
        days_in_trade = day.features.get("days_in_trade")
        signal_exit = vrp is not None and vrp <= cfg.exit_vrp_max
        time_exit = (
            cfg.max_days_in_trade is not None
            and days_in_trade is not None
            and days_in_trade > cfg.max_days_in_trade
        )
        if not signal_exit and not time_exit:
            if vrp is None:
                thesis = "hold: position open but no ATM IV available; holding on no signal"
            else:
                thesis = (
                    f"hold: VRP {vrp:.4f} still above exit threshold "
                    f"{cfg.exit_vrp_max:.4f}; keeping the short-vol position"
                )
            diagnostics = {} if vrp is None else {"vrp": vrp}
            return StrategyDecision(action="hold", thesis=thesis, diagnostics=diagnostics)

        orders = tuple(
            self._buy_back_order(day, position, days_in_trade) for position in open_positions
        )
        if time_exit and not signal_exit:
            reason = (
                f"days_in_trade {float(days_in_trade or 0):.0f} exceeded "
                f"max_days_in_trade {cfg.max_days_in_trade}"
            )
        else:
            reason = f"VRP {float(vrp or 0):.4f} <= exit threshold {cfg.exit_vrp_max:.4f}"
        return StrategyDecision(
            action="exit",
            orders=orders,
            thesis=f"exit: {reason}; buying back {len(orders)} leg(s)",
            diagnostics={} if vrp is None else {"vrp": vrp},
        )

    def _buy_back_order(
        self, day: MarketDay, position: Position, days_in_trade: float | None
    ) -> Order:
        contract = position.contract
        elapsed_years = (days_in_trade or 0.0) / _DAYS_PER_YEAR
        remaining = max(contract.expiry - elapsed_years, _MIN_EXIT_EXPIRY)
        if day.surface is not None:
            vol = _lookup_vol(day.surface, contract.strike, remaining)
        else:
            feature = day.features.get("atm_iv")
            vol = float(feature) if feature is not None else day.realized_vol
        price = float(
            bs_price(day.spot, contract.strike, remaining, day.rate, vol, contract.option_type)
        )
        return Order(
            symbol=contract.symbol,
            quantity=-position.quantity,
            price=price,
            contract=contract,
        )

    def _leg_symbol(self, option_type: OptionType, strike: float) -> str:
        cp = "C" if option_type is OptionType.CALL else "P"
        return f"VRP-{cp}-{strike:.2f}-{self._config.tenor_days}D"


__all__ = ["VRPConfig", "VRPStrategy", "strike_from_delta"]

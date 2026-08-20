"""Band-based delta hedger: turns portfolio delta into hold/rebalance decisions.

The hedger is stateless -- ``decide`` is a pure function of the inputs, so the
caller (simulator, live engine) owns positions and sequencing. The
no-transaction band is Whalley-Wilmott (1997), optionally scaled by the
gamma-scalping RV/IV rule from :mod:`optitrade.hedging.gamma_scalper`.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal

from optitrade.core import Order
from optitrade.hedging.band import BandParams, whalley_wilmott_half_width
from optitrade.hedging.gamma_scalper import ScalpingParams, scalping_band_scale


@dataclass(frozen=True, slots=True)
class HedgeDecision:
    """Outcome of one hedging check, journal-ready.

    Attributes:
        action: ``"hold"`` or ``"rebalance"``.
        order: Underlying hedge order (``contract=None``) when rebalancing,
            else ``None``.
        portfolio_delta: The delta the decision was made on.
        band_half_width: Effective (scaled, clamped) band half-width used.
        band_scale: Gamma-scalping multiplier applied to the raw band (1.0
            when no vol information was supplied).
        rationale: One plain-English sentence with the numbers behind the call.
        confidence: How far outside the band the delta sits, as a fraction of
            the half-width, capped to [0, 1] (0 inside the band).
    """

    action: Literal["hold", "rebalance"]
    order: Order | None
    portfolio_delta: float
    band_half_width: float
    band_scale: float
    rationale: str
    confidence: float


class DeltaHedger:
    """Decides underlying hedge trades from portfolio delta and a WW band.

    Stateless by design: every call to :meth:`decide` is independent, so the
    same instance can serve many books or simulation paths concurrently.
    """

    def __init__(
        self,
        underlying_symbol: str,
        band_params: BandParams,
        scalping_params: ScalpingParams | None = None,
    ) -> None:
        self.underlying_symbol = underlying_symbol
        self.band_params = band_params
        self.scalping_params = scalping_params

    def decide(
        self,
        portfolio_delta: float,
        gamma: float,
        spot: float,
        realized_vol: float | None = None,
        implied_vol: float | None = None,
    ) -> HedgeDecision:
        """Hold or rebalance based on the Whalley-Wilmott band.

        The raw half-width is scaled by the gamma-scalping RV/IV rule when
        scalping parameters are configured and both vols are supplied, then
        re-clamped to the band's [min, max] limits. A rebalance targets flat
        delta: order quantity is ``-portfolio_delta`` rounded toward zero to
        whole shares (a sub-share residual is deliberately left unhedged; if
        rounding yields zero shares the decision degrades to a hold).
        """
        raw_half_width = whalley_wilmott_half_width(gamma, spot, self.band_params)
        scale = 1.0
        if (
            self.scalping_params is not None
            and realized_vol is not None
            and implied_vol is not None
        ):
            scale = scalping_band_scale(realized_vol, implied_vol, self.scalping_params)
        # Re-clamp after scaling so the configured floors/caps stay invariant.
        half_width = min(
            max(raw_half_width * scale, self.band_params.min_half_width),
            self.band_params.max_half_width,
        )

        abs_delta = abs(portfolio_delta)
        if half_width > 0.0:
            confidence = min(1.0, max(0.0, (abs_delta - half_width) / half_width))
        else:
            confidence = 1.0 if abs_delta > 0.0 else 0.0

        if abs_delta <= half_width:
            return HedgeDecision(
                action="hold",
                order=None,
                portfolio_delta=portfolio_delta,
                band_half_width=half_width,
                band_scale=scale,
                rationale=(
                    f"Portfolio delta {portfolio_delta:.4f} is inside the no-trade band "
                    f"half-width {half_width:.4f} (scale {scale:.2f}), so holding."
                ),
                confidence=confidence,
            )

        hedge_quantity = float(math.trunc(-portfolio_delta))
        if hedge_quantity == 0.0:
            return HedgeDecision(
                action="hold",
                order=None,
                portfolio_delta=portfolio_delta,
                band_half_width=half_width,
                band_scale=scale,
                rationale=(
                    f"Portfolio delta {portfolio_delta:.4f} breaches the band half-width "
                    f"{half_width:.4f} (scale {scale:.2f}) but rounds to zero whole shares, "
                    f"so holding."
                ),
                confidence=confidence,
            )

        side = "buying" if hedge_quantity > 0 else "selling"
        return HedgeDecision(
            action="rebalance",
            order=Order(
                symbol=self.underlying_symbol,
                quantity=hedge_quantity,
                price=spot,
                contract=None,
            ),
            portfolio_delta=portfolio_delta,
            band_half_width=half_width,
            band_scale=scale,
            rationale=(
                f"Portfolio delta {portfolio_delta:.4f} breaches the no-trade band half-width "
                f"{half_width:.4f} (scale {scale:.2f}), so {side} {abs(hedge_quantity):.0f} "
                f"{self.underlying_symbol} at {spot:.2f} to restore delta neutrality."
            ),
            confidence=confidence,
        )


__all__ = ["DeltaHedger", "HedgeDecision"]

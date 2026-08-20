"""No-transaction band for delta hedging under proportional transaction costs.

Continuous rebalancing is infinitely expensive under proportional costs, while
rebalancing too rarely leaves unhedged delta and hedging-error variance.
Whalley & Wilmott (1997) resolve this trade-off asymptotically (small costs)
for a CARA investor: hold the hedge whenever portfolio delta is within a band
of half-width ``H`` around the target, and trade back to the band edge (here:
to the target) when it escapes.

Reference: A. E. Whalley & P. Wilmott (1997), "An asymptotic analysis of an
optimal hedging model for option pricing with transaction costs",
Mathematical Finance 7(3), 307-324.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True, slots=True)
class BandParams:
    """Parameters of the Whalley-Wilmott no-transaction band.

    Attributes:
        proportional_cost: Cost as a fraction of traded value (5bp = 5e-4).
            Per share of underlying the cash cost is ``proportional_cost * S``.
        risk_aversion: CARA risk-aversion coefficient lambda (> 0). Higher
            aversion tolerates less hedging error, so the band tightens.
        min_half_width: Floor on the half-width, in delta units.
        max_half_width: Cap on the half-width, in delta units.
    """

    proportional_cost: float
    risk_aversion: float
    min_half_width: float = 0.0
    max_half_width: float = np.inf

    def __post_init__(self) -> None:
        if self.proportional_cost < 0.0:
            raise ValueError(f"proportional_cost must be >= 0, got {self.proportional_cost}")
        if self.risk_aversion <= 0.0:
            raise ValueError(f"risk_aversion must be > 0, got {self.risk_aversion}")
        if self.min_half_width < 0.0:
            raise ValueError(f"min_half_width must be >= 0, got {self.min_half_width}")
        if self.max_half_width < self.min_half_width:
            raise ValueError(
                f"max_half_width {self.max_half_width} < min_half_width {self.min_half_width}"
            )


def whalley_wilmott_half_width(gamma: float, spot: float, params: BandParams) -> float:
    """Asymptotically optimal no-transaction band half-width (delta units).

    Implements the classical Whalley-Wilmott (1997) form

        H = (1.5 * k * S * Gamma**2 / lambda) ** (1/3)

    where ``k`` is the proportional cost (``k * S`` is the cash cost per share
    of underlying), ``S`` the spot, ``Gamma`` the position gamma and ``lambda``
    the risk aversion. The width optimises the trade-off between transaction
    costs (paid when rebalancing often, i.e. a narrow band) and the variance
    of the hedging error (accumulated when rebalancing rarely, i.e. a wide
    band); it is the leading-order optimum of a CARA utility expansion for
    small proportional costs.

    The width scales as ``|Gamma|**(2/3)``: high-gamma books drift out of
    hedge faster, so they are given a wider band rather than being churned.
    ``Gamma == 0`` returns ``min_half_width``; the result is always clamped to
    ``[min_half_width, max_half_width]``.

    Reference: Whalley & Wilmott (1997), Mathematical Finance 7(3), 307-324.
    """
    if spot <= 0.0:
        raise ValueError(f"spot must be positive, got {spot}")
    if gamma == 0.0:
        return params.min_half_width
    raw = (1.5 * params.proportional_cost * spot * gamma * gamma / params.risk_aversion) ** (
        1.0 / 3.0
    )
    return float(min(max(raw, params.min_half_width), params.max_half_width))


__all__ = ["BandParams", "whalley_wilmott_half_width"]

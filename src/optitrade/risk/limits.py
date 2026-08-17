"""Hard pre-trade risk limits.

Every cap is mandatory: explicit limits are a feature, not boilerplate. A
book without a stated delta cap does not have "no limit", it has an
unexamined one.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class RiskLimits:
    """Immutable limit set consumed by the pre-trade checks.

    ``max_drawdown`` and ``max_concentration`` are fractions in (0, 1];
    ``margin_buffer`` is a multiplier >= 1.0 applied to required margin
    (1.25 means "keep 25% spare margin").
    """

    max_abs_delta: float
    max_abs_gamma: float
    max_abs_vega: float
    max_drawdown: float
    max_concentration: float
    margin_buffer: float = 1.0

    def __post_init__(self) -> None:
        for cap_name in ("max_abs_delta", "max_abs_gamma", "max_abs_vega"):
            cap = getattr(self, cap_name)
            if cap <= 0:
                raise ValueError(f"{cap_name} must be positive, got {cap}")
        if not 0.0 < self.max_drawdown <= 1.0:
            raise ValueError(f"max_drawdown must be a fraction in (0, 1], got {self.max_drawdown}")
        if not 0.0 < self.max_concentration <= 1.0:
            raise ValueError(
                f"max_concentration must be a fraction in (0, 1], got {self.max_concentration}"
            )
        if self.margin_buffer < 1.0:
            raise ValueError(f"margin_buffer must be >= 1.0, got {self.margin_buffer}")


__all__ = ["RiskLimits"]

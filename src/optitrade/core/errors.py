"""Error hierarchy for the quant core.

Every optitrade-raised exception derives from :class:`OptiTradeError` so the
platform layer can catch the whole family at its boundary. Risk *rejections*
are decisions, not exceptions — see ``optitrade.risk`` (ADR-008).
"""

from __future__ import annotations


class OptiTradeError(Exception):
    """Base class for all quant-core errors."""


class NumericalError(OptiTradeError):
    """A numerical routine failed to converge or produced invalid output."""


class CalibrationError(OptiTradeError):
    """Model calibration (SABR, spline fit) failed or exceeded tolerance."""


class ArbitrageViolationError(OptiTradeError):
    """A constructed surface violates static no-arbitrage constraints."""


class JournalError(OptiTradeError):
    """The event journal could not be written or replayed."""


__all__ = [
    "ArbitrageViolationError",
    "CalibrationError",
    "JournalError",
    "NumericalError",
    "OptiTradeError",
]

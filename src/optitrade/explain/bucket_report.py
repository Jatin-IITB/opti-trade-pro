"""Expiry-bucketed exposure report: where the book's Greeks live on the curve.

Two books with identical aggregate Greeks can behave very differently — gamma
concentrated in the front week is a pin-risk problem, gamma in the back months
is a vega problem in disguise. Bucketing the quantity-scaled Greeks by time to
expiry makes that concentration visible.

Buckets partition ``(0, inf)`` as half-open ``[lo, hi)`` intervals (a position
exactly on a boundary belongs to the longer bucket). ``BookPosition.expiry``
is always > 0, so every position lands in exactly one bucket and the bucket
rows sum to the whole-book Greeks by construction (up to float summation
order) — the report never "loses" risk.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np

from optitrade.core import Greeks, OptionType
from optitrade.greeks.scenario import BookPosition
from optitrade.pricing.black_scholes import bs_greeks

# (lower bound, upper bound, label) in year fractions, ACT/365.
EXPIRY_BUCKETS: tuple[tuple[float, float, str], ...] = (
    (0.0, 7.0 / 365.0, "0-7d"),
    (7.0 / 365.0, 30.0 / 365.0, "7-30d"),
    (30.0 / 365.0, 90.0 / 365.0, "30-90d"),
    (90.0 / 365.0, math.inf, "90d+"),
)


@dataclass(frozen=True, slots=True)
class BucketReport:
    """Per-bucket aggregate Greeks plus whole-book totals.

    ``rows`` has one ``(label, greeks, n_positions)`` entry per bucket in
    :data:`EXPIRY_BUCKETS` order — empty buckets included with zero Greeks so
    downstream tables have a fixed shape. ``totals`` is the sum of all bucket
    Greeks, which equals the whole-book Greeks (see module docstring).
    """

    rows: tuple[tuple[str, Greeks, int], ...]
    totals: Greeks


def _aggregate_greeks(
    legs: Sequence[BookPosition],
    spot: float,
    rate: float,
    dividend_yield: float,
) -> Greeks:
    """Quantity-scaled BS Greeks summed over ``legs``, vectorised per option type."""
    total = Greeks()
    for option_type in (OptionType.CALL, OptionType.PUT):
        selected = [p for p in legs if p.option_type is option_type]
        if not selected:
            continue
        strikes = np.array([p.strike for p in selected], dtype=np.float64)
        expiries = np.array([p.expiry for p in selected], dtype=np.float64)
        vols = np.array([p.vol for p in selected], dtype=np.float64)
        quantities = np.array([p.quantity for p in selected], dtype=np.float64)
        g = bs_greeks(spot, strikes, expiries, rate, vols, option_type, dividend_yield)
        total = total + Greeks(
            delta=float(quantities @ np.asarray(g.delta)),
            gamma=float(quantities @ np.asarray(g.gamma)),
            vega=float(quantities @ np.asarray(g.vega)),
            theta=float(quantities @ np.asarray(g.theta)),
            rho=float(quantities @ np.asarray(g.rho)),
            vanna=float(quantities @ np.asarray(g.vanna)),
            volga=float(quantities @ np.asarray(g.volga)),
        )
    return total


def bucket_exposures(
    book: Sequence[BookPosition],
    spot: float,
    rate: float,
    dividend_yield: float = 0.0,
) -> BucketReport:
    """Aggregate the book's quantity-scaled Greeks per expiry bucket.

    Each bucket is one vectorised :func:`bs_greeks` call per option type (at
    most two), so the whole report costs O(len(book)) array work. Invariant:
    ``totals`` equals the whole-book aggregate because the buckets partition
    the expiry axis.
    """
    if spot <= 0:
        raise ValueError(f"spot must be positive, got {spot}")
    rows: list[tuple[str, Greeks, int]] = []
    totals = Greeks()
    for lower, upper, label in EXPIRY_BUCKETS:
        legs = [p for p in book if lower <= p.expiry < upper]
        greeks = _aggregate_greeks(legs, spot, rate, dividend_yield)
        rows.append((label, greeks, len(legs)))
        totals = totals + greeks
    return BucketReport(rows=tuple(rows), totals=totals)


__all__ = ["EXPIRY_BUCKETS", "BucketReport", "bucket_exposures"]

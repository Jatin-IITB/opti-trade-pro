"""Explain a day's book P&L from two persisted book snapshots.

Bridges :mod:`~options_trading.services.book_snapshot_store` to
:func:`~optitrade.explain.pnl_explain.explain_pnl`. The quant function takes
start-of-period Greeks and the period's spot, vol and time moves; this module
derives those from two snapshots and, critically, decides *which legs may be
compared at all*.

The trade problem
-----------------
``explain_pnl`` decomposes the P&L of a **held** position. If the user bought
or sold between the two snapshots, the change in book value contains trade
cash flow that no Greek explains, and folding it in would either inflate a
bucket or dump the whole trade into the residual while still calling the rest
"explained".

So the comparison runs only over legs present in both snapshots **at the same
quantity**. Everything else — opened, closed, resized — is excluded and
counted, the same fail-closed rule ``price_book`` applies to un-invertible
legs (ADR-008). ``coverage`` reports what fraction of the starting book the
explained P&L actually covers, so a decomposition of a third of the book
cannot be read as a decomposition of all of it.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass

import numpy as np

from optitrade.core import Greeks
from optitrade.explain import explain_pnl
from optitrade.explain.pnl_explain import PnLExplain

from .book_snapshot_store import BookSnapshot, LegSnapshot

logger = logging.getLogger(__name__)

# ACT/365 per ADR-003, matching the expiry convention used everywhere else.
_SECONDS_PER_YEAR = 365.0 * 24.0 * 3600.0
# Quantities are unit counts; anything below this is float noise, not a trade.
_QUANTITY_EPS = 1e-9
# Fewest spots for a quadratic-variation estimate: two points give a single
# return, which is the close-to-close move the estimator exists to improve on.
_MIN_SPOTS_FOR_RV = 3


@dataclass(frozen=True)
class BookPnLExplain:
    """One period's decomposition plus how much of the book it covers."""

    explain: PnLExplain
    start_timestamp: float
    end_timestamp: float
    n_legs_compared: int
    n_legs_changed: int
    gross_value_compared: float
    gross_value_total: float
    d_spot: float
    d_vol: float
    dt: float
    realized_variance: float | None

    @property
    def coverage(self) -> float:
        """Share of the starting book's value the explanation covers, in [0, 1].

        A decomposition of half the book is not a decomposition of the book;
        this is the number that says which one the reader is looking at.

        Measured on **gross** value, ``sum(|leg value|)``, not net. A short
        straddle against a long wing nets to near zero, so a net denominator
        turns any spread book's coverage into a ratio of a real number to
        ~0 — clamped to 1.0, reporting "100% covered" for a decomposition
        that dropped the larger leg. Gross is the quantity the caveat is
        actually about: how much of the position is represented.
        """
        if self.gross_value_total <= 0.0:
            return 0.0
        return min(1.0, self.gross_value_compared / self.gross_value_total)


def _comparable_legs(
    start: BookSnapshot, end: BookSnapshot
) -> tuple[list[tuple[LegSnapshot, LegSnapshot]], int]:
    """Legs held at both snapshots at an unchanged quantity, plus a changed count.

    A resized leg is excluded rather than compared at the smaller quantity:
    the partial close crystallises P&L at a fill price the snapshots never
    saw, so no Greek attribution of it would be correct.
    """
    end_by_symbol = end.by_symbol
    pairs: list[tuple[LegSnapshot, LegSnapshot]] = []
    n_changed = 0
    for symbol, start_leg in start.by_symbol.items():
        end_leg = end_by_symbol.get(symbol)
        if end_leg is None or abs(end_leg.quantity - start_leg.quantity) > _QUANTITY_EPS:
            n_changed += 1
            continue
        pairs.append((start_leg, end_leg))
    # Legs opened during the period are changes too: they contribute value at
    # the end that no starting Greek could have predicted.
    n_changed += sum(1 for symbol in end_by_symbol if symbol not in start.by_symbol)
    return pairs, n_changed


def _vega_weighted_vol_move(pairs: list[tuple[LegSnapshot, LegSnapshot]]) -> float:
    """Aggregate vol move the book's vega actually faced.

    Without a factor model ``explain_pnl`` prices the vega bucket as
    ``total_vega * d_vol``. For that to equal the true ``sum(vega_i *
    dvol_i)``, ``d_vol`` must be the *vega-weighted* mean of the per-leg vol
    moves — a plain average would misattribute whenever the smile moved
    non-parallel. Signed weights are correct here because the identity being
    matched is signed.

    The guard is the defining property of a mean rather than a threshold: a
    weighted average of the per-leg moves must lie **within their range**.
    Vega weights are signed, so on a near-cancelling book (a calendar spread
    nets ~1 out of two 4e5 legs) the quotient escapes that range entirely,
    reaching thousands — a hundred-thousand vol points. The vega bucket
    absorbs it harmlessly, being the same division inverted, but
    ``explain_pnl`` then *squares* it into the volga term, yielding a ~1e12
    bucket and an equal, opposite residual.

    When the quotient is not a mean, the unweighted mean is used instead. The
    vega bucket is then near zero because net vega is near zero, and the book's
    true vega P&L — real, but not expressible as one parallel level shift —
    lands in the residual, which is where genuinely unexplained P&L belongs.

    Any absolute floor is the wrong shape for this: net vega is a difference
    of numbers around 1e5, so no fixed epsilon separates "cancelled" from
    "small".
    """
    if not pairs:
        return 0.0
    weights = [start.greeks.vega * start.quantity for start, _ in pairs]
    moves = [end.iv - start.iv for start, end in pairs]
    unweighted = float(np.mean(moves))
    total_weight = sum(weights)
    if total_weight == 0.0:
        return unweighted
    weighted = sum(w * m for w, m in zip(weights, moves, strict=True)) / total_weight
    if not math.isfinite(weighted) or not min(moves) <= weighted <= max(moves):
        return unweighted
    return weighted


def _realized_variance(spots: list[float], dt: float) -> float | None:
    """Annualised realized variance of the path, or None if unmeasurable.

    This is *quadratic variation*, ``sum(r_i^2) / dt``, not a sample variance.
    The distinction is the whole content of the gamma bucket:
    ``explain_pnl`` computes ``0.5 * gamma * S^2 * realized_variance * dt``,
    and for that to reproduce the true path-wise gamma P&L
    ``0.5 * gamma * sum(dS_i^2)`` the product ``realized_variance * dt`` must
    equal ``sum(r_i^2)`` exactly.

    A demeaned estimator (``close_to_close_vol``) is wrong here twice over.
    Subtracting the mean removes the drift, but for gamma the drift *is* P&L:
    a day that trends steadily up in six steps has real quadratic variation
    and a demeaned variance of essentially zero, so the gamma bucket would
    read 0.00 and the entire move would land in the residual. And ``ddof=1``
    divides by ``n-1`` where the annualisation multiplies by ``n``, inflating
    the result by ``n/(n-1)`` — exactly 2x at the three-spot minimum.
    """
    if len(spots) < _MIN_SPOTS_FOR_RV or dt <= 0.0:
        return None
    path = np.asarray(spots, dtype=np.float64)
    if not np.all(np.isfinite(path)) or np.any(path <= 0.0):
        return None
    log_returns = np.diff(np.log(path))
    quadratic_variation = float(np.sum(log_returns * log_returns))
    if not math.isfinite(quadratic_variation) or quadratic_variation <= 0.0:
        return None
    return quadratic_variation / dt


def explain_book_pnl(
    start: BookSnapshot,
    end: BookSnapshot,
    intraday_spots: list[float] | None = None,
) -> BookPnLExplain | None:
    """Decompose the P&L of the legs held unchanged between two snapshots.

    Returns ``None`` when no leg survives the comparison — the book turned
    over completely, so there is no held position whose P&L a Greek could
    explain. That is reported as an absence, never as a zero decomposition.
    """
    if end.timestamp <= start.timestamp:
        raise ValueError(
            f"end snapshot must be later than start; got {end.timestamp} <= {start.timestamp}"
        )
    if start.spot <= 0:
        raise ValueError(f"start spot must be positive, got {start.spot}")

    pairs, n_changed = _comparable_legs(start, end)
    if not pairs:
        logger.info(
            "No legs held unchanged between book snapshots; P&L explain has nothing to decompose"
        )
        return None

    book_greeks = Greeks()
    total_pnl = 0.0
    gross_value_compared = 0.0
    for start_leg, end_leg in pairs:
        book_greeks = book_greeks + start_leg.greeks.scaled(start_leg.quantity)
        total_pnl += start_leg.quantity * (end_leg.mark - start_leg.mark)
        gross_value_compared += abs(start_leg.value)

    dt = (end.timestamp - start.timestamp) / _SECONDS_PER_YEAR
    d_spot = end.spot - start.spot
    d_vol = _vega_weighted_vol_move(pairs)
    realized_variance = _realized_variance(list(intraday_spots or []), dt)

    explain = explain_pnl(
        book_greeks,
        spot=start.spot,
        d_spot=d_spot,
        dt=dt,
        surface_move_scores={"level": d_vol},
        total_pnl=total_pnl,
        realized_variance=realized_variance,
    )
    return BookPnLExplain(
        explain=explain,
        start_timestamp=start.timestamp,
        end_timestamp=end.timestamp,
        n_legs_compared=len(pairs),
        n_legs_changed=n_changed,
        gross_value_compared=gross_value_compared,
        gross_value_total=start.gross_value,
        d_spot=d_spot,
        d_vol=d_vol,
        dt=dt,
        realized_variance=realized_variance,
    )


__all__ = ["BookPnLExplain", "explain_book_pnl"]

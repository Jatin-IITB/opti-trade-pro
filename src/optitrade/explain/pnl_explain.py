"""Daily P&L explain: theta carry + gamma-vs-realized-variance + factor vega + residual.

Builds on the plain Taylor attribution in :mod:`optitrade.hedging.pnl` by
splitting the vol bucket into surface factors and marking gamma against
realized variance, so the residual isolates what is genuinely unexplained.

Economics of the buckets
------------------------
Over one day a (delta-hedged) option book earns or pays:

- **theta carry** ``theta * dt`` — deterministic time decay, the premium the
  book collects (short options) or bleeds (long options) for holding gamma
  and vega.
- **delta** ``delta * dS`` — ~0 for a hedged book; reported so unhedged books
  still reconcile.
- **gamma vs realized variance** — in continuous time gamma P&L accrues as
  ``0.5 * gamma * S^2 * sigma_realized^2 * dt``: it realises against the
  *realized variance of the path*, not one squared close-to-close move. When
  ``realized_variance`` is supplied the gamma bucket is marked against it and
  the gap versus the naive single-move ``0.5 * gamma * dS^2`` form lands in
  the residual — that gap is exactly the sampling noise between the two
  measurement conventions, and hiding it inside the gamma bucket would
  overstate how well the book is understood. Without ``realized_variance``
  the single-move Taylor form is used.
- **vega vs surface factors** — the day's surface move is expressed in factor
  scores (level/term/skew, :mod:`optitrade.explain.factors`) and the book's
  grid vega profile is contracted against each single-factor reconstruction;
  the part of the surface move outside the factor span prices as
  ``vega_residual_move``. With no factor model, a single "vega" bucket uses
  ``Greeks.vega`` times the aggregate vol move.
- **vanna/volga** — second-order spot-vol cross terms against the level vol
  move: ``vanna * dS * dsigma + 0.5 * volga * dsigma^2``.

Everything else (higher-order terms, discreteness, marking noise) is the
residual, and ``explained_fraction`` — the headline metric — is the share of
``|total|`` not left in it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np

from optitrade.core import Greeks
from optitrade.explain.factors import SurfaceFactorModel, reconstruct
from optitrade.journal import EventLog

# Denominator floor keeping explained_fraction defined on a flat-P&L day.
_TOTAL_FLOOR = 1e-12


@dataclass(frozen=True)
class PnLExplain:
    """One day's P&L decomposition; ``total = sum(buckets) + residual``."""

    theta_carry: float
    delta_pnl: float
    gamma_vs_rv: float
    vega_from_factors: dict[str, float]
    vega_residual_move: float
    vanna_volga: float
    residual: float
    total: float

    @property
    def explained_fraction(self) -> float:
        """Share of ``|total|`` explained, ``1 - |residual| / |total|``, in [0, 1]."""
        fraction = 1.0 - abs(self.residual) / max(abs(self.total), _TOTAL_FLOOR)
        return min(1.0, max(0.0, fraction))

    def to_event_data(self) -> dict[str, Any]:
        """Journal-friendly plain-dict view (JSON-serialisable floats only)."""
        return {
            "theta_carry": self.theta_carry,
            "delta_pnl": self.delta_pnl,
            "gamma_vs_rv": self.gamma_vs_rv,
            "vega_from_factors": dict(self.vega_from_factors),
            "vega_residual_move": self.vega_residual_move,
            "vanna_volga": self.vanna_volga,
            "residual": self.residual,
            "total": self.total,
            "explained_fraction": self.explained_fraction,
        }


def _level_vol_move(model: SurfaceFactorModel, scores: dict[str, float]) -> float:
    """Average parallel vol shift implied by the "level" factor score.

    A factor score is in score units, not vol units; the average vol move it
    implies is ``score * mean(level loadings)``. Models without a "level"
    factor contribute no aggregate shift (vanna/volga then read zero).
    """
    if "level" not in model.factor_names:
        return 0.0
    index = model.factor_names.index("level")
    return scores.get("level", 0.0) * float(np.mean(model.components[index]))


def explain_pnl(
    book_greeks: Greeks,
    spot: float,
    d_spot: float,
    dt: float,
    surface_move_scores: dict[str, float],
    vega_profile_scores: dict[str, float] | None = None,
    *,
    total_pnl: float,
    realized_variance: float | None = None,
    factor_model: SurfaceFactorModel | None = None,
    book_vega_profile: np.ndarray | None = None,
    surface_move: np.ndarray | None = None,
    journal: EventLog | None = None,
) -> PnLExplain:
    """Decompose one day's realized ``total_pnl`` into economic buckets.

    Args:
        book_greeks: Whole-book Greeks at the start of the day (already
            quantity-scaled; units per :mod:`optitrade.core.types`).
        spot: Underlying level at the start of the day.
        d_spot: Spot change over the day.
        dt: Elapsed time as a year fraction.
        surface_move_scores: The day's surface move in factor scores keyed by
            factor name. Without a factor model this degrades to a single
            aggregate vol move passed as ``{"level": d_vol}``.
        vega_profile_scores: Optional precomputed book-vega loadings per
            factor (``project(model, book_vega_profile)`` as a dict); lets a
            caller attribute without shipping full grid arrays.
        total_pnl: Realized mark-to-market P&L being explained.
        realized_variance: Annualised realized variance of the underlying over
            the day; switches the gamma bucket to the realized-variance form
            (see module docstring).
        factor_model: Fitted :class:`SurfaceFactorModel`; with
            ``book_vega_profile`` enables per-factor vega attribution.
        book_vega_profile: Book vega per grid point, flattened like the model
            grid.
        surface_move: Optional raw flat surface move; when given (factor path)
            the part outside the factor span prices as ``vega_residual_move``.
        journal: Optional event log; when given, a ``pnl_explain`` event is
            appended with :meth:`PnLExplain.to_event_data`.

    Vega paths, in precedence order:

    1. ``factor_model`` + ``book_vega_profile``: per factor ``f``,
       ``vega_pnl[f] = profile . reconstruct(model, e_f * score_f)``
       (= ``score_f * (profile . component_f)`` since components are
       orthonormal).
    2. ``vega_profile_scores``: ``vega_pnl[f] = loading_f * score_f``.
    3. neither: a single ``{"vega": Greeks.vega * d_vol}`` bucket with
       ``d_vol = surface_move_scores["level"]``.
    """
    if spot <= 0:
        raise ValueError(f"spot must be positive, got {spot}")
    if dt < 0:
        raise ValueError(f"dt must be non-negative, got {dt}")
    if realized_variance is not None and realized_variance < 0:
        raise ValueError(f"realized_variance must be non-negative, got {realized_variance}")

    theta_carry = book_greeks.theta * dt
    delta_pnl = book_greeks.delta * d_spot
    if realized_variance is None:
        gamma_vs_rv = 0.5 * book_greeks.gamma * d_spot * d_spot
    else:
        gamma_vs_rv = 0.5 * book_greeks.gamma * spot * spot * realized_variance * dt

    vega_residual_move = 0.0
    if factor_model is not None and book_vega_profile is not None:
        profile = np.asarray(book_vega_profile, dtype=np.float64).ravel()
        if profile.size != factor_model.mean_move.size:
            raise ValueError(
                f"book_vega_profile must have {factor_model.mean_move.size} grid points, "
                f"got {profile.size}"
            )
        vega_from_factors = {
            name: surface_move_scores.get(name, 0.0) * float(profile @ component)
            for name, component in zip(
                factor_model.factor_names, factor_model.components, strict=True
            )
        }
        if surface_move is not None:
            scores_vector = np.array(
                [surface_move_scores.get(name, 0.0) for name in factor_model.factor_names],
                dtype=np.float64,
            )
            remainder = np.asarray(surface_move, dtype=np.float64).ravel() - reconstruct(
                factor_model, scores_vector
            )
            vega_residual_move = float(profile @ remainder)
        d_sigma_level = _level_vol_move(factor_model, surface_move_scores)
    elif vega_profile_scores is not None:
        vega_from_factors = {
            name: loading * surface_move_scores.get(name, 0.0)
            for name, loading in vega_profile_scores.items()
        }
        # Without the model there is no loadings-to-vol-units conversion, so
        # callers wanting vanna/volga on this path must pass factor_model too.
        d_sigma_level = (
            _level_vol_move(factor_model, surface_move_scores) if factor_model is not None else 0.0
        )
    else:
        d_sigma_level = surface_move_scores.get("level", 0.0)
        vega_from_factors = {"vega": book_greeks.vega * d_sigma_level}

    vanna_volga = (
        book_greeks.vanna * d_spot * d_sigma_level
        + 0.5 * book_greeks.volga * d_sigma_level * d_sigma_level
    )

    explained = (
        theta_carry
        + delta_pnl
        + gamma_vs_rv
        + sum(vega_from_factors.values())
        + vega_residual_move
        + vanna_volga
    )
    result = PnLExplain(
        theta_carry=theta_carry,
        delta_pnl=delta_pnl,
        gamma_vs_rv=gamma_vs_rv,
        vega_from_factors=vega_from_factors,
        vega_residual_move=vega_residual_move,
        vanna_volga=vanna_volga,
        residual=total_pnl - explained,
        total=total_pnl,
    )
    if journal is not None:
        journal.append(event_type="pnl_explain", data=result.to_event_data())
    return result


__all__ = ["PnLExplain", "explain_pnl"]

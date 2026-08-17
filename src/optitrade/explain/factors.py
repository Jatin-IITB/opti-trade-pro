"""Volatility-surface dynamics factors via PCA (numpy SVD only).

Daily implied-vol surface moves — flattened over a fixed (moneyness x expiry)
grid — are decomposed into a small number of orthonormal principal components.
Empirically (Cont & da Fonseca, *Dynamics of implied volatility surfaces*,
2002) about three factors explain most daily surface variance and line up with
trader vocabulary:

- **level**: near-parallel shift of the whole surface,
- **term**:  short-end vs long-end rotation (loadings monotone in expiry),
- **skew**:  put-wing vs call-wing rotation (loadings monotone in moneyness).

The factor scores of one day's move are the coordinates the P&L-explain engine
attributes vega P&L to (:mod:`optitrade.explain.pnl_explain`).

Grid convention: a surface indexed ``[i_moneyness, j_expiry]`` is flattened
row-major (C order), so ``flat_index = i * n_expiries + j``.

Centering convention: the daily mean move is subtracted only while *fitting*
(PCA estimates the covariance of moves). :func:`project` and
:func:`reconstruct` operate on raw moves in pure factor space — no mean is
subtracted or re-added — so the decomposition is exactly additive::

    move = sum_f score_f * component_f  +  remainder,   remainder ⟂ components

which is what makes per-factor vega P&L plus a residual-move term sum to the
full vega P&L without double counting. ``mean_move`` is kept as fitted
metadata (the average daily drift of the surface).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import numpy.typing as npt

FloatArray = npt.NDArray[np.float64]

# Naming-heuristic thresholds (see _classify_component): a component is "level"
# when at least this fraction of loadings share one sign and the loading
# dispersion is below this multiple of the mean absolute loading.
_LEVEL_SIGN_AGREEMENT = 0.90
_LEVEL_MAX_DISPERSION = 0.5


@dataclass(frozen=True)
class SurfaceFactorModel:
    """Fitted PCA factor model of daily implied-vol surface moves.

    ``components`` is ``(n_factors, n_grid)`` with orthonormal rows;
    ``mean_move`` is the flat ``(n_grid,)`` average daily move (fitting
    metadata, see module docstring); ``explained_variance_ratio`` is the share
    of total move variance captured by each retained factor.
    """

    grid_moneyness: FloatArray
    grid_expiries: FloatArray
    mean_move: FloatArray
    components: FloatArray
    explained_variance_ratio: FloatArray
    factor_names: tuple[str, ...]

    def __post_init__(self) -> None:
        n_grid = int(self.grid_moneyness.size) * int(self.grid_expiries.size)
        if self.mean_move.shape != (n_grid,):
            raise ValueError(f"mean_move must have shape ({n_grid},), got {self.mean_move.shape}")
        if self.components.ndim != 2 or self.components.shape[1] != n_grid:
            raise ValueError(
                f"components must have shape (n_factors, {n_grid}), got {self.components.shape}"
            )
        if len(self.factor_names) != self.components.shape[0]:
            raise ValueError(
                f"{len(self.factor_names)} factor names for {self.components.shape[0]} components"
            )

    @property
    def n_factors(self) -> int:
        return int(self.components.shape[0])


def _is_strictly_monotone(profile: FloatArray) -> bool:
    """True when the 1-D profile is strictly increasing or strictly decreasing."""
    diffs = np.diff(profile)
    if diffs.size == 0:
        return False
    return bool(np.all(diffs > 0.0) or np.all(diffs < 0.0))


def _classify_component(grid: FloatArray) -> str | None:
    """Heuristic factor name from the loading structure on the 2-D grid.

    Deliberately simple (heuristics, not proofs):

    1. **level** — loadings have a near-constant sign (>= 90% agreement) and
       low dispersion (std below half the mean absolute loading): the factor
       moves the whole surface roughly in parallel.
    2. **term** — the expiry profile (loadings averaged over moneyness) is
       strictly monotone and varies at least as much as the moneyness profile.
    3. **skew** — the moneyness profile (averaged over expiries) is strictly
       monotone.
    4. otherwise ``None`` (caller falls back to a positional "pc{i}" name).
    """
    flat = grid.ravel()
    sign_fraction = max(float(np.mean(flat > 0.0)), float(np.mean(flat < 0.0)))
    mean_abs = float(np.mean(np.abs(flat)))
    if sign_fraction >= _LEVEL_SIGN_AGREEMENT and float(np.std(flat)) <= (
        _LEVEL_MAX_DISPERSION * mean_abs
    ):
        return "level"
    moneyness_profile = np.asarray(grid.mean(axis=1), dtype=np.float64)
    expiry_profile = np.asarray(grid.mean(axis=0), dtype=np.float64)
    moneyness_range = float(moneyness_profile.max() - moneyness_profile.min())
    expiry_range = float(expiry_profile.max() - expiry_profile.min())
    if _is_strictly_monotone(expiry_profile) and expiry_range >= moneyness_range:
        return "term"
    if _is_strictly_monotone(moneyness_profile):
        return "skew"
    return None


def fit_surface_factors(
    moves: np.ndarray,
    grid_moneyness: np.ndarray,
    grid_expiries: np.ndarray,
    n_factors: int = 3,
) -> SurfaceFactorModel:
    """Fit a PCA factor model to a history of daily surface moves.

    Args:
        moves: ``(n_days, n_grid)`` daily implied-vol changes (decimals),
            each row one day's surface move flattened row-major over the
            (moneyness x expiry) grid.
        grid_moneyness: 1-D moneyness axis of the grid.
        grid_expiries: 1-D expiry axis (year fractions) of the grid.
        n_factors: number of principal components to retain.

    The move history is centered, decomposed with ``np.linalg.svd`` and the
    top ``n_factors`` right singular vectors kept as orthonormal components.
    Each component's sign is fixed so its loadings sum to a non-negative value
    (a "level up = vols up" convention that also makes fits deterministic),
    then named via :func:`_classify_component`; duplicate or unclassifiable
    components get positional ``pc{i}`` names.
    """
    moves_arr = np.asarray(moves, dtype=np.float64)
    moneyness = np.asarray(grid_moneyness, dtype=np.float64).ravel()
    expiries = np.asarray(grid_expiries, dtype=np.float64).ravel()
    if moneyness.size == 0 or expiries.size == 0:
        raise ValueError("grid_moneyness and grid_expiries must be non-empty")
    n_grid = moneyness.size * expiries.size
    if moves_arr.ndim != 2 or moves_arr.shape[1] != n_grid:
        raise ValueError(f"moves must have shape (n_days, {n_grid}), got {moves_arr.shape}")
    if moves_arr.shape[0] < 2:
        raise ValueError(f"need at least 2 days of moves, got {moves_arr.shape[0]}")
    if not 1 <= n_factors <= min(moves_arr.shape):
        raise ValueError(f"n_factors must be in [1, {min(moves_arr.shape)}], got {n_factors}")

    mean_move = np.asarray(moves_arr.mean(axis=0), dtype=np.float64)
    centered = moves_arr - mean_move
    _, singular_values, vt = np.linalg.svd(centered, full_matrices=False)
    total_variance = float(np.sum(singular_values**2))
    if total_variance <= 0.0:
        raise ValueError("moves have zero variance; cannot fit surface factors")

    components = np.asarray(vt[:n_factors], dtype=np.float64).copy()
    signs = np.where(components.sum(axis=1) >= 0.0, 1.0, -1.0)
    components *= signs[:, None]
    explained = np.asarray(singular_values[:n_factors] ** 2 / total_variance, dtype=np.float64)

    names: list[str] = []
    for i, component in enumerate(components):
        name = _classify_component(component.reshape(moneyness.size, expiries.size))
        if name is None or name in names:
            name = f"pc{i + 1}"
        names.append(name)

    return SurfaceFactorModel(
        grid_moneyness=moneyness,
        grid_expiries=expiries,
        mean_move=mean_move,
        components=components,
        explained_variance_ratio=explained,
        factor_names=tuple(names),
    )


def project(model: SurfaceFactorModel, move: np.ndarray) -> FloatArray:
    """Factor scores of one day's flat surface move.

    Pure factor-space projection ``scores = components @ move`` — no mean
    subtraction (see module docstring), so scores are exactly the coordinates
    of the move's component within the factor span.
    """
    flat = np.asarray(move, dtype=np.float64).ravel()
    if flat.size != model.mean_move.size:
        raise ValueError(f"move must have {model.mean_move.size} grid points, got {flat.size}")
    return np.asarray(model.components @ flat, dtype=np.float64)


def reconstruct(model: SurfaceFactorModel, scores: np.ndarray) -> FloatArray:
    """Flat surface move implied by factor scores (``scores @ components``).

    Inverse of :func:`project` on the factor span; the part of a raw move
    outside the span is exactly ``move - reconstruct(model, project(model,
    move))`` and is orthogonal to every component.
    """
    scores_arr = np.asarray(scores, dtype=np.float64).ravel()
    if scores_arr.size != model.n_factors:
        raise ValueError(f"expected {model.n_factors} scores, got {scores_arr.size}")
    return np.asarray(scores_arr @ model.components, dtype=np.float64)


__all__ = ["FloatArray", "SurfaceFactorModel", "fit_surface_factors", "project", "reconstruct"]

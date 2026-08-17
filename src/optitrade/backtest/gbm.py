"""Geometric Brownian motion path simulation for hedging backtests."""

from __future__ import annotations

import numpy as np
import numpy.typing as npt


def simulate_gbm_paths(
    spot: float,
    drift: float,
    vol: float,
    dt: float,
    n_steps: int,
    n_paths: int,
    seed: int,
) -> npt.NDArray[np.float64]:
    """Simulate GBM paths with the exact log-Euler scheme.

    Log increments are drawn as ``(drift - vol**2 / 2) * dt + vol * sqrt(dt) * Z``
    with ``Z ~ N(0, 1)`` from a seeded :class:`numpy.random.Generator`, so the
    scheme is exact in distribution at the grid points (no discretisation
    bias) and fully deterministic for a given seed.

    Returns:
        Array of shape ``(n_paths, n_steps + 1)``; column 0 is ``spot``.
    """
    if spot <= 0.0:
        raise ValueError(f"spot must be positive, got {spot}")
    if vol < 0.0:
        raise ValueError(f"vol must be >= 0, got {vol}")
    if dt <= 0.0:
        raise ValueError(f"dt must be positive, got {dt}")
    if n_steps < 1 or n_paths < 1:
        raise ValueError(f"n_steps and n_paths must be >= 1, got {n_steps}, {n_paths}")

    rng = np.random.default_rng(seed)
    shocks = rng.standard_normal((n_paths, n_steps))
    log_increments = (drift - 0.5 * vol * vol) * dt + vol * np.sqrt(dt) * shocks
    paths = np.empty((n_paths, n_steps + 1), dtype=np.float64)
    paths[:, 0] = spot
    paths[:, 1:] = spot * np.exp(np.cumsum(log_increments, axis=1))
    return paths


__all__ = ["simulate_gbm_paths"]

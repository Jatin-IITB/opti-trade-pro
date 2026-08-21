"""Greeks engines: finite-difference, adjoint AD, JAX AD, and scenario revaluation.

Four independent routes to sensitivities, cross-validated against the
analytic Black-Scholes Greeks in :mod:`optitrade.pricing`:

- :func:`fd_greeks` — model-agnostic bump-and-reprice,
- :func:`bs_price_adjoint` — tape-based reverse-mode AD,
- :func:`bs_price_jax` — JAX automatic differentiation (optional ``[jax]`` extra),
- :func:`run_scenario_grid` — full-revaluation spot x vol x time PnL cube.
"""

from optitrade.greeks.adjoint import Tape, Var, bs_price_adjoint
from optitrade.greeks.finite_difference import FDBumps, PriceFn, fd_greeks
from optitrade.greeks.scenario import (
    BookPosition,
    ScenarioGrid,
    ScenarioResult,
    run_scenario_grid,
)

__all__ = [
    "BookPosition",
    "FDBumps",
    "PriceFn",
    "ScenarioGrid",
    "ScenarioResult",
    "Tape",
    "Var",
    "bs_price_adjoint",
    "fd_greeks",
    "run_scenario_grid",
]

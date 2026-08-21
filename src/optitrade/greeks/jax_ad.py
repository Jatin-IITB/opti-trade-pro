"""JAX-based automatic differentiation for Black-Scholes-Merton Greeks.

Uses ``jax.grad`` and nested derivatives instead of a hand-rolled tape:

- **First order** (delta, vega, theta, rho): exact via ``jax.grad``.
- **Second order** (gamma, vanna, volga): exact via nested ``jax.grad``
  — no finite-difference bumping needed.
- **Higher order** (charm, veta, speed, color, ultima, vomma): exact via
  further nesting; these are unavailable from the tape-based engine.
- **Vectorised book Greeks**: ``jax.vmap`` maps the scalar pricer across a
  book of positions in one fused XLA call.

The module is an optional dependency (``pip install optitrade-pro[jax]``).
All public functions raise ``ImportError`` with an install hint when JAX is
absent. When JAX *is* available, the first call to each JIT-compiled function
incurs a one-time XLA compilation cost; subsequent calls run at native speed.

Cross-validated against the analytic, finite-difference, and tape-based
adjoint methods in ``tests/unit/quant/test_greeks_cross.py`` (ADR-006).
"""

from __future__ import annotations

from typing import Any

from optitrade.core import Greeks, OptionType

try:
    import jax

    jax.config.update("jax_enable_x64", True)

    import jax.numpy as jnp
    from jax.scipy.stats import norm as jax_norm

    _HAS_JAX = True
except ImportError:
    _HAS_JAX = False

_INSTALL_HINT = 'pip install "optitrade-pro[jax]"'

_MIN_EXPIRY = 1e-12
_MIN_VOL = 1e-12


def _require_jax() -> None:
    if not _HAS_JAX:
        raise ImportError(f"JAX is required for jax_ad but not installed. Run: {_INSTALL_HINT}")


# ---------------------------------------------------------------------------
# BSM pricer and derivative functions — built lazily on first use
# ---------------------------------------------------------------------------
# JAX tracer values can be float or Array interchangeably, so all internal
# functions use Any for JAX-traced args to keep mypy happy.

_GRADS: dict[str, Any] | None = None


def _bs_price_scalar(
    spot: Any,
    strike: Any,
    expiry: Any,
    rate: Any,
    vol: Any,
    is_call: Any,
    dividend_yield: Any,
) -> Any:
    """Pure-function BSM pricer suitable for ``jax.grad``."""
    t = jnp.maximum(expiry, _MIN_EXPIRY)
    v = jnp.maximum(vol, _MIN_VOL)
    sqrt_t = jnp.sqrt(t)
    d1 = (jnp.log(spot / strike) + (rate - dividend_yield + 0.5 * v * v) * t) / (v * sqrt_t)
    d2 = d1 - v * sqrt_t
    df_r = jnp.exp(-rate * t)
    df_q = jnp.exp(-dividend_yield * t)

    call_price = spot * df_q * jax_norm.cdf(d1) - strike * df_r * jax_norm.cdf(d2)
    put_price = strike * df_r * jax_norm.cdf(-d2) - spot * df_q * jax_norm.cdf(-d1)
    return jnp.where(is_call, call_price, put_price)


def _build_grads() -> dict[str, Any]:
    """Construct all gradient functions once, on first call."""
    # argnums: 0=spot, 1=strike, 2=expiry, 3=rate, 4=vol, 5=is_call, 6=div_yield
    d_spot = jax.grad(_bs_price_scalar, argnums=0)
    d_vol = jax.grad(_bs_price_scalar, argnums=4)
    d_rate = jax.grad(_bs_price_scalar, argnums=3)
    d_expiry = jax.grad(_bs_price_scalar, argnums=2)

    d2_spot = jax.grad(d_spot, argnums=0)  # gamma
    d_spot_vol = jax.grad(d_spot, argnums=4)  # vanna
    d2_vol = jax.grad(d_vol, argnums=4)  # volga

    d_spot_expiry = jax.grad(d_spot, argnums=2)  # charm
    d_vol_expiry = jax.grad(d_vol, argnums=2)  # veta
    d3_spot = jax.grad(d2_spot, argnums=0)  # speed
    d2_spot_expiry = jax.grad(jax.grad(d_spot, argnums=0), argnums=2)  # color
    d3_vol = jax.grad(d2_vol, argnums=4)  # ultima
    d_spot2_vol = jax.grad(d2_spot, argnums=4)  # zomma

    return {
        "d_spot": d_spot,
        "d_vol": d_vol,
        "d_rate": d_rate,
        "d_expiry": d_expiry,
        "d2_spot": d2_spot,
        "d_spot_vol": d_spot_vol,
        "d2_vol": d2_vol,
        "d_spot_expiry": d_spot_expiry,
        "d_vol_expiry": d_vol_expiry,
        "d3_spot": d3_spot,
        "d2_spot_expiry": d2_spot_expiry,
        "d3_vol": d3_vol,
        "d_spot2_vol": d_spot2_vol,
    }


def _grads() -> dict[str, Any]:
    """Return (and cache) the gradient function registry."""
    global _GRADS
    if _GRADS is None:
        _GRADS = _build_grads()
    return _GRADS


def _to_option_args(
    spot: float,
    strike: float,
    expiry: float,
    rate: float,
    vol: float,
    option_type: OptionType | str,
    dividend_yield: float,
) -> tuple[float, float, float, float, float, bool, float]:
    is_call = OptionType(option_type) is OptionType.CALL
    return (
        float(spot),
        float(strike),
        float(expiry),
        float(rate),
        float(vol),
        is_call,
        float(dividend_yield),
    )


def bs_price_jax(
    spot: float,
    strike: float,
    expiry: float,
    rate: float,
    vol: float,
    option_type: OptionType | str,
    dividend_yield: float = 0.0,
) -> tuple[float, Greeks]:
    """BSM price and first+second order Greeks via JAX automatic differentiation.

    Same signature and return type as :func:`~optitrade.greeks.adjoint.bs_price_adjoint`
    for drop-in cross-validation. All Greeks are exact (no finite-difference bumping).

    Unit conventions follow :mod:`optitrade.core.types` (vega per unit vol,
    rho per unit rate, theta per year).
    """
    _require_jax()
    g = _grads()
    args = _to_option_args(spot, strike, expiry, rate, vol, option_type, dividend_yield)

    price = float(_bs_price_scalar(*args))
    delta = float(g["d_spot"](*args))
    vega = float(g["d_vol"](*args))
    rho = float(g["d_rate"](*args))
    theta = -float(g["d_expiry"](*args))  # theta = -dP/dtau

    gamma = float(g["d2_spot"](*args))
    vanna = float(g["d_spot_vol"](*args))
    volga = float(g["d2_vol"](*args))

    greeks = Greeks(
        delta=delta,
        gamma=gamma,
        vega=vega,
        theta=theta,
        rho=rho,
        vanna=vanna,
        volga=volga,
    )
    return price, greeks


def bs_higher_order_greeks(
    spot: float,
    strike: float,
    expiry: float,
    rate: float,
    vol: float,
    option_type: OptionType | str,
    dividend_yield: float = 0.0,
) -> dict[str, float]:
    """Higher-order Greeks only available via JAX nested differentiation.

    Returns a dict with keys: charm, veta, speed, color, ultima, zomma.
    These require third- or mixed-second-order derivatives that the tape-based
    engine cannot compute without additional FD bumping.
    """
    _require_jax()
    g = _grads()
    args = _to_option_args(spot, strike, expiry, rate, vol, option_type, dividend_yield)

    return {
        "charm": -float(g["d_spot_expiry"](*args)),  # -d(delta)/d(tau)
        "veta": -float(g["d_vol_expiry"](*args)),  # -d(vega)/d(tau)
        "speed": float(g["d3_spot"](*args)),  # d(gamma)/d(spot)
        "color": -float(g["d2_spot_expiry"](*args)),  # -d(gamma)/d(tau)
        "ultima": float(g["d3_vol"](*args)),  # d(volga)/d(vol)
        "zomma": float(g["d_spot2_vol"](*args)),  # d(gamma)/d(vol)
    }


def bs_greeks_book_jax(
    spots: list[float],
    strikes: list[float],
    expiries: list[float],
    rates: list[float],
    vols: list[float],
    is_calls: list[bool],
    dividend_yields: list[float],
) -> list[tuple[float, Greeks]]:
    """Vectorised book-level pricing and Greeks via ``jax.vmap``.

    Each position is described by a row across the seven parallel lists.
    Returns a list of ``(price, Greeks)`` tuples, one per position.
    The entire book is priced in one fused XLA kernel call.
    """
    _require_jax()
    n = len(spots)
    lengths_match = (
        len(strikes) == n
        and len(expiries) == n
        and len(rates) == n
        and len(vols) == n
        and len(is_calls) == n
        and len(dividend_yields) == n
    )
    if not lengths_match:
        raise ValueError("All input lists must have the same length")
    if n == 0:
        return []

    g = _grads()

    s = jnp.array(spots)
    k = jnp.array(strikes)
    t = jnp.array(expiries)
    r = jnp.array(rates)
    v = jnp.array(vols)
    ic = jnp.array(is_calls)
    q = jnp.array(dividend_yields)

    def _price_and_greeks_one(
        spot: Any, strike: Any, expiry: Any, rate: Any, vol: Any, is_call: Any, div_yield: Any
    ) -> tuple[Any, ...]:
        price = _bs_price_scalar(spot, strike, expiry, rate, vol, is_call, div_yield)
        delta = g["d_spot"](spot, strike, expiry, rate, vol, is_call, div_yield)
        vega = g["d_vol"](spot, strike, expiry, rate, vol, is_call, div_yield)
        rho = g["d_rate"](spot, strike, expiry, rate, vol, is_call, div_yield)
        theta = -g["d_expiry"](spot, strike, expiry, rate, vol, is_call, div_yield)
        gamma = g["d2_spot"](spot, strike, expiry, rate, vol, is_call, div_yield)
        vanna = g["d_spot_vol"](spot, strike, expiry, rate, vol, is_call, div_yield)
        volga = g["d2_vol"](spot, strike, expiry, rate, vol, is_call, div_yield)
        return price, delta, gamma, vega, theta, rho, vanna, volga

    vmapped = jax.vmap(_price_and_greeks_one)(s, k, t, r, v, ic, q)

    results: list[tuple[float, Greeks]] = []
    for i in range(n):
        price_i = float(vmapped[0][i])
        greeks_i = Greeks(
            delta=float(vmapped[1][i]),
            gamma=float(vmapped[2][i]),
            vega=float(vmapped[3][i]),
            theta=float(vmapped[4][i]),
            rho=float(vmapped[5][i]),
            vanna=float(vmapped[6][i]),
            volga=float(vmapped[7][i]),
        )
        results.append((price_i, greeks_i))
    return results


__all__ = [
    "bs_greeks_book_jax",
    "bs_higher_order_greeks",
    "bs_price_jax",
]

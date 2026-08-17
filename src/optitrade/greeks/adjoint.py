"""Tape-based reverse-mode (adjoint) algorithmic differentiation.

A minimal operator-overloading implementation of the reverse mode of AD after
Griewank & Walther, *Evaluating Derivatives: Principles and Techniques of
Algorithmic Differentiation* (2nd ed., SIAM, 2008), ch. 3-5. The forward pass
records every elementary operation on a :class:`Tape` (a Wengert list) as a
:class:`Var` holding its value and the local partials with respect to its
parents. :meth:`Tape.backward` seeds the output adjoint with 1 and sweeps the
tape in reverse recorded order — a topological order of the computational DAG,
because every parent is recorded before any node that consumes it — so one
sweep yields the gradient with respect to *all* inputs at a cost of O(1)
forward evaluations (the "cheap gradient principle").

:func:`bs_price_adjoint` applies this to Black-Scholes-Merton: one forward and
one backward pass produce the price together with delta, vega, rho and theta.
Second-order Greeks (gamma, vanna, volga) are central finite differences *of
the AD first-order Greeks* (FD-of-AD): two bumped tape evaluations per bump
axis (spot for gamma, vol for vanna/volga). Because the differenced quantities
are machine-accurate first derivatives, tiny bumps (1e-5) are stable and the
O(h^2) truncation error is negligible.

No external AD framework is used (no autograd/jax/torch).
"""

from __future__ import annotations

import math

from optitrade.core import Greeks, NumericalError, OptionType

_INV_SQRT_2 = 1.0 / math.sqrt(2.0)
_INV_SQRT_2PI = 1.0 / math.sqrt(2.0 * math.pi)

# Match the floors in optitrade.pricing.black_scholes so both engines degrade
# identically to (discounted) intrinsic value at expiry / zero vol.
_MIN_EXPIRY = 1e-12
_MIN_VOL = 1e-12

# FD-of-AD bump sizes for second-order Greeks. Differencing exact first
# derivatives avoids the price-roundoff amplification of second differences of
# prices, so bumps an order smaller than fd_greeks' defaults are stable.
_REL_SPOT_BUMP = 1e-5
_ABS_VOL_BUMP = 1e-5

Parents = tuple[tuple["Var", float], ...]


class Tape:
    """Records elementary operations (a Wengert list) for one reverse sweep."""

    __slots__ = ("_nodes",)

    def __init__(self) -> None:
        self._nodes: list[Var] = []

    def var(self, value: float) -> Var:
        """Create and record a leaf (independent input) variable."""
        return self._record(value, ())

    def _record(self, value: float, parents: Parents) -> Var:
        node = Var(value, self, parents)
        self._nodes.append(node)
        return node

    def __len__(self) -> int:
        return len(self._nodes)

    def backward(self, output: Var) -> None:
        """Propagate adjoints from ``output`` to every recorded node.

        Resets all adjoints first, so ``backward`` may be called repeatedly
        (for different outputs) on the same tape. After the sweep,
        ``leaf.adjoint`` holds ``d(output)/d(leaf)``.
        """
        if output.tape is not self:
            raise NumericalError("backward() called with a Var recorded on a different tape")
        for node in self._nodes:
            node.adjoint = 0.0
        output.adjoint = 1.0
        # Reverse recorded order is a reverse topological order of the DAG:
        # each node's adjoint is final before it is pushed to its parents.
        for node in reversed(self._nodes):
            a = node.adjoint
            if a == 0.0:
                continue
            for parent, partial in node.parents:
                parent.adjoint += a * partial


class Var:
    """A tape node: value, adjoint, and parent edges with local partials."""

    __slots__ = ("adjoint", "parents", "tape", "value")

    def __init__(self, value: float, tape: Tape, parents: Parents) -> None:
        self.value = float(value)
        self.tape = tape
        self.parents = parents
        self.adjoint = 0.0

    def __repr__(self) -> str:
        return f"Var(value={self.value!r}, adjoint={self.adjoint!r})"

    def __add__(self, other: Var | float) -> Var:
        if isinstance(other, Var):
            return self.tape._record(self.value + other.value, ((self, 1.0), (other, 1.0)))
        return self.tape._record(self.value + float(other), ((self, 1.0),))

    def __radd__(self, other: float) -> Var:
        return self.__add__(other)

    def __sub__(self, other: Var | float) -> Var:
        if isinstance(other, Var):
            return self.tape._record(self.value - other.value, ((self, 1.0), (other, -1.0)))
        return self.tape._record(self.value - float(other), ((self, 1.0),))

    def __rsub__(self, other: float) -> Var:
        return self.tape._record(float(other) - self.value, ((self, -1.0),))

    def __mul__(self, other: Var | float) -> Var:
        if isinstance(other, Var):
            return self.tape._record(
                self.value * other.value, ((self, other.value), (other, self.value))
            )
        c = float(other)
        return self.tape._record(self.value * c, ((self, c),))

    def __rmul__(self, other: float) -> Var:
        return self.__mul__(other)

    def __truediv__(self, other: Var | float) -> Var:
        if isinstance(other, Var):
            inv = 1.0 / other.value
            return self.tape._record(
                self.value * inv, ((self, inv), (other, -self.value * inv * inv))
            )
        inv = 1.0 / float(other)
        return self.tape._record(self.value * inv, ((self, inv),))

    def __rtruediv__(self, other: float) -> Var:
        c = float(other)
        inv = 1.0 / self.value
        return self.tape._record(c * inv, ((self, -c * inv * inv),))

    def __pow__(self, other: Var | float) -> Var:
        if isinstance(other, Var):
            # d(x^y)/dx = y*x^(y-1); d(x^y)/dy = x^y * ln(x) needs x > 0.
            if self.value <= 0.0:
                raise NumericalError("Var ** Var requires a strictly positive base")
            val = self.value**other.value
            return self.tape._record(
                val,
                (
                    (self, other.value * self.value ** (other.value - 1.0)),
                    (other, val * math.log(self.value)),
                ),
            )
        p = float(other)
        if self.value < 0.0 and p != int(p):
            raise NumericalError("Var ** p with negative base requires an integer exponent")
        return self.tape._record(self.value**p, ((self, p * self.value ** (p - 1.0)),))

    def __neg__(self) -> Var:
        return self.tape._record(-self.value, ((self, -1.0),))


def log(x: Var) -> Var:
    """Natural logarithm; d/dx log(x) = 1/x."""
    if x.value <= 0.0:
        raise NumericalError(f"log of non-positive value {x.value}")
    return x.tape._record(math.log(x.value), ((x, 1.0 / x.value),))


def exp(x: Var) -> Var:
    """Exponential; d/dx exp(x) = exp(x)."""
    v = math.exp(x.value)
    return x.tape._record(v, ((x, v),))


def sqrt(x: Var) -> Var:
    """Square root; d/dx sqrt(x) = 1/(2 sqrt(x))."""
    if x.value <= 0.0:
        raise NumericalError(f"sqrt of non-positive value {x.value}")
    v = math.sqrt(x.value)
    return x.tape._record(v, ((x, 0.5 / v),))


def _phi(x: float) -> float:
    """Standard normal density."""
    return _INV_SQRT_2PI * math.exp(-0.5 * x * x)


def norm_cdf(x: Var) -> Var:
    """Standard normal CDF via erfc (accurate in both tails); Phi'(x) = phi(x)."""
    v = 0.5 * math.erfc(-x.value * _INV_SQRT_2)
    return x.tape._record(v, ((x, _phi(x.value)),))


def norm_pdf(x: Var) -> Var:
    """Standard normal PDF; phi'(x) = -x * phi(x)."""
    v = _phi(x.value)
    return x.tape._record(v, ((x, -x.value * v),))


def _bs_first_order(
    spot: float,
    strike: float,
    expiry: float,
    rate: float,
    vol: float,
    option_type: OptionType | str,
    dividend_yield: float,
) -> tuple[float, float, float, float, float]:
    """One forward + one backward pass over the BSM graph.

    Returns ``(price, dP/dS, dP/dvol, dP/dr, dP/dtau)``. Strike and dividend
    yield enter as constants; spot, expiry, rate and vol are the tape leaves.
    """
    tape = Tape()
    s = tape.var(spot)
    t = tape.var(max(expiry, _MIN_EXPIRY))
    r = tape.var(rate)
    v = tape.var(max(vol, _MIN_VOL))
    q = dividend_yield

    sqrt_t = sqrt(t)
    d1 = (log(s / strike) + (r - q + 0.5 * v * v) * t) / (v * sqrt_t)
    d2 = d1 - v * sqrt_t
    df_r = exp(-r * t)
    df_q = exp(-q * t)
    if OptionType(option_type) is OptionType.CALL:
        price = s * df_q * norm_cdf(d1) - strike * df_r * norm_cdf(d2)
    else:
        price = strike * df_r * norm_cdf(-d2) - s * df_q * norm_cdf(-d1)

    tape.backward(price)
    return price.value, s.adjoint, v.adjoint, r.adjoint, t.adjoint


def bs_price_adjoint(
    spot: float,
    strike: float,
    expiry: float,
    rate: float,
    vol: float,
    option_type: OptionType | str,
    dividend_yield: float = 0.0,
) -> tuple[float, Greeks]:
    """Black-Scholes-Merton price and Greeks via adjoint AD.

    First order (delta, vega, rho, theta) comes from a single forward plus a
    single backward tape sweep and is exact to machine precision. The tape
    leaf is tau = time-TO-expiry, so the calendar-time decay flips the sign:
    ``theta = -dP/dtau`` (matching :func:`optitrade.pricing.bs_greeks_at`).

    Second order is FD-of-AD: gamma, vanna and volga are central differences
    of the AD delta/vega over two spot-bumped and two vol-bumped tape
    evaluations (four extra sweeps in total, still O(1) pricings).

    Unit conventions follow :mod:`optitrade.core.types` (vega per unit vol,
    rho per unit rate, theta per year).
    """
    price, delta, vega, rho, dp_dtau = _bs_first_order(
        spot, strike, expiry, rate, vol, option_type, dividend_yield
    )

    ds = spot * _REL_SPOT_BUMP
    dv = _ABS_VOL_BUMP
    _, delta_su, _, _, _ = _bs_first_order(
        spot + ds, strike, expiry, rate, vol, option_type, dividend_yield
    )
    _, delta_sd, _, _, _ = _bs_first_order(
        spot - ds, strike, expiry, rate, vol, option_type, dividend_yield
    )
    _, delta_vu, vega_vu, _, _ = _bs_first_order(
        spot, strike, expiry, rate, vol + dv, option_type, dividend_yield
    )
    _, delta_vd, vega_vd, _, _ = _bs_first_order(
        spot, strike, expiry, rate, vol - dv, option_type, dividend_yield
    )

    greeks = Greeks(
        delta=delta,
        gamma=(delta_su - delta_sd) / (2.0 * ds),
        vega=vega,
        theta=-dp_dtau,
        rho=rho,
        vanna=(delta_vu - delta_vd) / (2.0 * dv),
        volga=(vega_vu - vega_vd) / (2.0 * dv),
    )
    return price, greeks


__all__ = [
    "Tape",
    "Var",
    "bs_price_adjoint",
    "exp",
    "log",
    "norm_cdf",
    "norm_pdf",
    "sqrt",
]

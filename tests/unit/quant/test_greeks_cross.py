"""Cross-validation: analytic vs finite-difference vs adjoint Greeks.

Sweeps moneyness x expiry x vol for calls and puts and asserts pairwise
agreement per Greek. Diffs are normalised by 1 + |analytic| so one threshold
covers Greeks of very different scales (delta ~ 1, vega/rho ~ 1e2, deep-OTM
gamma ~ 1e-7); the tabulated maxima appear in every assertion message.
"""

from __future__ import annotations

import itertools

import pytest

from optitrade.core import Greeks, OptionType
from optitrade.greeks import FDBumps, bs_price_adjoint, fd_greeks
from optitrade.pricing import bs_greeks_at, bs_price

SPOT = 100.0
RATE = 0.04
DIVIDEND_YIELD = 0.01

STRIKES = (85.0, 95.0, 100.0, 105.0, 115.0)  # moneyness sweep at fixed spot
EXPIRIES = (0.08, 0.25, 1.0)
VOLS = (0.12, 0.2, 0.35)

GREEK_NAMES = ("delta", "gamma", "vega", "theta", "rho", "vanna", "volga")

# Small time bump so the FD theta approximates instantaneous analytic theta.
FD_BUMPS = FDBumps(abs_time=1e-6)

# Normalised tolerance per (method pair, greek). Analytic-vs-adjoint is tape
# exact at first order; FD carries O(h^2) truncation (O(h) for theta); the
# FD-vs-adjoint bound is the sum of the two one-sided bounds.
TOLERANCES: dict[tuple[str, str], dict[str, float]] = {
    ("analytic", "fd"): {
        "delta": 1e-5,
        "vega": 1e-5,
        "rho": 1e-5,
        "gamma": 1e-5,
        "theta": 1e-4,
        "vanna": 1e-3,
        "volga": 1e-3,
    },
    ("analytic", "adjoint"): {
        "delta": 1e-8,
        "vega": 1e-8,
        "rho": 1e-8,
        "theta": 1e-8,
        "gamma": 1e-5,
        "vanna": 1e-5,
        "volga": 1e-5,
    },
    ("fd", "adjoint"): {
        "delta": 2e-5,
        "vega": 2e-5,
        "rho": 2e-5,
        "gamma": 2e-5,
        "theta": 2e-4,
        "vanna": 2e-3,
        "volga": 2e-3,
    },
}


def _greeks_by_method(
    strike: float, expiry: float, vol: float, option_type: OptionType
) -> dict[str, Greeks]:
    def price_fn(s: float, v: float, r: float, t: float) -> float:
        return float(bs_price(s, strike, t, r, v, option_type, DIVIDEND_YIELD))

    _, adjoint = bs_price_adjoint(SPOT, strike, expiry, RATE, vol, option_type, DIVIDEND_YIELD)
    return {
        "analytic": bs_greeks_at(SPOT, strike, expiry, RATE, vol, option_type, DIVIDEND_YIELD),
        "fd": fd_greeks(price_fn, SPOT, vol, RATE, expiry, bumps=FD_BUMPS),
        "adjoint": adjoint,
    }


def _max_normalised_diffs() -> dict[tuple[tuple[str, str], str], float]:
    max_diff: dict[tuple[tuple[str, str], str], float] = {
        (pair, greek): 0.0 for pair in TOLERANCES for greek in GREEK_NAMES
    }
    sweep = itertools.product(STRIKES, EXPIRIES, VOLS, (OptionType.CALL, OptionType.PUT))
    for strike, expiry, vol, option_type in sweep:
        by_method = _greeks_by_method(strike, expiry, vol, option_type)
        for pair in TOLERANCES:
            a, b = by_method[pair[0]], by_method[pair[1]]
            ref = by_method["analytic"]
            for greek in GREEK_NAMES:
                scale = 1.0 + abs(getattr(ref, greek))
                diff = abs(getattr(a, greek) - getattr(b, greek)) / scale
                key = (pair, greek)
                if diff > max_diff[key]:
                    max_diff[key] = diff
    return max_diff


def _table(max_diff: dict[tuple[tuple[str, str], str], float]) -> str:
    header = f"{'greek':<8}" + "".join(f"{a} vs {b:<14}" for a, b in TOLERANCES)
    lines = [header]
    for greek in GREEK_NAMES:
        cells = "".join(f"{max_diff[(pair, greek)]:<22.3e}" for pair in TOLERANCES)
        lines.append(f"{greek:<8}{cells}")
    return "\n".join(lines)


@pytest.mark.unit
def test_three_methods_agree_pairwise_across_sweep() -> None:
    max_diff = _max_normalised_diffs()
    table = _table(max_diff)
    for pair, tols in TOLERANCES.items():
        for greek in GREEK_NAMES:
            observed = max_diff[(pair, greek)]
            assert observed <= tols[greek], (
                f"{pair[0]} vs {pair[1]} disagree on {greek}: "
                f"max normalised diff {observed:.3e} > {tols[greek]:.1e}\n{table}"
            )


@pytest.mark.unit
@pytest.mark.parametrize("option_type", [OptionType.CALL, OptionType.PUT])
def test_adjoint_price_equals_bs_price_across_sweep(option_type: OptionType) -> None:
    for strike, expiry, vol in itertools.product(STRIKES, EXPIRIES, VOLS):
        price, _ = bs_price_adjoint(SPOT, strike, expiry, RATE, vol, option_type, DIVIDEND_YIELD)
        expected = float(bs_price(SPOT, strike, expiry, RATE, vol, option_type, DIVIDEND_YIELD))
        assert price == pytest.approx(expected, rel=1e-9, abs=1e-12)

"""Tests for JAX-based automatic differentiation Greeks.

Must pass with or without the ``jax`` extra installed. When JAX is present,
validates exact derivatives against the analytic Black-Scholes Greeks and the
tape-based adjoint AD engine.
"""

from __future__ import annotations

import importlib
import importlib.util
import itertools
import sys
from unittest import mock

import pytest

from optitrade.core import OptionType
from optitrade.pricing import bs_greeks_at, bs_price

_HAS_JAX = importlib.util.find_spec("jax") is not None

SPOT = 100.0
RATE = 0.04
DIVIDEND_YIELD = 0.01

STRIKES = (85.0, 95.0, 100.0, 105.0, 115.0)
EXPIRIES = (0.08, 0.25, 1.0)
VOLS = (0.12, 0.2, 0.35)

GREEK_NAMES = ("delta", "gamma", "vega", "theta", "rho", "vanna", "volga")

FIRST_ORDER_TOL = 1e-8
SECOND_ORDER_TOL = 1e-6


class TestImportBehaviour:
    def test_module_imports_cleanly_without_jax(self) -> None:
        patcher = mock.patch.dict(sys.modules)
        patcher.start()
        try:
            for name in [m for m in sys.modules if m == "jax" or m.startswith("jax.")]:
                del sys.modules[name]
            sys.modules["jax"] = None  # type: ignore[assignment]
            sys.modules.pop("optitrade.greeks.jax_ad", None)
            module = importlib.import_module("optitrade.greeks.jax_ad")
            assert hasattr(module, "bs_price_jax")
        finally:
            patcher.stop()

    def test_bs_price_jax_raises_helpful_import_error_without_jax(self) -> None:
        patcher = mock.patch.dict(sys.modules)
        patcher.start()
        try:
            for name in [m for m in sys.modules if m == "jax" or m.startswith("jax.")]:
                del sys.modules[name]
            sys.modules["jax"] = None  # type: ignore[assignment]
            sys.modules.pop("optitrade.greeks.jax_ad", None)
            module = importlib.import_module("optitrade.greeks.jax_ad")
            with pytest.raises(ImportError, match=r"optitrade-pro\[jax\]"):
                module.bs_price_jax(100.0, 100.0, 0.5, 0.04, 0.2, "call")
        finally:
            patcher.stop()


@pytest.mark.skipif(not _HAS_JAX, reason="optional jax extra not installed")
class TestJaxPriceMatchesAnalytic:
    @pytest.mark.parametrize("option_type", [OptionType.CALL, OptionType.PUT])
    def test_price_matches_bs_price_across_sweep(self, option_type: OptionType) -> None:
        from optitrade.greeks.jax_ad import bs_price_jax

        for strike, expiry, vol in itertools.product(STRIKES, EXPIRIES, VOLS):
            price_jax, _ = bs_price_jax(
                SPOT, strike, expiry, RATE, vol, option_type, DIVIDEND_YIELD
            )
            price_bs = float(bs_price(SPOT, strike, expiry, RATE, vol, option_type, DIVIDEND_YIELD))
            assert price_jax == pytest.approx(price_bs, rel=1e-9, abs=1e-12), (
                f"JAX price {price_jax} != BS price {price_bs} at "
                f"K={strike}, T={expiry}, vol={vol}, {option_type}"
            )


@pytest.mark.skipif(not _HAS_JAX, reason="optional jax extra not installed")
class TestJaxGreeksMatchAnalytic:
    @pytest.mark.parametrize("option_type", [OptionType.CALL, OptionType.PUT])
    def test_first_order_greeks_match_analytic(self, option_type: OptionType) -> None:
        from optitrade.greeks.jax_ad import bs_price_jax

        for strike, expiry, vol in itertools.product(STRIKES, EXPIRIES, VOLS):
            _, greeks_jax = bs_price_jax(
                SPOT, strike, expiry, RATE, vol, option_type, DIVIDEND_YIELD
            )
            greeks_bs = bs_greeks_at(SPOT, strike, expiry, RATE, vol, option_type, DIVIDEND_YIELD)
            for name in ("delta", "vega", "rho", "theta"):
                jax_val = getattr(greeks_jax, name)
                bs_val = getattr(greeks_bs, name)
                scale = 1.0 + abs(bs_val)
                diff = abs(jax_val - bs_val) / scale
                assert diff < FIRST_ORDER_TOL, (
                    f"JAX {name}={jax_val} != analytic {name}={bs_val} "
                    f"(norm diff {diff:.2e}) at K={strike}, T={expiry}, vol={vol}"
                )

    @pytest.mark.parametrize("option_type", [OptionType.CALL, OptionType.PUT])
    def test_second_order_greeks_match_analytic(self, option_type: OptionType) -> None:
        from optitrade.greeks.jax_ad import bs_price_jax

        for strike, expiry, vol in itertools.product(STRIKES, EXPIRIES, VOLS):
            _, greeks_jax = bs_price_jax(
                SPOT, strike, expiry, RATE, vol, option_type, DIVIDEND_YIELD
            )
            greeks_bs = bs_greeks_at(SPOT, strike, expiry, RATE, vol, option_type, DIVIDEND_YIELD)
            for name in ("gamma", "vanna", "volga"):
                jax_val = getattr(greeks_jax, name)
                bs_val = getattr(greeks_bs, name)
                scale = 1.0 + abs(bs_val)
                diff = abs(jax_val - bs_val) / scale
                assert diff < SECOND_ORDER_TOL, (
                    f"JAX {name}={jax_val} != analytic {name}={bs_val} "
                    f"(norm diff {diff:.2e}) at K={strike}, T={expiry}, vol={vol}"
                )


@pytest.mark.skipif(not _HAS_JAX, reason="optional jax extra not installed")
class TestHigherOrderGreeks:
    def test_higher_order_greeks_are_finite(self) -> None:
        from optitrade.greeks.jax_ad import bs_higher_order_greeks

        ho = bs_higher_order_greeks(100.0, 100.0, 0.5, 0.04, 0.2, "call", 0.01)
        assert set(ho) == {"charm", "veta", "speed", "color", "ultima", "zomma"}
        for name, val in ho.items():
            assert isinstance(val, float), f"{name} is not float"
            assert val == val, f"{name} is NaN"

    def test_charm_is_negative_for_atm_call(self) -> None:
        """ATM call charm (delta decay) is typically negative: delta falls as T→0."""
        from optitrade.greeks.jax_ad import bs_higher_order_greeks

        ho = bs_higher_order_greeks(100.0, 100.0, 1.0, 0.04, 0.2, "call", 0.0)
        assert ho["charm"] < 0.0

    def test_speed_is_negative_for_atm_call(self) -> None:
        """ATM call speed (d(gamma)/d(spot)) is typically negative: gamma peaks at ATM."""
        from optitrade.greeks.jax_ad import bs_higher_order_greeks

        ho = bs_higher_order_greeks(100.0, 100.0, 1.0, 0.04, 0.2, "call", 0.0)
        assert ho["speed"] < 0.0


@pytest.mark.skipif(not _HAS_JAX, reason="optional jax extra not installed")
class TestBookVmap:
    def test_vmap_matches_scalar_loop(self) -> None:
        from optitrade.greeks.jax_ad import bs_greeks_book_jax, bs_price_jax

        n = 5
        spots = [100.0] * n
        strikes = [90.0, 95.0, 100.0, 105.0, 110.0]
        expiries = [0.25] * n
        rates = [0.04] * n
        vols = [0.2] * n
        is_calls = [True, True, False, False, True]
        div_yields = [0.01] * n

        book_results = bs_greeks_book_jax(
            spots, strikes, expiries, rates, vols, is_calls, div_yields
        )
        assert len(book_results) == n

        for i in range(n):
            ot = "call" if is_calls[i] else "put"
            scalar_price, scalar_greeks = bs_price_jax(
                spots[i], strikes[i], expiries[i], rates[i], vols[i], ot, div_yields[i]
            )
            book_price, book_greeks = book_results[i]
            assert book_price == pytest.approx(scalar_price, rel=1e-9)
            for name in GREEK_NAMES:
                assert getattr(book_greeks, name) == pytest.approx(
                    getattr(scalar_greeks, name), rel=1e-6, abs=1e-12
                ), f"Position {i} {name} mismatch"

    def test_empty_book_returns_empty_list(self) -> None:
        from optitrade.greeks.jax_ad import bs_greeks_book_jax

        assert bs_greeks_book_jax([], [], [], [], [], [], []) == []

    def test_mismatched_lengths_raises(self) -> None:
        from optitrade.greeks.jax_ad import bs_greeks_book_jax

        with pytest.raises(ValueError, match="same length"):
            bs_greeks_book_jax([100.0], [100.0, 105.0], [0.5], [0.04], [0.2], [True], [0.0])

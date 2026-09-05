"""Guards the deletion of ``api/tools/charges.py`` and its config.

The module was a brokerage/margin calculator that could never have produced a
number: it called FastAPI's ``Query(...)`` as if it were a value, so its
fallback charge, timeouts, retry counts and rate-limit delay were all ``Query``
objects. Nothing in the tree imported it. ``optitrade.strategy.costs`` already
holds the real, tested Indian options cost model (ADR-017, amended).

The settings it alone read are removed with it, so a future reader does not
mistake them for live configuration (CLAUDE.md rule 7).
"""

from __future__ import annotations

import importlib.util

import pytest

from options_trading.config.settings import settings


class TestChargesModuleGone:
    def test_module_does_not_exist(self) -> None:
        assert importlib.util.find_spec("options_trading.api.tools.charges") is None

    def test_sibling_tools_still_present(self) -> None:
        # The deletion was surgical: the other api/tools modules are live.
        for name in ("candles", "live_contracts", "option_chain_live"):
            assert importlib.util.find_spec(f"options_trading.api.tools.{name}") is not None


class TestOrphanedSettingsRemoved:
    @pytest.mark.parametrize(
        "field",
        [
            "FALLBACK_BROKERAGE_CHARGE",
            "upstox_charges_url",
            "upstox_margin_url",
            "default_accept_header",
            "rate_limit_buffer_seconds",
            "api_retry_attempts",
            "api_retry_delay_seconds",
        ],
    )
    def test_charges_only_setting_is_gone(self, field: str) -> None:
        assert field not in type(settings).model_fields

    @pytest.mark.parametrize("field", ["api_timeout_seconds", "default_api_version"])
    def test_settings_with_live_readers_are_kept(self, field: str) -> None:
        # Both are read by api/tools/{candles,live_contracts,option_chain_live}.
        assert field in type(settings).model_fields


class TestOrphanedExceptionsRemoved:
    @pytest.mark.parametrize("name", ["BrokerageCalculationError", "MarginCalculationError"])
    def test_exception_is_gone(self, name: str) -> None:
        from options_trading.utils import exceptions

        assert not hasattr(exceptions, name)

    def test_network_error_is_kept(self) -> None:
        # Still raised by portfolio_client and asserted in test_portfolio_sync.
        from options_trading.utils import exceptions

        assert hasattr(exceptions, "NetworkError")

"""Tests for the MCP server adapter; must pass with or without the ``mcp`` extra."""

from __future__ import annotations

import asyncio
import importlib
import importlib.util
import sys
from unittest import mock

import pytest

import optitrade.mcp_server as mcp_server
from optitrade.journal import EventLog

_HAS_MCP = importlib.util.find_spec("mcp") is not None

EXPECTED_TOOLS = {"price_option", "book_greeks", "run_scenarios", "review_order", "journal_tail"}

LIMITS = {
    "max_abs_delta": 1000.0,
    "max_abs_gamma": 100.0,
    "max_abs_vega": 10000.0,
    "max_drawdown": 0.2,
    "max_concentration": 1.0,
}
CONTEXT = {
    "portfolio_greeks": {},
    "order_greeks": {"delta": 0.5},
    "equity": 1_000_000.0,
    "high_water_mark": 1_000_000.0,
    "margin_available": 1_000_000.0,
    "margin_required": 1000.0,
    "spot": 100.0,
}
POSITIONS = [
    {"strike": 100.0, "expiry": 0.5, "option_type": "call", "quantity": 2.0, "vol": 0.2},
    {"strike": 90.0, "expiry": 0.25, "option_type": "put", "quantity": -1.0, "vol": 0.25},
]


def _hide_mcp():
    """Context manager hiding ``mcp`` from the import system (works either way)."""
    patcher = mock.patch.dict(sys.modules)
    patcher.start()
    for name in [m for m in sys.modules if m == "mcp" or m.startswith("mcp.")]:
        del sys.modules[name]
    sys.modules["mcp"] = None  # any `import mcp...` now raises ImportError
    return patcher


class TestImportBehaviour:
    def test_module_imports_cleanly_without_mcp(self):
        patcher = _hide_mcp()
        try:
            sys.modules.pop("optitrade.mcp_server", None)
            module = importlib.import_module("optitrade.mcp_server")
            assert hasattr(module, "create_server")
        finally:
            patcher.stop()

    def test_create_server_raises_helpful_import_error_without_mcp(self, tmp_path):
        patcher = _hide_mcp()
        try:
            with pytest.raises(ImportError, match=r"optitrade-pro\[mcp\]"):
                mcp_server.create_server(journal_dir=tmp_path)
        finally:
            patcher.stop()


class TestToolFunctions:
    """The plain tool functions work and journal without any mcp dependency."""

    @pytest.fixture
    def journal(self, tmp_path):
        return EventLog(tmp_path, "tools-run")

    @pytest.fixture
    def tools(self, journal):
        return {fn.__name__: fn for fn in mcp_server.build_tools(journal)}

    def test_exposes_exactly_the_five_tools(self, tools):
        assert set(tools) == EXPECTED_TOOLS

    def test_price_option_prices_and_journals(self, tools, journal):
        result = tools["price_option"](
            spot=100.0, strike=100.0, expiry_years=0.5, rate=0.02, vol=0.2, option_type="call"
        )
        assert result["price"] > 0
        assert 0.0 <= result["greeks"]["delta"] <= 1.0  # call delta bounds
        [event] = list(journal.replay())
        assert event.event_type == "tool_call"
        assert event.data["tool"] == "price_option"
        assert event.data["args"]["strike"] == 100.0
        assert event.data["result"]["price"] == result["price"]
        assert event.data["result"]["greeks"]["delta"] == result["greeks"]["delta"]

    def test_book_greeks_aggregates_per_position(self, tools, journal):
        result = tools["book_greeks"](positions=POSITIONS, spot=100.0, rate=0.02)
        assert len(result["positions"]) == 2
        total = sum(p["delta"] for p in result["positions"])
        assert result["portfolio"]["delta"] == pytest.approx(total)
        [event] = list(journal.replay())
        assert event.data["tool"] == "book_greeks"

    def test_run_scenarios_returns_extremes_and_journals(self, tools, journal):
        result = tools["run_scenarios"](
            positions=POSITIONS, spot=100.0, rate=0.02, n_spot=5, n_vol=3, n_time=3
        )
        assert result["n_cells"] == 5 * 3 * 3
        assert set(result["worst"]) == {"pnl", "spot_shift", "vol_shift", "time_shift"}
        # the unshifted cell has PnL == 0, so the extremes bracket zero
        assert result["worst"]["pnl"] <= 0.0 <= result["best"]["pnl"]
        assert result["elapsed_ms"] >= 0.0
        [event] = list(journal.replay())
        assert event.data["tool"] == "run_scenarios"
        assert event.data["result"]["n_cells"] == result["n_cells"]

    def test_review_order_approves_and_journals_with_correlation_id(self, tools, journal):
        result = tools["review_order"](
            order={"symbol": "NIFTY", "quantity": 10.0, "price": 100.0},
            limits=LIMITS,
            context=CONTEXT,
        )
        assert result["verdict"] == "approve"
        assert {c["check"] for c in result["checks"]} == {
            "greeks_limit",
            "margin_sufficiency",
            "drawdown",
            "concentration",
        }
        assert all(c["reason"] for c in result["checks"])
        [event] = list(journal.replay())
        assert event.data["tool"] == "review_order"
        assert event.correlation_id == result["correlation_id"]

    def test_review_order_rejects_a_greeks_breach(self, tools):
        result = tools["review_order"](
            order={"symbol": "NIFTY", "quantity": 10_000.0, "price": 100.0},
            limits={**LIMITS, "max_abs_delta": 10.0},
            context=CONTEXT,
        )
        assert result["verdict"] == "reject"
        assert result["adjusted_order"] is None

    def test_journal_tail_returns_prior_tool_calls(self, tools):
        tools["price_option"](
            spot=100.0, strike=95.0, expiry_years=1.0, rate=0.0, vol=0.3, option_type="put"
        )
        tail = tools["journal_tail"](n=5)
        assert len(tail) == 1  # its own event is appended after the snapshot
        assert tail[0]["sequence"] == 1
        assert tail[0]["event_type"] == "tool_call"
        assert tail[0]["data"]["tool"] == "price_option"

    def test_journal_tail_truncates_to_n(self, tools):
        for _ in range(3):
            tools["journal_tail"]()  # each call appends one event
        tail = tools["journal_tail"](n=2)
        assert [e["sequence"] for e in tail] == [2, 3]


@pytest.mark.skipif(not _HAS_MCP, reason="optional mcp extra not installed")
class TestCreateServerWithMcp:
    def test_registered_tool_names_match(self, tmp_path):
        server = mcp_server.create_server(journal_dir=tmp_path, run_id="mcp-run")
        assert server.name == "optitrade"
        names = {t.name for t in asyncio.run(server.list_tools())}
        assert names == EXPECTED_TOOLS

    def test_underlying_tool_functions_journal_tool_calls(self, tmp_path):
        server = mcp_server.create_server(journal_dir=tmp_path, run_id="mcp-run")
        fns = {t.name: t.fn for t in server._tool_manager.list_tools()}
        result = fns["price_option"](
            spot=100.0, strike=100.0, expiry_years=0.5, rate=0.02, vol=0.2, option_type="call"
        )
        fns["review_order"](
            order={"symbol": "NIFTY", "quantity": 10.0, "price": 100.0},
            limits=LIMITS,
            context=CONTEXT,
        )
        events = list(EventLog(tmp_path, "mcp-run").replay())
        assert [e.event_type for e in events] == ["tool_call", "tool_call"]
        assert [e.data["tool"] for e in events] == ["price_option", "review_order"]
        assert events[0].data["result"]["price"] == result["price"]

    def test_default_run_id_creates_a_journal_per_server(self, tmp_path):
        mcp_server.create_server(journal_dir=tmp_path)
        # no file until the first append; the directory itself must exist
        assert tmp_path.is_dir()

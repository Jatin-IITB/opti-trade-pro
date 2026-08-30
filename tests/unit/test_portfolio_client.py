"""Tests for the Upstox portfolio client and position mapper."""

from __future__ import annotations

import httpx
import pytest

from options_trading.services.portfolio_client import (
    UpstoxPortfolioClient,
    _expiry_str_to_date,
    _expiry_year_fraction,
    _parse_holding,
    _parse_order,
    _parse_position,
    _parse_trading_symbol,
    to_core_portfolio,
)
from options_trading.utils.exceptions import AuthError, RateLimitError

SAMPLE_POSITION_RAW = {
    "instrument_token": "NSE_FO|NIFTY2490724500CE",
    "tradingsymbol": "NIFTY2490724500CE",
    "exchange": "NSE_FO",
    "product": "D",
    "quantity": 50,
    "average_buy_price": 180.0,
    "average_sell_price": 0.0,
    "last_price": 260.0,
    "pnl": 4000.0,
    "lot_size": 50,
    "option_type": "CE",
    "strike_price": 24500.0,
    "expiry": "2025-09-07",
}

SAMPLE_POSITION_PE = {
    "instrument_token": "NSE_FO|NIFTY2490724000PE",
    "tradingsymbol": "NIFTY2490724000PE",
    "exchange": "NSE_FO",
    "product": "D",
    "quantity": -50,
    "average_buy_price": 0.0,
    "average_sell_price": 120.0,
    "last_price": 95.0,
    "pnl": 1250.0,
    "lot_size": 50,
    "option_type": "PE",
    "strike_price": 24000.0,
    "expiry": "2025-09-07",
}

SAMPLE_HOLDING_RAW = {
    "isin": "INE002A01018",
    "trading_symbol": "RELIANCE",
    "exchange": "NSE",
    "quantity": 10,
    "average_price": 2450.0,
    "last_price": 2520.0,
    "pnl": 700.0,
    "day_change": 15.5,
    "day_change_percentage": 0.62,
}

SAMPLE_ORDER_RAW = {
    "order_id": "240830000001",
    "tradingsymbol": "NIFTY2490724500CE",
    "exchange": "NSE_FO",
    "order_type": "LIMIT",
    "transaction_type": "BUY",
    "quantity": 50,
    "price": 180.0,
    "trigger_price": 0.0,
    "status": "complete",
    "filled_quantity": 50,
    "average_price": 180.0,
    "order_timestamp": "2025-08-30T10:15:00",
    "product": "D",
}


class TestParsePosition:
    def test_parses_all_fields(self):
        pos = _parse_position(SAMPLE_POSITION_RAW)
        assert pos.trading_symbol == "NIFTY2490724500CE"
        assert pos.quantity == 50
        assert pos.buy_price == 180.0
        assert pos.last_price == 260.0
        assert pos.pnl == 4000.0
        assert pos.option_type == "CE"
        assert pos.strike_price == 24500.0
        assert pos.expiry == "2025-09-07"
        assert pos.multiplier == 50

    def test_handles_alias_fields(self):
        raw = {"instrument_key": "KEY", "trading_symbol": "SYM", "net_quantity": 25}
        pos = _parse_position(raw)
        assert pos.instrument_key == "KEY"
        assert pos.trading_symbol == "SYM"
        assert pos.quantity == 25

    def test_handles_empty_dict(self):
        pos = _parse_position({})
        assert pos.quantity == 0
        assert pos.strike_price is None


class TestParseHolding:
    def test_parses_holding(self):
        h = _parse_holding(SAMPLE_HOLDING_RAW)
        assert h.trading_symbol == "RELIANCE"
        assert h.quantity == 10
        assert h.average_price == 2450.0
        assert h.pnl == 700.0


class TestParseOrder:
    def test_parses_order(self):
        o = _parse_order(SAMPLE_ORDER_RAW)
        assert o.order_id == "240830000001"
        assert o.status == "complete"
        assert o.filled_quantity == 50
        assert o.transaction_type == "BUY"


class TestParseTradingSymbol:
    def test_parses_nifty_ce(self):
        _exp, strike, opt = _parse_trading_symbol("NIFTY2490724500CE")
        assert strike == 24500.0
        assert opt == "CE"

    def test_parses_nifty_pe(self):
        _exp, strike, opt = _parse_trading_symbol("NIFTY2490724000PE")
        assert strike == 24000.0
        assert opt == "PE"

    def test_returns_none_for_equity(self):
        _exp, strike, opt = _parse_trading_symbol("RELIANCE")
        assert strike is None
        assert opt is None


class TestExpiryParsing:
    def test_yyyy_mm_dd(self):
        d = _expiry_str_to_date("2025-09-07")
        assert d is not None
        assert d.year == 2025
        assert d.month == 9
        assert d.day == 7

    def test_ddmmmyy(self):
        d = _expiry_str_to_date("07Sep25")
        assert d is not None
        assert d.day == 7
        assert d.month == 9

    def test_invalid_returns_none(self):
        assert _expiry_str_to_date("not-a-date") is None

    def test_year_fraction_positive(self):
        from datetime import date as d

        expiry = d(2025, 12, 25)
        now = 1_724_000_000.0
        yf = _expiry_year_fraction(expiry, now)
        assert yf > 0


class TestToCorePortfolio:
    def test_maps_option_positions(self):
        pos = _parse_position(SAMPLE_POSITION_RAW)
        portfolio = to_core_portfolio([pos], spot=24500.0, now_fn=lambda: 1_724_000_000.0)
        assert len(portfolio.positions) == 1
        cp = portfolio.positions[0]
        assert cp.contract.strike == 24500.0
        assert cp.contract.option_type.value == "call"
        assert cp.quantity == 50.0
        assert cp.entry_price == 180.0

    def test_maps_put_position(self):
        pos = _parse_position(SAMPLE_POSITION_PE)
        portfolio = to_core_portfolio([pos], spot=24500.0, now_fn=lambda: 1_724_000_000.0)
        assert len(portfolio.positions) == 1
        cp = portfolio.positions[0]
        assert cp.contract.option_type.value == "put"
        assert cp.quantity == -50.0
        assert cp.entry_price == 120.0

    def test_skips_equity_positions(self):
        equity_raw = {
            "tradingsymbol": "RELIANCE",
            "quantity": 10,
            "average_buy_price": 2450.0,
            "last_price": 2520.0,
            "pnl": 700.0,
        }
        pos = _parse_position(equity_raw)
        portfolio = to_core_portfolio([pos], spot=2500.0, now_fn=lambda: 1_724_000_000.0)
        assert len(portfolio.positions) == 0

    def test_empty_positions(self):
        portfolio = to_core_portfolio([], spot=24500.0)
        assert len(portfolio.positions) == 0
        assert portfolio.equity == 0.0

    def test_multiple_positions(self):
        pos_ce = _parse_position(SAMPLE_POSITION_RAW)
        pos_pe = _parse_position(SAMPLE_POSITION_PE)
        portfolio = to_core_portfolio(
            [pos_ce, pos_pe], spot=24500.0, now_fn=lambda: 1_724_000_000.0
        )
        assert len(portfolio.positions) == 2
        assert portfolio.equity == 4000.0 + 1250.0

    def test_expiry_year_fraction_is_positive(self):
        pos = _parse_position(SAMPLE_POSITION_RAW)
        portfolio = to_core_portfolio([pos], spot=24500.0, now_fn=lambda: 1_724_000_000.0)
        assert portfolio.positions[0].contract.expiry > 0

    def test_deterministic(self):
        pos = _parse_position(SAMPLE_POSITION_RAW)

        def now_fn() -> float:
            return 1_724_000_000.0

        a = to_core_portfolio([pos], spot=24500.0, now_fn=now_fn)
        b = to_core_portfolio([pos], spot=24500.0, now_fn=now_fn)
        assert a.positions[0].contract.expiry == b.positions[0].contract.expiry
        assert a.equity == b.equity


class TestUpstoxPortfolioClient:
    def test_requires_token(self):
        with pytest.raises(ValueError, match="Either"):
            UpstoxPortfolioClient()

    def test_accepts_access_token(self):
        client = UpstoxPortfolioClient(access_token="test-token")
        assert client._get_token() == "test-token"

    def test_accepts_callable(self):
        client = UpstoxPortfolioClient(get_token=lambda: "dynamic-token")
        assert client._get_token() == "dynamic-token"

    @pytest.mark.asyncio
    async def test_fetch_positions_parses(self, monkeypatch):
        async def mock_get(self, url, **kwargs):
            return httpx.Response(
                200,
                json={"status": "success", "data": [SAMPLE_POSITION_RAW]},
                request=httpx.Request("GET", url),
            )

        monkeypatch.setattr(httpx.AsyncClient, "get", mock_get)
        client = UpstoxPortfolioClient(access_token="tok")
        positions = await client.fetch_positions()
        assert len(positions) == 1
        assert positions[0].trading_symbol == "NIFTY2490724500CE"

    @pytest.mark.asyncio
    async def test_fetch_positions_handles_empty(self, monkeypatch):
        async def mock_get(self, url, **kwargs):
            return httpx.Response(
                200,
                json={"status": "success", "data": []},
                request=httpx.Request("GET", url),
            )

        monkeypatch.setattr(httpx.AsyncClient, "get", mock_get)
        client = UpstoxPortfolioClient(access_token="tok")
        positions = await client.fetch_positions()
        assert positions == []

    @pytest.mark.asyncio
    async def test_fetch_positions_raises_on_401(self, monkeypatch):
        async def mock_get(self, url, **kwargs):
            return httpx.Response(
                401,
                json={"error": "unauthorized"},
                request=httpx.Request("GET", url),
            )

        monkeypatch.setattr(httpx.AsyncClient, "get", mock_get)
        client = UpstoxPortfolioClient(access_token="bad-tok")
        with pytest.raises(AuthError):
            await client.fetch_positions()

    @pytest.mark.asyncio
    async def test_fetch_positions_raises_on_429(self, monkeypatch):
        async def mock_get(self, url, **kwargs):
            return httpx.Response(
                429,
                json={"error": "rate limited"},
                request=httpx.Request("GET", url),
            )

        monkeypatch.setattr(httpx.AsyncClient, "get", mock_get)
        client = UpstoxPortfolioClient(access_token="tok")
        with pytest.raises(RateLimitError):
            await client.fetch_positions()

    @pytest.mark.asyncio
    async def test_fetch_holdings_parses(self, monkeypatch):
        async def mock_get(self, url, **kwargs):
            return httpx.Response(
                200,
                json={"status": "success", "data": [SAMPLE_HOLDING_RAW]},
                request=httpx.Request("GET", url),
            )

        monkeypatch.setattr(httpx.AsyncClient, "get", mock_get)
        client = UpstoxPortfolioClient(access_token="tok")
        holdings = await client.fetch_holdings()
        assert len(holdings) == 1
        assert holdings[0].trading_symbol == "RELIANCE"

    @pytest.mark.asyncio
    async def test_fetch_orders_parses(self, monkeypatch):
        async def mock_get(self, url, **kwargs):
            return httpx.Response(
                200,
                json={"status": "success", "data": [SAMPLE_ORDER_RAW]},
                request=httpx.Request("GET", url),
            )

        monkeypatch.setattr(httpx.AsyncClient, "get", mock_get)
        client = UpstoxPortfolioClient(access_token="tok")
        orders = await client.fetch_orders()
        assert len(orders) == 1
        assert orders[0].order_id == "240830000001"

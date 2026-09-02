"""Tests for the Upstox portfolio client and position mapper."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from options_trading.services.portfolio_client import (
    MIN_EXPIRY_YEARS,
    UpstoxFunds,
    UpstoxPortfolioClient,
    _expiry_str_to_date,
    _expiry_year_fraction,
    _parse_funds,
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
        portfolio = to_core_portfolio([pos], now_fn=lambda: 1_724_000_000.0)
        assert len(portfolio.positions) == 1
        cp = portfolio.positions[0]
        assert cp.contract.strike == 24500.0
        assert cp.contract.option_type.value == "call"
        assert cp.quantity == 50.0
        assert cp.entry_price == 180.0

    def test_maps_put_position(self):
        pos = _parse_position(SAMPLE_POSITION_PE)
        portfolio = to_core_portfolio([pos], now_fn=lambda: 1_724_000_000.0)
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
        portfolio = to_core_portfolio([pos], now_fn=lambda: 1_724_000_000.0)
        assert len(portfolio.positions) == 0

    def test_empty_positions(self):
        portfolio = to_core_portfolio([])
        assert len(portfolio.positions) == 0
        assert portfolio.equity == 0.0

    def test_multiple_positions(self):
        pos_ce = _parse_position(SAMPLE_POSITION_RAW)
        pos_pe = _parse_position(SAMPLE_POSITION_PE)
        portfolio = to_core_portfolio([pos_ce, pos_pe], now_fn=lambda: 1_724_000_000.0)
        assert len(portfolio.positions) == 2

    def test_equity_is_zero_without_funds(self):
        """Equity must never be back-filled from P&L.

        Regression guard: equity was previously set to sum(position.pnl),
        which then flowed into ``spot`` and corrupted every book Greek.
        """
        pos_ce = _parse_position(SAMPLE_POSITION_RAW)
        pos_pe = _parse_position(SAMPLE_POSITION_PE)
        total_pnl = pos_ce.pnl + pos_pe.pnl
        portfolio = to_core_portfolio([pos_ce, pos_pe], now_fn=lambda: 1_724_000_000.0)
        assert portfolio.equity == 0.0
        assert portfolio.equity != total_pnl
        assert portfolio.margin_available == 0.0

    def test_equity_comes_from_funds(self):
        pos = _parse_position(SAMPLE_POSITION_RAW)
        funds = UpstoxFunds(used_margin=45_000.0, available_margin=155_000.0)
        portfolio = to_core_portfolio([pos], now_fn=lambda: 1_724_000_000.0, funds=funds)
        assert portfolio.equity == 200_000.0
        assert portfolio.margin_available == 155_000.0

    def test_expiry_year_fraction_is_positive(self):
        pos = _parse_position(SAMPLE_POSITION_RAW)
        portfolio = to_core_portfolio([pos], now_fn=lambda: 1_724_000_000.0)
        assert portfolio.positions[0].contract.expiry > 0

    def test_deterministic(self):
        pos = _parse_position(SAMPLE_POSITION_RAW)

        def now_fn() -> float:
            return 1_724_000_000.0

        a = to_core_portfolio([pos], now_fn=now_fn)
        b = to_core_portfolio([pos], now_fn=now_fn)
        assert a.positions[0].contract.expiry == b.positions[0].contract.expiry
        assert a.equity == b.equity


class TestUpstoxFunds:
    def test_total_equity_is_used_plus_available(self):
        funds = UpstoxFunds(used_margin=45_000.0, available_margin=155_000.0)
        assert funds.total_equity == 200_000.0

    def test_margin_utilization(self):
        funds = UpstoxFunds(used_margin=50_000.0, available_margin=150_000.0)
        assert funds.margin_utilization == pytest.approx(0.25)

    def test_margin_utilization_undefined_on_empty_account(self):
        """No denominator means unknown, not 'no margin used'."""
        assert UpstoxFunds().margin_utilization is None

    def test_margin_shortfall_is_not_clamped(self):
        """available_margin goes negative on a shortfall — routine on F&O.

        An over-100% reading is real information the user needs, so it is
        reported rather than clamped.
        """
        funds = UpstoxFunds(used_margin=100_000.0, available_margin=-50_000.0)
        assert funds.margin_utilization == pytest.approx(2.0)

    def test_severe_shortfall_reports_unknown_not_zero(self):
        """When equity is wiped out the ratio is undefined, not 0.0."""
        funds = UpstoxFunds(used_margin=100_000.0, available_margin=-100_000.0)
        assert funds.margin_utilization is None

    def test_parses_upstox_shape(self):
        raw = {
            "used_margin": 45_000.5,
            "payin_amount": 0,
            "span_margin": 30_000.0,
            "adhoc_margin": 0,
            "notional_cash": 0,
            "available_margin": 155_000.25,
            "exposure_margin": 15_000.0,
        }
        funds = _parse_funds(raw)
        assert funds.used_margin == 45_000.5
        assert funds.available_margin == 155_000.25
        assert funds.span_margin == 30_000.0
        assert funds.exposure_margin == 15_000.0

    def test_parses_nulls_as_zero(self):
        funds = _parse_funds({"used_margin": None, "available_margin": 1000.0})
        assert funds.used_margin == 0.0
        assert funds.available_margin == 1000.0


class TestUpstoxPortfolioClient:
    def test_requires_token(self):
        with pytest.raises(ValueError, match="Either"):
            UpstoxPortfolioClient()

    @pytest.mark.asyncio
    async def test_accepts_access_token(self):
        client = UpstoxPortfolioClient(access_token="test-token")
        assert await client._resolve_token() == "test-token"

    @pytest.mark.asyncio
    async def test_resolves_through_token_provider(self):
        """A provider is consulted per request, so refreshes are picked up."""
        tokens = iter(["first-token", "second-token"])
        provider = MagicMock()
        provider.get = AsyncMock(side_effect=lambda: next(tokens))

        client = UpstoxPortfolioClient(token_provider=provider)
        assert await client._resolve_token() == "first-token"
        assert await client._resolve_token() == "second-token"

    @pytest.mark.asyncio
    async def test_headers_carry_resolved_token(self):
        provider = MagicMock()
        provider.get = AsyncMock(return_value="fresh-token")
        client = UpstoxPortfolioClient(token_provider=provider)
        headers = await client._headers()
        assert headers["Authorization"] == "Bearer fresh-token"

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


class TestNullTolerantParsing:
    """Upstox sends explicit nulls for fields that don't apply to an instrument.

    ``dict.get(k, default)`` returns the default only for a *missing* key, so
    a null reached ``int()``/``float()`` and raised, failing the whole sync.
    """

    def test_null_quantity_does_not_crash(self):
        pos = _parse_position({"tradingsymbol": "X", "quantity": None})
        assert pos.quantity == 0

    def test_null_prices_default_to_zero(self):
        pos = _parse_position(
            {"tradingsymbol": "X", "last_price": None, "pnl": None, "buy_price": None}
        )
        assert pos.last_price == 0.0
        assert pos.pnl == 0.0
        assert pos.buy_price == 0.0

    def test_null_holding_fields(self):
        h = _parse_holding({"trading_symbol": "RELIANCE", "quantity": None, "pnl": None})
        assert h.quantity == 0
        assert h.pnl == 0.0

    def test_null_order_fields(self):
        o = _parse_order({"order_id": "1", "quantity": None, "price": None})
        assert o.quantity == 0
        assert o.price == 0.0

    def test_non_numeric_value_falls_back(self):
        pos = _parse_position({"tradingsymbol": "X", "quantity": "not-a-number"})
        assert pos.quantity == 0

    def test_multiplier_defaults_to_one(self):
        assert _parse_position({"tradingsymbol": "X", "multiplier": None}).multiplier == 1


class TestOptionFieldRecovery:
    """Option fields are recovered from the trading symbol at parse time.

    Previously only to_core_portfolio did this, so /summary priced a leg that
    /signals simultaneously labelled moneyness "unknown".
    """

    def test_recovers_strike_and_type_from_symbol(self):
        pos = _parse_position({"tradingsymbol": "NIFTY2490724500CE", "quantity": 50})
        assert pos.strike_price == 24500.0
        assert pos.option_type == "CE"

    def test_explicit_fields_win_over_symbol(self):
        pos = _parse_position(
            {
                "tradingsymbol": "NIFTY2490724500CE",
                "strike_price": 24000.0,
                "option_type": "PE",
            }
        )
        assert pos.strike_price == 24000.0
        assert pos.option_type == "PE"

    def test_equity_symbol_yields_no_option_fields(self):
        pos = _parse_position({"tradingsymbol": "RELIANCE", "quantity": 10})
        assert pos.strike_price is None
        assert pos.option_type is None


class TestExpiredPositions:
    """Expired legs must be dropped before the year-fraction floor applies.

    ``_expiry_year_fraction`` floors at one hour, so an expired option would
    otherwise carry T=1h — where gamma ~ 1/(S*sigma*sqrt(T)) is enormous — and
    a single stale leg would dominate the book's aggregate Greeks.
    """

    # 2024-08-18; the sample positions expire 2025-09-07.
    BEFORE_EXPIRY = 1_724_000_000.0
    # 2025-10-01, comfortably after.
    AFTER_EXPIRY = 1_759_300_000.0

    def test_live_position_is_kept(self):
        pos = _parse_position(SAMPLE_POSITION_RAW)
        book = to_core_portfolio([pos], now_fn=lambda: self.BEFORE_EXPIRY)
        assert len(book.positions) == 1

    def test_expired_position_is_dropped(self):
        pos = _parse_position(SAMPLE_POSITION_RAW)
        book = to_core_portfolio([pos], now_fn=lambda: self.AFTER_EXPIRY)
        assert len(book.positions) == 0

    def test_expiry_floor_is_never_reached_by_expired_legs(self):
        """Guard against the floor silently resurrecting an expired option."""
        pos = _parse_position(SAMPLE_POSITION_RAW)
        book = to_core_portfolio([pos], now_fn=lambda: self.AFTER_EXPIRY)
        assert all(p.contract.expiry > MIN_EXPIRY_YEARS for p in book.positions)

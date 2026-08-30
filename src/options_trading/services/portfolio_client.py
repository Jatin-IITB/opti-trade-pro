"""Upstox portfolio API client: holdings, positions, orders, P&L history.

Typed async client wrapping Upstox v2 portfolio REST endpoints. Uses the
existing ``AuthService.get_valid_access_token()`` on every call so tokens
auto-refresh transparently. All types are frozen dataclasses following the
``CaptureReport`` pattern. Position data is logged at DEBUG only (sensitive).
"""

from __future__ import annotations

import logging
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime
from datetime import time as dt_time
from zoneinfo import ZoneInfo

import httpx

from optitrade.core.types import OptionContract, OptionType, Portfolio, Position

from ..config.settings import settings
from ..utils.exceptions import APIError, AuthError, NetworkError, RateLimitError

logger = logging.getLogger(__name__)

IST = ZoneInfo("Asia/Kolkata")
NSE_CLOSE_TIME = dt_time(15, 30)
SECONDS_PER_YEAR = 365.0 * 86_400.0
MIN_EXPIRY_YEARS = 1.0 / 365.0 / 24.0
FALLBACK_RISK_FREE_RATE = 0.065


@dataclass(frozen=True)
class UpstoxHolding:
    instrument_key: str
    trading_symbol: str
    exchange: str
    quantity: int
    average_price: float
    last_price: float
    pnl: float
    day_change: float
    day_change_percentage: float


@dataclass(frozen=True)
class UpstoxPosition:
    instrument_key: str
    trading_symbol: str
    exchange: str
    product: str
    quantity: int
    buy_price: float
    sell_price: float
    last_price: float
    pnl: float
    multiplier: int
    option_type: str | None
    strike_price: float | None
    expiry: str | None


@dataclass(frozen=True)
class UpstoxOrder:
    order_id: str
    trading_symbol: str
    exchange: str
    order_type: str
    transaction_type: str
    quantity: int
    price: float
    trigger_price: float
    status: str
    filled_quantity: int
    average_price: float
    placed_at: str
    product: str


@dataclass(frozen=True)
class UpstoxPnLEntry:
    trading_symbol: str
    trade_type: str
    quantity: int
    buy_average: float
    sell_average: float
    pnl: float
    charges: float
    trade_date: str


def _first_present(d: dict, keys: tuple[str, ...]) -> object:
    for k in keys:
        if k in d and d[k] is not None:
            return d[k]
    return None


_SYMBOL_RE = re.compile(
    r"^(?P<underlying>[A-Z]+)(?P<expiry>\d{2}[A-Z]{3}\d{2}|\d{2}\d{3})"
    r"(?P<strike>\d+(?:\.\d+)?)?(?P<opt_type>[CP]E)?$"
)


def _parse_trading_symbol(symbol: str) -> tuple[str | None, float | None, str | None]:
    m = _SYMBOL_RE.match(symbol)
    if not m:
        return None, None, None
    opt_type = m.group("opt_type")
    strike_str = m.group("strike")
    expiry_str = m.group("expiry")
    strike = float(strike_str) if strike_str else None
    return expiry_str, strike, opt_type


def _expiry_str_to_date(expiry_str: str) -> date | None:
    for fmt in ("%d%b%y", "%y%m%d", "%Y-%m-%d"):
        try:
            return datetime.strptime(expiry_str, fmt).date()
        except ValueError:
            continue
    return None


def _expiry_year_fraction(expiry_day: date, now_epoch: float) -> float:
    expiry_epoch = datetime.combine(expiry_day, NSE_CLOSE_TIME, tzinfo=IST).timestamp()
    return max((expiry_epoch - now_epoch) / SECONDS_PER_YEAR, MIN_EXPIRY_YEARS)


def _parse_holding(raw: dict) -> UpstoxHolding:
    return UpstoxHolding(
        instrument_key=raw.get("instrument_token", raw.get("isin", "")),
        trading_symbol=raw.get("tradingsymbol", raw.get("trading_symbol", "")),
        exchange=raw.get("exchange", ""),
        quantity=int(raw.get("quantity", 0)),
        average_price=float(raw.get("average_price", 0.0)),
        last_price=float(raw.get("last_price", raw.get("close_price", 0.0))),
        pnl=float(raw.get("pnl", 0.0)),
        day_change=float(raw.get("day_change", 0.0)),
        day_change_percentage=float(raw.get("day_change_percentage", 0.0)),
    )


def _parse_position(raw: dict) -> UpstoxPosition:
    return UpstoxPosition(
        instrument_key=raw.get("instrument_token", raw.get("instrument_key", "")),
        trading_symbol=raw.get("tradingsymbol", raw.get("trading_symbol", "")),
        exchange=raw.get("exchange", ""),
        product=raw.get("product", ""),
        quantity=int(raw.get("quantity", raw.get("net_quantity", 0))),
        buy_price=float(raw.get("buy_price", raw.get("average_buy_price", 0.0))),
        sell_price=float(raw.get("sell_price", raw.get("average_sell_price", 0.0))),
        last_price=float(raw.get("last_price", 0.0)),
        pnl=float(raw.get("pnl", raw.get("realised", 0.0))),
        multiplier=int(raw.get("multiplier", raw.get("lot_size", 1))),
        option_type=raw.get("option_type"),
        strike_price=_safe_float(raw.get("strike_price", raw.get("strike"))),
        expiry=raw.get("expiry"),
    )


def _parse_order(raw: dict) -> UpstoxOrder:
    return UpstoxOrder(
        order_id=raw.get("order_id", ""),
        trading_symbol=raw.get("tradingsymbol", raw.get("trading_symbol", "")),
        exchange=raw.get("exchange", ""),
        order_type=raw.get("order_type", ""),
        transaction_type=raw.get("transaction_type", ""),
        quantity=int(raw.get("quantity", 0)),
        price=float(raw.get("price", 0.0)),
        trigger_price=float(raw.get("trigger_price", 0.0)),
        status=raw.get("status", ""),
        filled_quantity=int(raw.get("filled_quantity", 0)),
        average_price=float(raw.get("average_price", 0.0)),
        placed_at=raw.get("order_timestamp", raw.get("placed_at", "")),
        product=raw.get("product", ""),
    )


def _parse_pnl_entry(raw: dict) -> UpstoxPnLEntry:
    return UpstoxPnLEntry(
        trading_symbol=raw.get("tradingsymbol", raw.get("trading_symbol", "")),
        trade_type=raw.get("trade_type", ""),
        quantity=int(raw.get("quantity", 0)),
        buy_average=float(raw.get("buy_average", raw.get("buy_price", 0.0))),
        sell_average=float(raw.get("sell_average", raw.get("sell_price", 0.0))),
        pnl=float(raw.get("pnl", 0.0)),
        charges=float(raw.get("charges", 0.0)),
        trade_date=raw.get("trade_date", ""),
    )


def _safe_float(v: object) -> float | None:
    if v is None:
        return None
    try:
        return float(v)  # type: ignore[arg-type]
    except (ValueError, TypeError):
        return None


class UpstoxPortfolioClient:
    """Async client for Upstox portfolio REST API endpoints."""

    def __init__(
        self,
        get_token: Callable[[], str] | None = None,
        access_token: str | None = None,
    ) -> None:
        if get_token is not None:
            self._get_token = get_token
        elif access_token is not None:
            self._get_token = lambda: access_token
        else:
            raise ValueError("Either get_token or access_token must be provided")

    def _headers(self) -> dict[str, str]:
        return {
            "Accept": "application/json",
            "Api-Version": "2.0",
            "Authorization": f"Bearer {self._get_token()}",
        }

    async def _get(self, url: str, params: dict | None = None) -> dict:
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(15.0)) as client:
                resp = await client.get(url, headers=self._headers(), params=params)
        except httpx.ConnectError as exc:
            raise NetworkError(f"Connection failed: {exc}") from exc
        except httpx.TimeoutException as exc:
            raise NetworkError(f"Request timed out: {exc}") from exc

        if resp.status_code == 401:
            raise AuthError("Upstox token expired or invalid")
        if resp.status_code == 429:
            raise RateLimitError("Upstox rate limit exceeded")
        if resp.status_code >= 400:
            raise APIError(f"Upstox API error {resp.status_code}: {resp.text}")

        return resp.json()

    async def fetch_holdings(self) -> list[UpstoxHolding]:
        url = getattr(
            settings,
            "upstox_holdings_url",
            f"{settings.upstox_base_url}/v2/portfolio/long-term-holdings",
        )
        body = await self._get(url)
        raw_list = body.get("data", [])
        holdings = [_parse_holding(r) for r in raw_list]
        logger.info("Fetched %d holdings", len(holdings))
        logger.debug("Holdings: %s", holdings)
        return holdings

    async def fetch_positions(self) -> list[UpstoxPosition]:
        url = getattr(
            settings,
            "upstox_positions_url",
            f"{settings.upstox_base_url}/v2/portfolio/short-term-positions",
        )
        body = await self._get(url)
        raw_list = body.get("data", [])
        positions = [_parse_position(r) for r in raw_list]
        logger.info("Fetched %d positions", len(positions))
        logger.debug("Positions: %s", positions)
        return positions

    async def fetch_orders(self) -> list[UpstoxOrder]:
        url = getattr(
            settings,
            "upstox_orders_url",
            f"{settings.upstox_base_url}/v2/order/retrieve-all",
        )
        body = await self._get(url)
        raw_list = body.get("data", [])
        orders = [_parse_order(r) for r in raw_list]
        logger.info("Fetched %d orders", len(orders))
        logger.debug("Orders: %s", orders)
        return orders

    async def fetch_pnl_history(self, from_date: str, to_date: str) -> list[UpstoxPnLEntry]:
        url = getattr(
            settings,
            "upstox_pnl_url",
            f"{settings.upstox_base_url}/v2/trade/profit-loss/data",
        )
        body = await self._get(url, params={"from_date": from_date, "to_date": to_date})
        raw_list = body.get("data", [])
        entries = [_parse_pnl_entry(r) for r in raw_list]
        logger.info("Fetched %d P&L entries", len(entries))
        return entries


def to_core_portfolio(
    positions: list[UpstoxPosition],
    spot: float,
    rate: float | None = None,
    now_fn: Callable[[], float] = time.time,
) -> Portfolio:
    """Map Upstox F&O positions to ``optitrade.core.types.Portfolio``.

    Equity-only positions (no strike/option_type) are skipped; this mapper
    handles index/stock options only. Year-fraction uses ACT/365 + IST
    matching the capture service convention.
    """
    if rate is None:
        rate = float(getattr(settings, "risk_free_rate", FALLBACK_RISK_FREE_RATE))

    now_epoch = now_fn()
    core_positions: list[Position] = []

    for pos in positions:
        strike = pos.strike_price
        opt_type_raw = pos.option_type

        if strike is None or opt_type_raw is None:
            if pos.trading_symbol:
                expiry_str, parsed_strike, parsed_opt = _parse_trading_symbol(pos.trading_symbol)
                if parsed_strike is not None and parsed_opt is not None:
                    strike = parsed_strike
                    opt_type_raw = parsed_opt

        if strike is None or opt_type_raw is None:
            logger.debug("Skipping non-option position: %s", pos.trading_symbol)
            continue

        opt_type_raw_upper = opt_type_raw.upper()
        if opt_type_raw_upper in ("CE", "CALL", "C"):
            option_type = OptionType.CALL
        elif opt_type_raw_upper in ("PE", "PUT", "P"):
            option_type = OptionType.PUT
        else:
            logger.debug("Unknown option type %r for %s", opt_type_raw, pos.trading_symbol)
            continue

        expiry_date_str = pos.expiry
        if expiry_date_str is None:
            _, _, _ = _parse_trading_symbol(pos.trading_symbol)
            logger.debug("No expiry for %s, skipping", pos.trading_symbol)
            continue

        expiry_day = _expiry_str_to_date(expiry_date_str)
        if expiry_day is None:
            logger.debug("Cannot parse expiry %r for %s", expiry_date_str, pos.trading_symbol)
            continue

        expiry_yf = _expiry_year_fraction(expiry_day, now_epoch)

        contract = OptionContract(
            symbol=pos.trading_symbol,
            strike=strike,
            expiry=expiry_yf,
            option_type=option_type,
            lot_size=max(pos.multiplier, 1),
        )
        entry_price = pos.buy_price if pos.quantity > 0 else pos.sell_price
        core_positions.append(
            Position(contract=contract, quantity=float(pos.quantity), entry_price=entry_price)
        )

    total_pnl = sum(p.pnl for p in positions)
    return Portfolio(
        positions=tuple(core_positions),
        equity=total_pnl,
    )


__all__ = [
    "UpstoxHolding",
    "UpstoxOrder",
    "UpstoxPnLEntry",
    "UpstoxPortfolioClient",
    "UpstoxPosition",
    "to_core_portfolio",
]

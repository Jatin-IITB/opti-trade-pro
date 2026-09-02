"""Upstox portfolio API client: holdings, positions, orders, funds, P&L history.

Typed async client wrapping Upstox v2 portfolio REST endpoints. When built
with a :class:`~options_trading.services.token_provider.TokenProvider`, every
request resolves a currently-valid token, so a long-running sync survives the
daily Upstox token expiry. All types are frozen dataclasses following the
``CaptureReport`` pattern. Position data is logged at DEBUG only (sensitive).
"""

from __future__ import annotations

import logging
import re
import ssl
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime
from datetime import time as dt_time
from zoneinfo import ZoneInfo

import httpx
import truststore

from optitrade.core.types import OptionContract, OptionType, Portfolio, Position

from ..config.settings import settings
from ..utils.exceptions import APIError, AuthError, NetworkError, RateLimitError
from .token_provider import TokenProvider

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


@dataclass(frozen=True)
class UpstoxFunds:
    """Account funds and margin for one segment of ``/v2/user/get-funds-and-margin``."""

    used_margin: float = 0.0
    available_margin: float = 0.0
    payin_amount: float = 0.0
    span_margin: float = 0.0
    adhoc_margin: float = 0.0
    notional_cash: float = 0.0
    exposure_margin: float = 0.0

    @property
    def total_equity(self) -> float:
        """Account value: margin currently deployed plus what is still free."""
        return self.used_margin + self.available_margin

    @property
    def margin_utilization(self) -> float | None:
        """Used margin as a fraction of account value, or None if undefined.

        Not clamped to ``[0, 1]``: ``available_margin`` goes negative on a
        margin shortfall, which is routine on an F&O account after an adverse
        move, and a reading above 100% is real information the user needs.

        Returns None when total equity is non-positive — there is no
        meaningful denominator. Returning 0.0 there would read as "no margin
        used", which is the opposite of the truth.
        """
        total = self.total_equity
        if total <= 0:
            return None
        return self.used_margin / total


def _first_present(d: dict, keys: tuple[str, ...]) -> object:
    for k in keys:
        if k in d and d[k] is not None:
            return d[k]
    return None


def _num(raw: dict, *keys: str, default: float = 0.0) -> float:
    """First non-null value among ``keys`` as a float, else ``default``.

    ``dict.get(k, default)`` returns the default only for a *missing* key, so
    an explicit ``"quantity": null`` from the broker reaches ``float()``/
    ``int()`` and raises, failing the entire sync. Upstox does send nulls for
    fields that do not apply to a given instrument.
    """
    value = _first_present(raw, keys)
    if value is None:
        return default
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        logger.debug("Non-numeric value %r for keys %s; using default", value, keys)
        return default


def _int(raw: dict, *keys: str, default: int = 0) -> int:
    """Integer counterpart of :func:`_num`."""
    return int(_num(raw, *keys, default=float(default)))


def _str(raw: dict, *keys: str, default: str = "") -> str:
    """First non-null value among ``keys`` as a string, else ``default``."""
    value = _first_present(raw, keys)
    return default if value is None else str(value)


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
    strike = float(strike_str) if strike_str else None
    return m.group("expiry"), strike, opt_type


def _expiry_str_to_date(expiry_str: str) -> date | None:
    for fmt in ("%d%b%y", "%y%m%d", "%Y-%m-%d"):
        try:
            return datetime.strptime(expiry_str, fmt).date()
        except ValueError:
            continue
    return None


def _expiry_epoch(expiry_day: date) -> float:
    """Epoch of expiry-day close (15:30 IST)."""
    return datetime.combine(expiry_day, NSE_CLOSE_TIME, tzinfo=IST).timestamp()


def _has_expired(expiry_day: date, now_epoch: float) -> bool:
    """True once expiry-day close has passed."""
    return _expiry_epoch(expiry_day) <= now_epoch


def _expiry_year_fraction(expiry_day: date, now_epoch: float) -> float:
    """ACT/365 year fraction to expiry-day close, floored at one hour.

    The floor keeps same-day expiries numerically sane. It must never be
    reached by an *already expired* option: at T=1h, gamma ~ 1/(S·sigma·sqrt(T))
    is enormous, so one stale expired leg would dominate the whole book's
    aggregate Greeks. Callers must reject expired legs via :func:`_has_expired`
    first — the floor makes a downstream ``expiry <= 0`` check unreachable.
    """
    return max((_expiry_epoch(expiry_day) - now_epoch) / SECONDS_PER_YEAR, MIN_EXPIRY_YEARS)


def _parse_holding(raw: dict) -> UpstoxHolding:
    return UpstoxHolding(
        instrument_key=_str(raw, "instrument_token", "isin"),
        trading_symbol=_str(raw, "tradingsymbol", "trading_symbol"),
        exchange=_str(raw, "exchange"),
        quantity=_int(raw, "quantity"),
        average_price=_num(raw, "average_price"),
        last_price=_num(raw, "last_price", "close_price"),
        pnl=_num(raw, "pnl"),
        day_change=_num(raw, "day_change"),
        day_change_percentage=_num(raw, "day_change_percentage"),
    )


def _parse_position(raw: dict) -> UpstoxPosition:
    option_type = _first_present(raw, ("option_type",))
    strike_price = _safe_float(_first_present(raw, ("strike_price", "strike")))
    expiry = _first_present(raw, ("expiry",))
    trading_symbol = _str(raw, "tradingsymbol", "trading_symbol")

    # Upstox omits the option fields on some instruments. Recover them from
    # the trading symbol here, so every consumer sees a complete position —
    # previously only to_core_portfolio did this, so /summary priced a leg
    # that /signals simultaneously labelled "unknown".
    if trading_symbol and (option_type is None or strike_price is None or expiry is None):
        parsed_expiry, parsed_strike, parsed_type = _parse_trading_symbol(trading_symbol)
        option_type = option_type if option_type is not None else parsed_type
        strike_price = strike_price if strike_price is not None else parsed_strike
        expiry = expiry if expiry is not None else parsed_expiry

    return UpstoxPosition(
        instrument_key=_str(raw, "instrument_token", "instrument_key"),
        trading_symbol=trading_symbol,
        exchange=_str(raw, "exchange"),
        product=_str(raw, "product"),
        quantity=_int(raw, "quantity", "net_quantity"),
        buy_price=_num(raw, "buy_price", "average_buy_price"),
        sell_price=_num(raw, "sell_price", "average_sell_price"),
        last_price=_num(raw, "last_price"),
        pnl=_num(raw, "pnl", "realised"),
        multiplier=_int(raw, "multiplier", "lot_size", default=1),
        option_type=str(option_type) if option_type is not None else None,
        strike_price=strike_price,
        expiry=str(expiry) if expiry is not None else None,
    )


def _parse_order(raw: dict) -> UpstoxOrder:
    return UpstoxOrder(
        order_id=_str(raw, "order_id"),
        trading_symbol=_str(raw, "tradingsymbol", "trading_symbol"),
        exchange=_str(raw, "exchange"),
        order_type=_str(raw, "order_type"),
        transaction_type=_str(raw, "transaction_type"),
        quantity=_int(raw, "quantity"),
        price=_num(raw, "price"),
        trigger_price=_num(raw, "trigger_price"),
        status=_str(raw, "status"),
        filled_quantity=_int(raw, "filled_quantity"),
        average_price=_num(raw, "average_price"),
        placed_at=_str(raw, "order_timestamp", "placed_at"),
        product=_str(raw, "product"),
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


def _parse_funds(raw: dict) -> UpstoxFunds:
    return UpstoxFunds(
        used_margin=float(raw.get("used_margin", 0.0) or 0.0),
        available_margin=float(raw.get("available_margin", 0.0) or 0.0),
        payin_amount=float(raw.get("payin_amount", 0.0) or 0.0),
        span_margin=float(raw.get("span_margin", 0.0) or 0.0),
        adhoc_margin=float(raw.get("adhoc_margin", 0.0) or 0.0),
        notional_cash=float(raw.get("notional_cash", 0.0) or 0.0),
        exposure_margin=float(raw.get("exposure_margin", 0.0) or 0.0),
    )


def _safe_float(v: object) -> float | None:
    if v is None:
        return None
    try:
        return float(v)  # type: ignore[arg-type]
    except (ValueError, TypeError):
        return None


class UpstoxPortfolioClient:
    """Async client for Upstox portfolio REST API endpoints.

    Prefer ``token_provider``: it resolves a fresh token per request, so a
    long-running sync survives the daily Upstox token expiry. A bare
    ``access_token`` string is accepted for tests and one-shot scripts, but a
    client built that way stops working the moment that token expires.
    """

    def __init__(
        self,
        token_provider: TokenProvider | None = None,
        access_token: str | None = None,
    ) -> None:
        if token_provider is None and access_token is None:
            raise ValueError("Either token_provider or access_token must be provided")
        self._token_provider = token_provider
        self._static_token = access_token

    async def _resolve_token(self) -> str:
        if self._token_provider is not None:
            return await self._token_provider.get()
        assert self._static_token is not None
        return self._static_token

    async def _headers(self) -> dict[str, str]:
        return {
            "Accept": "application/json",
            "Api-Version": "2.0",
            "Authorization": f"Bearer {await self._resolve_token()}",
        }

    async def _get(self, url: str, params: dict | None = None) -> dict:
        headers = await self._headers()
        try:
            _ctx = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            async with httpx.AsyncClient(timeout=httpx.Timeout(15.0), verify=_ctx) as client:
                resp = await client.get(url, headers=headers, params=params)
        except httpx.ConnectError as exc:
            raise NetworkError(f"Connection failed: {exc}") from exc
        except httpx.TimeoutException as exc:
            raise NetworkError(f"Request timed out: {exc}") from exc

        if resp.status_code == 401:
            # The token may have been revoked before its nominal expiry; drop
            # the cache so the next call re-resolves rather than replaying it.
            if self._token_provider is not None:
                self._token_provider.invalidate()
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

    async def fetch_funds(self, segment: str = "equity") -> UpstoxFunds:
        """Fetch account funds and margin for one segment.

        Upstox returns ``{"data": {"equity": {...}, "commodity": {...}}}``.
        ``segment`` selects which block to read; F&O margin lives under
        ``equity``. Raises ``APIError`` if the requested segment is absent
        rather than returning zeros, so a shape change surfaces instead of
        silently reporting an empty account.
        """
        url = getattr(
            settings,
            "upstox_funds_url",
            f"{settings.upstox_base_url}/v2/user/get-funds-and-margin",
        )
        body = await self._get(url)
        data = body.get("data", {})
        if segment not in data:
            raise APIError(
                f"Upstox funds response has no {segment!r} segment (got: {sorted(data)})"
            )
        funds = _parse_funds(data[segment])
        logger.info(
            "Fetched funds: %.1f%% margin utilization",
            funds.margin_utilization * 100.0,
        )
        return funds


def to_core_portfolio(
    positions: list[UpstoxPosition],
    rate: float | None = None,
    now_fn: Callable[[], float] = time.time,
    funds: UpstoxFunds | None = None,
) -> Portfolio:
    """Map Upstox F&O positions to ``optitrade.core.types.Portfolio``.

    Equity-only positions (no strike/option_type) are skipped; this mapper
    handles index/stock options only. Year-fraction uses ACT/365 + IST
    matching the capture service convention.

    ``equity`` and ``margin_available`` come from ``funds`` (the broker's
    funds-and-margin call). They are left at zero when ``funds`` is None —
    they must never be back-filled from P&L, which is a different quantity
    and, when used as a spot proxy, silently corrupts every downstream Greek.
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
                _expiry_str, parsed_strike, parsed_opt = _parse_trading_symbol(pos.trading_symbol)
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
            logger.debug("No expiry for %s, skipping", pos.trading_symbol)
            continue

        expiry_day = _expiry_str_to_date(expiry_date_str)
        if expiry_day is None:
            logger.debug("Cannot parse expiry %r for %s", expiry_date_str, pos.trading_symbol)
            continue

        if _has_expired(expiry_day, now_epoch):
            # Must be dropped here: _expiry_year_fraction floors at one hour,
            # so an expired leg would otherwise carry a huge gamma/theta and
            # swamp the book's aggregate Greeks.
            logger.debug(
                "Skipping expired position %s (expired %s)", pos.trading_symbol, expiry_day
            )
            continue

        expiry_yf = _expiry_year_fraction(expiry_day, now_epoch)

        contract = OptionContract(
            symbol=pos.trading_symbol,
            strike=strike,
            expiry=expiry_yf,
            option_type=option_type,
            # lot_size=1 because Upstox reports `quantity` in UNITS, already
            # lot-multiplied, whereas the core convention is quantity-in-lots
            # scaled by lot_size (e.g. walk_forward: quantity * lot_size * price).
            # Setting 1 makes `quantity * lot_size` the true unit count.
            #
            # Do NOT use `multiplier` here: it is a P&L scaling factor, not a
            # contract size. Upstox returns multiplier=1.0 for an NSE F&O option
            # whose lot size is 15, and 1000.0 for a currency derivative — so
            # mapping it to lot_size silently inflated notional and
            # concentration by 1000x on the CDS segment.
            lot_size=1,
        )
        entry_price = pos.buy_price if pos.quantity > 0 else pos.sell_price
        core_positions.append(
            Position(contract=contract, quantity=float(pos.quantity), entry_price=entry_price)
        )

    return Portfolio(
        positions=tuple(core_positions),
        equity=funds.total_equity if funds is not None else 0.0,
        margin_available=funds.available_margin if funds is not None else 0.0,
    )


__all__ = [
    "UpstoxFunds",
    "UpstoxHolding",
    "UpstoxOrder",
    "UpstoxPnLEntry",
    "UpstoxPortfolioClient",
    "UpstoxPosition",
    "to_core_portfolio",
]

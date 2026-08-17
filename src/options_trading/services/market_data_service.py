from __future__ import annotations

import asyncio
import functools
import logging
import math
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

import numpy as np
import pandas as pd

# API helpers (adjust import path if your helpers live elsewhere)
from ..api.tools.candles import fetch_option_candles, fetch_upstox_historical_data
from ..api.tools.expiries import get_expiries

# new: live option chain helper (implement in api/tools/option_chain_live.py)
from ..api.tools.option_chain_live import fetch_live_option_chain
from ..config.settings import get_settings
from ..market_data.manager import MarketDataManager
from ..market_data.preprocessing import (
    compute_iv_in_memory,
)
from ..models.dashboard import ConnectionStatus, MarketDataFeed, MarketDataStatus
from ..models.market_data import GreeksSnapshot, OptionChain, OptionData, VolatilitySurface
from ..utils.cache import AsyncCache
from ..utils.exceptions import DataQualityError

logger = logging.getLogger(__name__)


def _last_thursday_of_month(year: int, month: int) -> date:
    if month == 12:
        first_next = date(year + 1, 1, 1)
    else:
        first_next = date(year, month + 1, 1)
    last_day = first_next - timedelta(days=1)
    return last_day - timedelta(days=(last_day.weekday() - 3) % 7)


def _is_expired(expiry: str) -> bool:
    try:
        d = datetime.strptime(expiry, "%Y-%m-%d").date()
        return d < date.today()
    except Exception:
        return True


def _safe_float(val: Any) -> float | None:
    """Return a finite float or None (guards nan/inf/None/invalid)."""
    if val is None:
        return None
    try:
        f = float(val)
        if math.isnan(f) or math.isinf(f):
            return None
        return f
    except Exception:
        return None


def to_decimal_safe_np(x):
    try:
        if x is None or not np.isfinite(x):
            return None
        return Decimal(str(float(x)))
    except Exception:
        return None


class MarketDataService:
    CACHE_TTL = 30
    SURFACE_TTL = 600  # 10 minutes

    def __init__(
        self, market_data_manager: MarketDataManager | None = None, cache: AsyncCache | None = None
    ):
        self.settings = get_settings()
        self.market_data_manager = market_data_manager
        self.cache = cache or AsyncCache(ttl=self.CACHE_TTL, max_size=1_000)
        self._data_feeds: dict[str, Any] = {}
        self._last_update: dict[str, datetime] = {}
        self._last_thursday_of_month = _last_thursday_of_month
        # concurrency tuning: how many blocking candle fetches we'll allow concurrently
        self._max_concurrent_requests = getattr(self.settings, "max_concurrent_requests", 6)

    async def _run_blocking(self, func, *args, **kwargs):
        partial = functools.partial(func, *args, **kwargs)
        return await asyncio.to_thread(partial)

    # if func might be coroutine function or blocking function, dispatch correctly
    async def _maybe_await(self, func, *args, **kwargs):
        if asyncio.iscoroutinefunction(func):
            return await func(*args, **kwargs)
        return await self._run_blocking(func, *args, **kwargs)

    # ---------- status ----------
    async def get_data_status(self) -> MarketDataStatus:
        cache_key = "market_status"
        cached = await self.cache.get_by_key(cache_key)
        if cached:
            logger.debug("Using cached market status")
            return MarketDataStatus(**cached)

        overall = ConnectionStatus.DISCONNECTED
        try:
            if self.market_data_manager:
                # Resolve a known underlying to check connectivity
                await self.market_data_manager.get_underlying_key("Nifty 50", "NSE_INDEX")
                overall = ConnectionStatus.CONNECTED
        except Exception as exc:
            logger.warning("Market connectivity check failed: %s", exc)

        feeds = [
            MarketDataFeed(
                name="Upstox Options",
                status=overall,
                instruments_count=0,
                last_update=datetime.utcnow() - timedelta(seconds=2),
                latency_ms=15.0,
                error_rate=0.0,
            ),
            MarketDataFeed(
                name="NSE Index Data",
                status=overall,
                instruments_count=0,
                last_update=datetime.utcnow() - timedelta(seconds=1),
                latency_ms=12.0,
                error_rate=0.0,
            ),
        ]
        md_status = MarketDataStatus(
            overall_status=overall,
            feeds_connected=len([f for f in feeds if f.status == ConnectionStatus.CONNECTED]),
            total_instruments=sum(f.instruments_count for f in feeds),
            last_update=datetime.utcnow(),
            feeds=feeds,
            response_time_ms=50.0,
        )
        await self.cache.set_by_key(cache_key, md_status.dict(), ttl=self.CACHE_TTL)
        return md_status

    # ---------- option chain ----------
    async def get_option_chain(
        self,
        symbol: str,
        *,
        expiry_date: str | None = None,
        strikes_range: int = 10,
    ) -> OptionChain:
        """
        Return OptionChain. mode controls which upstream path to use:
          - "live": call Upstox /v2/option/chain wrapper (requires fetch_live_option_chain)
          - "expired": use expired endpoints + local IV/greeks computations
          - "auto": choose live if expiry is today/future else expired
        Raises DataQualityError on predictable data problems (404-like).
        """
        cache_key = f"option_chain::{symbol}::{expiry_date or 'auto'}::{strikes_range}"
        cached = await self.cache.get_by_key(cache_key)
        if cached:
            return OptionChain(**cached)

        if not self.market_data_manager:
            logger.warning("No MarketDataManager available — returning fallback chain")
            return self._get_fallback_option_chain(symbol, expiry_date)

        # Resolve underlying key and expiry (expiry discovery if needed)
        uk = await self.market_data_manager.get_underlying_key(symbol, "NSE_EQ")

        if not expiry_date:
            today = date.today()
            cand = self._last_thursday_of_month(today.year, today.month)
            if cand < today:
                y, m = (today.year + 1, 1) if today.month == 12 else (today.year, today.month + 1)
                cand = self._last_thursday_of_month(y, m)
            expiry_date = cand.strftime("%Y-%m-%d")
            logger.info(
                "No expiry provided - using last-Thursday expiry %s for %s", expiry_date, symbol
            )

        expiry_is_expired = _is_expired(expiry_date)

        # Decide flow
        if expiry_is_expired:
            raise DataQualityError(
                f"Requested expiry {expiry_date} is in the past for {symbol}. Use a future expiry or enable expired mode."
            )
        else:
            chosen_flow = "live"
            logger.debug("Selected expiry %s (future) for symbol %s", expiry_date, symbol)

        # -------------------- LIVE FLOW --------------------
        if chosen_flow == "live":
            try:
                data = await self._run_blocking(
                    fetch_live_option_chain, uk, expiry_date, self.market_data_manager.access_token
                )
            except Exception as e:
                logger.warning("Live option chain API failed for %s %s: %s", symbol, expiry_date, e)
                # prefer explicit error so caller can fallback or inform user
                raise DataQualityError("Live chain unavailable")

            # defensive mapping of the live response into OptionChain
            spot_val = None
            strikes_rows = []

            if isinstance(data, dict):
                # unpack common shapes
                spot_val = (
                    data.get("underlying_spot_price")
                    or data.get("underlying_price")
                    or data.get("spot_price")
                )
                if "data" in data and isinstance(data["data"], list):
                    strikes_rows = data["data"]
                elif "option_chain" in data and isinstance(data["option_chain"], list):
                    strikes_rows = data["option_chain"]
                elif isinstance(data.get("data"), dict) and "rows" in data["data"]:
                    strikes_rows = data["data"]["rows"]
                elif isinstance(data, list):
                    strikes_rows = data
            elif isinstance(data, list):
                strikes_rows = data

            calls: list[OptionData] = []
            puts: list[OptionData] = []

            for row in strikes_rows:
                strike_val = row.get("strike_price") or row.get("strike") or row.get("strikePrice")
                if strike_val is None:
                    continue

                ce = (
                    row.get("call_options")
                    or row.get("call_option")
                    or row.get("CE")
                    or row.get("call")
                )
                pe = (
                    row.get("put_options")
                    or row.get("put_option")
                    or row.get("PE")
                    or row.get("put")
                )

                # strike_val bound as a default: the closure must capture this
                # iteration's strike, not the loop variable's final value (B023).
                def map_leg(
                    leg: dict, opt_type: str, strike_val: object = strike_val
                ) -> OptionData | None:
                    if not leg or not isinstance(leg, dict):
                        return None
                    md = leg.get("market_data") or {}
                    gr = leg.get("option_greeks") or {}

                    last = (
                        md.get("ltp")
                        or md.get("close_price")
                        or leg.get("ltp")
                        or leg.get("last_price")
                    )
                    bid = (
                        md.get("bid_price")
                        or md.get("bid")
                        or md.get("best_bid_price")
                        or leg.get("bid")
                    )
                    ask = (
                        md.get("ask_price")
                        or md.get("ask")
                        or md.get("best_ask_price")
                        or leg.get("ask")
                    )
                    vol = md.get("volume") or md.get("vol") or 0
                    oi = md.get("open_interest") or md.get("oi") or 0
                    iv = gr.get("iv") or gr.get("implied_volatility") or md.get("iv") or 0.0

                    last_dec = to_decimal_safe_np(last)
                    if last_dec is None:
                        return None

                    bid_dec = to_decimal_safe_np(bid) or (last_dec * Decimal("0.995")).quantize(
                        Decimal("0.01")
                    )
                    ask_dec = to_decimal_safe_np(ask) or (last_dec * Decimal("1.005")).quantize(
                        Decimal("0.01")
                    )
                    iv_dec = to_decimal_safe_np(iv) or Decimal("0.0")

                    try:
                        greeks_obj = GreeksSnapshot(
                            delta=None
                            if _safe_float(gr.get("delta")) is None
                            else Decimal(str(_safe_float(gr.get("delta")))),
                            gamma=None
                            if _safe_float(gr.get("gamma")) is None
                            else Decimal(str(_safe_float(gr.get("gamma")))),
                            theta=None
                            if _safe_float(gr.get("theta")) is None
                            else Decimal(str(_safe_float(gr.get("theta")))),
                            vega=None
                            if _safe_float(gr.get("vega")) is None
                            else Decimal(str(_safe_float(gr.get("vega")))),
                            rho=None
                            if _safe_float(gr.get("rho")) is None
                            else Decimal(str(_safe_float(gr.get("rho")))),
                        )
                    except Exception:
                        # If the model rejects None, replace with zeros (adjust to your model constraints)
                        try:
                            greeks_obj = GreeksSnapshot(
                                delta=Decimal("0"),
                                gamma=Decimal("0"),
                                theta=Decimal("0"),
                                vega=Decimal("0"),
                                rho=Decimal("0"),
                            )
                        except Exception:
                            greeks_obj = None

                    return OptionData(
                        strike=Decimal(str(float(strike_val))),
                        option_type=opt_type,
                        last_price=last_dec,
                        bid=bid_dec,
                        ask=ask_dec,
                        volume=int(vol or 0),
                        open_interest=int(oi or 0),
                        implied_volatility=iv_dec,
                        greeks=greeks_obj,
                        last_updated=datetime.utcnow(),
                    )

                ce_mapped = map_leg(ce, "CE")
                pe_mapped = map_leg(pe, "PE")
                if ce_mapped:
                    calls.append(ce_mapped)
                if pe_mapped:
                    puts.append(pe_mapped)

            # trim to ATM window to reduce payload
            spot_f = _safe_float(spot_val)
            spot_dec = Decimal(str(spot_f)) if spot_f is not None else None

            if spot_dec and strikes_range and (calls or puts):

                def keep_near_atm(options: list[OptionData]) -> list[OptionData]:
                    return sorted(options, key=lambda x: abs(float(x.strike) - float(spot_dec)))[
                        : max(10, strikes_range * 2)
                    ]

                calls = keep_near_atm(calls)
                puts = keep_near_atm(puts)

            chain = OptionChain(
                symbol=symbol,
                spot_price=spot_dec or Decimal("0.0"),
                expiry_date=expiry_date,
                call_options=sorted(calls, key=lambda x: x.strike),
                put_options=sorted(puts, key=lambda x: x.strike),
                timestamp=datetime.utcnow(),
            )
            await self.cache.set_by_key(cache_key, chain.dict(), ttl=self.CACHE_TTL)
            return chain

    # ---------- volatility surface ----------
    async def get_volatility_surface(self, symbol: str) -> VolatilitySurface:
        cache_key = f"vol_surface::{symbol}"
        cached = await self.cache.get_by_key(cache_key)
        if cached:
            return VolatilitySurface(**cached)

        if not self.market_data_manager:
            raise DataQualityError("MarketDataManager not available")

        surface_rows: list[dict[str, Any]] = []
        try:
            uk = await self.market_data_manager.get_underlying_key(symbol, "NSE_EQ")

            expiries_all = (
                await self._run_blocking(get_expiries, uk, self.market_data_manager.access_token)
                or []
            )
            expiries = expiries_all[-4:] if expiries_all else []

            # spot reference
            spot_ref = await self._get_real_spot_price(symbol)
            spot_float = float(spot_ref)

            # concurrency semaphore for strikes/expiry fetches as well
            semaphore = asyncio.Semaphore(self._max_concurrent_requests)

            async def process_strike_sample(
                expiry: str, strike: float, row_for_meta: pd.Series
            ) -> dict[str, Any] | None:
                async with semaphore:
                    try:
                        start = (pd.to_datetime(expiry) - pd.Timedelta(days=7)).strftime("%Y-%m-%d")
                        instrument_key = row_for_meta.name
                        option_df = await self._maybe_await(
                            fetch_option_candles,
                            instrument_key,
                            self.settings.default_option_interval,
                            start,
                            expiry,
                            self.market_data_manager.access_token,
                        )
                        if option_df is None or getattr(option_df, "empty", True):
                            return None
                        if hasattr(option_df, "reset_index"):
                            option_df = option_df.reset_index()
                        last = option_df.iloc[-1]
                        last_price = float(last.get("close", last.get("close_option", 0)))

                        temp = pd.DataFrame(
                            {
                                "timestamp": [
                                    pd.to_datetime(last.get("timestamp", datetime.utcnow()))
                                ],
                                "close_option": [last_price],
                                "close_spot": [spot_float],
                            }
                        )
                        temp = await self._run_blocking(
                            compute_iv_in_memory,
                            temp,
                            float(strike),
                            pd.to_datetime(expiry),
                            row_for_meta.get("instrument_type", "CE"),
                        )
                        if not temp.empty:
                            iv_val = float(temp.iloc[0].get("iv", 0.0))
                        else:
                            iv_val = 0.0

                        return {
                            "strike": Decimal(str(strike)),
                            "expiry": expiry,
                            "implied_volatility": iv_val,
                            "time_to_expiry": self._calculate_time_to_expiry(expiry),
                            "moneyness": (float(strike) / spot_float) if spot_float else None,
                        }
                    except Exception as e:
                        logger.debug(
                            "Skipping strike %s @ %s due to %s", strike, expiry, e, exc_info=True
                        )
                        return None

            # iterate expiries and sample strikes
            for expiry in expiries:
                try:
                    contracts_df = await self.market_data_manager.fetch_contracts_for_expiry(
                        symbol, "NSE_EQ", expiry
                    )
                    if contracts_df is None or contracts_df.empty:
                        continue

                    sample_strikes = sorted(contracts_df["strike_price"].dropna().unique())[:20]
                    # build tasks for this expiry
                    stasks = []
                    for strike in sample_strikes:
                        subset = contracts_df[contracts_df["strike_price"] == strike]
                        if subset.empty:
                            continue
                        # prefer CE row for that strike
                        sel = subset[subset["instrument_type"] == "CE"]
                        if sel.empty:
                            sel = subset
                        row_meta = sel.iloc[0:1].squeeze()  # keep row with name
                        stasks.append(process_strike_sample(expiry, strike, row_meta))

                    strike_results = await asyncio.gather(*stasks, return_exceptions=False)
                    for r in strike_results:
                        if r:
                            surface_rows.append(r)
                except Exception as e:
                    logger.debug("Skipping expiry %s due to %s", expiry, e, exc_info=True)
                    continue

            surface = VolatilitySurface(
                symbol=symbol,
                spot_price=spot_ref,
                surface_data=surface_rows,
                timestamp=datetime.utcnow(),
            )
            await self.cache.set_by_key(cache_key, surface.dict(), ttl=self.SURFACE_TTL)
            return surface
        except Exception as e:
            logger.exception("Failed to build volatility surface for %s: %s", symbol, e)
            raise DataQualityError(f"Volatility surface unavailable: {e}")

    # ---------- helpers ----------
    async def _get_real_spot_price(self, symbol: str) -> Decimal:
        try:
            if self.market_data_manager:
                uk = await self.market_data_manager.get_underlying_key(symbol, "NSE_EQ")
                spot_df = await self._maybe_await(
                    self.market_data_manager.spot_cache.get_timeseries,
                    uk,
                    date.today().strftime("%Y-%m-%d"),
                    date.today().strftime("%Y-%m-%d"),
                    self.market_data_manager.access_token,
                    int(self.settings.default_spot_interval),
                    fetch_upstox_historical_data,
                    ttl=60,
                    unit="minutes",
                )
                if spot_df is not None and not getattr(spot_df, "empty", True):
                    if hasattr(spot_df, "reset_index"):
                        spot_df = spot_df.reset_index()
                    return Decimal(str(float(spot_df["close"].iloc[-1])))
        except Exception as e:
            logger.debug("Spot price fetch via cache failed: %s", e, exc_info=True)
        return Decimal("19450.25")

    def _get_fallback_option_chain(self, symbol: str, expiry_date: str | None) -> OptionChain:
        if not expiry_date:
            today = date.today()
            cand = self._last_thursday_of_month(today.year, today.month)
            if cand < today:  # already passed, move to next month
                y, m = (today.year + 1, 1) if today.month == 12 else (today.year, today.month + 1)
                cand = self._last_thursday_of_month(y, m)
            expiry_date = cand.strftime("%Y-%m-%d")
        spot_price = Decimal("19450.0")
        call_options = [
            OptionData(
                strike=Decimal("19400"),
                option_type="CE",
                last_price=Decimal("75.0"),
                bid=Decimal("74.0"),
                ask=Decimal("76.0"),
                volume=500,
                open_interest=2000,
                implied_volatility=Decimal("0.15"),
                greeks=GreeksSnapshot(delta=Decimal("0.55")),
                last_updated=datetime.utcnow(),
            )
        ]
        put_options = [
            OptionData(
                strike=Decimal("19400"),
                option_type="PE",
                last_price=Decimal("65.0"),
                bid=Decimal("64.0"),
                ask=Decimal("66.0"),
                volume=400,
                open_interest=1800,
                implied_volatility=Decimal("0.16"),
                greeks=GreeksSnapshot(delta=Decimal("-0.45")),
                last_updated=datetime.utcnow(),
            )
        ]
        return OptionChain(
            symbol=symbol,
            spot_price=spot_price,
            expiry_date=expiry_date,
            call_options=call_options,
            put_options=put_options,
            timestamp=datetime.utcnow(),
        )

    def _calculate_time_to_expiry(self, expiry_str: str) -> float:
        try:
            expiry_date = datetime.strptime(expiry_str, "%Y-%m-%d").date()
            days_to_expiry = (expiry_date - date.today()).days
            return max(days_to_expiry / 365.0, 0.001)
        except Exception:
            return 0.1

    async def refresh_market_data(self, symbols: list[str] | None = None) -> None:
        try:
            logger.info("Refreshing market data caches")
            if symbols:
                for s in symbols:
                    await self.cache.delete_pattern(f"*{s}*")
            else:
                await self.cache.clear()

            if self.market_data_manager:
                warm = symbols or ["NIFTY", "BANKNIFTY", "FINNIFTY"]
                for s in warm:
                    try:
                        await self.get_option_chain(s, strikes_range=5)
                    except Exception as e:
                        logger.debug("Warmup failed for %s: %s", s, e, exc_info=True)
            logger.info("Market data refresh complete")
        except Exception as e:
            logger.exception("Market data refresh failed: %s", e)

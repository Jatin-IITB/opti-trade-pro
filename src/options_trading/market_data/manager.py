# options_trading/market_data/manager.py
import asyncio
import functools
import logging
import os
from collections.abc import Callable
from datetime import date, datetime
from typing import Any

import pandas as pd

from ..api.tools.candles import fetch_option_candles, fetch_upstox_historical_data
from ..api.tools.contracts import fetch_expired_option_contracts_df
from ..api.tools.expiries import get_expiries
from ..api.tools.instrument_key import get_instrument_key
from ..api.tools.live_contracts import fetch_live_option_contracts_df
from ..api.tools.pick_strikes import pick_near_the_money_contracts
from ..market_data.preprocessing import (
    append_greeks_in_memory,
    append_rv_in_memory,
    clean_and_merge_option_spot,
    compute_iv_in_memory,
)
from ..utils.cache import MarketDataCache, market_data_cache
from ..utils.exceptions import DataQualityError
from ..utils.io import save_final_file
from ..utils.naming import generate_filename, generate_output_dir

logger = logging.getLogger(__name__)


def is_expired(expiry_str: str) -> bool:
    try:
        d = datetime.strptime(expiry_str, "%Y-%m-%d").date()
        return d < date.today()
    except Exception:
        return True


def get_expiry_slice(
    expiry: list[str], start: int | None = None, end: int | None = None
) -> list[str]:
    start = start if start is not None else 0
    end = min(end, len(expiry)) if end is not None else len(expiry)
    logger.debug(f"get_expiry_slice: start={start}, end={end}")
    return expiry[start:end]


class MarketDataManager:
    """
    Orchestrates fetching, cleaning, feature engineering, saving, and caching.
    Produces feature-rich DataFrames for option strategies or research.
    """

    def __init__(self, access_token: str, spot_cache: MarketDataCache | None = None):
        logger.debug("Initializing MarketDataManager (async)")
        self.access_token = access_token
        self.spot_cache = spot_cache or market_data_cache

    async def _maybe_await(self, func: Callable[..., Any], *args, **kwargs):
        """
        If func is coroutine function -> await it.
        Else run it in threadpool to avoid blocking event loop.
        """
        if asyncio.iscoroutinefunction(func):
            return await func(*args, **kwargs)
        loop = asyncio.get_running_loop()
        partial = functools.partial(func, *args, **kwargs)
        return await loop.run_in_executor(None, partial)

    async def get_underlying_key(self, symbol: str, exchange: str) -> str:
        logger.debug(f"Fetching underlying key for {symbol} on {exchange}")
        key = await self._maybe_await(get_instrument_key, symbol, exchange)
        if key is None:
            logger.error(f"Instrument key not found for {symbol} on {exchange}")
            raise DataQualityError(f"Instrument key not found: {symbol}/{exchange}")
        logger.debug(f"Underlying key for {symbol} is {key}")
        return key

    async def fetch_live_contracts_for_expiry(
        self, symbol: str, exchange: str, expiry: str
    ) -> pd.DataFrame:
        uk = await self.get_underlying_key(symbol, exchange)
        df = await self._maybe_await(fetch_live_option_contracts_df, uk, expiry, self.access_token)
        if not isinstance(df, pd.DataFrame) or df.empty:
            raise DataQualityError("Live contracts fetch returned empty")
        return df

    async def fetch_contracts_for_expiry(self, *args) -> pd.DataFrame:
        if len(args) == 2:
            uk, expiry = args
            df = await self._maybe_await(
                fetch_expired_option_contracts_df, uk, expiry, self.access_token
            )
            if not isinstance(df, pd.DataFrame):
                logger.error("fetch_expired_option_contracts_df returned non-DataFrame")
                raise DataQualityError("Contracts fetch failed or returned non-DataFrame")
            logger.debug(f"Fetched {len(df)} expired contracts for expiry {expiry} (uk={uk})")
            return df
        elif len(args) == 3:
            symbol, exchange, expiry = args
            if is_expired(expiry):
                uk = await self.get_underlying_key(symbol, exchange)
                df = await self._maybe_await(
                    fetch_expired_option_contracts_df, uk, expiry, self.access_token
                )
                if not isinstance(df, pd.DataFrame):
                    logger.error("fetch_expired_option_contracts_df returned non-DataFrame")
                    raise DataQualityError("Contracts fetch failed or returned non-DataFrame")
                logger.debug(f"Fetched {len(df)} expired contracts for {symbol} expiry {expiry}")
                return df
            else:
                return await self.fetch_live_contracts_for_expiry(symbol, exchange, expiry)
        else:
            raise TypeError(
                "fetch_contracts_for_expiry expects (underlying_key, expiry) or (symbol, exchange, expiry)"
            )

    async def save_features_for_expiry(
        self,
        symbol: str,
        exchange: str,
        expiry: str | None,  # allow None if using number_of_exp
        option_interval: str,
        spot_interval: str,
        days_back: int,
        strikes: int,
        out_base: str = "FINAL",
        number_of_exp: int | None = None,  # pick last `number_of_exp` expiries if expiry is None
    ) -> tuple[dict[str, str], str]:
        """
        Main entry. Either pass expiry (single string) OR pass number_of_exp to auto-select last N expiries.
        Returns a flat dict of saved files (keys: "<expiry>::<instrument_key>::<contract_key>") and out_dir (last used).
        """
        logger.debug(
            f"save_features_for_expiry called: symbol={symbol}, expiry={expiry}, number_of_exp={number_of_exp}, strikes={strikes}"
        )

        # Resolve underlying key once
        uk = await self.get_underlying_key(symbol, exchange)

        # If expiry not provided, discover expiries and pick last N
        expiries_to_process: list[str] = []
        if expiry:
            expiries_to_process = [expiry]
        else:
            # default last 1 expiry when number_of_exp not provided
            n = number_of_exp if (number_of_exp is not None) else 1
            logger.debug(f"Discovering expiries for underlying_key={uk} to pick last {n}")
            all_expiries = await self._maybe_await(get_expiries, uk, self.access_token)
            if not all_expiries:
                raise DataQualityError("No expiries found for underlying")
            # select last n items
            expiries_to_process = get_expiry_slice(all_expiries, start=-n)

        logger.debug(f"Expiries selected for processing: {expiries_to_process}")

        saved: dict[str, str] = {}
        out_dir = ""

        for expiry_str in expiries_to_process:
            logger.info(f"Processing expiry {expiry_str} for {symbol} ({exchange})")

            # fetch contracts for this expiry
            try:
                contracts_df = await self.fetch_contracts_for_expiry(uk, expiry_str)
            except DataQualityError as e:
                logger.warning(f"No contracts for expiry {expiry_str}: {e}")
                continue

            logger.debug(f"Contracts DataFrame shape: {contracts_df.shape}")

            # fetch a short spot window (use same expiry as both start & end to get last bar)
            spot_df_short = await self.spot_cache.get_timeseries(
                uk,
                expiry_str,
                expiry_str,
                self.access_token,
                int(spot_interval),
                fetch_upstox_historical_data,
                ttl=None,
                unit="minutes",
            )

            if spot_df_short is None or (hasattr(spot_df_short, "empty") and spot_df_short.empty):
                logger.warning(
                    f"No spot data for {symbol} {exchange} {expiry_str}, skipping expiry"
                )
                continue

            spot_price = float(spot_df_short["close"].iloc[-1])
            logger.debug(f"Spot price for {symbol} {expiry_str}: {spot_price}")

            atm_keys = pick_near_the_money_contracts(
                contracts_df, spot_price, num_itm=strikes, num_otm=strikes
            )
            logger.info(f"Selected {len(atm_keys)} strikes for {symbol} {expiry_str}")

            out_dir = generate_output_dir(out_base, symbol, option_interval, days_back)
            logger.debug(f"Output directory: {out_dir}")

            for key in atm_keys:
                logger.debug(f"Processing contract key: {key} (expiry {expiry_str})")
                try:
                    # resolve row robustly
                    if key in contracts_df.index:
                        row = contracts_df.loc[key]
                    else:
                        matches = contracts_df[contracts_df.get("trading_symbol", "") == key]
                        if matches.empty:
                            logger.warning("Contract key %s not in contracts_df (skipping)", key)
                            continue
                        row = matches.iloc[0]

                    trading_symbol = row.get("trading_symbol", "<unknown>")
                    instr_type = row.get("instrument_type", None)
                    if pd.isna(instr_type) or instr_type is None:
                        logger.warning("Missing instrument_type for %s; skipping", trading_symbol)
                        continue

                    expiry_ts = (
                        pd.to_datetime(row["expiry"])
                        if not isinstance(row["expiry"], pd.Timestamp)
                        else row["expiry"]
                    )
                    start_date = (expiry_ts - pd.Timedelta(days=days_back)).strftime("%Y-%m-%d")
                    expiry_date_str = expiry_ts.strftime("%Y-%m-%d")

                    # option candles
                    option_df = await self._maybe_await(
                        fetch_option_candles,
                        key,
                        option_interval,
                        start_date,
                        expiry_date_str,
                        self.access_token,
                    )
                    if hasattr(option_df, "reset_index"):
                        option_df = option_df.reset_index()
                    option_df["instrument_type"] = instr_type
                    logger.debug("option_df shape: %s", getattr(option_df, "shape", None))

                    # full spot series (cached)
                    spot_df_full = await self.spot_cache.get_timeseries(
                        uk,
                        start_date,
                        expiry_date_str,
                        self.access_token,
                        int(spot_interval),
                        fetch_upstox_historical_data,
                        ttl=None,
                        unit="minutes",
                    )
                    if hasattr(spot_df_full, "reset_index"):
                        spot_df_full = spot_df_full.reset_index()
                    logger.debug("spot_df_full shape: %s", getattr(spot_df_full, "shape", None))

                    # merge & compute
                    merged_df = clean_and_merge_option_spot(
                        option_df, spot_df_full, expiry_ts, float(row["strike_price"])
                    )
                    logger.debug("merged_df shape: %s", getattr(merged_df, "shape", None))
                    if merged_df.empty:
                        logger.warning("Empty merged_df for %s %s", trading_symbol, expiry_date_str)
                        continue

                    merged_df = compute_iv_in_memory(
                        merged_df, float(row["strike_price"]), expiry_ts, instr_type
                    )

                    # prepare a cache callable that fits the sync/async expectations of append_rv_in_memory
                    if asyncio.iscoroutinefunction(append_rv_in_memory):
                        # append_rv_in_memory is async -> pass async cache callable
                        cache_callable = self.spot_cache.get_timeseries
                    else:
                        # append_rv_in_memory is sync and will be executed in a threadpool (via _maybe_await).
                        # provide a synchronous wrapper that runs the async cache in a new event loop (safe since running in worker thread).
                        def cache_callable_sync(
                            uk_, s_, e_, token_, interval_, fetch_func_, ttl=None, unit="minutes"
                        ):
                            return asyncio.run(
                                self.spot_cache.get_timeseries(
                                    uk_, s_, e_, token_, interval_, fetch_func_, ttl=ttl, unit=unit
                                )
                            )

                        cache_callable = cache_callable_sync

                    # append realized vol (append_rv_in_memory can be sync or async; _maybe_await handles both)
                    merged_df = await self._maybe_await(
                        append_rv_in_memory,
                        merged_df,
                        self.access_token,
                        uk,
                        int(spot_interval),
                        fetch_upstox_historical_data,
                        cache_func=cache_callable,
                    )

                    # greeks
                    merged_df = await self._maybe_await(
                        append_greeks_in_memory,
                        merged_df,
                        float(row["strike_price"]),
                        expiry_ts,
                        instr_type,
                    )

                    # save
                    fname = generate_filename(
                        trading_symbol,
                        expiry_ts.date().isoformat(),
                        instr_type,
                        int(float(row["strike_price"])),
                        extension="parquet",
                    )
                    out_fp = os.path.join(out_dir, fname)
                    # save_final_file may be sync -> use _maybe_await
                    await self._maybe_await(save_final_file, merged_df, out_fp)
                    logger.info("✅ Saved features: %s", out_fp)

                    saved_key = f"{expiry_date_str}::{uk}::{key}"
                    saved[saved_key] = out_fp

                except Exception:
                    logger.exception("Failed processing contract %s for expiry %s", key, expiry_str)

        logger.debug("All expiries processed.")
        return saved, out_dir

    def cache_stats(self):
        logger.debug("Fetching spot_cache statistics")
        try:
            # MarketDataCache.get_stats is synchronous in our implementation
            return self.spot_cache.get_stats()
        except Exception:
            logger.exception("Error while fetching cache stats")
            return {}

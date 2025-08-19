import logging
import asyncio
from typing import Optional
from fastapi import Query
import pandas as pd
from ...config.settings import settings
INSTRUMENT_KEY_URL = settings.instrument_key_url
logger = logging.getLogger(__name__)

_instruments_df: Optional[pd.DataFrame] = None
_load_lock = asyncio.Lock()


async def _read_csv_async(url: str) -> pd.DataFrame:
    def _read():
        if isinstance(url, str) and url.endswith(".gz"):
            return pd.read_csv(url, compression="gzip")
        return pd.read_csv(url)

    return await asyncio.to_thread(_read)


async def _load_instruments_df_async(force_reload: bool = False) -> Optional[pd.DataFrame]:
    global _instruments_df

    # Fast path: already loaded and not forcing reload
    if _instruments_df is not None and not force_reload:
        return _instruments_df

    async with _load_lock:
        # Re-check inside lock to avoid double-load
        if _instruments_df is not None and not force_reload:
            return _instruments_df

        try:
            df = await _read_csv_async(INSTRUMENT_KEY_URL)

            # Normalize columns
            df.columns = [c.strip().lower() for c in df.columns]
            needed = {"instrument_key", "exchange", "tradingsymbol"}
            missing = needed - set(df.columns)
            if missing:
                logger.error(f"Instrument list missing columns: {missing}")
                _instruments_df = None
                return None

            # Normalize key fields
            df["exchange"] = df["exchange"].astype(str).str.strip().str.upper()
            df["tradingsymbol"] = df["tradingsymbol"].astype(str).str.strip().str.upper()

            # Optional expiry normalization
            if "expiry" in df.columns:
                try:
                    df["expiry"] = pd.to_datetime(df["expiry"]).dt.date
                except Exception:
                    # Keep as-is if parsing fails
                    pass

            _instruments_df = df
            logger.info(
                f"Loaded instruments: rows={len(df)}, exchanges={df['exchange'].nunique()}"
            )
            return _instruments_df

        except Exception as e:
            logger.error(f"Failed to load instrument list from {INSTRUMENT_KEY_URL}: {e}")
            _instruments_df = None
            return None


async def get_instrument_key_async(trading_symbol: str, exchange: str) -> Optional[str]:
    df = await _load_instruments_df_async()
    if df is None or df.empty:
        logger.warning("Instrument dataset unavailable or empty")
        return None

    ex = str(exchange).strip().upper()
    ts = str(trading_symbol).strip().upper()

    subset = df[df["exchange"] == ex]
    if subset.empty:
        logger.warning(f"No rows for exchange={ex}")
        return None

    # Exact match
    filtered = subset[subset["tradingsymbol"] == ts]

    # Relaxed search fallback if no exact match
    if filtered.empty:
        for col in ("tradingsymbol", "symbol", "name"):
            if col in subset.columns:
                candidates = subset[
                    subset[col].astype(str).str.upper().str.contains(ts, na=False)
                ]
                if not candidates.empty:
                    filtered = candidates
                    break

    if filtered.empty:
        logger.warning(f"No instrument found for {trading_symbol} on {exchange}")
        return None

    row = filtered.iloc[0]
    key = row.get("instrument_key")
    if not key:
        logger.warning(f"instrument_key missing for {trading_symbol}/{exchange}")
        return None

    logger.info(
        f"Resolved {trading_symbol}/{exchange} -> {key} (tradingsymbol={row.get('tradingsymbol')})"
    )
    return key


# Optional: sync-compatible wrappers if needed by existing code paths

def _load_instruments_df() -> Optional[pd.DataFrame]:
    return asyncio.run(_load_instruments_df_async())

def get_instrument_key(trading_symbol: str, exchange: str) -> Optional[str]:
    return asyncio.run(get_instrument_key_async(trading_symbol, exchange))

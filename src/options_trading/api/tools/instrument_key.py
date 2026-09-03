import asyncio
import logging

import pandas as pd

from ...config.settings import settings

INSTRUMENT_KEY_URL = settings.instrument_key_url
logger = logging.getLogger(__name__)

_instruments_df: pd.DataFrame | None = None
_load_lock = asyncio.Lock()


async def _read_csv_async(url: str) -> pd.DataFrame:
    def _read():
        if isinstance(url, str) and url.endswith(".gz"):
            return pd.read_csv(url, compression="gzip")
        return pd.read_csv(url)

    return await asyncio.to_thread(_read)


async def _load_instruments_df_async(force_reload: bool = False) -> pd.DataFrame | None:
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
            logger.info(f"Loaded instruments: rows={len(df)}, exchanges={df['exchange'].nunique()}")
            return _instruments_df

        except Exception as e:
            logger.error(f"Failed to load instrument list from {INSTRUMENT_KEY_URL}: {e}")
            _instruments_df = None
            return None


#: Columns searched for a symbol, in preference order. An index carries its
#: short code in ``tradingsymbol`` (Nifty 50 is ``NIFTY``) and its full label
#: in ``name`` (``Nifty 50``), so a lookup must consider both.
_MATCH_COLUMNS = ("tradingsymbol", "symbol", "name")


def _normalised(subset: pd.DataFrame, col: str) -> pd.Series:
    return subset[col].astype(str).str.strip().str.upper()


def _match_instrument(subset: pd.DataFrame, ts: str) -> tuple[pd.Series, bool] | None:
    """Best row for ``ts`` in ``subset``, and whether the match was exact.

    Every candidate column is tried for an **exact** match before any
    substring matching. Doing it the other way round silently resolves the
    wrong instrument: searching "NIFTY 50" substring-matches "NIFTY 500" in
    ``tradingsymbol``, and taking that first hit never reaches ``name``,
    where "Nifty 50" sits as an exact match. That mis-resolution is not
    visible downstream — the caller receives a well-formed instrument key
    for the wrong index and captures an empty option chain.

    When only substring candidates exist the shortest value wins, so a query
    that is a prefix of several symbols resolves to the closest one rather
    than to whichever row the dataset happened to list first.
    """
    for col in _MATCH_COLUMNS:
        if col not in subset.columns:
            continue
        exact = subset[_normalised(subset, col) == ts]
        if not exact.empty:
            return exact.iloc[0], True

    best: tuple[int, pd.Series] | None = None
    for col in _MATCH_COLUMNS:
        if col not in subset.columns:
            continue
        values = _normalised(subset, col)
        candidates = subset[values.str.contains(ts, na=False, regex=False)]
        if candidates.empty:
            continue
        lengths = values.loc[candidates.index].str.len()
        index = lengths.idxmin()
        if best is None or int(lengths.loc[index]) < best[0]:
            best = (int(lengths.loc[index]), subset.loc[index])
    if best is not None:
        return best[1], False
    return None


async def get_instrument_key_async(trading_symbol: str, exchange: str) -> str | None:
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

    match = _match_instrument(subset, ts)
    if match is None:
        logger.warning(f"No instrument found for {trading_symbol} on {exchange}")
        return None

    row, is_exact = match
    key = row.get("instrument_key")
    if not key:
        logger.warning(f"instrument_key missing for {trading_symbol}/{exchange}")
        return None

    if not is_exact:
        # Surfaced at warning level: an inexact resolution is the failure mode
        # that captures the wrong instrument's chain, and it is otherwise
        # indistinguishable from a correct one.
        logger.warning(
            f"Resolved {trading_symbol}/{exchange} -> {key} by partial match "
            f"(tradingsymbol={row.get('tradingsymbol')}, name={row.get('name')}); "
            "set trading_symbol to an exact symbol or name to remove the ambiguity"
        )
    else:
        logger.info(
            f"Resolved {trading_symbol}/{exchange} -> {key} "
            f"(tradingsymbol={row.get('tradingsymbol')})"
        )
    return key


# Optional: sync-compatible wrappers if needed by existing code paths


def _load_instruments_df() -> pd.DataFrame | None:
    return asyncio.run(_load_instruments_df_async())


def get_instrument_key(trading_symbol: str, exchange: str) -> str | None:
    return asyncio.run(get_instrument_key_async(trading_symbol, exchange))

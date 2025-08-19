import pandas as pd
from constants import INSTRUMENT_KEY_URL
import logging
_instruments_df = None

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

def get_instrument_key(trading_symbol: str, exchange: str) -> str | None:
    global _instruments_df
    if _instruments_df is None:
        try:
            df = pd.read_csv(INSTRUMENT_KEY_URL, compression='gzip')
            df['expiry'] = pd.to_datetime(df['expiry']).dt.date
            _instruments_df = df
        except Exception as e:
            logger.error(f"Failed to load instrument list: {e}")
            return None

    # Filter by exchange and symbol
    filtered = _instruments_df[
        (_instruments_df['exchange'] == exchange) &
        (_instruments_df['tradingsymbol'] == trading_symbol)
    ]
    if filtered.empty:
        logger.warning(f"No instrument found for {trading_symbol} on {exchange}")
        return None
    # Return the first match
    return filtered.iloc[0]['instrument_key']

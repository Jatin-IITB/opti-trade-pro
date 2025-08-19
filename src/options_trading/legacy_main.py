import logging
from pathlib import Path
from constants import (
    LOG_LEVEL, LOG_DIR, LOG_FORMAT, LOG_DATE_FORMAT, MAIN_LOG_FILE,
    EXCHANGE, TRADING_SYMOBOL, DEFAULT_OPTION_INTERVAL, DEFAULT_SPOT_INTERVAL, DEFAULT_DAYS,
    DEFAULT_STRIKES, EXPIRY_START_INDEX, EXPIRY_END_INDEX
)
import os
from api.auth import get_access_token_automated
from api.expiries import get_expiries
from api.instrument_key import get_instrument_key
from options_trading.market_data.manager import MarketDataManager, get_expiry_slice
from utils.plots import plot_2x2_continuous_from_dir
from utils.io import sample_and_show_heads
from utils.cache import SpotDataCache
Path(LOG_DIR).mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL),
    format=LOG_FORMAT,
    datefmt=LOG_DATE_FORMAT,
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(Path(LOG_DIR) / MAIN_LOG_FILE, encoding='utf-8')
    ]
)
logger = logging.getLogger(__name__)

def main():
    logger.debug("Starting main()")
    access_token = get_access_token_automated()
    logger.debug("Obtained access token.")

    underlying_key = get_instrument_key(TRADING_SYMOBOL, EXCHANGE)
    if not underlying_key:
        logger.error(f"❌ Could not get instrument key for {TRADING_SYMOBOL} on {EXCHANGE}")
        return
    logger.debug(f"Underlying key for {TRADING_SYMOBOL}/{EXCHANGE}: {underlying_key}")

    all_expiries = get_expiries(underlying_key, access_token)
    logger.info(f"Found {len(all_expiries)} expiries for {TRADING_SYMOBOL} on {EXCHANGE}.")
    logger.debug(f"All expiries: {all_expiries}")

    expiries_to_process = get_expiry_slice(all_expiries, EXPIRY_START_INDEX, EXPIRY_END_INDEX)
    logger.debug(f"Expiries to process (slice): {expiries_to_process}")

    spot_cache = SpotDataCache(max_cache_size=10)
    logger.debug("SpotDataCache initialized.")

    data_manager = MarketDataManager(access_token, spot_cache=spot_cache)
    logger.debug("MarketDataManager instantiated.")

    for expiry in expiries_to_process:
        logger.info(f"Processing {TRADING_SYMOBOL} expiry {expiry}...")
        logger.debug(f"Calling save_features_for_expiry with expiry={expiry}")
        try:
            saved_files,out_dir = data_manager.save_features_for_expiry(
                symbol=TRADING_SYMOBOL,
                exchange=EXCHANGE,
                expiry=expiry,
                option_interval=DEFAULT_OPTION_INTERVAL,
                spot_interval=DEFAULT_SPOT_INTERVAL,
                days_back=DEFAULT_DAYS,
                strikes=DEFAULT_STRIKES,
            )
            logger.info(f"Saved {len(saved_files)} feature files for expiry {expiry}.")
            logger.debug(f"Saved files for expiry {expiry}: {saved_files}")
        except Exception as e:
            logger.error(f"Failed to process expiry {expiry}: {e}", exc_info=True)

    # 6️⃣ Output stats, run a sample plot, finish
    plot_2x2_continuous_from_dir(out_dir,sample_size=3)
    sample_and_show_heads(out_dir, sample_size=5)
    spot_cache.print_stats()
    logger.info("🎉 Pipeline completed successfully!")

if __name__ == "__main__":
    logger.debug("Starting program as __main__")
    main()

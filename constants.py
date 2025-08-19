# from pathlib import Path
# # API Endpoints
# UPSTOX_BASE_URL = "https://api.upstox.com"
# UPSTOX_EXPIRED_EXPIRIES_URL = f"{UPSTOX_BASE_URL}/v2/expired-instruments/expiries"
# UPSTOX_EXPIRED_CONTRACTS_URL = f"{UPSTOX_BASE_URL}/v2/expired-instruments/option/contract"
# UPSTOX_OPTION_CANDLES_URL = f"{UPSTOX_BASE_URL}/v2/expired-instruments/historical-candle"
# UPSTOX_SPOT_CANDLES_URL = f"{UPSTOX_BASE_URL}/v3/historical-candle"
# INSTRUMENT_KEY_URL = "https://assets.upstox.com/market-quote/instruments/exchange/complete.csv.gz"
# # API Keys
# DEFAULT_API_VERSION = "2.0"
# DEFAULT_ACCEPT_HEADER = "application/json"
# DEFAULT_CONTENT_TYPE = "application/x-www-form-urlencoded"
# DEFAULT_WEBHOOK_PORT = 8000
# # Timeout and retry settings
# API_TIMEOUT_SECONDS = 30
# API_RETRY_ATTEMPTS = 3
# API_RETRY_DELAY_SECONDS = 2
# MAX_CONCURRENT_REQUESTS = 5

# # Rate Limiting
# API_CALLS_PER_MINUTE = 50
# RATE_LIMIT_BUFFER_SECONDS = 1.2  # Safety buffer

# UPSTOX_AUTH_URL = f"{UPSTOX_BASE_URL}/v2/login/authorization/token"
# UPSTOX_PROFILE_URL = f"{UPSTOX_BASE_URL}/v2/user/profile"
# UPSTOX_CHARGES_URL = f"{UPSTOX_BASE_URL}/v2/charges/brokerage"
# UPSTOX_MARGIN_URL = f"{UPSTOX_BASE_URL}/v2/charges/margin"
# UPSTOX_OAUTH_DIALOG_URL = "https://api-v2.upstox.com/login/authorization/dialog"
# UPSTOX_INSTRUMENTS_URL = "https://assets.upstox.com/market-quote/instruments/exchange/complete.csv.gz"
# # Defaults
# DEFAULT_OPTION_INTERVAL = "3minute"   # For options API
# DEFAULT_SPOT_INTERVAL = "3"           # For spot API (in minutes, as string)
# DEFAULT_STRIKES = 1
# DEFAULT_DAYS = 4
# EXCHANGE = "NSE_INDEX"
# TRADING_SYMOBOL="Nifty 50"
# EXPIRY_START_INDEX=0
# EXPIRY_END_INDEX=None
# LATEST_EXPIRY = 5  # Special value to fetch the latest expiry expiries_to_process = expiries_all[-latest_n:]
# # DEFAULT_UNDERLYING = "NSE_EQ|INE002A01018"  #Reference "https://assets.upstox.com/market-quote/instruments/exchange/complete.csv.gz"
# DEFAULT_PORT = 8000
# DEFAULT_TRIM_HOURS = 2
# RISK_FREE_RATE = 0.0679
# # Realized Volatility Parameters
# RV_WINDOW_1MIN = 390          # 390 1-minute bars ≈ 1.5 trading days
# RV_WINDOW_3MIN = 130          # 130 3-minute bars ≈ 1.5 trading days
# RV_BUFFER_WEEKS = 2           # Weeks of buffer data to fetch
# MIN_RV_COVERAGE = 0.8         # Minimum coverage threshold for warnings
# PERIODS_PER_YEAR=252
# # Authentication and Token Management
# TOKEN_STORAGE_DIR = Path.home()
# TOKEN_STORAGE_FILE = ".upstox_tokens.json"
# TOKEN_EXPIRY_BUFFER_MINUTES = 5  # Refresh tokens 5 minutes before expiry
# MAX_TOKEN_AGE_DAYS = 7          # Maximum age before forcing re-authentication
# VALIDATION_API_TIMEOUT = 10     # Timeout for token validation API calls
# TOKEN_FILE_NAME = ".upstox_pairs_tokens.json"
# TOKEN_KEYRING_SERVICE = "efs_upstox"
# TOKEN_KEYRING_USERNAME = "access_info"
# TOKEN_EXPIRY_BUFFER_MINUTES = 5  # Refresh tokens 5 minutes before expiry
# OAUTH_TIMEOUT_SECONDS = 120
# DEFAULT_REDIRECT_PORT = 8000
# # OAuth Configuration
# OAUTH_TIMEOUT_SECONDS = 120     # OAuth flow timeout
# LOCAL_SERVER_PORT = 8000        # Default port for OAuth redirect
# LOG_LEVEL = "DEBUG"
# LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
# LOG_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

# LOG_DIR = Path.cwd() / "logs"
# MAIN_LOG_FILE = "pairs_trading.log"
# TRADE_LOG_FILE = "trades.log"
# ERROR_LOG_FILE = "errors.log"
# MAX_LOG_FILE_SIZE_MB = 10
# LOG_BACKUP_COUNT = 5

# # Live trading logs
# LIVE_TRADING_LOG_DIR = LOG_DIR / "live_trading"
# MARKET_DATA_LOG_FILE = "market_data_stream.log"
# TRADE_EXECUTION_LOG_FILE = "trade_execution.log"
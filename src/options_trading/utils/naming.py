import os
from datetime import datetime

def generate_output_dir(base: str, underlying: str, interval: str, days: int) -> str:
    """
    Returns a directory name for final output.
    Example: FINAL_NIFTY_3minute_7d
    """
    underlying_clean = underlying.replace(" ", "").replace("|", "_")
    return f"{base}_{underlying_clean}_{interval}_{days}d"

def generate_filename(trading_symbol: str, expiry: str,instrument_type:str, strike_price:int, extension:str) -> str:
# def generate_filename(trading_symbol: str) -> str:
    """
    Returns a file name for a contract's data.
    Example: NIFTY_22450_CE_09_JUL_25.parquet
    """
    # # Expect expiry as 'YYYY-MM-DD'
    # expiry_dt = datetime.strptime(expiry, "%Y-%m-%d")
    # expiry_str = expiry_dt.strftime("%d_%b_%y").upper()
    # name = trading_symbol.replace(" ", "_")
    # return f"{name}_{strike_price}_{instrument_type}_{expiry_str}.{extension}"
    fname = trading_symbol.replace(" ", "_") + ".parquet"
    return fname
    
def parse_filename(fname: str):
    """
    Parses a filename like NIFTY_22450_CE_09_JUL_25.parquet
    Returns (strike, opt_type, expiry)
    """
    parts = os.path.splitext(os.path.basename(fname))[0].split("_")
    strike = float(parts[1])
    opt_type = 'call' if parts[2].lower() == 'ce' else 'put'
    expiry = f"{parts[3]}_{parts[4]}_{parts[5]}"
    expiry_dt = datetime.strptime(expiry, "%d_%b_%y")
    return strike, opt_type, expiry_dt

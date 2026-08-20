import pandas as pd


def ensure_kolkata_tz(col):
    """
    Ensures a pandas datetime column is localized to Asia/Kolkata.
    """
    col = pd.to_datetime(col)
    if not hasattr(col.dtype, "tz") or col.dt.tz is None:
        return col.dt.tz_localize("Asia/Kolkata", ambiguous="NaT", nonexistent="NaT")
    return col.dt.tz_convert("Asia/Kolkata")


def is_trading_hour(ts):
    """
    Checks if a timestamp is within NSE trading hours (9:15-15:30 IST).
    """
    t = ts.time()
    return (t >= pd.Timestamp("09:15:00").time()) and (t <= pd.Timestamp("15:30:00").time())


def filter_trading_hours(df, time_col="timestamp"):
    """
    Filters DataFrame rows to only those within trading hours.
    """
    df = df.copy()
    df[time_col] = ensure_kolkata_tz(df[time_col])
    df["time"] = df[time_col].dt.time
    mask = (df["time"] >= pd.Timestamp("09:15:00").time()) & (
        df["time"] <= pd.Timestamp("15:30:00").time()
    )
    return df[mask].drop(columns=["time"])

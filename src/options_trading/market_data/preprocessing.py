# market_data/preprocessing.py

import numpy as np
import pandas as pd
import logging

from datetime import datetime, timedelta, time as dtime
from typing import Any
from ..config.settings import settings
from ..utils.time import ensure_kolkata_tz, filter_trading_hours
from ..utils.data_quality import validate_option_data

logger = logging.getLogger(__name__)

###################
# Data Cleaning   #
###################

def clean_and_merge_option_spot(option_df: pd.DataFrame, spot_df: pd.DataFrame, expiry: str, strike: int) -> pd.DataFrame:
    """Trim to trading hours, merge spot/option, cut off pre-expiry window, uniform columns."""
    option_df['timestamp'] = ensure_kolkata_tz(option_df['timestamp'])
    spot_df['timestamp'] = ensure_kolkata_tz(spot_df['timestamp'])

    expiry_date = pd.to_datetime(expiry).date()
    end_time = dtime(15, 30)
    expiry_cutoff = pd.Timestamp(datetime.combine(expiry_date, end_time)) - timedelta(hours=settings.default_trim_hours)
    expiry_cutoff = ensure_kolkata_tz(pd.Series([expiry_cutoff])).iloc[0]

    option_df = filter_trading_hours(option_df)
    spot_df = filter_trading_hours(spot_df)

    option_df = option_df[option_df['timestamp'] < expiry_cutoff]
    spot_df = spot_df[spot_df['timestamp'] < expiry_cutoff]

    # Standardize names
    option_df = option_df.rename(columns={'open': 'open_option', 'high': 'high_option',
                                          'low': 'low_option', 'close': 'close_option'})
    spot_df = spot_df.rename(columns={'open': 'open_spot', 'high': 'high_spot',
                                      'low': 'low_spot', 'close': 'close_spot'})
    option_df = option_df.set_index('timestamp')
    spot_df = spot_df.set_index('timestamp')

    merged = pd.merge(
        option_df[['open_option', 'high_option', 'low_option', 'close_option']],
        spot_df[['open_spot', 'high_spot', 'low_spot', 'close_spot']],
        left_index=True, right_index=True, how='inner'
    )
    if not validate_option_data(merged, strike, option_df['instrument_type'].iloc[0]):
        logger.warning(f"Data validation failed - continuing with NaNs")
    return merged.reset_index()

###################
# IV Calculation  #
###################

from scipy.stats import norm
from scipy.optimize import brentq

def bs_price(S, K, T, r, sigma, option_type):
    if S <= 0 or K <= 0 or T <= 0 or sigma <= 1e-8:
        return np.nan
    try:
        d1 = (np.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * np.sqrt(T))
        d2 = d1 - sigma * np.sqrt(T)
        if option_type.lower() in ['ce', 'call']:
            return S * norm.cdf(d1) - K * np.exp(-r * T) * norm.cdf(d2)
        else:
            return K * np.exp(-r * T) * norm.cdf(-d2) - S * norm.cdf(-d1)
    except (ZeroDivisionError, ValueError, OverflowError) as e:
        logger.error(f"Black-Scholes price calculation error: {e}")
        return np.nan

def implied_volatility(price, S, K, T, r, option_type):
    if price <= 0 or S <= 0 or K <= 0 or T <= 0:
        return np.nan
    # Check price bounds
    if option_type.lower() in ['ce', 'call']:
        intrinsic = max(0, S - K)
        max_price = S
    else:
        intrinsic = max(0, K - S)
        max_price = K * np.exp(-r * T)
    if price < intrinsic * 0.99 or price > max_price * 1.01:
        logger.debug(f"IV price bounds fail: price={price}, intrinsic={intrinsic}, max={max_price}")
        return np.nan
    def objective(sigma):
        return bs_price(S, K, T, r, sigma, option_type) - price
    try:
        return brentq(objective, 0.01, 10.0, maxiter=200)
    except Exception:
        return np.nan

def compute_iv_in_memory(df: pd.DataFrame, strike, expiry, option_type) -> pd.DataFrame:
    """Compute Black-Scholes IV for each row. Adds 'iv' column."""
    required = ['timestamp', 'close_option', 'close_spot']
    if any(col not in df.columns for col in required):
        logger.error(f"Missing columns for IV computation: {required}")
        df['iv'] = np.nan
        return df
    expiry_ts = pd.to_datetime(expiry)
    expiry_ts = ensure_kolkata_tz(pd.Series([expiry_ts])).iloc[0]
    df = df.copy()
    df['timestamp'] = ensure_kolkata_tz(df['timestamp'])
    def calc_iv(row):
        try:
            T = (expiry_ts - row['timestamp']).total_seconds() / (365 * 24 * 3600)
            if T <= 1/365:
                return np.nan
            price = float(row['close_option'])
            S = float(row['close_spot'])
            if pd.isna(price) or pd.isna(S) or price <= 0 or S <= 0:
                return np.nan
            return implied_volatility(price, S, strike, T, settings.risk_free_rate, option_type)
        except Exception:
            return np.nan
    df['iv'] = df.apply(calc_iv, axis=1)
    n = len(df)
    n_nan = df['iv'].isna().sum()
    pct = (n - n_nan) / n * 100 if n > 0 else 0
    logger.info(f"IV calculation: {pct:.1f}% non-NaN success ({n-n_nan}/{n})")
    return df

###################
# Greeks          #
###################

def calculate_greeks(S, K, T, r, sigma, option_type):
    if S <= 0 or K <= 0 or T <= 0 or sigma <= 0:
        return np.nan, np.nan, np.nan, np.nan, np.nan
    try:
        d1 = (np.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * np.sqrt(T))
        d2 = d1 - sigma * np.sqrt(T)
        norm_d1 = norm.cdf(d1)
        norm_pdf_d1 = norm.pdf(d1)
        gamma = norm_pdf_d1 / (S * sigma * np.sqrt(T))
        vega = S * norm_pdf_d1 * np.sqrt(T) / 100
        if option_type.lower() in ['ce', 'call']:
            delta = norm_d1
            theta = ((-S * norm_pdf_d1 * sigma) / (2 * np.sqrt(T)) - r * K * np.exp(-r * T) * norm.cdf(d2)) / 365
            rho = K * T * np.exp(-r * T) * norm.cdf(d2) / 100
        else:
            delta = norm_d1 - 1
            theta = ((-S * norm_pdf_d1 * sigma) / (2 * np.sqrt(T)) + r * K * np.exp(-r * T) * norm.cdf(-d2)) / 365
            rho = -K * T * np.exp(-r * T) * norm.cdf(-d2) / 100
        return delta, gamma, theta, vega, rho
    except Exception:
        return np.nan, np.nan, np.nan, np.nan, np.nan

def append_greeks_in_memory(df: pd.DataFrame, strike, expiry, option_type) -> pd.DataFrame:
    """Adds delta, gamma, theta, vega, rho columns."""
    required = ['timestamp', 'close_option', 'close_spot', 'iv']
    if any(col not in df.columns for col in required):
        logger.error(f"Missing columns for Greeks: {required}")
        for col in ['delta', 'gamma', 'theta', 'vega', 'rho']:
            df[col] = np.nan
        return df
    expiry_ts = ensure_kolkata_tz(pd.Series([pd.to_datetime(expiry)])).iloc[0]
    df = df.copy()
    df['timestamp'] = ensure_kolkata_tz(df['timestamp'])
    def calc_greeks_row(row):
        try:
            T = (expiry_ts - row['timestamp']).total_seconds() / (365 * 24 * 3600)
            if T <= 1/365 or pd.isna(row['iv']) or row['iv'] <= 0:
                return pd.Series([np.nan]*5, index=['delta', 'gamma', 'theta', 'vega', 'rho'])
            S = float(row['close_spot'])
            sigma = float(row['iv'])
            return pd.Series(calculate_greeks(S, strike, T, settings.risk_free_rate, sigma, option_type),
                             index=['delta', 'gamma', 'theta', 'vega', 'rho'])
        except Exception:
            return pd.Series([np.nan]*5, index=['delta', 'gamma', 'theta', 'vega', 'rho'])
    return pd.concat([df, df.apply(calc_greeks_row, axis=1)], axis=1)

###################
# Realized Volatility (RV)
###################

def garman_klass_rv(df, window, periods_per_year):
    log_hl = np.log(df['high'] / df['low'])
    log_co = np.log(df['close'] / df['open'])
    var_gk = 0.5 * log_hl ** 2 - (2 * np.log(2) - 1) * log_co ** 2
    rolling_var = var_gk.rolling(window=window, min_periods=window).sum()
    realized_vol = np.sqrt(rolling_var) * np.sqrt(periods_per_year / window)
    return realized_vol

def parkinson_rv(df, window, periods_per_year):
    log_hl = np.log(df['high'] / df['low'])
    park_var = log_hl ** 2 / (4 * np.log(2))
    rolling_var = park_var.rolling(window=window, min_periods=window).sum()
    realized_vol = np.sqrt(rolling_var) * np.sqrt(periods_per_year / window)
    return realized_vol


def fetch_extended_spot_data(df_option, access_token, underlying_key, interval_minutes, fetch_func, cache_func=None):
    timestamps = pd.to_datetime(df_option['timestamp'])
    first_ts = timestamps.min()
    last_ts = timestamps.max()
    fetch_start_dt = first_ts - timedelta(weeks=1)
    fetch_end_dt = last_ts + timedelta(days=1)
    from_date = fetch_start_dt.strftime('%Y-%m-%d')
    to_date = fetch_end_dt.strftime('%Y-%m-%d')

    try:
        if cache_func:
            logger.info(f"Using cached spot data from {from_date} to {to_date}")
            df_spot = cache_func(underlying_key, from_date, to_date, access_token, interval_minutes, fetch_func)
        else:
            logger.info(f"Fetching extended spot data from {from_date} to {to_date}")
            df_spot = fetch_func(
                access_token, underlying_key, from_date, to_date, str(interval_minutes), unit="minutes"
            )
        if df_spot.empty:
            logger.error("Received empty spot data")
            return pd.DataFrame()
        if 'timestamp' not in df_spot.columns:
            for col in ['datetime', 'date', 'time']:
                if col in df_spot.columns:
                    df_spot = df_spot.rename(columns={col: 'timestamp'})
                    break
            else:
                if df_spot.index.name in ['datetime', 'timestamp', 'date', 'time']:
                    df_spot = df_spot.reset_index()
                    df_spot = df_spot.rename(columns={df_spot.columns[0]: 'timestamp'})
        df_spot['timestamp'] = ensure_kolkata_tz(df_spot['timestamp'])
        df_spot = df_spot.sort_values('timestamp')
        mask = (df_spot['timestamp'] <= last_ts)
        df_spot = df_spot.loc[mask].copy()
        logger.info(f"Extended spot data: {len(df_spot)} bars available")
        return df_spot
    except Exception as e:
        logger.error(f"Failed to fetch extended spot data: {e}")
        return pd.DataFrame()

def append_rv_in_memory(df, access_token, underlying_key, interval_minutes, fetch_func, window=None, cache_func=None):
    if window is None:
        if interval_minutes == 1:
            window = settings.rv_window_1min
        elif interval_minutes == 3:
            window = settings.rv_window_3min
        else:
            window = max(100, 390 // interval_minutes)
    periods_per_year = settings.periods_per_year * (390 / window)
    logger.info(f"Calculating RV with window={window}, periods_per_year={periods_per_year:.0f}")
    df_spot_extended = fetch_extended_spot_data(
        df, access_token, underlying_key, interval_minutes, fetch_func, cache_func
    )
    if df_spot_extended.empty:
        logger.error("No extended spot data available for RV calculation")
        df['rv_gk'] = np.nan
        df['rv_parkinson'] = np.nan
        return df
    df_spot_extended['rv_gk'] = garman_klass_rv(df_spot_extended, window, periods_per_year)
    df_spot_extended['rv_parkinson'] = parkinson_rv(df_spot_extended, window, periods_per_year)
    df_spot_rv = df_spot_extended[['timestamp', 'rv_gk', 'rv_parkinson']]
    df_merged = pd.merge(df, df_spot_rv, on='timestamp', how='left')
    n_nan_gk = df_merged['rv_gk'].isna().sum()
    n_nan_park = df_merged['rv_parkinson'].isna().sum()
    total_rows = len(df_merged)
    if n_nan_gk > 0:
        logger.warning(f"⚠️ {n_nan_gk}/{total_rows} NaN values in Garman-Klass RV")
    if n_nan_park > 0:
        logger.warning(f"⚠️ {n_nan_park}/{total_rows} NaN values in Parkinson RV")
    gk_coverage = (total_rows - n_nan_gk) / total_rows * 100
    park_coverage = (total_rows - n_nan_park) / total_rows * 100
    logger.info(f"RV calculation complete: GK {gk_coverage:.1f}% coverage, Parkinson {park_coverage:.1f}% coverage")
    return df_merged

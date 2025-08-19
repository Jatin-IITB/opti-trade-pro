# api/contracts.py

import requests
import pandas as pd
from constants import UPSTOX_EXPIRED_CONTRACTS_URL
from api.exceptions import APIError, DataQualityError

def fetch_expired_option_contracts_df(
    instrument_key: str,
    expiry_date: str,
    access_token: str
) -> pd.DataFrame:
    """
    Fetch all available option contracts for a given underlying and expiry.
    Returns a DataFrame indexed by instrument_key.
    """
    url = UPSTOX_EXPIRED_CONTRACTS_URL
    headers = {
        "Accept": "application/json",
        "Authorization": f"Bearer {access_token}",
    }
    params = {"instrument_key": instrument_key, "expiry_date": expiry_date}
    resp = requests.get(url, headers=headers, params=params)
    payload = resp.json()
    if payload.get("status") != "success":
        raise APIError(f"Contracts API error: {payload}")

    df = pd.DataFrame(payload["data"])
    essential = [
        "instrument_key", "trading_symbol", "expiry", "strike_price",
        "instrument_type", "underlying_key", "weekly"
    ]
    for c in essential:
        if c not in df.columns:
            df[c] = pd.NA
    df = df[essential].copy()
    df["expiry"] = pd.to_datetime(df["expiry"])
    df["strike_price"] = df["strike_price"].astype(float)
    df["weekly"] = df["weekly"].astype(bool)

    if df.empty:
        raise DataQualityError("No contracts found in response.")
    return df.set_index("instrument_key")

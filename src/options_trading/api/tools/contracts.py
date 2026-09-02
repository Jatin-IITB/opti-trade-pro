# api/contracts.py
import pandas as pd

from ...config.settings import settings
from ...utils.exceptions import APIError, DataQualityError
from ...utils.http import get_session

url = settings.upstox_expired_contracts_url


def fetch_expired_option_contracts_df(
    instrument_key: str, expiry_date: str, access_token: str
) -> pd.DataFrame:
    headers = {
        "Accept": "application/json",
        "Authorization": f"Bearer {access_token}",
    }
    params = {"instrument_key": instrument_key, "expiry_date": expiry_date}
    resp = get_session().get(url, headers=headers, params=params)
    if resp.status_code != 200:
        raise APIError(f"Contracts HTTP {resp.status_code}: {resp.text}")
    payload = resp.json()
    if payload.get("status") != "success":
        raise APIError(f"Contracts API error: {payload}")
    data = payload.get("data", [])
    if not data:
        raise DataQualityError("No contracts found in response.")
    df = pd.DataFrame(data)

    essential = [
        "instrument_key",
        "trading_symbol",
        "expiry",
        "strike_price",
        "instrument_type",
        "underlying_key",
        "weekly",
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

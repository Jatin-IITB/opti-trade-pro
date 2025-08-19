# api/candles.py
import requests
import pandas as pd
from ...config.settings import settings
from ...utils.exceptions import APIError, DataQualityError
UPSTOX_OPTION_CANDLES_URL=settings.upstox_option_candles_url
UPSTOX_SPOT_CANDLES_URL=settings.upstox_spot_candles_url
def fetch_option_candles(expired_instrument_key: str, interval: str, from_date: str, to_date: str, access_token: str) -> pd.DataFrame:
    url = f"{UPSTOX_OPTION_CANDLES_URL}/{expired_instrument_key}/{interval}/{to_date}/{from_date}"
    headers = {
        "Accept": "application/json",
        "Authorization": f"Bearer {access_token}",
    }
    resp = requests.get(url, headers=headers)
    if resp.status_code != 200:
        raise APIError(f"Option candle fetch HTTP {resp.status_code}: {resp.text}")
    payload = resp.json()
    candles = payload.get("data", {}).get("candles", [])
    if not candles:
        raise DataQualityError("No candles returned by option API")
    columns = ["timestamp", "open", "high", "low", "close", "volume", "open_interest"]
    df = pd.DataFrame(candles, columns=columns)
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    df = df.set_index("timestamp").sort_index()
    return df

def fetch_upstox_historical_data(access_token: str, instrument_token: str, from_date: str, to_date: str, interval: str, unit: str = 'minutes') -> pd.DataFrame:
    headers = {
        'Accept': 'application/json',
        'Api-Version': '3.0',
        'Authorization': f"Bearer {access_token}",
    }
    url = f'{UPSTOX_SPOT_CANDLES_URL}/{instrument_token}/{unit}/{interval}/{to_date}/{from_date}'
    response = requests.get(url, headers=headers)
    if response.status_code != 200:
        raise APIError(f"Spot candle fetch HTTP {response.status_code}: {response.text}")
    data = response.json()
    candles = data.get('data', {}).get('candles', [])
    if not candles:
        raise DataQualityError("No spot candles returned by API")
    df = pd.DataFrame(candles, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume', 'oi'])
    df['timestamp'] = pd.to_datetime(df['timestamp'])
    df.set_index('timestamp', inplace=True)
    return df

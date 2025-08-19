# api/pick_strikes.py

import pandas as pd
from typing import List
from constants import DEFAULT_STRIKES

def pick_near_the_money_contracts(
    df_contracts: pd.DataFrame,
    spot_price: float,
    num_itm: int = DEFAULT_STRIKES,
    num_otm: int = DEFAULT_STRIKES
) -> List[str]:
    """
    Pick near-the-money ATM, ITM, and OTM option contract instrument_keys for both calls and puts.
    Returns a list of contract indices for targeted strikes.
    """
    df = df_contracts.copy()
    df["moneyness"] = df["strike_price"] - spot_price
    calls = df[df["instrument_type"] == "CE"].sort_values("moneyness")
    puts = df[df["instrument_type"] == "PE"].sort_values("moneyness")
    itm_calls = calls[calls["moneyness"] < 0].tail(num_itm)
    otm_calls = calls[calls["moneyness"] > 0].head(num_otm)
    itm_puts = puts[puts["moneyness"] > 0].head(num_itm)
    otm_puts = puts[puts["moneyness"] < 0].tail(num_otm)

    return (
        itm_calls.index.tolist()
        + otm_calls.index.tolist()
        + itm_puts.index.tolist()
        + otm_puts.index.tolist()
    )

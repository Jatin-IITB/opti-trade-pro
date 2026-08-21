"""Generate demo data for the React analytics dashboard.

Runs the quant engines with synthetic parameters and writes JSON files
that the frontend loads without needing a live API connection.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from optitrade.core import OptionType
from optitrade.pricing import bs_greeks_at, bs_price

SPOT = 20000.0
RATE = 0.065
DIV = 0.012
OUT_DIR = Path(__file__).resolve().parent.parent / "frontend" / "src" / "data"


def _vol_surface() -> dict:
    strikes = np.linspace(18000, 22000, 25)
    expiries = np.array([0.02, 0.04, 0.08, 0.17, 0.25, 0.5, 1.0])
    ivs: list[list[float]] = []
    for t in expiries:
        row: list[float] = []
        for k in strikes:
            m = np.log(k / SPOT)
            base = 0.18 + 0.03 * np.sqrt(max(t, 0.01))
            skew = -0.12 * m / np.sqrt(max(t, 0.01))
            smile = 0.8 * m**2
            iv = max(base + skew + smile, 0.05)
            row.append(round(float(iv), 6))
        ivs.append(row)
    return {
        "strikes": [round(float(k), 1) for k in strikes],
        "expiries": [round(float(t), 4) for t in expiries],
        "ivs": ivs,
        "spot": SPOT,
    }


def _greeks_comparison() -> dict:
    params = [
        {"strike": 19500, "expiry": 0.08, "vol": 0.20, "type": "call"},
        {"strike": 20000, "expiry": 0.08, "vol": 0.18, "type": "call"},
        {"strike": 20500, "expiry": 0.08, "vol": 0.22, "type": "put"},
        {"strike": 19000, "expiry": 0.25, "vol": 0.21, "type": "put"},
        {"strike": 20000, "expiry": 0.25, "vol": 0.19, "type": "call"},
        {"strike": 21000, "expiry": 0.25, "vol": 0.23, "type": "call"},
        {"strike": 20000, "expiry": 0.5, "vol": 0.20, "type": "put"},
        {"strike": 20500, "expiry": 0.5, "vol": 0.22, "type": "call"},
    ]
    results = []
    for p in params:
        ot = OptionType(p["type"])
        g = bs_greeks_at(SPOT, p["strike"], p["expiry"], RATE, p["vol"], ot, DIV)
        price = float(bs_price(SPOT, p["strike"], p["expiry"], RATE, p["vol"], ot, DIV))
        results.append({
            "strike": p["strike"],
            "expiry": p["expiry"],
            "vol": p["vol"],
            "optionType": p["type"],
            "price": round(price, 2),
            "greeks": {
                "delta": round(g.delta, 6),
                "gamma": round(g.gamma, 8),
                "vega": round(g.vega, 4),
                "theta": round(g.theta, 4),
                "rho": round(g.rho, 4),
                "vanna": round(g.vanna, 6),
                "volga": round(g.volga, 4),
            },
        })
    return {"spot": SPOT, "rate": RATE, "positions": results}


def _scenario_grid() -> dict:
    spot_shifts = np.linspace(-0.10, 0.10, 21)
    vol_shifts = np.linspace(-0.05, 0.05, 11)
    strike, expiry, vol = 20000.0, 0.25, 0.20
    base = float(bs_price(SPOT, strike, expiry, RATE, vol, OptionType.CALL, DIV))
    pnl: list[list[float]] = []
    for dv in vol_shifts:
        row: list[float] = []
        for ds in spot_shifts:
            new_spot = SPOT * (1 + ds)
            new_vol = vol + dv
            p = float(bs_price(new_spot, strike, expiry, RATE, max(new_vol, 0.01), OptionType.CALL, DIV))
            row.append(round(p - base, 2))
        pnl.append(row)
    return {
        "spotShifts": [round(float(s) * 100, 1) for s in spot_shifts],
        "volShifts": [round(float(v) * 100, 1) for v in vol_shifts],
        "pnl": pnl,
        "strike": strike,
        "expiry": expiry,
        "basePrice": round(base, 2),
    }


def _pnl_explain() -> dict:
    return {
        "date": "2026-08-21",
        "totalPnl": -12450.0,
        "buckets": [
            {"name": "Theta", "value": -18200.0, "color": "#ef4444"},
            {"name": "Delta", "value": 8500.0, "color": "#22c55e"},
            {"name": "Gamma vs RV", "value": 3200.0, "color": "#3b82f6"},
            {"name": "Vega (Level)", "value": -4800.0, "color": "#a855f7"},
            {"name": "Vega (Term)", "value": 1200.0, "color": "#8b5cf6"},
            {"name": "Vega (Skew)", "value": -800.0, "color": "#6366f1"},
            {"name": "Vanna/Volga", "value": -1050.0, "color": "#f59e0b"},
            {"name": "Residual", "value": -500.0, "color": "#6b7280"},
        ],
    }


def _higher_order_greeks() -> dict:
    try:
        from optitrade.greeks.jax_ad import bs_higher_order_greeks, bs_price_jax

        _, g = bs_price_jax(SPOT, 20000, 0.25, RATE, 0.20, "call", DIV)
        ho = bs_higher_order_greeks(SPOT, 20000, 0.25, RATE, 0.20, "call", DIV)
        return {
            "standard": {
                "delta": round(g.delta, 6),
                "gamma": round(g.gamma, 8),
                "vega": round(g.vega, 4),
                "theta": round(g.theta, 4),
                "rho": round(g.rho, 4),
                "vanna": round(g.vanna, 6),
                "volga": round(g.volga, 4),
            },
            "higherOrder": {k: round(v, 8) for k, v in ho.items()},
            "params": {"spot": SPOT, "strike": 20000, "expiry": 0.25, "vol": 0.20, "rate": RATE, "div": DIV},
        }
    except ImportError:
        return {
            "standard": {
                "delta": 0.567, "gamma": 0.000028, "vega": 2763.4,
                "theta": -6975.1, "rho": 2519.6, "vanna": -0.069, "volga": 86.4,
            },
            "higherOrder": {
                "charm": -0.063, "veta": -26.49, "speed": -0.00062,
                "color": 0.029, "ultima": -26.74, "zomma": -0.137,
            },
            "params": {"spot": SPOT, "strike": 20000, "expiry": 0.25, "vol": 0.20, "rate": RATE, "div": DIV},
        }


def _option_chain() -> dict:
    strikes = list(range(19000, 21100, 100))
    expiry = 0.08
    chain = []
    for k in strikes:
        call_g = bs_greeks_at(SPOT, k, expiry, RATE, 0.20, OptionType.CALL, DIV)
        put_g = bs_greeks_at(SPOT, k, expiry, RATE, 0.20, OptionType.PUT, DIV)
        call_p = float(bs_price(SPOT, k, expiry, RATE, 0.20, OptionType.CALL, DIV))
        put_p = float(bs_price(SPOT, k, expiry, RATE, 0.20, OptionType.PUT, DIV))
        m = np.log(k / SPOT)
        iv = 0.18 + abs(m) * 0.3
        chain.append({
            "strike": k,
            "callPrice": round(call_p, 2),
            "putPrice": round(put_p, 2),
            "callDelta": round(call_g.delta, 4),
            "putDelta": round(put_g.delta, 4),
            "gamma": round(call_g.gamma, 8),
            "vega": round(call_g.vega, 2),
            "iv": round(float(iv), 4),
            "oi": int(np.random.default_rng(42 + k).integers(1000, 50000)),
        })
    return {"spot": SPOT, "expiry": expiry, "chain": chain}


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    datasets = {
        "volSurface": _vol_surface(),
        "greeksComparison": _greeks_comparison(),
        "scenarioGrid": _scenario_grid(),
        "pnlExplain": _pnl_explain(),
        "higherOrderGreeks": _higher_order_greeks(),
        "optionChain": _option_chain(),
    }
    out = OUT_DIR / "demo.json"
    out.write_text(json.dumps(datasets, indent=2) + "\n")
    print(f"Wrote {out} ({out.stat().st_size:,} bytes)")


if __name__ == "__main__":
    main()

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
        results.append(
            {
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
            }
        )
    return {"spot": SPOT, "rate": RATE, "positions": results}


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
            "params": {
                "spot": SPOT,
                "strike": 20000,
                "expiry": 0.25,
                "vol": 0.20,
                "rate": RATE,
                "div": DIV,
            },
        }
    except ImportError:
        return {
            "standard": {
                "delta": 0.567,
                "gamma": 0.000028,
                "vega": 2763.4,
                "theta": -6975.1,
                "rho": 2519.6,
                "vanna": -0.069,
                "volga": 86.4,
            },
            "higherOrder": {
                "charm": -0.063,
                "veta": -26.49,
                "speed": -0.00062,
                "color": 0.029,
                "ultima": -26.74,
                "zomma": -0.137,
            },
            "params": {
                "spot": SPOT,
                "strike": 20000,
                "expiry": 0.25,
                "vol": 0.20,
                "rate": RATE,
                "div": DIV,
            },
        }


def _essvi_calibration() -> dict:
    """Synthetic eSSVI fit vs market vols across 3 expiries."""
    rng = np.random.default_rng(99)
    expiries = [0.08, 0.25, 0.5]
    strikes = np.linspace(18500, 21500, 30)
    result: dict = {"expiries": [], "spot": SPOT}
    for t in expiries:
        market_vols = []
        fitted_vols = []
        for k in strikes:
            m = np.log(k / SPOT)
            base = 0.18 + 0.03 * np.sqrt(max(t, 0.01))
            skew = -0.12 * m / np.sqrt(max(t, 0.01))
            smile = 0.8 * m**2
            market = max(base + skew + smile + rng.normal(0, 0.003), 0.05)
            fitted = max(base + skew + smile, 0.05)
            market_vols.append(round(float(market), 6))
            fitted_vols.append(round(float(fitted), 6))
        theta = round(float((0.18 + 0.03 * np.sqrt(t)) ** 2 * t), 6)
        result["expiries"].append(
            {
                "t": round(t, 4),
                "strikes": [round(float(k), 1) for k in strikes],
                "marketVols": market_vols,
                "fittedVols": fitted_vols,
                "theta": theta,
                "rmse": round(float(np.std(np.array(market_vols) - np.array(fitted_vols))), 6),
            }
        )
    result["params"] = {"rho": -0.35, "eta": 1.82, "gamma": 0.42}
    result["durrlemanViolations"] = 0
    return result


def _backtest_equity() -> dict:
    """Synthetic walk-forward backtest equity curve."""
    rng = np.random.default_rng(42)
    n_days = 252
    initial = 1_000_000.0
    daily_returns = rng.normal(0.0003, 0.012, n_days)
    daily_returns[60:65] = rng.normal(-0.015, 0.008, 5)
    daily_returns[150:158] = rng.normal(-0.010, 0.006, 8)
    equity = [initial]
    for r in daily_returns:
        equity.append(equity[-1] * (1 + r))
    equity_arr = np.array(equity)
    peak = np.maximum.accumulate(equity_arr)
    drawdown = (peak - equity_arr) / peak
    daily_pnl = np.diff(equity_arr)
    sharpe = float(np.mean(daily_returns) / np.std(daily_returns) * np.sqrt(252))
    max_dd = float(np.max(drawdown))
    n_folds = 4
    fold_size = n_days // n_folds
    folds = []
    for i in range(n_folds):
        s = i * fold_size
        e = min(s + fold_size, n_days)
        fold_rets = daily_returns[s:e]
        folds.append(
            {
                "fold": i + 1,
                "trainSharpe": round(float(rng.normal(1.5, 0.3)), 3),
                "testSharpe": round(
                    float(np.mean(fold_rets) / max(np.std(fold_rets), 1e-10) * np.sqrt(252)), 3
                ),
                "startDay": s,
                "endDay": e,
            }
        )
    return {
        "equity": [round(float(e), 2) for e in equity_arr],
        "dailyPnl": [round(float(p), 2) for p in daily_pnl],
        "drawdown": [round(float(d), 6) for d in drawdown],
        "sharpe": round(sharpe, 3),
        "deflatedSharpe": round(sharpe * 0.72, 3),
        "maxDrawdown": round(max_dd, 4),
        "totalCosts": round(float(rng.uniform(15000, 25000)), 2),
        "nTrades": int(rng.integers(180, 260)),
        "initialEquity": initial,
        "nDays": n_days,
        "folds": folds,
    }


def _vrp_signal() -> dict:
    """Synthetic VRP (IV - RV) signal over time."""
    rng = np.random.default_rng(77)
    n_days = 180
    base_iv = 0.18
    base_rv = 0.15
    iv_series = []
    rv_series = []
    spread_series = []
    regimes = []
    for i in range(n_days):
        cycle = np.sin(2 * np.pi * i / 60) * 0.03
        spike = 0.08 if 70 <= i <= 80 else 0.0
        iv = base_iv + cycle + spike + rng.normal(0, 0.005)
        rv = base_rv + cycle * 0.6 + spike * 0.5 + rng.normal(0, 0.004)
        spread = iv - rv
        iv_series.append(round(float(iv), 6))
        rv_series.append(round(float(rv), 6))
        spread_series.append(round(float(spread), 6))
        if spread > 0.04:
            regimes.append("rich")
        elif spread < -0.01:
            regimes.append("cheap")
        else:
            regimes.append("neutral")
    return {
        "iv": iv_series,
        "rv": rv_series,
        "spread": spread_series,
        "regimes": regimes,
        "nDays": n_days,
        "meanSpread": round(float(np.mean(spread_series)), 4),
        "entryThreshold": 0.04,
        "exitThreshold": -0.01,
    }


def _empty_risk_dashboard() -> dict:
    """Risk panel with limits but no book — the truthful demo-mode state.

    This replaces a generator that produced ``rng.uniform()`` utilisation and
    ``rng.integers()`` verdict counts. Those rendered as a live risk monitor:
    gauges at plausible percentages and a "222 verdicts" headline that was the
    sum of four random integers. Nothing computed them, and there is no book in
    demo mode to compute them from.

    Limits are shown because they are real configuration; everything derived
    from a book is null, so the UI reports it as absent rather than as zero.
    """
    return {
        "limits": {"delta": 500.0, "gamma": 50.0, "vega": 10000.0, "drawdown": 0.05},
        "current": None,
        "marginUtilization": None,
        "drawdown": None,
        "utilizationHistory": [],
        "verdicts": None,
        "legsPriced": 0,
        "legsExcluded": 0,
        "hasBook": False,
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
        chain.append(
            {
                "strike": k,
                "callPrice": round(call_p, 2),
                "putPrice": round(put_p, 2),
                "callDelta": round(call_g.delta, 4),
                "putDelta": round(put_g.delta, 4),
                "gamma": round(call_g.gamma, 8),
                "vega": round(call_g.vega, 2),
                "iv": round(float(iv), 4),
                "oi": int(np.random.default_rng(42 + k).integers(1000, 50000)),
            }
        )
    return {"spot": SPOT, "expiry": expiry, "chain": chain}


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    datasets = {
        "volSurface": _vol_surface(),
        "greeksComparison": _greeks_comparison(),
        # No book in demo mode, so no book-shaped panels. These used to
        # emit rng.uniform() utilisation and a revalued invented contract,
        # which rendered as if they described the user's own account.
        "scenarioGrid": None,
        "pnlExplain": _pnl_explain(),
        "higherOrderGreeks": _higher_order_greeks(),
        "optionChain": _option_chain(),
        "essviCalibration": _essvi_calibration(),
        "backtestEquity": _backtest_equity(),
        "vrpSignal": _vrp_signal(),
        "riskDashboard": _empty_risk_dashboard(),
    }
    out = OUT_DIR / "demo.json"
    out.write_text(json.dumps(datasets, indent=2) + "\n")
    print(f"Wrote {out} ({out.stat().st_size:,} bytes)")


if __name__ == "__main__":
    main()

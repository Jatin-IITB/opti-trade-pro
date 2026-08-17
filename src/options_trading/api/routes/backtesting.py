# src/options_trading/api/routes/backtesting.py
"""
Backtesting API Routes - Integrates with your existing data pipeline
"""

import json
import logging
import os
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pandas as pd
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from pydantic import BaseModel

from ...market_data.manager import MarketDataManager
from ..dependencies import get_market_data_manager

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/backtesting", tags=["backtesting"])


class BacktestRequest(BaseModel):
    strategy_name: str
    symbol: str
    expiry_date: str
    strikes_from_atm: int = 2
    days_back: int = 7
    option_interval: str = "3minute"
    spot_interval: str = "3"
    exchange: str = "NSE"


class StrategyConfig(BaseModel):
    strategy_type: str  # "delta_neutral", "gamma_scalping", "iron_condor", etc.
    entry_conditions: dict[str, Any]
    exit_conditions: dict[str, Any]
    risk_limits: dict[str, float]
    position_sizing: dict[str, Any]


class BacktestResult(BaseModel):
    backtest_id: str
    strategy_name: str
    symbol: str
    expiry_date: str
    total_pnl: Decimal
    sharpe_ratio: Decimal | None = None
    max_drawdown: Decimal
    win_rate: float
    total_trades: int
    avg_trade_duration: float  # hours
    performance_metrics: dict[str, Any]
    risk_metrics: dict[str, Any]


@router.get("/strategies")
async def list_available_strategies():
    strategies = [
        {
            "name": "delta_neutral_straddle",
            "description": "Delta neutral straddle strategy",
            "parameters": {"delta_threshold": 0.05, "gamma_target": 0.01, "time_to_exit": 2},
        },
        {
            "name": "gamma_scalping",
            "description": "Gamma scalping with dynamic hedging",
            "parameters": {
                "gamma_threshold": 0.02,
                "hedge_frequency": 30,
                "volatility_target": 0.15,
            },
        },
        {
            "name": "volatility_arbitrage",
            "description": "Implied vs realized volatility arbitrage",
            "parameters": {"iv_threshold": 0.20, "rv_window": 20, "min_vol_diff": 0.05},
        },
        {
            "name": "iron_condor",
            "description": "Iron condor range-bound strategy",
            "parameters": {"wing_width": 100, "probability_target": 0.70, "dte_entry": 30},
        },
    ]
    return {
        "strategies": strategies,
        "total": len(strategies),
        "timestamp": datetime.now().isoformat(),
    }


@router.get("/expiries/{symbol}")
async def get_available_expiries(
    symbol: str,
    exchange: str = "NSE",
    limit: int = Query(default=10, ge=1, le=50),
    market_manager: MarketDataManager = Depends(get_market_data_manager),
):
    """Get available past expiry dates for backtesting using your system"""
    try:
        from ..tools.expiries import get_expiries

        underlying_key = market_manager.get_underlying_key(symbol, exchange)
        expiries = get_expiries(underlying_key, market_manager.access_token)
        past_expiries = [exp for exp in expiries if exp < date.today().strftime("%Y-%m-%d")]
        return {
            "symbol": symbol,
            "exchange": exchange,
            "expiries": past_expiries[-limit:],
            "total": len(past_expiries),
            "timestamp": datetime.now().isoformat(),
        }
    except Exception as e:
        logger.error(f"Failed to get expiries for {symbol}: {e}")
        raise HTTPException(status_code=500, detail="Failed to fetch expiries")


@router.post("/run")
async def run_backtest(
    background_tasks: BackgroundTasks,
    request: BacktestRequest,
    strategy_config: StrategyConfig | None = None,
    market_manager: MarketDataManager = Depends(get_market_data_manager),
):
    """Run backtest using your existing data processing pipeline"""
    backtest_id = (
        f"bt_{request.strategy_name}_{request.symbol}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    )

    def execute_backtest():
        try:
            logger.info(f"Starting backtest {backtest_id}")

            # STEP 1: Process historical data using your REAL pipeline
            logger.info("Processing historical data...")
            saved_files, output_dir = market_manager.save_features_for_expiry(
                symbol=request.symbol,
                exchange=request.exchange,
                expiry=request.expiry_date,
                option_interval=request.option_interval,
                spot_interval=request.spot_interval,
                days_back=request.days_back,
                strikes=request.strikes_from_atm,
            )

            # STEP 2: Load processed data for backtesting
            logger.info("Loading processed data for backtesting...")
            backtest_data = []
            for file_path in saved_files.values():
                if os.path.exists(file_path):
                    df = pd.read_parquet(file_path)
                    df["file_source"] = file_path
                    backtest_data.append(df)

            if not backtest_data:
                raise ValueError("No data available for backtesting")

            # STEP 3: Execute strategy logic
            logger.info(f"Executing {request.strategy_name} strategy...")
            if request.strategy_name == "delta_neutral_straddle":
                results = execute_delta_neutral_strategy(backtest_data, strategy_config)
            elif request.strategy_name == "gamma_scalping":
                results = execute_gamma_scalping_strategy(backtest_data, strategy_config)
            elif request.strategy_name == "volatility_arbitrage":
                results = execute_vol_arbitrage_strategy(backtest_data, strategy_config)
            else:
                results = execute_generic_strategy(
                    backtest_data, request.strategy_name, strategy_config
                )

            # STEP 4: Calculate performance metrics
            performance_metrics = calculate_performance_metrics(results)

            # STEP 5: Save results
            results_file = Path(output_dir) / f"{backtest_id}_results.json"
            with open(results_file, "w") as f:
                json.dump(
                    {
                        "backtest_id": backtest_id,
                        "request": request.dict(),
                        "strategy_config": strategy_config.dict() if strategy_config else None,
                        "results": results,
                        "performance_metrics": performance_metrics,
                        "data_files": list(saved_files.values()),
                        "timestamp": datetime.now().isoformat(),
                    },
                    f,
                    default=str,
                    indent=2,
                )

            logger.info(f"Backtest {backtest_id} completed successfully")
            return {
                "backtest_id": backtest_id,
                "status": "completed",
                "results_file": str(results_file),
                "performance": performance_metrics,
            }
        except Exception as e:
            logger.error(f"Backtest {backtest_id} failed: {e}", exc_info=True)
            return {"backtest_id": backtest_id, "status": "failed", "error": str(e)}

    background_tasks.add_task(execute_backtest)
    return {
        "message": "Backtest initiated successfully",
        "backtest_id": backtest_id,
        "strategy": request.strategy_name,
        "symbol": request.symbol,
        "expiry_date": request.expiry_date,
        "estimated_duration": "2-5 minutes",
        "status_endpoint": f"/backtesting/status/{backtest_id}",
        "timestamp": datetime.now().isoformat(),
    }


@router.get("/status/{backtest_id}")
async def get_backtest_status(backtest_id: str):
    """Get backtest execution status"""
    try:
        base_dir = Path("FINAL")
        results_files = list(base_dir.rglob(f"{backtest_id}_results.json"))
        if results_files:
            with open(results_files[0]) as f:
                results = json.load(f)
            return {
                "backtest_id": backtest_id,
                "status": "completed",
                "results": results,
                "timestamp": datetime.now().isoformat(),
            }
        else:
            return {
                "backtest_id": backtest_id,
                "status": "running",
                "message": "Backtest in progress. Check back in a few minutes.",
                "timestamp": datetime.now().isoformat(),
            }
    except Exception as e:
        logger.error(f"Failed to get backtest status: {e}")
        raise HTTPException(status_code=500, detail="Status check failed")


@router.get("/results")
async def list_backtest_results(
    limit: int = Query(default=20, ge=1, le=100),
    symbol: str | None = None,
    strategy: str | None = None,
):
    """List completed backtests with filtering"""
    try:
        base_dir = Path("FINAL")
        if not base_dir.exists():
            return {"results": [], "total": 0, "message": "No backtests found"}

        results = []
        pattern = "*_results.json"
        for results_file in base_dir.rglob(pattern):
            try:
                with open(results_file) as f:
                    data = json.load(f)
                if symbol and data.get("request", {}).get("symbol") != symbol:
                    continue
                if strategy and data.get("request", {}).get("strategy_name") != strategy:
                    continue
                summary = {
                    "backtest_id": data.get("backtest_id"),
                    "strategy_name": data.get("request", {}).get("strategy_name"),
                    "symbol": data.get("request", {}).get("symbol"),
                    "expiry_date": data.get("request", {}).get("expiry_date"),
                    "total_pnl": data.get("performance_metrics", {}).get("total_pnl", 0),
                    "sharpe_ratio": data.get("performance_metrics", {}).get("sharpe_ratio"),
                    "max_drawdown": data.get("performance_metrics", {}).get("max_drawdown", 0),
                    "win_rate": data.get("performance_metrics", {}).get("win_rate", 0),
                    "completed_at": data.get("timestamp"),
                    "file_path": str(results_file),
                }
                results.append(summary)
            except Exception as e:
                logger.warning(f"Could not read results file {results_file}: {e}")
                continue

        results.sort(key=lambda x: x.get("completed_at", ""), reverse=True)
        return {
            "results": results[:limit],
            "total": len(results),
            "filters": {"symbol": symbol, "strategy": strategy},
            "timestamp": datetime.now().isoformat(),
        }
    except Exception as e:
        logger.error(f"Failed to list backtest results: {e}")
        raise HTTPException(status_code=500, detail="Failed to fetch results")


@router.get("/results/{backtest_id}/detailed")
async def get_detailed_results(backtest_id: str):
    """Get detailed backtest results including trade-by-trade analysis"""
    try:
        base_dir = Path("FINAL")
        results_files = list(base_dir.rglob(f"{backtest_id}_results.json"))
        if not results_files:
            raise HTTPException(status_code=404, detail="Backtest results not found")
        with open(results_files[0]) as f:
            detailed_results = json.load(f)
        return detailed_results
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get detailed results: {e}")
        raise HTTPException(status_code=500, detail="Failed to fetch detailed results")


@router.delete("/results/{backtest_id}")
async def delete_backtest_results(backtest_id: str):
    """Delete backtest results"""
    try:
        base_dir = Path("FINAL")
        results_files = list(base_dir.rglob(f"{backtest_id}_results.json"))
        if not results_files:
            raise HTTPException(status_code=404, detail="Backtest results not found")
        for file_path in results_files:
            os.remove(file_path)
        return {
            "message": f"Backtest {backtest_id} results deleted successfully",
            "timestamp": datetime.now().isoformat(),
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to delete backtest results: {e}")
        raise HTTPException(status_code=500, detail="Failed to delete results")


# Strategy execution helpers (as provided, kept simple)
def execute_delta_neutral_strategy(data_list, config):
    trades = []
    for df in data_list:
        if df.empty:
            continue
        for _, row in df.iterrows():
            if abs(row.get("delta", 0)) < 0.05:
                trades.append(
                    {
                        "timestamp": row["timestamp"],
                        "action": "enter_straddle",
                        "option_price": row.get("close_option"),
                        "underlying_price": row.get("close_spot"),
                        "delta": row.get("delta", 0),
                        "gamma": row.get("gamma", 0),
                        "iv": row.get("iv", 0),
                    }
                )
    return trades


def execute_gamma_scalping_strategy(data_list, config):
    trades = []
    for df in data_list:
        if df.empty:
            continue
        for _, row in df.iterrows():
            gamma = row.get("gamma", 0)
            if gamma > 0.01:
                trades.append(
                    {
                        "timestamp": row["timestamp"],
                        "action": "gamma_scalp",
                        "gamma": gamma,
                        "delta": row.get("delta", 0),
                        "underlying_move": row.get("close_spot", 0) - row.get("open_spot", 0),
                    }
                )
    return trades


def execute_vol_arbitrage_strategy(data_list, config):
    trades = []
    for df in data_list:
        if df.empty:
            continue
        for _, row in df.iterrows():
            iv = row.get("iv", 0)
            rv = row.get("rv_gk", 0)
            if iv and rv and abs(iv - rv) > 0.05:
                trades.append(
                    {
                        "timestamp": row["timestamp"],
                        "action": "vol_arbitrage",
                        "implied_vol": iv,
                        "realized_vol": rv,
                        "vol_spread": iv - rv,
                    }
                )
    return trades


def execute_generic_strategy(data_list, strategy_name, config):
    trades = []
    for df in data_list:
        if df.empty:
            continue
        for idx in range(0, len(df), 10):
            row = df.iloc[idx]
            trades.append(
                {
                    "timestamp": row["timestamp"],
                    "action": f"{strategy_name}_signal",
                    "option_price": row.get("close_option"),
                    "underlying_price": row.get("close_spot"),
                }
            )
    return trades


def calculate_performance_metrics(trades):
    if not trades:
        return {
            "total_pnl": 0,
            "sharpe_ratio": None,
            "max_drawdown": 0,
            "win_rate": 0,
            "total_trades": 0,
        }
    # Placeholder metrics (replace with actual calculations)
    import random

    total_trades = len(trades)
    winning_trades = int(total_trades * random.uniform(0.4, 0.7))
    return {
        "total_pnl": round(random.uniform(-5000, 15000), 2),
        "sharpe_ratio": round(random.uniform(0.5, 2.0), 3),
        "max_drawdown": round(random.uniform(1000, 8000), 2),
        "win_rate": round(winning_trades / total_trades * 100, 2) if total_trades > 0 else 0,
        "total_trades": total_trades,
        "winning_trades": winning_trades,
        "losing_trades": total_trades - winning_trades,
        "avg_trade_pnl": round(random.uniform(-200, 400), 2),
        "best_trade": round(random.uniform(500, 2000), 2),
        "worst_trade": round(random.uniform(-1500, -200), 2),
    }

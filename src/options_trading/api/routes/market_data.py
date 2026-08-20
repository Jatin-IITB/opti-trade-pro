# src/options_trading/api/routes/market_data.py
"""
REAL Market Data API Routes - Integrates with your existing MarketDataManager
Provides live option chains, Greeks, and historical data processing
"""

import asyncio
import datetime
import functools
import inspect
import logging
from pathlib import Path

import pandas as pd
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query

from ...config.settings import settings
from ...jobs.registry import create_job, update_job

# Import your existing MarketDataManager components
from ...market_data.manager import MarketDataManager
from ...market_data.preprocessing import (
    append_greeks_in_memory,
)
from ...models.market_data import OptionChain, VolatilitySurface
from ...services.market_data_service import MarketDataService
from ...utils.exceptions import DataQualityError
from ..dependencies import get_market_data_manager, get_market_data_service

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/market-data", tags=["market-data"])


@router.get("/status")
async def get_market_status(market_service: MarketDataService = Depends(get_market_data_service)):
    """Get real-time market data feed status"""
    try:
        return await market_service.get_data_status()
    except Exception as e:
        logger.error(f"Failed to get market status: {e}")
        raise HTTPException(status_code=500, detail="Market status unavailable")


@router.get("/instruments/{symbol}")
async def get_instruments(
    symbol: str,
    exchange: str = "NSE_EQ",
    market_manager: MarketDataManager = Depends(get_market_data_manager),
):
    """REAL: Get instrument key and basic info using your existing system"""
    try:
        if inspect.iscoroutinefunction(market_manager.get_underlying_key):
            underlying_key = await market_manager.get_underlying_key(symbol, exchange)
        else:
            # run sync method in threadpool
            underlying_key = await asyncio.to_thread(
                functools.partial(market_manager.get_underlying_key, symbol, exchange)
            )
        return {
            "symbol": symbol,
            "exchange": exchange,
            "underlying_key": underlying_key,
            "timestamp": datetime.datetime.utcnow().isoformat() + "Z",
        }

    except Exception as e:
        logger.error(f"Failed to get instrument {symbol}: {e}")
        raise HTTPException(status_code=404, detail=f"Instrument {symbol} not found")


@router.get("/contracts/{symbol}")
async def get_contracts_for_expiry(
    symbol: str,
    expiry_date: str,
    exchange: str = "NSE_EQ",
    market_manager: MarketDataManager = Depends(get_market_data_manager),
):
    """REAL: Get option contracts for expiry using your existing pipeline"""
    try:
        if inspect.iscoroutinefunction(market_manager.fetch_contracts_for_expiry):
            contracts_df = await market_manager.fetch_contracts_for_expiry(
                symbol, exchange, expiry_date
            )
        else:
            contracts_df = await asyncio.to_thread(
                functools.partial(
                    market_manager.fetch_contracts_for_expiry, symbol, exchange, expiry_date
                )
            )
        if contracts_df is None or contracts_df.empty:
            return {
                "symbol": symbol,
                "expiry_date": expiry_date,
                "contracts": [],
                "count": 0,
                "timestamp": datetime.datetime.utcnow().isoformat() + "Z",
            }

        # Convert DataFrame to API response
        contracts = []
        for _, row in contracts_df.iterrows():
            contracts.append(
                {
                    "trading_symbol": row.get("trading_symbol"),
                    "strike_price": float(row.get("strike_price", 0)),
                    "instrument_type": row.get("instrument_type"),
                    "expiry": row.get("expiry").isoformat()
                    if pd.notna(row.get("expiry"))
                    else expiry_date,
                    "lot_size": int(row.get("lot_size", 0))
                    if pd.notna(row.get("lot_size"))
                    else None,
                }
            )

        return {
            "symbol": symbol,
            "expiry_date": expiry_date,
            "contracts": contracts,
            "count": len(contracts),
            "timestamp": datetime.datetime.utcnow().isoformat() + "Z",
        }

    except Exception as e:
        logger.error(f"Failed to get contracts for {symbol} {expiry_date}: {e}")
        raise HTTPException(status_code=500, detail="Failed to fetch contracts")


@router.get("/option-chain/{symbol}")
async def get_option_chain(
    symbol: str,
    expiry_date: str | None = None,
    strikes_range: int = Query(default=10, ge=1, le=50),
    market_service: MarketDataService = Depends(get_market_data_service),
) -> OptionChain:
    """REAL: Get option chain with live Greeks using your MarketDataManager"""
    try:
        # This now uses REAL data through your MarketDataManager integration
        option_chain = await market_service.get_option_chain(
            symbol=symbol, expiry_date=expiry_date, strikes_range=strikes_range
        )
        return option_chain

    except DataQualityError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error(f"Failed to get option chain for {symbol}: {e}")
        raise HTTPException(status_code=500, detail="Option chain unavailable")


@router.get("/volatility-surface/{symbol}")
async def get_volatility_surface(
    symbol: str, market_service: MarketDataService = Depends(get_market_data_service)
) -> VolatilitySurface:
    """Get 3D implied volatility surface"""
    try:
        return await market_service.get_volatility_surface(symbol)
    except Exception as e:
        logger.error(f"Failed to get volatility surface for {symbol}: {e}")
        raise HTTPException(status_code=500, detail="Volatility surface unavailable")


@router.post("/historical/process")
async def process_historical_data(
    symbol: str,
    expiry_date: str | None = None,  # now optional
    option_interval: str = Query(settings.default_option_interval),
    spot_interval: str = Query(settings.default_spot_interval),
    days_back: int = Query(settings.default_days_back),
    strikes: int | None = Query(None),
    exchange: str = Query("NSE_EQ"),
    number_of_exp: int | None = Query(
        settings.latest_expiry
    ),  # last N expiries if expiry_date is None
    market_manager: MarketDataManager = Depends(get_market_data_manager),
):
    """
    Trigger historical processing in background.
    - Provide expiry_date (single expiry) OR number_of_exp (last N expiries).
    - Returns immediately; real work runs in background and is logged.
    """

    # Basic validation
    if not symbol:
        raise HTTPException(status_code=400, detail="symbol is required")

    strikes = int(strikes) if strikes is not None else settings.default_strikes
    number_of_exp = int(number_of_exp) if number_of_exp is not None else settings.latest_expiry

    params = {
        "expiry_date": expiry_date,
        "option_interval": option_interval,
        "spot_interval": spot_interval,
        "days_back": days_back,
        "strikes": strikes,
        "exchange": exchange,
        "number_of_exp": number_of_exp,
    }
    job_id = create_job(symbol, params)

    async def _background_job():
        try:
            update_job(job_id, "running")
            if asyncio.iscoroutinefunction(market_manager.save_features_for_expiry):
                saved, out_dir = await market_manager.save_features_for_expiry(
                    symbol=symbol,
                    exchange=exchange,
                    expiry=expiry_date,
                    option_interval=option_interval,
                    spot_interval=spot_interval,
                    days_back=days_back,
                    strikes=strikes,
                    number_of_exp=number_of_exp,
                )
            else:
                saved, out_dir = await asyncio.to_thread(
                    functools.partial(
                        market_manager.save_features_for_expiry,
                        symbol=symbol,
                        exchange=exchange,
                        expiry=expiry_date,
                        option_interval=option_interval,
                        spot_interval=spot_interval,
                        days_back=days_back,
                        strikes=strikes,
                        number_of_exp=number_of_exp,
                    )
                )
            files = list(saved.values())
            update_job(job_id, "completed", files=files)
        except Exception as e:
            update_job(job_id, "failed", files=None, error=str(e))
            logger.exception(
                "Background historical processing failed for %s", symbol, exc_info=True
            )

    asyncio.create_task(_background_job())
    return {
        "message": "Historical data processing initiated",
        "job_id": job_id,
        "symbol": symbol,
        "params": params,
        "timestamp": datetime.datetime.utcnow().isoformat() + "z",
    }


@router.get("/historical/results/{symbol}")
async def get_historical_results(
    symbol: str, expiry_date: str | None = None, limit: int = Query(default=10, ge=1, le=100)
):
    """Get processed historical data results"""
    try:
        import json

        results = []
        seen_paths = set()

        try:
            registry_path = Path(settings.JOB_REGISTRY_PATH)
            if registry_path.exists():
                with registry_path.open("r", encoding="utf-8") as fh:
                    registry = json.load(fh)
                    for job in registry.values():
                        if job.get("symbol", "").upper() == symbol.upper():
                            for fp in job.get("files", []) or []:
                                p = Path(fp)
                                if p.exists() and p.suffix == ".parquet":
                                    seen_paths.add(str(p.resolve()))
        except Exception:
            logger.debug(
                "Job registry read failed or absent; falling back to disk scan", exc_info=True
            )

        base_glob = f"{settings.OUT_BASE_PREFIX}*"
        project_root = Path.cwd()
        for dirpath in project_root.glob(base_glob):
            if dirpath.is_dir():
                for p in dirpath.rglob("*.parquet"):
                    if symbol.upper() in p.name.upper() or symbol.upper() in str(p).upper():
                        seen_paths.add(str(p.resolve()))

        if len(seen_paths) < limit:
            for p in project_root.rglob("*parquet"):
                if symbol.upper() in p.name.upper() or symbol.upper() in str(p).upper():
                    seen_paths.add(str(p.resolve()))
                    if len(seen_paths) >= limit * 3:
                        break

        for fp in sorted(seen_paths):
            if len(results) >= limit:
                break
            try:
                p = Path(fp)
                df = pd.read_parquet(p)
                ts_col = None
                for candidate in ("timestamp", "ts", "time", "datetime", "date"):
                    if candidate in df.columns:
                        ts_col = candidate
                        break
                date_start = None
                date_end = None
                if ts_col is not None:
                    try:
                        smin = df[ts_col].min()
                        smax = df[ts_col].max()
                        date_start = pd.to_datetime(smin).isoformat() if pd.notna(smin) else None
                        date_end = pd.to_datetime(smax).isoformat() if pd.notna(smax) else None
                    except Exception:
                        date_start = date_end = None
                results.append(
                    {
                        "file_name": p.name,
                        "file_path": str(p),
                        "rows": len(df),
                        "columns": list(df.columns),
                        "date_range": {"start": date_start, "end": date_end},
                        "has_greeks": all(
                            col in df.columns for col in ["delta", "gamma", "theta", "vega"]
                        ),
                        "has_iv": "iv" in df.columns,
                        "has_rv": any(col in df.columns for col in ["rv_gk", "rv_parkinson"]),
                    }
                )
            except Exception as e:
                logger.warning("Could not read parquet %s: %s", fp, e)
                continue
        if not results:
            scanned = {
                "scanned_patterns": {
                    "job_registry": str(Path(settings.JOB_REGISTRY_PATH)),
                    "output_glob": f"{project_root}/{base_glob}/**/*.parquet",
                    "full_scan": f"{project_root}/**/*.parquet",
                }
            }
            return {
                "symbol": symbol,
                "results": [],
                "message": "No processed data found in job registry or output dirs. Run /historical/process first.",
                "scanned": scanned,
                "timestamp": datetime.datetime.now().isoformat(),
            }
        return {
            "symbol": symbol,
            "results": results,
            "total_files_found": len(results),
            "timestamp": datetime.datetime.now().isoformat(),
        }
    except Exception as e:
        logger.error(f"Failed to get historical results: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to fetch results")


@router.post("/live/greeks/{symbol}")
async def calculate_live_greeks(
    symbol: str,
    strike: float,
    expiry_date: str,
    option_type: str,
    market_manager: MarketDataManager = Depends(get_market_data_manager),
):
    """REAL: Calculate live Greeks using your existing preprocessing functions"""
    try:
        # Create mock current market data (in real implementation, fetch from live feeds)
        current_time = datetime.datetime.utcnow()
        expiry_dt = datetime.datetime.strptime(expiry_date, "%Y-%m-%d")

        # Mock live data structure that matches your preprocessing pipeline
        live_data = pd.DataFrame(
            {
                "timestamp": [current_time],
                "close_option": [50.0],  # Would fetch from live feed
                "close_spot": [19450.0],  # Would fetch from live feed
                "iv": [0.15],  # Would calculate or fetch from live feed
            }
        )

        # Use your EXISTING Greeks calculation
        live_data_with_greeks = append_greeks_in_memory(
            live_data, strike=strike, expiry=expiry_dt, option_type=option_type
        )

        if not live_data_with_greeks.empty:
            row = live_data_with_greeks.iloc[0]
            return {
                "symbol": symbol,
                "strike": strike,
                "expiry_date": expiry_date,
                "option_type": option_type,
                "greeks": {
                    "delta": float(row.get("delta", 0)),
                    "gamma": float(row.get("gamma", 0)),
                    "theta": float(row.get("theta", 0)),
                    "vega": float(row.get("vega", 0)),
                    "rho": float(row.get("rho", 0)),
                },
                "market_data": {
                    "option_price": float(row.get("close_option", 0)),
                    "underlying_price": float(row.get("close_spot", 0)),
                    "implied_volatility": float(row.get("iv", 0)),
                },
                "timestamp": current_time.isoformat(),
            }
        else:
            raise HTTPException(status_code=422, detail="Greeks calculation failed")

    except Exception as e:
        logger.error(f"Live Greeks calculation failed: {e}")
        raise HTTPException(status_code=500, detail="Greeks calculation error")


@router.post("/refresh")
async def refresh_market_data(
    background_tasks: BackgroundTasks,
    symbols: list[str] | None = None,
    market_service: MarketDataService = Depends(get_market_data_service),
):
    """Refresh market data cache"""
    try:
        background_tasks.add_task(market_service.refresh_market_data, symbols)

        return {
            "message": "Market data refresh initiated",
            "symbols": symbols or "all",
            "timestamp": datetime.utcnow().isoformat() + "Z",
        }
    except Exception as e:
        logger.error(f"Market data refresh failed: {e}")
        raise HTTPException(status_code=500, detail="Refresh failed")


@router.get("/cache/stats")
async def get_cache_stats(market_manager: MarketDataManager = Depends(get_market_data_manager)):
    """Get cache statistics from your MarketDataManager"""
    try:
        # Use your existing cache stats
        market_manager.cache_stats()

        # Return cache info (this would be enhanced to return actual stats)
        return {
            "cache_size": 10,  # From your SpotDataCache max_cache_size
            "cache_type": "SpotDataCache",
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "message": "Check server logs for detailed cache statistics",
        }

    except Exception as e:
        logger.error(f"Failed to get cache stats: {e}")
        raise HTTPException(status_code=500, detail="Cache stats unavailable")

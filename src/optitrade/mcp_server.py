"""MCP stdio server exposing the OptiTrade quant core as agent tools.

Thin adapter only: map JSON-friendly tool arguments into ``optitrade`` engine
calls and engine results back to JSON — no math lives here. Two properties
are load-bearing:

- Every tool call appends an ``event_type="tool_call"`` journal event carrying
  the tool name, arguments and a result summary. Those events are the citation
  targets for :mod:`optitrade.audit`: an agent claim is only trusted insofar
  as it cites journaled engine output (the Prism toolshed/auditor pattern).
- Agents observe, explain and propose — they never execute. No tool here
  mutates a book or routes an order; ``review_order`` returns a verdict.

The ``mcp`` dependency is an optional extra, so this module must import
cleanly without it; the import happens inside :func:`create_server`.
"""

from __future__ import annotations

import argparse
import time
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

from optitrade.backtest.market_replay import SyntheticVRPMarket
from optitrade.backtest.walk_forward import BacktestConfig, run_walk_forward
from optitrade.core.types import Greeks, OptionType, Order, Portfolio
from optitrade.greeks.scenario import BookPosition, ScenarioGrid, run_scenario_grid
from optitrade.hedging.band import BandParams
from optitrade.journal.event_log import EventLog
from optitrade.journal.events import Event
from optitrade.pricing.black_scholes import bs_greeks_at, bs_price
from optitrade.risk.checks import RiskContext
from optitrade.risk.engine import RiskEngine
from optitrade.risk.limits import RiskLimits
from optitrade.strategy.vrp import VRPConfig, VRPStrategy

_DEFAULT_JOURNAL_DIR = Path("runtime_data")
_MCP_INSTALL_HINT = (
    "the OptiTrade MCP server needs the optional 'mcp' package; "
    'install it with: pip install "optitrade-pro[mcp]"'
)


def _greeks_dict(greeks: Greeks) -> dict[str, float]:
    return {
        "delta": greeks.delta,
        "gamma": greeks.gamma,
        "vega": greeks.vega,
        "theta": greeks.theta,
        "rho": greeks.rho,
        "vanna": greeks.vanna,
        "volga": greeks.volga,
    }


def _event_dict(event: Event) -> dict[str, Any]:
    return {
        "sequence": event.sequence,
        "event_type": event.event_type,
        "timestamp": event.timestamp,
        "correlation_id": event.correlation_id,
        "data": event.data,
    }


def _book(positions: list[dict[str, Any]]) -> list[BookPosition]:
    return [
        BookPosition(
            strike=float(p["strike"]),
            expiry=float(p["expiry"]),
            option_type=OptionType(str(p["option_type"])),
            quantity=float(p["quantity"]),
            vol=float(p["vol"]),
        )
        for p in positions
    ]


def _cell(cell: tuple[float, float, float, float]) -> dict[str, float]:
    pnl, spot_shift, vol_shift, time_shift = cell
    return {"pnl": pnl, "spot_shift": spot_shift, "vol_shift": vol_shift, "time_shift": time_shift}


def build_tools(journal: EventLog) -> tuple[Callable[..., Any], ...]:
    """The five plain-python tool functions, closed over ``journal``."""

    def _journal_call(
        tool: str, args: dict[str, Any], result: Any, correlation_id: str | None = None
    ) -> None:
        journal.append(
            "tool_call",
            {"tool": tool, "args": args, "result": result},
            correlation_id=correlation_id,
        )

    def price_option(
        spot: float,
        strike: float,
        expiry_years: float,
        rate: float,
        vol: float,
        option_type: str,
        dividend_yield: float = 0.0,
    ) -> dict[str, Any]:
        """Price one European option (Black-Scholes-Merton) with analytic Greeks."""
        kind = OptionType(option_type)
        args = {
            "spot": spot,
            "strike": strike,
            "expiry_years": expiry_years,
            "rate": rate,
            "vol": vol,
            "option_type": kind.value,
            "dividend_yield": dividend_yield,
        }
        result = {
            "price": float(bs_price(spot, strike, expiry_years, rate, vol, kind, dividend_yield)),
            "greeks": _greeks_dict(
                bs_greeks_at(spot, strike, expiry_years, rate, vol, kind, dividend_yield)
            ),
        }
        _journal_call("price_option", args, result)
        return result

    def book_greeks(
        positions: list[dict[str, Any]],
        spot: float,
        rate: float,
        dividend_yield: float = 0.0,
    ) -> dict[str, Any]:
        """Aggregate and per-position Greeks for an option book (per-unit conventions)."""
        total = Greeks()
        per_position: list[dict[str, float]] = []
        for p in _book(positions):
            g = bs_greeks_at(
                spot, p.strike, p.expiry, rate, p.vol, p.option_type, dividend_yield
            ).scaled(p.quantity)
            per_position.append(_greeks_dict(g))
            total = total + g
        result = {"portfolio": _greeks_dict(total), "positions": per_position}
        args = {
            "positions": positions,
            "spot": spot,
            "rate": rate,
            "dividend_yield": dividend_yield,
        }
        _journal_call("book_greeks", args, result)
        return result

    def run_scenarios(
        positions: list[dict[str, Any]],
        spot: float,
        rate: float,
        n_spot: int = 11,
        spot_width: float = 0.1,
        n_vol: int = 7,
        vol_width: float = 0.05,
        n_time: int = 7,
        max_days: int = 30,
    ) -> dict[str, Any]:
        """Full-revaluation spot x vol x time PnL grid; returns the extreme cells."""
        grid = ScenarioGrid.regular(
            n_spot=n_spot,
            spot_width=spot_width,
            n_vol=n_vol,
            vol_width=vol_width,
            n_time=n_time,
            max_days=float(max_days),
        )
        started = time.perf_counter()
        res = run_scenario_grid(_book(positions), spot, rate, grid)
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        result = {
            "n_cells": grid.size,
            "worst": _cell(res.worst),
            "best": _cell(res.best),
            "elapsed_ms": elapsed_ms,
        }
        args = {
            "positions": positions,
            "spot": spot,
            "rate": rate,
            "n_spot": n_spot,
            "spot_width": spot_width,
            "n_vol": n_vol,
            "vol_width": vol_width,
            "n_time": n_time,
            "max_days": max_days,
        }
        _journal_call("run_scenarios", args, result)
        return result

    def review_order(
        order: dict[str, Any],
        limits: dict[str, Any],
        context: dict[str, Any],
    ) -> dict[str, Any]:
        """Fail-closed pre-trade risk review: a verdict and reasons, never an execution."""
        risk_limits = RiskLimits(**{k: float(v) for k, v in limits.items()})
        ctx = RiskContext(
            portfolio=Portfolio(
                equity=float(context.get("equity", 0.0)),
                high_water_mark=float(context.get("high_water_mark", 0.0)),
                margin_available=float(context.get("margin_available", 0.0)),
            ),
            portfolio_greeks=Greeks(**context.get("portfolio_greeks", {})),
            order_greeks=Greeks(**context.get("order_greeks", {})),
            margin_required=float(context.get("margin_required", 0.0)),
            spot=float(context.get("spot", 0.0)),
        )
        proposed = Order(
            symbol=str(order["symbol"]),
            quantity=float(order["quantity"]),
            price=float(order["price"]),
        )
        decision = RiskEngine(risk_limits).review(proposed, ctx)
        result = {
            "verdict": decision.verdict.value,
            "adjusted_order": None
            if decision.adjusted_order is None
            else {
                "symbol": decision.adjusted_order.symbol,
                "quantity": decision.adjusted_order.quantity,
                "price": decision.adjusted_order.price,
            },
            "checks": [
                {
                    "check": r.check_name,
                    "verdict": r.verdict.value,
                    "reason": r.reason,
                    "allowed_quantity": r.allowed_quantity,
                }
                for r in decision.results
            ],
            "correlation_id": decision.correlation_id,
        }
        args = {"order": order, "limits": limits, "context": context}
        _journal_call("review_order", args, result, correlation_id=decision.correlation_id)
        return result

    def journal_tail(n: int = 20) -> list[dict[str, Any]]:
        """Last ``n`` journal events — the citation source for agent claims."""
        events = [_event_dict(e) for e in journal.replay()]
        tail = events[-n:] if n > 0 else []
        _journal_call("journal_tail", {"n": n}, {"returned": len(tail)})
        return tail

    def run_experiment(
        config: dict[str, Any],
        n_days: int = 40,
        spot: float = 100.0,
        rate: float = 0.05,
        realized_vol: float = 0.18,
        vrp: float = 0.06,
        seed: int = 42,
    ) -> dict[str, Any]:
        """Run a walk-forward backtest with a proposed VRP config (backtest-as-tool).

        The agent proposes parameter changes via ``config`` (a dict of
        VRPConfig fields); the tool runs walk-forward over a synthetic
        market and returns the out-of-sample Sharpe and deflated Sharpe.
        The experiment result is journaled so subsequent claims can cite it.
        """
        vrp_config = VRPConfig(**{k: v for k, v in config.items() if v is not None})
        market = SyntheticVRPMarket(
            n_days=max(n_days, 20),
            spot=spot,
            rate=rate,
            realized_vol=realized_vol,
            vrp=vrp,
            seed=seed,
        )
        days = list(market)
        bt_config = BacktestConfig(
            risk_limits=RiskLimits(
                max_abs_delta=500.0,
                max_abs_gamma=50.0,
                max_abs_vega=5_000.0,
                max_drawdown=0.15,
                max_concentration=1.0,
            ),
            band_params=BandParams(proportional_cost=5e-4, risk_aversion=1.0),
            lot_size=50,
        )

        def strategy_factory(cfg: VRPConfig) -> VRPStrategy:
            return VRPStrategy(cfg, lot_size=50)

        wf = run_walk_forward(
            strategy_factory=strategy_factory,
            param_grid=[vrp_config],
            replay=days,
            config=bt_config,
            n_folds=2,
            train_frac=0.6,
        )
        result = {
            "oos_sharpe": wf.oos_sharpe,
            "deflated_sharpe": wf.deflated_sharpe,
            "n_trials": wf.n_trials,
            "n_oos_days": int(wf.oos_daily_pnl.size),
            "config": config,
        }
        args = {
            "config": config,
            "n_days": n_days,
            "spot": spot,
            "rate": rate,
            "realized_vol": realized_vol,
            "vrp": vrp,
            "seed": seed,
        }
        _journal_call("run_experiment", args, result)
        return result

    return (price_option, book_greeks, run_scenarios, review_order, journal_tail, run_experiment)


def create_server(journal_dir: Path = _DEFAULT_JOURNAL_DIR, run_id: str | None = None) -> Any:
    """Build the FastMCP server "optitrade" over a fresh (or resumed) run journal.

    Raises a helpful :class:`ImportError` when the optional ``mcp`` extra is
    not installed.
    """
    # Optional extra: resolvable only with `pip install "optitrade-pro[mcp]"`.
    try:
        from mcp.server.fastmcp import FastMCP  # type: ignore[import-not-found]
    except ImportError:
        try:
            # mcp >= 2.0 renamed FastMCP to MCPServer (same tool/run API).
            from mcp.server.mcpserver import (  # type: ignore[import-not-found]
                MCPServer as FastMCP,
            )
        except ImportError as exc:
            raise ImportError(_MCP_INSTALL_HINT) from exc
    journal = EventLog(journal_dir, run_id or f"mcp-{uuid.uuid4().hex[:12]}")
    server = FastMCP("optitrade")
    for fn in build_tools(journal):
        server.tool()(fn)
    return server


def main() -> None:
    """Run the MCP server over stdio."""
    parser = argparse.ArgumentParser(
        prog="optitrade-mcp",
        description="OptiTrade MCP stdio server (agents observe/explain/propose, never execute)",
    )
    parser.add_argument(
        "--journal-dir",
        type=Path,
        default=_DEFAULT_JOURNAL_DIR,
        help="directory for the append-only run journal (JSONL)",
    )
    args = parser.parse_args()
    create_server(journal_dir=args.journal_dir).run()


__all__ = ["build_tools", "create_server", "main"]

if __name__ == "__main__":
    main()

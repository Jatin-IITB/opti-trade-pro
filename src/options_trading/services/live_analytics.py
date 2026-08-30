"""Dashboard payload builder: RawChain → frontend-ready dicts.

Takes a live (or synthetic) ``RawChain``, runs the quant engines, and reshapes
the output into the exact JSON structures each React component expects. Each
builder is wrapped in try/except so one tab's failure does not block the others.

This module is the single place where backend analytics shapes are mapped to
frontend component props — the translation table lives here and nowhere else.
"""

from __future__ import annotations

import logging
import math
from collections import defaultdict
from dataclasses import dataclass
from typing import Any

import numpy as np

from optitrade.core.types import Greeks, MarketSnapshot, OptionQuote, OptionType, Portfolio
from optitrade.data.capture import to_market_snapshot
from optitrade.data.models import RawChain
from optitrade.pricing import bs_greeks_at
from optitrade.pricing.implied_vol import implied_vol, strip_chain
from optitrade.vol.arbitrage import check_durrleman
from optitrade.vol.essvi import ESSVISurface
from optitrade.vol.surface import VolSurface

from .chain_converter import raw_chain_to_chain_in

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class LiveAnalyticsConfig:
    vol_model: str = "essvi"
    n_strike_grid: int = 25
    essvi_n_starts: int = 5
    essvi_seed: int = 0


@dataclass
class LiveDashboardPayload:
    vol_surface: dict[str, Any] | None = None
    option_chain: dict[str, Any] | None = None
    greeks_book: dict[str, Any] | None = None
    essvi_calibration: dict[str, Any] | None = None
    risk_dashboard: dict[str, Any] | None = None
    timestamp: float = 0.0
    underlying: str = ""
    spot: float = 0.0


class LiveAnalytics:
    """Stateless transformer: RawChain → LiveDashboardPayload."""

    def __init__(self, config: LiveAnalyticsConfig = LiveAnalyticsConfig()) -> None:
        self._config = config

    def build_from_raw_chain(
        self, chain: RawChain, portfolio: Portfolio | None = None
    ) -> LiveDashboardPayload:
        """Run all builders and return a payload with partial results on failure."""
        snapshot = to_market_snapshot(chain)
        chain_in = raw_chain_to_chain_in(chain)

        payload = LiveDashboardPayload(
            timestamp=chain.timestamp,
            underlying=chain.underlying,
            spot=chain.spot,
        )

        for name, builder in [
            ("vol_surface", lambda: self._build_vol_surface(snapshot)),
            ("option_chain", lambda: self._build_option_chain(chain, snapshot)),
            ("greeks_book", lambda: self._build_greeks_book(snapshot, portfolio)),
            ("essvi_calibration", lambda: self._build_essvi_calibration(snapshot)),
            ("risk_dashboard", lambda: self._build_risk_dashboard(snapshot, portfolio)),
        ]:
            try:
                setattr(payload, name, builder())
            except Exception:
                logger.exception("Live analytics builder %s failed", name)

        return payload

    def _build_vol_surface(self, snapshot: MarketSnapshot) -> dict[str, Any]:
        """Build ``{strikes, expiries, ivs, spot}`` matching VolSurface.tsx."""
        expiries = sorted({q.expiry for q in snapshot.quotes})
        all_strikes = [q.strike for q in snapshot.quotes]
        strikes = np.linspace(min(all_strikes), max(all_strikes), self._config.n_strike_grid)

        if self._config.vol_model == "essvi":
            surface = ESSVISurface.from_snapshot(
                snapshot,
                n_starts=self._config.essvi_n_starts,
                seed=self._config.essvi_seed,
            )
        else:
            surface = VolSurface.from_snapshot(snapshot)

        ivs: list[list[float]] = []
        for t in expiries:
            row = np.asarray(surface.vol(strikes, t)).tolist()
            ivs.append(row)

        return {
            "strikes": strikes.tolist(),
            "expiries": expiries,
            "ivs": ivs,
            "spot": snapshot.spot,
        }

    def _build_option_chain(self, chain: RawChain, snapshot: MarketSnapshot) -> dict[str, Any]:
        """Build ``{spot, expiry, chain: [ChainRow]}`` matching OptionChain.tsx.

        Pivots the flat quote list into per-strike rows with paired call/put
        data, computes per-strike Greeks and strips IV.
        """
        expiries = sorted({q.expiry for q in snapshot.quotes})
        if not expiries:
            return {"spot": snapshot.spot, "expiry": 0.0, "chain": []}
        nearest_expiry = expiries[0]

        by_strike: dict[float, dict[str, OptionQuote]] = defaultdict(dict)
        for q in snapshot.quotes:
            if q.expiry == nearest_expiry:
                by_strike[q.strike][q.option_type.value] = q

        rows: list[dict[str, Any]] = []
        for strike in sorted(by_strike.keys()):
            legs = by_strike[strike]
            call = legs.get("call")
            put = legs.get("put")

            call_price = call.mid if call else 0.0
            put_price = put.mid if put else 0.0

            call_iv = (
                self._safe_iv(
                    call_price,
                    snapshot.spot,
                    strike,
                    nearest_expiry,
                    snapshot.rate,
                    OptionType.CALL,
                    snapshot.dividend_yield,
                )
                if call
                else 0.0
            )

            call_greeks = (
                bs_greeks_at(
                    snapshot.spot,
                    strike,
                    nearest_expiry,
                    snapshot.rate,
                    call_iv if call_iv > 0 else 0.15,
                    OptionType.CALL,
                    snapshot.dividend_yield,
                )
                if call
                else None
            )

            put_greeks = (
                bs_greeks_at(
                    snapshot.spot,
                    strike,
                    nearest_expiry,
                    snapshot.rate,
                    call_iv if call_iv > 0 else 0.15,
                    OptionType.PUT,
                    snapshot.dividend_yield,
                )
                if put
                else None
            )

            total_oi = 0
            if call:
                for rq in chain.quotes:
                    if (
                        rq.strike == strike
                        and rq.expiry == nearest_expiry
                        and rq.option_type is OptionType.CALL
                    ):
                        total_oi += rq.open_interest
            if put:
                for rq in chain.quotes:
                    if (
                        rq.strike == strike
                        and rq.expiry == nearest_expiry
                        and rq.option_type is OptionType.PUT
                    ):
                        total_oi += rq.open_interest

            rows.append(
                {
                    "strike": strike,
                    "callPrice": call_price,
                    "putPrice": put_price,
                    "callDelta": call_greeks.delta if call_greeks else 0.0,
                    "putDelta": put_greeks.delta if put_greeks else 0.0,
                    "gamma": call_greeks.gamma if call_greeks else 0.0,
                    "vega": call_greeks.vega if call_greeks else 0.0,
                    "iv": call_iv,
                    "oi": total_oi,
                }
            )

        return {
            "spot": snapshot.spot,
            "expiry": nearest_expiry,
            "chain": rows,
        }

    def _build_greeks_book(
        self, snapshot: MarketSnapshot, portfolio: Portfolio | None = None
    ) -> dict[str, Any]:
        """Build ``{spot, rate, positions}`` matching GreeksBook.tsx.

        When a real portfolio is available, computes Greeks for actual positions.
        Otherwise falls back to near-ATM quotes for a representative view.
        """
        if portfolio and portfolio.positions:
            return self._greeks_book_from_portfolio(snapshot, portfolio)

        expiries = sorted({q.expiry for q in snapshot.quotes})
        if not expiries:
            return {"spot": snapshot.spot, "rate": snapshot.rate, "positions": []}
        nearest_expiry = expiries[0]

        atm_quotes = [
            q
            for q in snapshot.quotes
            if q.expiry == nearest_expiry and abs(math.log(q.strike / snapshot.spot)) < 0.05
        ]

        positions: list[dict[str, Any]] = []
        for q in atm_quotes:
            iv = self._safe_iv(
                q.mid,
                snapshot.spot,
                q.strike,
                q.expiry,
                snapshot.rate,
                q.option_type,
                snapshot.dividend_yield,
            )
            if iv <= 0:
                continue
            g = bs_greeks_at(
                snapshot.spot,
                q.strike,
                q.expiry,
                snapshot.rate,
                iv,
                q.option_type,
                snapshot.dividend_yield,
            )
            positions.append(
                {
                    "strike": q.strike,
                    "expiry": q.expiry,
                    "vol": iv,
                    "optionType": q.option_type.value,
                    "price": q.mid,
                    "quantity": 1,
                    "greeks": {
                        "delta": g.delta,
                        "gamma": g.gamma,
                        "vega": g.vega,
                        "theta": g.theta,
                        "rho": g.rho,
                        "vanna": g.vanna,
                        "volga": g.volga,
                    },
                }
            )

        return {
            "spot": snapshot.spot,
            "rate": snapshot.rate,
            "positions": positions,
        }

    def _greeks_book_from_portfolio(
        self, snapshot: MarketSnapshot, portfolio: Portfolio
    ) -> dict[str, Any]:
        positions: list[dict[str, Any]] = []
        for pos in portfolio.positions:
            c = pos.contract
            if c.expiry <= 0:
                continue
            iv = self._safe_iv(
                pos.entry_price,
                snapshot.spot,
                c.strike,
                c.expiry,
                snapshot.rate,
                c.option_type,
                snapshot.dividend_yield,
            )
            if iv <= 0:
                iv = 0.20
            g = bs_greeks_at(
                snapshot.spot,
                c.strike,
                c.expiry,
                snapshot.rate,
                iv,
                c.option_type,
                snapshot.dividend_yield,
            )
            scaled = g.scaled(pos.quantity)
            positions.append(
                {
                    "strike": c.strike,
                    "expiry": c.expiry,
                    "vol": iv,
                    "optionType": c.option_type.value,
                    "price": pos.entry_price,
                    "quantity": pos.quantity,
                    "greeks": {
                        "delta": scaled.delta,
                        "gamma": scaled.gamma,
                        "vega": scaled.vega,
                        "theta": scaled.theta,
                        "rho": scaled.rho,
                        "vanna": scaled.vanna,
                        "volga": scaled.volga,
                    },
                }
            )

        return {
            "spot": snapshot.spot,
            "rate": snapshot.rate,
            "positions": positions,
        }

    def _build_essvi_calibration(self, snapshot: MarketSnapshot) -> dict[str, Any]:
        """Build ``{expiries, spot, params, durrlemanViolations}`` for EssviCalibration.tsx."""
        essvi = ESSVISurface.from_snapshot(
            snapshot,
            n_starts=self._config.essvi_n_starts,
            seed=self._config.essvi_seed,
        )
        iv_points = strip_chain(snapshot)

        by_expiry: dict[float, list[Any]] = defaultdict(list)
        for pt in iv_points:
            by_expiry[pt.expiry].append(pt)

        expiry_slices: list[dict[str, Any]] = []
        for t in sorted(by_expiry.keys()):
            pts = sorted(by_expiry[t], key=lambda p: p.strike)
            strikes = [p.strike for p in pts]
            market_vols = [p.iv for p in pts]
            fitted_vols = np.asarray(essvi.vol(np.array(strikes), t)).tolist()
            theta = float(
                np.interp(
                    t,
                    [te for te, _ in essvi.params.theta_by_expiry],
                    [th for _, th in essvi.params.theta_by_expiry],
                )
            )
            residuals = [abs(m - f) for m, f in zip(market_vols, fitted_vols, strict=True)]
            rmse = float(np.sqrt(np.mean(np.array(residuals) ** 2))) * 100.0

            expiry_slices.append(
                {
                    "t": t,
                    "strikes": strikes,
                    "marketVols": market_vols,
                    "fittedVols": fitted_vols if isinstance(fitted_vols, list) else [fitted_vols],
                    "theta": theta,
                    "rmse": rmse,
                }
            )

        durrleman_violations = sum(
            len(check_durrleman(essvi, float(t), essvi.forward(float(t)))) for t in essvi.expiries
        )

        return {
            "expiries": expiry_slices,
            "spot": snapshot.spot,
            "params": {
                "rho": essvi.params.rho,
                "eta": essvi.params.eta,
                "gamma": essvi.params.gamma_,
            },
            "durrlemanViolations": durrleman_violations,
        }

    def _build_risk_dashboard(
        self, snapshot: MarketSnapshot, portfolio: Portfolio | None = None
    ) -> dict[str, Any]:
        """Build ``{limits, current, utilizationHistory, verdicts}`` for RiskDashboard.tsx.

        When a real portfolio is available, computes aggregate Greeks for
        utilization display. Otherwise shows zero utilization.
        """
        limits = {
            "delta": 500.0,
            "gamma": 50.0,
            "vega": 10000.0,
            "drawdown": 0.05,
        }

        agg = Greeks()
        drawdown = 0.0
        if portfolio and portfolio.positions:
            for pos in portfolio.positions:
                c = pos.contract
                if c.expiry <= 0:
                    continue
                iv = self._safe_iv(
                    pos.entry_price,
                    snapshot.spot,
                    c.strike,
                    c.expiry,
                    snapshot.rate,
                    c.option_type,
                    snapshot.dividend_yield,
                )
                if iv <= 0:
                    iv = 0.20
                try:
                    g = bs_greeks_at(
                        snapshot.spot,
                        c.strike,
                        c.expiry,
                        snapshot.rate,
                        iv,
                        c.option_type,
                        snapshot.dividend_yield,
                    )
                    agg = agg + g.scaled(pos.quantity)
                except Exception:
                    pass
            drawdown = portfolio.drawdown

        current = {
            "delta": agg.delta,
            "gamma": agg.gamma,
            "vega": agg.vega,
            "drawdown": drawdown,
        }
        return {
            "limits": limits,
            "current": current,
            "utilizationHistory": [],
            "verdicts": {"APPROVE": 0, "RESIZE": 0, "REJECT": 0, "HALT": 0},
        }

    @staticmethod
    def _safe_iv(
        price: float,
        spot: float,
        strike: float,
        expiry: float,
        rate: float,
        option_type: OptionType,
        dividend_yield: float,
    ) -> float:
        try:
            return implied_vol(price, spot, strike, expiry, rate, option_type, dividend_yield)
        except Exception:
            return 0.0


__all__ = ["LiveAnalytics", "LiveAnalyticsConfig", "LiveDashboardPayload"]

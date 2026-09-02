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

from optitrade.core.types import MarketSnapshot, OptionQuote, OptionType, Portfolio
from optitrade.data.capture import to_market_snapshot
from optitrade.data.models import RawChain
from optitrade.greeks.scenario import ScenarioGrid, run_scenario_grid
from optitrade.pricing import bs_greeks_at
from optitrade.pricing.implied_vol import implied_vol, strip_chain
from optitrade.vol.arbitrage import check_durrleman
from optitrade.vol.essvi import ESSVISurface
from optitrade.vol.surface import VolSurface

from .book_pricing import PricedBook, price_book, risk_limits_from_settings
from .chain_converter import raw_chain_to_chain_in

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class LiveAnalyticsConfig:
    vol_model: str = "essvi"
    n_strike_grid: int = 25
    essvi_n_starts: int = 5
    essvi_seed: int = 0
    # Scenario heatmap axes: +/-10% spot in 21 steps, +/-5 vol points in 11.
    scenario_n_spot: int = 21
    scenario_spot_width: float = 0.10
    scenario_n_vol: int = 11
    scenario_vol_width: float = 0.05


@dataclass(frozen=True)
class BookContext:
    """The user's real book, supplied by the portfolio sync.

    Analytics builders treat this as optional: with it they describe the
    actual account, without it they describe the market only. No builder may
    invent a book — a panel with no ``BookContext`` reports that it has none.
    """

    portfolio: Portfolio
    marks: dict[str, float]
    equity: float | None = None
    margin_used: float | None = None
    margin_available: float | None = None


@dataclass
class LiveDashboardPayload:
    vol_surface: dict[str, Any] | None = None
    option_chain: dict[str, Any] | None = None
    greeks_book: dict[str, Any] | None = None
    essvi_calibration: dict[str, Any] | None = None
    risk_dashboard: dict[str, Any] | None = None
    scenario_grid: dict[str, Any] | None = None
    higher_order_greeks: dict[str, Any] | None = None
    timestamp: float = 0.0
    underlying: str = ""
    spot: float = 0.0

    def to_wire_dict(self) -> dict[str, Any]:
        """Serialize to the camelCase keys the frontend reads.

        Single source of truth for the wire format. Both the push path
        (``LivePipelineService.on_capture``) and the pull paths (WebSocket
        ``request_snapshot``, ``GET /dashboard/live/snapshot``) go through
        here. ``dataclasses.asdict`` must never be used on the wire — it
        emits snake_case keys the frontend silently discards.
        """
        return {
            "volSurface": self.vol_surface,
            "optionChain": self.option_chain,
            "greeksComparison": self.greeks_book,
            "essviCalibration": self.essvi_calibration,
            "riskDashboard": self.risk_dashboard,
            "scenarioGrid": self.scenario_grid,
            "higherOrderGreeks": self.higher_order_greeks,
            "timestamp": self.timestamp,
            "underlying": self.underlying,
            "spot": self.spot,
        }


class LiveAnalytics:
    """Stateless transformer: RawChain → LiveDashboardPayload."""

    def __init__(self, config: LiveAnalyticsConfig = LiveAnalyticsConfig()) -> None:
        self._config = config

    def build_from_raw_chain(
        self, chain: RawChain, book: BookContext | None = None
    ) -> LiveDashboardPayload:
        """Run all builders and return a payload with partial results on failure.

        ``book`` carries the user's synced positions. Panels that describe a
        book (scenarios, risk) return ``None`` without it rather than falling
        back to a representative or invented one.
        """
        snapshot = to_market_snapshot(chain)
        chain_in = raw_chain_to_chain_in(chain)
        priced = self._price_book(book, snapshot)

        payload = LiveDashboardPayload(
            timestamp=chain.timestamp,
            underlying=chain.underlying,
            spot=chain.spot,
        )

        for name, builder in [
            ("vol_surface", lambda: self._build_vol_surface(snapshot)),
            ("option_chain", lambda: self._build_option_chain(chain, snapshot)),
            ("greeks_book", lambda: self._build_greeks_book(snapshot)),
            ("essvi_calibration", lambda: self._build_essvi_calibration(snapshot)),
            ("risk_dashboard", lambda: self._build_risk_dashboard(snapshot, book, priced)),
            ("scenario_grid", lambda: self._build_scenario_grid(snapshot, priced)),
            ("higher_order_greeks", lambda: self._build_higher_order_greeks(snapshot)),
        ]:
            try:
                setattr(payload, name, builder())
            except Exception:
                logger.exception("Live analytics builder %s failed", name)

        return payload

    @staticmethod
    def _price_book(book: BookContext | None, snapshot: MarketSnapshot) -> PricedBook | None:
        """Price the real book at the captured spot, or None if there is none."""
        if book is None or not book.portfolio.positions:
            return None
        try:
            return price_book(
                book.portfolio,
                marks=book.marks,
                spot=snapshot.spot,
                rate=snapshot.rate,
            )
        except Exception:
            logger.exception("Failed to price the synced book; book panels will be empty")
            return None

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

    def _build_greeks_book(self, snapshot: MarketSnapshot) -> dict[str, Any]:
        """Build ``{spot, rate, positions}`` matching GreeksBook.tsx.

        Generates positions from near-ATM quotes to show a representative view.
        """
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
        self,
        snapshot: MarketSnapshot,
        book: BookContext | None,
        priced: PricedBook | None,
    ) -> dict[str, Any]:
        """Build limit utilisation for RiskDashboard.tsx from the real book.

        Limits come from configuration, exposures from the priced book, margin
        from the broker's funds call. Fields with no honest source are ``None``
        rather than zero:

        - ``drawdown`` needs an equity high-water mark, which is not persisted;
        - ``utilizationHistory`` needs a time series, likewise not persisted;
        - ``verdicts`` needs the pre-trade engine to be reviewing real orders,
          which it is not — this app is read-only today.

        Reporting those as zero would read as "no drawdown, no rejections",
        which is a claim, not an absence.
        """
        limits = risk_limits_from_settings()
        limits_out = {
            "delta": limits.max_abs_delta,
            "gamma": limits.max_abs_gamma,
            "vega": limits.max_abs_vega,
            "drawdown": limits.max_drawdown,
        }

        if priced is None:
            return {
                "limits": limits_out,
                "current": None,
                "marginUtilization": None,
                "drawdown": None,
                "utilizationHistory": [],
                "verdicts": None,
                "legsPriced": 0,
                "legsExcluded": 0,
                "hasBook": False,
            }

        agg = priced.aggregate_greeks
        margin_utilization = None
        if book is not None and book.margin_used is not None and book.equity:
            margin_utilization = book.margin_used / book.equity

        return {
            "limits": limits_out,
            "current": {
                "delta": agg.delta,
                "gamma": agg.gamma,
                "vega": agg.vega,
            },
            "marginUtilization": margin_utilization,
            "drawdown": None,
            "utilizationHistory": [],
            "verdicts": None,
            "legsPriced": priced.n_priced,
            "legsExcluded": priced.n_excluded,
            "hasBook": True,
        }

    def _build_scenario_grid(
        self, snapshot: MarketSnapshot, priced: PricedBook | None
    ) -> dict[str, Any] | None:
        """Full-revaluation spot x vol PnL cube over the user's actual book.

        Returns ``None`` with no book: a scenario grid for a contract the user
        does not hold answers a question nobody asked.
        """
        if priced is None or not priced.legs:
            return None

        scenario_book = priced.to_scenario_book()
        grid = ScenarioGrid.regular(
            n_spot=self._config.scenario_n_spot,
            spot_width=self._config.scenario_spot_width,
            n_vol=self._config.scenario_n_vol,
            vol_width=self._config.scenario_vol_width,
            n_time=1,
            max_days=0.0,
        )
        result = run_scenario_grid(
            scenario_book,
            spot=snapshot.spot,
            rate=snapshot.rate,
            grid=grid,
            dividend_yield=snapshot.dividend_yield,
        )

        # Wire contract for this panel (set by ScenarioHeatmap.tsx):
        #  - axes are in PERCENT, not fractions: the axes render with a "%"
        #    suffix, so emitting 0.10 would draw a 0.1% move as if it were 10%;
        #  - ``pnl`` is (n_vol, n_spot): Plotly heatmap z is indexed [y][x] and
        #    the engine's cube is (spot, vol, time), so it must be transposed.
        # The time axis is pinned to today (n_time=1).
        pnl_vol_by_spot = result.pnl[:, :, 0].T
        worst_pnl, worst_spot, worst_vol, _ = result.worst
        best_pnl, best_spot, best_vol, _ = result.best

        return {
            "spotShifts": (result.spot_shifts * 100.0).tolist(),
            "volShifts": (result.vol_shifts * 100.0).tolist(),
            "pnl": pnl_vol_by_spot.tolist(),
            "baseValue": result.base_value,
            "spot": snapshot.spot,
            "worst": {
                "pnl": worst_pnl,
                "spotShiftPct": worst_spot * 100.0,
                "volShiftPct": worst_vol * 100.0,
            },
            "best": {
                "pnl": best_pnl,
                "spotShiftPct": best_spot * 100.0,
                "volShiftPct": best_vol * 100.0,
            },
            "legsPriced": priced.n_priced,
            "legsExcluded": priced.n_excluded,
        }

    def _build_higher_order_greeks(self, snapshot: MarketSnapshot) -> dict[str, Any] | None:
        """Higher-order Greeks for the live ATM contract via JAX autodiff.

        Describes the at-the-money contract on the captured chain, not the
        user's book — the panel labels it that way. Returns ``None`` if JAX is
        unavailable rather than emitting hardcoded stand-in numbers.
        """
        if not snapshot.quotes:
            return None

        atm = min(snapshot.quotes, key=lambda q: (abs(q.strike - snapshot.spot), q.expiry))
        iv = self._safe_iv(
            atm.mid,
            snapshot.spot,
            atm.strike,
            atm.expiry,
            snapshot.rate,
            atm.option_type,
            snapshot.dividend_yield,
        )
        if iv <= 0:
            logger.debug("ATM IV would not invert; higher-order Greeks omitted")
            return None

        # jax_ad imports cleanly without JAX and raises ImportError on call,
        # so the guard has to wrap the call, not the import.
        try:
            from optitrade.greeks.jax_ad import bs_higher_order_greeks

            greeks = bs_higher_order_greeks(
                snapshot.spot,
                atm.strike,
                atm.expiry,
                snapshot.rate,
                iv,
                atm.option_type,
                snapshot.dividend_yield,
            )
        except ImportError:
            logger.info("JAX not installed; higher-order Greeks unavailable")
            return None

        payload = {k: float(v) for k, v in greeks.items()}
        payload["contract"] = {
            "spot": snapshot.spot,
            "strike": atm.strike,
            "expiry": atm.expiry,
            "rate": snapshot.rate,
            "vol": iv,
            "optionType": atm.option_type.value,
        }
        return payload

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


__all__ = ["BookContext", "LiveAnalytics", "LiveAnalyticsConfig", "LiveDashboardPayload"]

"""OptiTrade Pro CLI — synthetic end-to-end demonstration run.

``optitrade demo`` builds a synthetic market from a known SABR smile, then walks
the full decision pipeline: strip IVs → fit surfaces → validate no-arbitrage →
cross-check Greeks three ways → scenario grid → expert debate → fail-closed risk
review → delta-hedging simulation — journaling every decision to
``runtime_data/`` so the run can be replayed as evidence.
"""

from __future__ import annotations

import argparse
import time
from pathlib import Path

import numpy as np

import optitrade
from optitrade.audit import AgentClaim, GroundednessAuditor
from optitrade.core import (
    Greeks,
    MarketSnapshot,
    OptionContract,
    OptionQuote,
    OptionType,
    Order,
    Portfolio,
    Position,
)
from optitrade.governance import DebatePanel, ExecutionExpert, RiskOfficer, StrategyExpert
from optitrade.governance.experts import TradeProposal
from optitrade.greeks.adjoint import bs_price_adjoint
from optitrade.greeks.finite_difference import fd_greeks
from optitrade.greeks.scenario import BookPosition, ScenarioGrid, run_scenario_grid
from optitrade.hedging import BandParams, DeltaHedger
from optitrade.journal import EventLog
from optitrade.pricing import bs_greeks_at, bs_price
from optitrade.risk import RiskContext, RiskEngine, RiskLimits
from optitrade.vol.arbitrage import check_durrleman, validate_surface
from optitrade.vol.density import rnd_gate
from optitrade.vol.essvi import ESSVISurface
from optitrade.vol.sabr import SABRParams, hagan_implied_vol
from optitrade.vol.surface import SABRSurface, VolSurface

SPOT = 100.0
RATE = 0.05


def _synthetic_snapshot() -> MarketSnapshot:
    """Chain generated from known SABR smiles — ground truth for the demo."""
    quotes: list[OptionQuote] = []
    for expiry, alpha, rho, nu in (
        (30 / 365, 0.22, -0.35, 0.9),
        (91 / 365, 0.21, -0.30, 0.7),
        (182 / 365, 0.20, -0.25, 0.55),
    ):
        forward = SPOT * np.exp(RATE * expiry)
        params = SABRParams(alpha=alpha, beta=1.0, rho=rho, nu=nu, forward=forward, expiry=expiry)
        strikes = np.round(np.linspace(0.85, 1.15, 9) * SPOT)
        vols = np.asarray(hagan_implied_vol(strikes, params))
        for strike, vol in zip(strikes, vols, strict=True):
            opt_type = OptionType.CALL if strike >= SPOT else OptionType.PUT
            mid = float(bs_price(SPOT, strike, expiry, RATE, vol, opt_type))
            quotes.append(
                OptionQuote(strike=float(strike), expiry=expiry, option_type=opt_type, mid=mid)
            )
    return MarketSnapshot(spot=SPOT, rate=RATE, timestamp=time.time(), quotes=tuple(quotes))


def _fmt_greeks(g: Greeks) -> str:
    return (
        f"Δ={g.delta:+.4f}  Γ={g.gamma:+.5f}  vega={g.vega:+.3f}  "
        f"θ={g.theta:+.3f}/yr  ρ={g.rho:+.3f}"
    )


def demo(journal_dir: Path) -> int:
    run_id = f"demo-{time.strftime('%Y%m%d-%H%M%S')}"
    journal = EventLog(journal_dir, run_id)
    print(f"OptiTrade Pro {optitrade.__version__} — demo run {run_id}\n")

    # 1) Vol surfaces from the synthetic chain
    snapshot = _synthetic_snapshot()
    spline = VolSurface.from_snapshot(snapshot)
    sabr = SABRSurface.from_snapshot(snapshot)
    print(f"[vol] chain: {len(snapshot.quotes)} quotes, 3 expiries")
    for fit in sabr.slice_fits:
        print(
            f"[vol] SABR T={fit.params.expiry:.3f}: α={fit.params.alpha:.4f} "
            f"ρ={fit.params.rho:+.3f} ν={fit.params.nu:.3f} "
            f"RMSE={fit.rmse_vol_points:.4f} vol-pt"
        )
    spline_violations = validate_surface(spline, spot=SPOT, rate=RATE)
    sabr_violations = validate_surface(sabr, spot=SPOT, rate=RATE)
    print(
        f"[vol] no-arbitrage: spline {len(spline_violations)} violations "
        f"(checker catches the spline's wing kink at the extrapolation boundary, ADR-005), "
        f"SABR {len(sabr_violations)}"
    )

    # Surface engine v2 (ADR-012): joint arb-free calibration + density gate,
    # with per-expiry SABR as the reported benchmark.
    essvi = ESSVISurface.from_snapshot(snapshot)
    essvi_rmse = essvi.fit.rmse_vol_points if essvi.fit is not None else float("nan")
    durrleman = [
        v for t in essvi.expiries for v in check_durrleman(essvi, float(t), essvi.forward(float(t)))
    ]
    rnd_violations = rnd_gate(essvi, [float(t) for t in essvi.expiries], SPOT, RATE)
    print(
        f"[vol] eSSVI joint fit: RMSE {essvi_rmse:.4f} vol-pt vs SABR benchmark "
        f"{sabr.worst_rmse_vol_points:.4f}; Durrleman violations {len(durrleman)}; "
        f"density gate {len(rnd_violations)} violations (pdf>=0, integral~1, mean~forward)"
    )
    surface_event = journal.append(
        "surface_fit",
        {
            "quotes": len(snapshot.quotes),
            "worst_rmse_vol_points": sabr.worst_rmse_vol_points,
            "essvi_rmse_vol_points": essvi_rmse,
            "spline_arb_violations": len(spline_violations),
            "sabr_arb_violations": len(sabr_violations),
            "durrleman_violations": len(durrleman),
            "density_violations": len(rnd_violations),
        },
    )

    # 2) Greeks three ways on the 91d ATM straddle
    expiry, vol_atm = 91 / 365, float(spline.vol(SPOT, 91 / 365))
    analytic = bs_greeks_at(SPOT, SPOT, expiry, RATE, vol_atm, OptionType.CALL)
    fd = fd_greeks(
        lambda s, v, r, t: float(bs_price(s, SPOT, t, r, v, OptionType.CALL)),
        SPOT,
        vol_atm,
        RATE,
        expiry,
    )
    _, adjoint = bs_price_adjoint(SPOT, SPOT, expiry, RATE, vol_atm, OptionType.CALL)
    print(f"\n[greeks] ATM call T=0.25, σ={vol_atm:.4f}")
    print(f"[greeks] analytic  {_fmt_greeks(analytic)}")
    print(f"[greeks] finite-d  {_fmt_greeks(fd)}")
    print(f"[greeks] adjoint   {_fmt_greeks(adjoint)}")
    max_diff = max(
        abs(analytic.delta - adjoint.delta),
        abs(analytic.vega - adjoint.vega),
        abs(analytic.delta - fd.delta),
    )
    print(f"[greeks] max cross-method diff: {max_diff:.2e}")

    # 3) Scenario grid on a 50-position book
    rng = np.random.default_rng(7)
    book = [
        BookPosition(
            strike=float(k),
            expiry=float(t),
            option_type=OptionType.CALL if is_call else OptionType.PUT,
            quantity=float(q),
            vol=float(spline.vol(float(k), float(t))),
        )
        for k, t, is_call, q in zip(
            rng.uniform(85, 115, 50).round(),
            rng.choice([30 / 365, 91 / 365, 182 / 365], 50),
            rng.integers(0, 2, 50),
            rng.integers(-10, 11, 50),
            strict=True,
        )
        if q != 0
    ]
    grid = ScenarioGrid.regular(
        n_spot=11, spot_width=0.10, n_vol=7, vol_width=0.05, n_time=7, max_days=30
    )
    started = time.perf_counter()
    result = run_scenario_grid(book, SPOT, RATE, grid)
    elapsed_ms = (time.perf_counter() - started) * 1000
    worst_pnl, worst_ds, worst_dv, _ = result.worst
    print(f"\n[scenario] {grid.size} cells × {len(book)} positions in {elapsed_ms:.2f} ms")
    print(f"[scenario] worst cell: PnL {worst_pnl:+.1f} at ΔS={worst_ds:+.1%}, Δσ={worst_dv:+.3f}")
    journal.append(
        "scenario_grid", {"cells": grid.size, "elapsed_ms": elapsed_ms, "worst_pnl": worst_pnl}
    )

    # 4) Debate + risk review of a proposed short-vol trade
    contract = OptionContract(
        symbol="DEMO-C100", strike=SPOT, expiry=expiry, option_type=OptionType.CALL, lot_size=1
    )
    order = Order(
        symbol=contract.symbol,
        quantity=-20,
        price=float(bs_price(SPOT, SPOT, expiry, RATE, vol_atm)),
        contract=contract,
    )
    unit = bs_greeks_at(SPOT, SPOT, expiry, RATE, vol_atm, OptionType.CALL)
    limits = RiskLimits(
        max_abs_delta=50.0,
        max_abs_gamma=5.0,
        max_abs_vega=800.0,
        max_drawdown=0.15,
        max_concentration=0.35,
    )
    # Seeded holdings give the concentration check a meaningful denominator —
    # an empty book needs max_concentration = 1.0 to bootstrap (see risk.checks).
    seeded_positions = (
        Position(
            contract=OptionContract("DEMO-P95", 95.0, expiry, OptionType.PUT, lot_size=1),
            quantity=40.0,
            entry_price=2.10,
        ),
        Position(
            contract=OptionContract("DEMO-C105", 105.0, expiry, OptionType.CALL, lot_size=1),
            quantity=-35.0,
            entry_price=2.60,
        ),
        Position(
            contract=OptionContract("DEMO-C100-JUN", 100.0, 182 / 365, OptionType.CALL, 1),
            quantity=25.0,
            entry_price=6.40,
        ),
    )
    ctx = RiskContext(
        portfolio=Portfolio(
            positions=seeded_positions,
            equity=1_000_000,
            high_water_mark=1_050_000,
            margin_available=250_000,
        ),
        portfolio_greeks=Greeks(delta=4.0, gamma=0.4, vega=120.0),
        order_greeks=unit,
        margin_required=60_000.0,
        spot=SPOT,
    )
    proposal = TradeProposal(
        order=order,
        thesis="Short ATM vol: implied rich vs realized (VRP harvest)",
        expected_edge=4_500.0,
        estimated_cost=900.0,
        implied_vol=vol_atm,
        realized_vol=0.165,
        ctx=ctx,
    )
    panel = DebatePanel(
        experts=(RiskOfficer(limits), StrategyExpert(), ExecutionExpert()), journal=journal
    )
    record = panel.deliberate(proposal)
    print(f"\n[debate] consensus: {record.consensus.value} (score {record.approval_score:+.2f})")
    for op in record.opinions:
        print(
            f"[debate]   {op.expert_name}: {op.stance.value} ({op.confidence:.2f}) — {op.assessment}"
        )

    decision = RiskEngine(limits, journal=journal).review(order, ctx)
    print(f"\n[risk] verdict: {decision.verdict.value}")
    for res in decision.results:
        print(f"[risk]   {res.check_name}: {res.verdict.value} — {res.reason}")

    # 5) Delta-hedging simulation of the (approved-size) short call position
    from optitrade.backtest.hedging_sim import run_delta_hedge_sim

    hedger = DeltaHedger(
        underlying_symbol="DEMO",
        band_params=BandParams(proportional_cost=5e-4, risk_aversion=1.0),
    )
    sim = run_delta_hedge_sim(
        option=contract,
        implied_vol=vol_atm,
        realized_vol=vol_atm,
        spot=SPOT,
        rate=RATE,
        hedger=hedger,
        dt=1 / 252,
        n_steps=62,
        n_paths=64,
        seed=42,
        quantity=-20.0,
    )
    print(f"\n[hedge-sim] 64 GBM paths × 63 days, short 20 calls, RV=IV={vol_atm:.3f}")
    print(
        f"[hedge-sim] mean P&L {sim.mean_pnl:+.2f} (σ {sim.std_pnl:.2f}), mean costs {sim.mean_costs:.2f}"
    )
    print(
        f"[hedge-sim] rebalances/path {sim.n_rebalances_mean:.1f}, theta tracking {sim.theta_tracking:.2%}"
    )
    hedge_event = journal.append(
        "hedge_sim",
        {"mean_pnl": sim.mean_pnl, "theta_tracking": sim.theta_tracking, "paths": 64},
    )

    # 6) Groundedness audit (ADR-015): agent-style claims are only trusted when
    # every number they state is found in the journal events they cite.
    auditor = GroundednessAuditor(journal)
    claims = (
        AgentClaim(
            claim_id="c1",
            statement=f"The eSSVI joint fit achieved {essvi_rmse:.4f} vol-pt RMSE.",
            citations=(surface_event.sequence,),
            values=(("essvi_rmse_vol_points", essvi_rmse),),
        ),
        AgentClaim(
            claim_id="c2",
            statement="Hedging tracked theta to 0.1% — essentially perfect.",
            citations=(hedge_event.sequence,),
            values=(("theta_tracking", 0.001),),  # fabricated number, must fail
        ),
    )
    report = auditor.audit(list(claims))
    print(f"\n[audit] groundedness: {report.grounded_rate:.0%} of agent claims grounded")
    for verdict in report.verdicts:
        status = "grounded" if verdict.grounded else f"REJECTED ({'; '.join(verdict.reasons)})"
        print(f"[audit]   {verdict.claim_id}: {status}")

    n_events = sum(1 for _ in journal.replay())
    print(f"\n[journal] {n_events} events → {journal_dir / (run_id + '.jsonl')}")
    return 0


def cycle(days: int, seed: int, journal_dir: Path) -> int:
    """Run the paper desk over a synthetic VRP market — the phase-4 loop.

    Each day goes through the full deterministic money path (ADR-018):
    strategy → debate panel → fail-closed risk review → paper fill →
    Whalley-Wilmott hedge decision → journal. A drawdown HALT engages the
    file-based kill switch and every later day short-circuits.
    """
    from optitrade.backtest.market_replay import SyntheticVRPMarket
    from optitrade.desk import DeskConfig, KillSwitch, run_daily_cycle
    from optitrade.strategy import VRPConfig, VRPStrategy

    run_id = f"cycle-{time.strftime('%Y%m%d-%H%M%S')}"
    journal = EventLog(journal_dir, run_id)
    kill_switch = KillSwitch(journal_dir / "HALT")
    print(f"OptiTrade Pro {optitrade.__version__} — paper desk run {run_id} (synthetic market)\n")

    market = SyntheticVRPMarket(
        n_days=max(days, 2), spot=SPOT, rate=RATE, realized_vol=0.18, vrp=0.06, seed=seed
    )
    # Index-option scale: lot_size 50 and 4 lots put the flat ₹20/order
    # brokerage in proportion to premium — at toy size the execution expert
    # (correctly) rejects entries whose costs eat most of the edge.
    strategy = VRPStrategy(VRPConfig(quantity=4.0), lot_size=50)
    # max_concentration=1.0: the first trade into an empty book is 100% of
    # gross by definition — the bootstrap policy documented in risk.checks.
    limits = RiskLimits(
        max_abs_delta=500.0,
        max_abs_gamma=50.0,
        max_abs_vega=5_000.0,
        max_drawdown=0.15,
        max_concentration=1.0,
    )
    config = DeskConfig(
        limits=limits,
        band=BandParams(proportional_cost=5e-4, risk_aversion=1.0),
        underlying_symbol="SYNTH",
    )
    panel = DebatePanel(
        experts=(RiskOfficer(limits), StrategyExpert(), ExecutionExpert()), journal=journal
    )
    portfolio = Portfolio(
        cash=1_000_000.0,
        equity=1_000_000.0,
        high_water_mark=1_000_000.0,
        margin_available=500_000.0,
    )
    book: tuple[Position, ...] = ()

    for i, day in enumerate(market):
        result, book, portfolio = run_daily_cycle(
            day, portfolio, book, strategy, config, journal, kill_switch, panel
        )
        halted = "  ** HALTED **" if result.halted else ""
        print(
            f"[day {i + 1:02d}] {result.action_taken:<28} fills={len(result.fills)} "
            f"positions={len(book)} Δ={result.book_greeks.delta:+8.2f} "
            f"equity={portfolio.equity:>12,.2f}{halted}"
        )
        if result.halted:
            break

    from optitrade.desk import RegimeAnalyst, build_daily_report

    report = build_daily_report(
        journal, out_dir=journal_dir / "reports", regime_analyst=RegimeAnalyst()
    )
    print(f"\n[report] daily report → {report.path}")
    print(f"[report] analyst groundedness: {report.grounded_rate_overall:.0%}")

    n_events = sum(1 for _ in journal.replay())
    switch_state = f"ENGAGED ({kill_switch.reason()})" if kill_switch.is_engaged() else "clear"
    print(f"[desk] kill switch: {switch_state}")
    print(f"[journal] {n_events} events → {journal_dir / (run_id + '.jsonl')}")
    return 0


def research(days: int, seed: int, max_proposals: int, journal_dir: Path) -> int:
    """Run the research loop: grid-search proposals evaluated via walk-forward.

    Each proposal varies one VRP parameter from the baseline, runs walk-forward
    on a synthetic market, and reports OOS Sharpe and deflated Sharpe. Accepted
    proposals (Sharpe improvement >= 0.5) are journaled for governance review.
    """
    from optitrade.backtest.market_replay import SyntheticVRPMarket
    from optitrade.backtest.walk_forward import BacktestConfig
    from optitrade.hedging.band import BandParams
    from optitrade.research import GridSearchAgent, ProposalEvaluator, ResearchLoop
    from optitrade.risk.limits import RiskLimits
    from optitrade.strategy.vrp import VRPConfig

    run_id = f"research-{time.strftime('%Y%m%d-%H%M%S')}"
    journal = EventLog(journal_dir, run_id)
    print(f"OptiTrade Pro {optitrade.__version__} — research loop {run_id}\n")

    market = SyntheticVRPMarket(
        n_days=max(days, 20),
        spot=SPOT,
        rate=RATE,
        realized_vol=0.18,
        vrp=0.06,
        seed=seed,
    )
    replay_days = list(market)
    baseline = VRPConfig(quantity=4.0)
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
    agent = GridSearchAgent(steps=(0.5, 0.75, 1.5, 2.0))
    evaluator = ProposalEvaluator(
        replay_days=replay_days,
        backtest_config=bt_config,
        baseline_config=baseline,
        lot_size=50,
        min_improvement=0.5,
        journal=journal,
    )
    loop = ResearchLoop(agent=agent, evaluator=evaluator, journal=journal)

    print(
        f"[research] baseline: entry_vrp_min={baseline.entry_vrp_min}, "
        f"quantity={baseline.quantity}, structure={baseline.structure}"
    )
    print(f"[research] evaluating proposals over {len(replay_days)} synthetic days...\n")

    report = loop.run(baseline, max_proposals=max_proposals)

    print(
        f"[research] baseline OOS Sharpe: {report.baseline_sharpe:.4f}, "
        f"DSR: {report.baseline_dsr:.4f}"
    )
    print(
        f"[research] evaluated {len(report.experiments)} proposals, "
        f"{len(report.accepted)} accepted\n"
    )

    for rank, exp in enumerate(report.ranked, 1):
        status = "ACCEPTED" if exp.accepted else "rejected"
        print(f"  #{rank:02d} [{status}] {exp.proposal.thesis}")
        print(
            f"      Sharpe {exp.candidate_sharpe:+.4f} (Δ {exp.improvement_sharpe:+.4f}), "
            f"DSR {exp.candidate_dsr:.4f} (Δ {exp.improvement_dsr:+.4f}), "
            f"trades {exp.candidate_n_trades}"
        )

    n_events = sum(1 for _ in journal.replay())
    print(f"\n[journal] {n_events} events → {journal_dir / (run_id + '.jsonl')}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="optitrade", description=__doc__)
    sub = parser.add_subparsers(dest="command")
    demo_p = sub.add_parser("demo", help="run the synthetic end-to-end demonstration")
    demo_p.add_argument("--journal-dir", type=Path, default=Path("runtime_data"))
    cycle_p = sub.add_parser("cycle", help="run the paper desk over a synthetic market")
    cycle_p.add_argument("--days", type=int, default=20)
    cycle_p.add_argument("--seed", type=int, default=11)
    cycle_p.add_argument("--journal-dir", type=Path, default=Path("runtime_data"))
    research_p = sub.add_parser("research", help="run the research loop: propose → evaluate → rank")
    research_p.add_argument("--days", type=int, default=60)
    research_p.add_argument("--seed", type=int, default=42)
    research_p.add_argument("--max-proposals", type=int, default=12)
    research_p.add_argument("--journal-dir", type=Path, default=Path("runtime_data"))
    sub.add_parser("version", help="print version")
    args = parser.parse_args()

    if args.command == "version":
        print(optitrade.__version__)
        return 0
    journal_dir = getattr(args, "journal_dir", Path("runtime_data"))
    if args.command == "cycle":
        return cycle(args.days, args.seed, journal_dir)
    if args.command == "research":
        return research(args.days, args.seed, args.max_proposals, journal_dir)
    return demo(journal_dir)


if __name__ == "__main__":
    raise SystemExit(main())

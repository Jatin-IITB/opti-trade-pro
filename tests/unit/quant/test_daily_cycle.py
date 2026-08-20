"""Tests for the deterministic daily desk cycle (paper loop core).

Uses a scripted StubStrategy implementing the Strategy protocol inline — the
concrete VRP strategy is owned elsewhere and deliberately not imported.
"""

from __future__ import annotations

import pytest

from optitrade.core.types import OptionContract, OptionType, Order, Portfolio, Position
from optitrade.desk import DeskConfig, KillSwitch, run_daily_cycle
from optitrade.governance import DebatePanel, ExecutionExpert, RiskOfficer, StrategyExpert
from optitrade.hedging import BandParams
from optitrade.journal import EventLog
from optitrade.risk import RiskLimits
from optitrade.strategy import MarketDay, StrategyDecision

CONTRACT = OptionContract(
    symbol="NIFTY-100-CE", strike=100.0, expiry=0.25, option_type=OptionType.CALL, lot_size=1
)
SPREAD_FRAC = 0.005


class StubStrategy:
    """Minimal Strategy protocol implementation returning a scripted decision."""

    def __init__(self, decision: StrategyDecision) -> None:
        self._decision = decision

    @property
    def name(self) -> str:
        return "stub"

    def decide(self, day, open_positions):
        return self._decision


class FlatSurface:
    """VolLookup stub: one flat implied vol everywhere."""

    def __init__(self, level: float = 0.15) -> None:
        self._level = level

    def vol(self, strike, expiry):
        return self._level


def make_limits(**overrides) -> RiskLimits:
    params = {
        "max_abs_delta": 1000.0,
        "max_abs_gamma": 100.0,
        "max_abs_vega": 10_000.0,
        "max_drawdown": 0.2,
        "max_concentration": 1.0,
    }
    params.update(overrides)
    return RiskLimits(**params)


def make_config(limits: RiskLimits | None = None, **overrides) -> DeskConfig:
    params = {
        "limits": limits if limits is not None else make_limits(),
        "band": BandParams(
            proportional_cost=5e-4, risk_aversion=1.0, min_half_width=0.01, max_half_width=0.5
        ),
        "underlying_symbol": "NIFTY",
        "spread_frac": SPREAD_FRAC,
    }
    params.update(overrides)
    return DeskConfig(**params)


def make_day(surface=None) -> MarketDay:
    return MarketDay(
        timestamp=1_700_000_000.0, spot=100.0, rate=0.05, realized_vol=0.2, surface=surface
    )


def make_portfolio(cash=100_000.0, equity=100_000.0, hwm=100_000.0) -> Portfolio:
    return Portfolio(cash=cash, equity=equity, high_water_mark=hwm, margin_available=1e9)


def enter_decision(quantity=2.0, price=4.0, edge=300.0, cost=100.0) -> StrategyDecision:
    return StrategyDecision(
        action="enter",
        orders=(Order(symbol=CONTRACT.symbol, quantity=quantity, price=price, contract=CONTRACT),),
        thesis="variance risk premium is rich",
        expected_edge=edge,
        estimated_cost=cost,
    )


def event_types(journal: EventLog) -> list[str]:
    return [e.event_type for e in journal.replay()]


@pytest.fixture
def journal(tmp_path):
    return EventLog(tmp_path, "cycle-run")


@pytest.fixture
def kill_switch(tmp_path):
    return KillSwitch(tmp_path / "HALT")


class TestEnterPath:
    def test_debate_approve_risk_approve_fill_at_spread_adjusted_price(self, journal, kill_switch):
        limits = make_limits()
        panel = DebatePanel(
            [RiskOfficer(limits), StrategyExpert(), ExecutionExpert()], journal=journal
        )
        # Surface IV 15% below realized 20% keeps the long-vol thesis coherent.
        day = make_day(surface=FlatSurface(0.15))

        result, book, portfolio = run_daily_cycle(
            day=day,
            portfolio=make_portfolio(),
            book=(),
            strategy=StubStrategy(enter_decision()),
            config=make_config(limits),
            journal=journal,
            kill_switch=kill_switch,
            panel=panel,
        )

        assert not result.halted
        assert result.rejected == ()
        [fill] = result.fills
        assert fill.price == pytest.approx(4.0 * (1 + SPREAD_FRAC / 2))  # buy pays half-spread
        assert fill.quantity == 2.0

        [position] = book
        assert position.contract.symbol == CONTRACT.symbol
        assert position.quantity == 2.0
        assert position.entry_price == pytest.approx(fill.price)

        # Cash decreases by premium plus the half-spread.
        assert portfolio.cash == pytest.approx(100_000.0 - 2.0 * 4.0 * (1 + SPREAD_FRAC / 2))
        assert portfolio.positions == book
        assert not kill_switch.is_engaged()

        types = event_types(journal)
        expected_types = (
            "market_features",
            "debate_decision",
            "risk_decision",
            "hedge_decision",
            "daily_cycle",
        )
        for expected in expected_types:
            assert expected in types
        # The market picture is journaled before any decision-stage event.
        assert types.index("market_features") < types.index("debate_decision")

    def test_market_features_event_carries_the_day_and_its_features(self, journal, kill_switch):
        day = MarketDay(
            timestamp=1_700_000_000.0,
            spot=100.0,
            rate=0.05,
            realized_vol=0.2,
            surface=FlatSurface(0.15),
            features={"atm_iv": 0.15, "vrp": -0.05},
        )
        result, _, _ = run_daily_cycle(
            day=day,
            portfolio=make_portfolio(),
            book=(),
            strategy=StubStrategy(StrategyDecision(action="hold")),
            config=make_config(),
            journal=journal,
            kill_switch=kill_switch,
        )
        [event] = [e for e in journal.replay() if e.event_type == "market_features"]
        assert event.correlation_id == result.correlation_id
        assert event.data == {
            "ts": 1_700_000_000.0,
            "spot": 100.0,
            "realized_vol": 0.2,
            "atm_iv": 0.15,
            "vrp": -0.05,
        }

    def test_cycle_events_share_the_cycle_correlation_id(self, journal, kill_switch):
        result, _, _ = run_daily_cycle(
            day=make_day(surface=FlatSurface(0.15)),
            portfolio=make_portfolio(),
            book=(),
            strategy=StubStrategy(enter_decision()),
            config=make_config(),
            journal=journal,
            kill_switch=kill_switch,
        )
        by_type = {e.event_type: e for e in journal.replay()}
        assert by_type["daily_cycle"].correlation_id == result.correlation_id
        assert by_type["hedge_decision"].correlation_id == result.correlation_id
        assert by_type["daily_cycle"].data["halted"] is False

    def test_require_debate_false_skips_the_panel(self, journal, kill_switch):
        limits = make_limits()
        # This panel would reject (edge 10 nowhere near cost 100) — but it
        # must not even be consulted when require_debate is off.
        panel = DebatePanel(
            [RiskOfficer(limits), StrategyExpert(), ExecutionExpert()], journal=journal
        )
        result, book, _ = run_daily_cycle(
            day=make_day(surface=FlatSurface(0.15)),
            portfolio=make_portfolio(),
            book=(),
            strategy=StubStrategy(enter_decision(edge=10.0, cost=100.0)),
            config=make_config(limits, require_debate=False),
            journal=journal,
            kill_switch=kill_switch,
            panel=panel,
        )
        assert len(result.fills) == 1
        assert len(book) == 1
        assert "debate_decision" not in event_types(journal)


class TestDebateRejectPath:
    def test_reject_consensus_skips_the_order_and_journals_it(self, journal, kill_switch):
        limits = make_limits()
        panel = DebatePanel(
            [RiskOfficer(limits), StrategyExpert(), ExecutionExpert()], journal=journal
        )
        result, book, portfolio = run_daily_cycle(
            day=make_day(surface=FlatSurface(0.15)),
            portfolio=make_portfolio(),
            book=(),
            strategy=StubStrategy(enter_decision(edge=10.0, cost=100.0)),
            config=make_config(limits),
            journal=journal,
            kill_switch=kill_switch,
            panel=panel,
        )
        assert result.fills == ()
        assert book == ()
        assert portfolio.cash == pytest.approx(100_000.0)
        [(order, reason)] = result.rejected
        assert order.symbol == CONTRACT.symbol
        assert "debate" in reason
        types = event_types(journal)
        assert "debate_decision" in types
        assert "order_rejected" in types
        assert "risk_decision" not in types  # skipped before the risk engine


class TestRiskRejectPath:
    def test_limits_breach_blocks_the_fill_and_records_the_reason(self, journal, kill_switch):
        # 2 ATM calls carry ~1.1 delta; a 0.1 cap must reject them.
        result, book, portfolio = run_daily_cycle(
            day=make_day(),
            portfolio=make_portfolio(),
            book=(),
            strategy=StubStrategy(enter_decision()),
            config=make_config(make_limits(max_abs_delta=0.1)),
            journal=journal,
            kill_switch=kill_switch,
        )
        assert not result.halted
        assert result.fills == ()
        assert book == ()
        assert portfolio.cash == pytest.approx(100_000.0)
        [(order, reason)] = result.rejected
        assert order.quantity == 2.0
        assert "exceeds cap" in reason
        assert "risk_decision" in event_types(journal)
        assert not kill_switch.is_engaged()


class TestHaltPath:
    def test_drawdown_halt_engages_the_kill_switch_and_cancels_orders(
        self, journal, kill_switch, tmp_path
    ):
        # Equity 70k against a 100k high-water mark: 30% drawdown >= 20% limit.
        result, book, _ = run_daily_cycle(
            day=make_day(),
            portfolio=make_portfolio(cash=70_000.0, equity=70_000.0, hwm=100_000.0),
            book=(),
            strategy=StubStrategy(enter_decision()),
            config=make_config(),
            journal=journal,
            kill_switch=kill_switch,
        )
        assert result.halted
        assert result.fills == ()
        assert result.hedge is None  # a halted desk places no orders of any kind
        assert book == ()
        [(_, reason)] = result.rejected
        assert "drawdown" in reason

        assert kill_switch.is_engaged()
        assert (tmp_path / "HALT").exists()
        assert "drawdown" in kill_switch.reason()
        assert "kill_switch_engaged" in event_types(journal)

        # The next cycle is skipped outright.
        result2, _, _ = run_daily_cycle(
            day=make_day(),
            portfolio=make_portfolio(cash=70_000.0, equity=70_000.0, hwm=100_000.0),
            book=(),
            strategy=StubStrategy(enter_decision()),
            config=make_config(),
            journal=journal,
            kill_switch=kill_switch,
        )
        assert result2.halted
        assert result2.fills == ()
        assert event_types(journal).count("cycle_skipped") == 1


class TestHoldPath:
    def test_no_orders_but_the_hedge_decision_is_journaled(self, journal, kill_switch):
        book_in = (Position(contract=CONTRACT, quantity=5.0, entry_price=4.0),)
        result, book, portfolio = run_daily_cycle(
            day=make_day(),
            portfolio=make_portfolio(),
            book=book_in,
            strategy=StubStrategy(StrategyDecision(action="hold")),
            config=make_config(),
            journal=journal,
            kill_switch=kill_switch,
        )
        assert result.fills == ()
        assert result.rejected == ()
        assert book == book_in
        assert portfolio.cash == pytest.approx(100_000.0)

        # 5 ATM calls carry ~2.85 delta, well outside the WW band: rebalance.
        assert result.hedge is not None
        assert result.hedge.action == "rebalance"
        assert result.hedge.order.symbol == "NIFTY"
        assert result.hedge.order.quantity == -2.0  # trunc(-2.85) toward zero
        assert result.book_greeks.delta == pytest.approx(2.85, abs=0.05)
        assert "hedge_decision" in event_types(journal)


class TestExitPath:
    def test_position_closed_and_cash_credited_net_of_spread(self, journal, kill_switch):
        book_in = (Position(contract=CONTRACT, quantity=2.0, entry_price=4.0),)
        exit_decision = StrategyDecision(
            action="exit",
            orders=(Order(symbol=CONTRACT.symbol, quantity=-2.0, price=5.0, contract=CONTRACT),),
            thesis="edge realised, close the position",
        )
        result, book, portfolio = run_daily_cycle(
            day=make_day(),
            portfolio=make_portfolio(),
            book=book_in,
            strategy=StubStrategy(exit_decision),
            config=make_config(),
            journal=journal,
            kill_switch=kill_switch,
        )
        assert not result.halted
        [fill] = result.fills
        assert fill.price == pytest.approx(5.0 * (1 - SPREAD_FRAC / 2))  # sell receives less
        assert fill.quantity == -2.0
        assert book == ()  # closed exactly, position removed
        assert portfolio.positions == ()
        assert portfolio.cash == pytest.approx(100_000.0 + 2.0 * 5.0 * (1 - SPREAD_FRAC / 2))
        assert portfolio.equity == pytest.approx(portfolio.cash)  # empty book marks to cash

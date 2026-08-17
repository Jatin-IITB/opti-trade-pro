"""Tests for the fail-closed pre-trade risk engine."""

from __future__ import annotations

import math

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from optitrade.core.types import Greeks, OptionContract, OptionType, Order, Portfolio, Position
from optitrade.journal import EventLog
from optitrade.risk import (
    CheckResult,
    ConcentrationCheck,
    DrawdownCheck,
    GreeksLimitCheck,
    MarginSufficiencyCheck,
    RiskContext,
    RiskEngine,
    RiskLimits,
    Verdict,
)


def make_limits(
    delta: float = 100.0,
    gamma: float = 50.0,
    vega: float = 200.0,
    drawdown: float = 0.20,
    concentration: float = 1.0,
    buffer: float = 1.0,
) -> RiskLimits:
    return RiskLimits(
        max_abs_delta=delta,
        max_abs_gamma=gamma,
        max_abs_vega=vega,
        max_drawdown=drawdown,
        max_concentration=concentration,
        margin_buffer=buffer,
    )


def make_ctx(
    portfolio: Portfolio | None = None,
    portfolio_greeks: Greeks | None = None,
    order_greeks: Greeks | None = None,
    margin_required: float = 0.0,
) -> RiskContext:
    return RiskContext(
        portfolio=portfolio or Portfolio(equity=100.0, high_water_mark=100.0, margin_available=1e9),
        portfolio_greeks=portfolio_greeks or Greeks(),
        order_greeks=order_greeks or Greeks(),
        margin_required=margin_required,
        spot=100.0,
    )


def make_contract(symbol: str, lot_size: int = 1) -> OptionContract:
    return OptionContract(
        symbol=symbol, strike=100.0, expiry=0.05, option_type=OptionType.CALL, lot_size=lot_size
    )


ORDER = Order(symbol="NIFTY", quantity=10.0, price=100.0)


class TestLimitsValidation:
    def test_rejects_non_positive_caps(self):
        with pytest.raises(ValueError, match="max_abs_delta"):
            make_limits(delta=0.0)

    def test_rejects_out_of_range_fractions_and_buffer(self):
        with pytest.raises(ValueError, match="max_drawdown"):
            make_limits(drawdown=1.5)
        with pytest.raises(ValueError, match="max_concentration"):
            make_limits(concentration=0.0)
        with pytest.raises(ValueError, match="margin_buffer"):
            make_limits(buffer=0.9)


class TestGreeksLimitCheck:
    def test_within_limits_approves(self):
        ctx = make_ctx(portfolio_greeks=Greeks(delta=50.0), order_greeks=Greeks(delta=1.0))
        result = GreeksLimitCheck().evaluate(ORDER, ctx, make_limits(delta=100.0))
        assert result.verdict is Verdict.APPROVE  # post delta 60 <= 100

    def test_breach_rejects_naming_the_greek_and_both_numbers(self):
        ctx = make_ctx(portfolio_greeks=Greeks(delta=50.0), order_greeks=Greeks(delta=1.0))
        result = GreeksLimitCheck().evaluate(ORDER, ctx, make_limits(delta=55.0))
        assert result.verdict is Verdict.REJECT
        assert "delta" in result.reason
        assert "60" in result.reason and "55" in result.reason

    def test_short_side_breach_uses_absolute_value(self):
        ctx = make_ctx(portfolio_greeks=Greeks(vega=-190.0), order_greeks=Greeks(vega=-2.0))
        result = GreeksLimitCheck().evaluate(ORDER, ctx, make_limits(vega=200.0))
        assert result.verdict is Verdict.REJECT
        assert "vega" in result.reason


class TestMarginSufficiencyCheck:
    def test_sufficient_margin_approves(self):
        portfolio = Portfolio(equity=100.0, high_water_mark=100.0, margin_available=1000.0)
        ctx = make_ctx(portfolio=portfolio, margin_required=800.0)
        result = MarginSufficiencyCheck().evaluate(ORDER, ctx, make_limits(buffer=1.0))
        assert result.verdict is Verdict.APPROVE

    def test_buffer_pushes_borderline_margin_to_reject(self):
        portfolio = Portfolio(equity=100.0, high_water_mark=100.0, margin_available=1000.0)
        ctx = make_ctx(portfolio=portfolio, margin_required=900.0)
        result = MarginSufficiencyCheck().evaluate(ORDER, ctx, make_limits(buffer=1.25))
        assert result.verdict is Verdict.REJECT  # 900 * 1.25 = 1125 > 1000
        assert "1125" in result.reason and "1000" in result.reason


class TestDrawdownCheck:
    def test_below_limit_approves(self):
        portfolio = Portfolio(equity=95.0, high_water_mark=100.0, margin_available=1e9)
        result = DrawdownCheck().evaluate(ORDER, make_ctx(portfolio=portfolio), make_limits())
        assert result.verdict is Verdict.APPROVE  # 5% < 20%

    def test_at_or_beyond_limit_halts_and_says_cancel_all_orders(self):
        portfolio = Portfolio(equity=75.0, high_water_mark=100.0, margin_available=1e9)
        result = DrawdownCheck().evaluate(ORDER, make_ctx(portfolio=portfolio), make_limits())
        assert result.verdict is Verdict.HALT  # 25% >= 20%
        assert "cancel all open orders" in result.reason


class TestConcentrationCheck:
    def _book(self) -> Portfolio:
        # Two symbols, gross notional 1000: NIFTY 400, BANKNIFTY 600.
        return Portfolio(
            positions=(
                Position(contract=make_contract("NIFTY"), quantity=4.0, entry_price=100.0),
                Position(contract=make_contract("BANKNIFTY"), quantity=6.0, entry_price=100.0),
            ),
            equity=100.0,
            high_water_mark=100.0,
            margin_available=1e9,
        )

    def test_within_cap_approves(self):
        ctx = make_ctx(portfolio=self._book())
        order = Order(symbol="NIFTY", quantity=1.0, price=100.0)
        result = ConcentrationCheck().evaluate(order, ctx, make_limits(concentration=0.5))
        assert result.verdict is Verdict.APPROVE  # 500/1100 = 45.5% <= 50%

    def test_breach_resizes_to_the_exact_boundary_quantity(self):
        ctx = make_ctx(portfolio=self._book())
        order = Order(symbol="NIFTY", quantity=5.0, price=100.0)
        result = ConcentrationCheck().evaluate(order, ctx, make_limits(concentration=0.5))
        assert result.verdict is Verdict.RESIZE  # 900/1500 = 60% > 50%
        assert result.allowed_quantity is not None
        # (existing + q*100) / (1000 + q*100) == 0.5  =>  q == 2
        assert result.allowed_quantity == pytest.approx(2.0)

    def test_resize_keeps_the_order_sign(self):
        ctx = make_ctx(portfolio=self._book())
        order = Order(symbol="NIFTY", quantity=-5.0, price=100.0)
        result = ConcentrationCheck().evaluate(order, ctx, make_limits(concentration=0.5))
        assert result.verdict is Verdict.RESIZE
        assert result.allowed_quantity == pytest.approx(-2.0)

    def test_already_over_concentrated_rejects(self):
        book = Portfolio(
            positions=(
                Position(contract=make_contract("NIFTY"), quantity=9.0, entry_price=100.0),
                Position(contract=make_contract("BANKNIFTY"), quantity=1.0, entry_price=100.0),
            ),
            equity=100.0,
            high_water_mark=100.0,
            margin_available=1e9,
        )
        order = Order(symbol="NIFTY", quantity=1.0, price=100.0)
        result = ConcentrationCheck().evaluate(
            order, make_ctx(portfolio=book), make_limits(concentration=0.5)
        )
        assert result.verdict is Verdict.REJECT  # 900/1000 already at 90%

    def test_first_order_into_empty_book_needs_cap_of_one(self):
        empty = Portfolio(equity=100.0, high_water_mark=100.0, margin_available=1e9)
        order = Order(symbol="NIFTY", quantity=1.0, price=100.0)
        strict = ConcentrationCheck().evaluate(
            order, make_ctx(portfolio=empty), make_limits(concentration=0.5)
        )
        assert strict.verdict is Verdict.REJECT  # any first order is 100% concentrated
        permissive = ConcentrationCheck().evaluate(
            order, make_ctx(portfolio=empty), make_limits(concentration=1.0)
        )
        assert permissive.verdict is Verdict.APPROVE


class _StubCheck:
    """Configurable check for engine-level tests."""

    def __init__(self, name: str, verdict: Verdict, allowed_quantity: float | None = None):
        self._name = name
        self._verdict = verdict
        self._allowed_quantity = allowed_quantity

    @property
    def name(self) -> str:
        return self._name

    def evaluate(self, order: Order, ctx: RiskContext, limits: RiskLimits) -> CheckResult:
        return CheckResult(self._name, self._verdict, f"stub {self._name}", self._allowed_quantity)


class _RaisingCheck:
    @property
    def name(self) -> str:
        return "raising"

    def evaluate(self, order: Order, ctx: RiskContext, limits: RiskLimits) -> CheckResult:
        raise RuntimeError("greeks feed unavailable")


class TestRiskEngine:
    def test_all_approve_returns_original_order(self):
        engine = RiskEngine(make_limits())
        decision = engine.review(ORDER, make_ctx())
        assert decision.verdict is Verdict.APPROVE
        assert decision.adjusted_order is ORDER
        assert len(decision.results) == 4  # every default check reported

    @pytest.mark.parametrize(
        ("verdicts", "expected"),
        [
            ((Verdict.APPROVE, Verdict.RESIZE), Verdict.RESIZE),
            ((Verdict.RESIZE, Verdict.REJECT), Verdict.REJECT),
            ((Verdict.REJECT, Verdict.HALT), Verdict.HALT),
            ((Verdict.HALT, Verdict.APPROVE), Verdict.HALT),
        ],
    )
    def test_verdict_precedence_takes_the_worst(self, verdicts, expected):
        checks = [
            _StubCheck(f"c{i}", v, allowed_quantity=1.0 if v is Verdict.RESIZE else None)
            for i, v in enumerate(verdicts)
        ]
        decision = RiskEngine(make_limits(), checks=checks).review(ORDER, make_ctx())
        assert decision.verdict is expected

    def test_reject_and_halt_return_no_order(self):
        decision = RiskEngine(make_limits(), checks=[_StubCheck("c", Verdict.REJECT)]).review(
            ORDER, make_ctx()
        )
        assert decision.adjusted_order is None

    def test_multiple_resizes_take_the_smallest_magnitude(self):
        checks = [
            _StubCheck("loose", Verdict.RESIZE, allowed_quantity=5.0),
            _StubCheck("tight", Verdict.RESIZE, allowed_quantity=3.0),
        ]
        decision = RiskEngine(make_limits(), checks=checks).review(ORDER, make_ctx())
        assert decision.verdict is Verdict.RESIZE
        assert decision.adjusted_order is not None
        assert decision.adjusted_order.quantity == 3.0
        assert decision.adjusted_order.symbol == ORDER.symbol

    def test_every_check_runs_no_short_circuit(self):
        checks = [_StubCheck("halting", Verdict.HALT), _StubCheck("after", Verdict.APPROVE)]
        decision = RiskEngine(make_limits(), checks=checks).review(ORDER, make_ctx())
        assert [r.check_name for r in decision.results] == ["halting", "after"]

    def test_raising_check_fails_closed_as_reject(self):
        decision = RiskEngine(make_limits(), checks=[_RaisingCheck()]).review(ORDER, make_ctx())
        assert decision.verdict is Verdict.REJECT
        assert decision.adjusted_order is None
        assert "greeks feed unavailable" in decision.results[0].reason

    def test_journal_records_the_decision(self, tmp_path):
        journal = EventLog(tmp_path, "risk-run")
        engine = RiskEngine(make_limits(), journal=journal)
        decision = engine.review(ORDER, make_ctx())
        events = list(journal.replay())
        assert len(events) == 1
        assert events[0].event_type == "risk_decision"
        assert events[0].correlation_id == decision.correlation_id
        assert events[0].data["verdict"] == "approve"
        assert len(events[0].data["results"]) == 4


# The "100% of out-of-bound orders blocked" claim, mechanically enforced:
# across random orders, limits and greeks, no order whose post-trade greeks
# breach a cap is ever approved.
@settings(max_examples=200, deadline=None)
@given(
    cap_delta=st.floats(min_value=1e-3, max_value=1e6),
    cap_gamma=st.floats(min_value=1e-3, max_value=1e6),
    cap_vega=st.floats(min_value=1e-3, max_value=1e6),
    book_delta=st.floats(min_value=-1e6, max_value=1e6),
    book_gamma=st.floats(min_value=-1e6, max_value=1e6),
    book_vega=st.floats(min_value=-1e6, max_value=1e6),
    unit_delta=st.floats(min_value=-1e3, max_value=1e3),
    unit_gamma=st.floats(min_value=-1e3, max_value=1e3),
    unit_vega=st.floats(min_value=-1e3, max_value=1e3),
    quantity=st.floats(min_value=-1e3, max_value=1e3),
)
def test_no_breaching_order_is_ever_approved(
    cap_delta,
    cap_gamma,
    cap_vega,
    book_delta,
    book_gamma,
    book_vega,
    unit_delta,
    unit_gamma,
    unit_vega,
    quantity,
):
    limits = make_limits(
        delta=cap_delta, gamma=cap_gamma, vega=cap_vega, drawdown=0.99, concentration=1.0
    )
    ctx = make_ctx(
        portfolio_greeks=Greeks(delta=book_delta, gamma=book_gamma, vega=book_vega),
        order_greeks=Greeks(delta=unit_delta, gamma=unit_gamma, vega=unit_vega),
    )
    order = Order(symbol="NIFTY", quantity=quantity, price=100.0)
    decision = RiskEngine(limits).review(order, ctx)

    post = ctx.portfolio_greeks + ctx.order_greeks.scaled(order.quantity)
    breached = (
        abs(post.delta) > cap_delta or abs(post.gamma) > cap_gamma or abs(post.vega) > cap_vega
    )
    assert all(map(math.isfinite, (post.delta, post.gamma, post.vega)))
    if breached:
        assert decision.verdict is not Verdict.APPROVE
        assert decision.adjusted_order is None
    else:
        assert decision.verdict is Verdict.APPROVE

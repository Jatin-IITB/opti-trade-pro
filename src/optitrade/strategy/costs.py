"""Indian options transaction-cost model (NSE/SEBI rate card).

Every statutory component is itemised so a fill's cost is auditable line by
line, not a single opaque "fees" number. Rates live in the frozen
:class:`IndianCostRates` dataclass — nothing is hardcoded in the flow — and
each default cites its rate-card source. All rates are as of 2025 and
configurable; regulators revise them, so treat the defaults as a snapshot.

Rate card (defaults):
- ``brokerage_per_order``: INR 20 flat per executed order — the discount
  broker F&O standard (Zerodha/Upstox published brokerage card).
- ``stt_sell_frac``: 0.1% of premium on the SELL side only — Securities
  Transaction Tax on option sales (Finance (No. 2) Act 2024 schedule,
  effective 1 Oct 2024).
- ``exchange_txn_frac``: 0.03503% of premium — NSE transaction charge for
  index/stock options (NSE circular effective 1 Oct 2024).
- ``sebi_frac``: INR 10 per crore of premium = 1e-6 — SEBI turnover fee.
- ``gst_frac``: 18% on (brokerage + exchange transaction charge + SEBI fee)
  — CGST+SGST/IGST on services per the GST schedule.
- ``stamp_buy_frac``: 0.003% of premium on the BUY side only — stamp duty on
  option purchases (Indian Stamp Act uniform schedule, 1 Jul 2020).
- ``hedge_cost_frac``: 5 bp proportional on underlying/futures hedge fills —
  a modelling bundle of futures brokerage, charges and impact, not a
  statutory rate; the backtester applies it to hedge trades.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class IndianCostRates:
    """NSE/SEBI options rate card, as of 2025; every field is configurable.

    Fractions apply to premium notional (``|quantity| * price * lot_size``)
    unless stated otherwise. See the module docstring for per-field sources.
    """

    brokerage_per_order: float = 20.0  # INR flat per executed order
    stt_sell_frac: float = 0.001  # 0.1% of sell-side premium (STT)
    exchange_txn_frac: float = 0.0003503  # NSE options transaction charge
    gst_frac: float = 0.18  # 18% on brokerage + txn + SEBI
    sebi_frac: float = 1e-6  # INR 10 / crore turnover fee
    stamp_buy_frac: float = 0.00003  # 0.003% of buy-side premium
    hedge_cost_frac: float = 5e-4  # proportional, underlying/futures hedges

    def __post_init__(self) -> None:
        for name in (
            "brokerage_per_order",
            "stt_sell_frac",
            "exchange_txn_frac",
            "gst_frac",
            "sebi_frac",
            "stamp_buy_frac",
            "hedge_cost_frac",
        ):
            value = getattr(self, name)
            if value < 0.0:
                raise ValueError(f"{name} must be >= 0, got {value}")


@dataclass(frozen=True, slots=True)
class CostBreakdown:
    """Itemised cost of one (or a round trip of) option fill(s), in INR."""

    brokerage: float
    stt: float
    exchange_txn: float
    gst: float
    sebi: float
    stamp: float
    total: float

    def __add__(self, other: CostBreakdown) -> CostBreakdown:
        return CostBreakdown(
            brokerage=self.brokerage + other.brokerage,
            stt=self.stt + other.stt,
            exchange_txn=self.exchange_txn + other.exchange_txn,
            gst=self.gst + other.gst,
            sebi=self.sebi + other.sebi,
            stamp=self.stamp + other.stamp,
            total=self.total + other.total,
        )


class IndianOptionsCostModel:
    """Applies :class:`IndianCostRates` to option fills.

    Premium notional is ``|quantity| * price * lot_size``; the trade side
    (``is_buy``) decides STT (sell only) versus stamp duty (buy only).
    """

    def __init__(self, rates: IndianCostRates | None = None) -> None:
        self._rates = rates if rates is not None else IndianCostRates()

    @property
    def rates(self) -> IndianCostRates:
        return self._rates

    def cost_of(
        self, price: float, quantity: float, lot_size: int = 1, *, is_buy: bool
    ) -> CostBreakdown:
        """Itemised cost of one fill of ``quantity`` contracts at ``price``.

        ``quantity`` may be signed; only its magnitude enters the premium.
        The side is taken from ``is_buy``, not from the quantity sign, so
        buy-to-close and buy-to-open are charged identically (as they are).
        """
        if price <= 0.0:
            raise ValueError(f"price must be positive, got {price}")
        if quantity == 0.0:
            raise ValueError("quantity must be non-zero for a fill")
        if lot_size < 1:
            raise ValueError(f"lot_size must be >= 1, got {lot_size}")
        r = self._rates
        premium = abs(quantity) * price * lot_size
        brokerage = r.brokerage_per_order
        stt = 0.0 if is_buy else r.stt_sell_frac * premium
        exchange_txn = r.exchange_txn_frac * premium
        sebi = r.sebi_frac * premium
        gst = r.gst_frac * (brokerage + exchange_txn + sebi)
        stamp = r.stamp_buy_frac * premium if is_buy else 0.0
        total = brokerage + stt + exchange_txn + gst + sebi + stamp
        return CostBreakdown(
            brokerage=brokerage,
            stt=stt,
            exchange_txn=exchange_txn,
            gst=gst,
            sebi=sebi,
            stamp=stamp,
            total=total,
        )

    def round_trip(
        self, price_in: float, price_out: float, quantity: float, lot_size: int = 1
    ) -> CostBreakdown:
        """Combined cost of opening at ``price_in`` and closing at ``price_out``.

        The sign of ``quantity`` fixes the leg sides: positive opens with a
        buy and closes with a sell; negative (short first) opens with a sell
        and closes with a buy.
        """
        opens_long = quantity > 0.0
        leg_in = self.cost_of(price_in, quantity, lot_size, is_buy=opens_long)
        leg_out = self.cost_of(price_out, quantity, lot_size, is_buy=not opens_long)
        return leg_in + leg_out

    def hedge_cost(self, price: float, quantity: float) -> float:
        """Proportional cost of an underlying/futures hedge fill.

        ``hedge_cost_frac * |quantity| * price`` — one bundled rate instead
        of an itemised futures card (see the module docstring).
        """
        if price <= 0.0:
            raise ValueError(f"price must be positive, got {price}")
        return self._rates.hedge_cost_frac * abs(quantity) * price


__all__ = ["CostBreakdown", "IndianCostRates", "IndianOptionsCostModel"]

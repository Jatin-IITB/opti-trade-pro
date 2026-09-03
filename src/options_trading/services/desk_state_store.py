"""Durable state for the paper desk: the book, the account, and what ran.

The desk advances one cycle per captured trading day, and each cycle's fills
mutate a book that the next cycle marks and trades against. That makes the
desk *stateful* in a way none of the other panels are: a restart that forgot
the book would not merely blank a chart, it would silently re-enter positions
the desk already holds the next time the same stored day came round.

Two properties follow, and both are deliberate:

**Idempotence by recorded date.** ``processed_dates`` is the authoritative
record of which stored days have been cycled. Advancing skips any date
already in it, so re-running over the same history is a no-op rather than a
double fill. The state is written after *every* cycle, not once at the end,
because a crash halfway through a ten-day backlog must resume at day six.

**A corrupt state file is not an empty desk.** :meth:`DeskStateStore.load`
distinguishes "nothing here yet" (returns ``None``; a fresh desk is correct)
from "there is something here and it does not parse" (raises
:class:`DeskStateError`). Falling back to a fresh book on a parse failure
would put a flat book and an untouched equity figure on screen, which reads
as a desk that traded and holds nothing — the opposite of the truth, and
exactly the class of fabrication this app has spent three phases removing
(ADR-008: an error is a rejection, never a pass-through).

Position detail lives here, so the file belongs under gitignored
``runtime_data`` alongside the book snapshots.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from optitrade.core.types import OptionContract, OptionType, Portfolio, Position

SCHEMA_VERSION = 1


class DeskStateError(RuntimeError):
    """Raised when persisted desk state exists but cannot be trusted."""


@dataclass(frozen=True)
class CycleRecord:
    """Display-ready summary of one completed cycle.

    Kept alongside the book rather than re-derived from the journal on every
    read: the journal is the audit trail and is authoritative, but rebuilding
    a table of N days from it means replaying every event on every dashboard
    tick. The ``correlation_id`` is the join back to the full trail.
    """

    date: str
    timestamp: float
    action: str
    action_taken: str
    n_fills: int
    n_rejected: int
    equity: float
    cash: float
    drawdown: float
    delta: float
    gamma: float
    vega: float
    theta: float
    hedge_action: str | None
    halted: bool
    correlation_id: str
    fills: tuple[dict[str, Any], ...] = ()
    rejected: tuple[dict[str, Any], ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "date": self.date,
            "timestamp": self.timestamp,
            "action": self.action,
            "action_taken": self.action_taken,
            "n_fills": self.n_fills,
            "n_rejected": self.n_rejected,
            "equity": self.equity,
            "cash": self.cash,
            "drawdown": self.drawdown,
            "delta": self.delta,
            "gamma": self.gamma,
            "vega": self.vega,
            "theta": self.theta,
            "hedge_action": self.hedge_action,
            "halted": self.halted,
            "correlation_id": self.correlation_id,
            "fills": [dict(f) for f in self.fills],
            "rejected": [dict(r) for r in self.rejected],
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> CycleRecord:
        return cls(
            date=str(raw["date"]),
            timestamp=float(raw["timestamp"]),
            action=str(raw["action"]),
            action_taken=str(raw["action_taken"]),
            n_fills=int(raw["n_fills"]),
            n_rejected=int(raw["n_rejected"]),
            equity=float(raw["equity"]),
            cash=float(raw["cash"]),
            drawdown=float(raw["drawdown"]),
            delta=float(raw["delta"]),
            gamma=float(raw["gamma"]),
            vega=float(raw["vega"]),
            theta=float(raw["theta"]),
            hedge_action=raw["hedge_action"],
            halted=bool(raw["halted"]),
            correlation_id=str(raw["correlation_id"]),
            fills=tuple(raw.get("fills", ())),
            rejected=tuple(raw.get("rejected", ())),
        )


@dataclass(frozen=True)
class DeskState:
    """Everything a restarting desk needs to continue where it left off."""

    journal_run_id: str
    book: tuple[Position, ...] = ()
    portfolio: Portfolio = field(default_factory=Portfolio)
    processed_dates: tuple[str, ...] = ()
    cycles: tuple[CycleRecord, ...] = ()


def _contract_to_dict(contract: OptionContract) -> dict[str, Any]:
    return {
        "symbol": contract.symbol,
        "strike": contract.strike,
        "expiry": contract.expiry,
        "option_type": contract.option_type.value,
        "lot_size": contract.lot_size,
    }


def _contract_from_dict(raw: dict[str, Any]) -> OptionContract:
    return OptionContract(
        symbol=str(raw["symbol"]),
        strike=float(raw["strike"]),
        expiry=float(raw["expiry"]),
        option_type=OptionType(raw["option_type"]),
        lot_size=int(raw["lot_size"]),
    )


class DeskStateStore:
    """Reads and writes one :class:`DeskState` as versioned JSON."""

    def __init__(self, path: Path) -> None:
        self._path = Path(path)

    @property
    def path(self) -> Path:
        return self._path

    def exists(self) -> bool:
        return self._path.exists()

    def save(self, state: DeskState) -> Path:
        """Persist ``state`` atomically; returns the path written."""
        payload = {
            "schema_version": SCHEMA_VERSION,
            "journal_run_id": state.journal_run_id,
            "processed_dates": list(state.processed_dates),
            "portfolio": {
                "cash": state.portfolio.cash,
                "equity": state.portfolio.equity,
                "high_water_mark": state.portfolio.high_water_mark,
                "margin_available": state.portfolio.margin_available,
            },
            "book": [
                {
                    "contract": _contract_to_dict(position.contract),
                    "quantity": position.quantity,
                    "entry_price": position.entry_price,
                }
                for position in state.book
            ],
            "cycles": [record.to_dict() for record in state.cycles],
        }
        self._path.parent.mkdir(parents=True, exist_ok=True)
        # Written whole then renamed: a crash mid-write must not leave a
        # shorter book that still parses (the BookSnapshotStore pattern).
        temporary = self._path.with_suffix(self._path.suffix + ".tmp")
        temporary.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        temporary.replace(self._path)
        return self._path

    def load(self) -> DeskState | None:
        """Return the persisted state, or ``None`` when nothing is stored.

        Raises :class:`DeskStateError` when a file exists but cannot be
        parsed or carries an unknown schema. The caller must surface that as
        an unavailable desk; see the module docstring for why a fresh book is
        the wrong fallback.
        """
        if not self._path.exists():
            return None
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise DeskStateError(f"desk state at {self._path} is unreadable: {exc}") from exc
        if not isinstance(raw, dict):
            raise DeskStateError(f"desk state at {self._path} is not a JSON object")
        version = raw.get("schema_version")
        if version != SCHEMA_VERSION:
            raise DeskStateError(
                f"desk state at {self._path} has schema_version {version!r}; "
                f"this build reads {SCHEMA_VERSION}"
            )
        try:
            account = raw["portfolio"]
            positions = tuple(
                Position(
                    contract=_contract_from_dict(entry["contract"]),
                    quantity=float(entry["quantity"]),
                    entry_price=float(entry["entry_price"]),
                )
                for entry in raw["book"]
            )
            portfolio = Portfolio(
                positions=positions,
                cash=float(account["cash"]),
                equity=float(account["equity"]),
                high_water_mark=float(account["high_water_mark"]),
                margin_available=float(account["margin_available"]),
            )
            return DeskState(
                journal_run_id=str(raw["journal_run_id"]),
                book=positions,
                portfolio=portfolio,
                processed_dates=tuple(str(d) for d in raw["processed_dates"]),
                cycles=tuple(CycleRecord.from_dict(c) for c in raw["cycles"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise DeskStateError(
                f"desk state at {self._path} is malformed: {type(exc).__name__}: {exc}"
            ) from exc


__all__ = [
    "SCHEMA_VERSION",
    "CycleRecord",
    "DeskState",
    "DeskStateError",
    "DeskStateStore",
]

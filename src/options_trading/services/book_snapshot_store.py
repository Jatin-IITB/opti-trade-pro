"""Persistence for the user's priced book, so a day's P&L can be explained.

:mod:`~optitrade.explain.pnl_explain` decomposes a day's P&L into theta,
delta, gamma-vs-realized-variance, vega and vanna/volga buckets. Every one of
those needs the book as it stood at the *start* of the period — the Greeks it
carried, the spot it faced, the vols its legs were marked at. The portfolio
sync only ever holds the current book, so without persistence the explain tab
has nothing to work with and can only show a fabricated waterfall, which is
what it did before this module existed.

Layout mirrors :class:`~optitrade.data.snapshot_store.SnapshotStore`:
``root/{YYYY-MM-DD}/{HHMMSS}.json`` in UTC, so lexicographic path order is
chronological and the last file of a date is that date's close.

JSON rather than Parquet: a book is tens of legs, not thousands of quotes, and
the per-leg Greeks are a nested record that a flat columnar frame would have
to shred. The files live under ``runtime_data/`` (gitignored) because
positions are private — this module never logs leg detail above DEBUG.
"""

from __future__ import annotations

import json
import logging
import shutil
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path

from optitrade.core.types import Greeks

from .book_pricing import PricedBook

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1

# Greek fields persisted per leg. Listed explicitly rather than derived from
# the dataclass so that adding a field to Greeks cannot silently change the
# on-disk schema without a version bump.
_GREEK_FIELDS = ("delta", "gamma", "vega", "theta", "rho", "vanna", "volga")


@dataclass(frozen=True)
class LegSnapshot:
    """One leg as it stood at a point in time."""

    symbol: str
    strike: float
    expiry: float
    option_type: str
    quantity: float
    mark: float
    iv: float
    greeks: Greeks

    @property
    def value(self) -> float:
        """Mark-to-market value of the position (quantity is in units)."""
        return self.quantity * self.mark


@dataclass(frozen=True)
class BookSnapshot:
    """The priced book at one instant, sufficient to explain a later P&L."""

    timestamp: float
    spot: float
    rate: float
    legs: tuple[LegSnapshot, ...]
    n_excluded: int = 0
    equity: float | None = None

    @property
    def value(self) -> float:
        """Net mark-to-market value; a spread book nets toward zero."""
        return sum(leg.value for leg in self.legs)

    @property
    def gross_value(self) -> float:
        """Sum of absolute leg values — how much position is on, not its net."""
        return sum(abs(leg.value) for leg in self.legs)

    @property
    def by_symbol(self) -> dict[str, LegSnapshot]:
        """Legs keyed by symbol, with same-symbol rows combined.

        Upstox returns one position row per *(instrument, product)*, and
        ``to_core_portfolio`` keys the contract on the trading symbol alone.
        So the same strike held both MIS and NRML — ordinary for an options
        trader — arrives as two legs with an identical symbol. A plain dict
        comprehension keeps only the last, which would drop half the position
        from the P&L attribution while ``value`` still counted both.

        Quantities are summed; the mark, vol and per-unit Greeks are taken
        from the first row because they describe the contract, which is the
        same one in both.
        """
        combined: dict[str, LegSnapshot] = {}
        for leg in self.legs:
            existing = combined.get(leg.symbol)
            combined[leg.symbol] = (
                leg
                if existing is None
                else replace(existing, quantity=existing.quantity + leg.quantity)
            )
        return combined

    @property
    def utc_date(self) -> str:
        return datetime.fromtimestamp(self.timestamp, tz=UTC).strftime("%Y-%m-%d")


def snapshot_from_priced_book(
    priced: PricedBook, timestamp: float, equity: float | None = None
) -> BookSnapshot:
    """Capture a :class:`PricedBook` for persistence.

    Only priced legs are carried: a leg excluded by ``price_book`` has no
    trustworthy vol or Greeks, and storing it with placeholders would let a
    later explain silently attribute P&L to a Greek that was never measured.
    ``n_excluded`` records how much of the book that leaves out.
    """
    return BookSnapshot(
        timestamp=timestamp,
        spot=priced.spot,
        rate=priced.rate,
        legs=tuple(
            LegSnapshot(
                symbol=leg.contract.symbol,
                strike=leg.contract.strike,
                expiry=leg.contract.expiry,
                option_type=leg.contract.option_type.value,
                quantity=leg.quantity,
                mark=leg.mark,
                iv=leg.iv,
                greeks=leg.greeks,
            )
            for leg in priced.legs
        ),
        n_excluded=priced.n_excluded,
        equity=equity,
    )


def _greeks_to_dict(greeks: Greeks) -> dict[str, float]:
    return {name: float(getattr(greeks, name)) for name in _GREEK_FIELDS}


def _greeks_from_dict(raw: dict[str, float]) -> Greeks:
    return Greeks(**{name: float(raw.get(name, 0.0)) for name in _GREEK_FIELDS})


class BookSnapshotStore:
    """Writes and reads :class:`BookSnapshot` records under a root directory."""

    def __init__(self, root: Path) -> None:
        self._root = Path(root)

    def write(self, snapshot: BookSnapshot) -> Path:
        """Persist ``snapshot``; returns the path written.

        An empty book is refused rather than stored: a flat book and a failed
        sync are indistinguishable once written, and explaining P&L against a
        phantom empty snapshot would report the whole move as residual.
        """
        if not snapshot.legs:
            raise ValueError(
                "refusing to persist a book snapshot with 0 priced legs; "
                "an empty book cannot be distinguished from a failed sync"
            )
        stamp = datetime.fromtimestamp(snapshot.timestamp, tz=UTC)
        path = self._root / stamp.strftime("%Y-%m-%d") / f"{stamp.strftime('%H%M%S')}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": SCHEMA_VERSION,
            "timestamp": snapshot.timestamp,
            "spot": snapshot.spot,
            "rate": snapshot.rate,
            "n_excluded": snapshot.n_excluded,
            "equity": snapshot.equity,
            "legs": [
                {
                    "symbol": leg.symbol,
                    "strike": leg.strike,
                    "expiry": leg.expiry,
                    "option_type": leg.option_type,
                    "quantity": leg.quantity,
                    "mark": leg.mark,
                    "iv": leg.iv,
                    "greeks": _greeks_to_dict(leg.greeks),
                }
                for leg in snapshot.legs
            ],
        }
        # Written whole then renamed so a crash mid-write cannot leave a
        # half-object that later parses as a smaller book.
        temporary = path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(payload), encoding="utf-8")
        temporary.replace(path)
        logger.debug("Wrote book snapshot %s (%d legs)", path.name, len(snapshot.legs))
        return path

    def read(self, path: Path) -> BookSnapshot:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        version = int(raw.get("schema_version", 0))
        if version != SCHEMA_VERSION:
            raise ValueError(
                f"book snapshot {path} has schema_version {version}; "
                f"this reader supports version {SCHEMA_VERSION}"
            )
        return BookSnapshot(
            timestamp=float(raw["timestamp"]),
            spot=float(raw["spot"]),
            rate=float(raw["rate"]),
            legs=tuple(
                LegSnapshot(
                    symbol=str(leg["symbol"]),
                    strike=float(leg["strike"]),
                    expiry=float(leg["expiry"]),
                    option_type=str(leg["option_type"]),
                    quantity=float(leg["quantity"]),
                    mark=float(leg["mark"]),
                    iv=float(leg["iv"]),
                    greeks=_greeks_from_dict(leg["greeks"]),
                )
                for leg in raw["legs"]
            ),
            n_excluded=int(raw.get("n_excluded", 0)),
            equity=None if raw.get("equity") is None else float(raw["equity"]),
        )

    def dates(self) -> list[str]:
        """Stored UTC dates, oldest first — directory names only, no globbing."""
        if not self._root.is_dir():
            return []
        return sorted(d.name for d in self._root.iterdir() if d.is_dir())

    def list_snapshots(self, date: str | None = None) -> list[Path]:
        """All snapshot paths, chronologically sorted; ``date`` filters to a UTC day."""
        if not self._root.is_dir():
            return []
        days = [self._root / date] if date is not None else [self._root / d for d in self.dates()]
        return [path for day in days if day.is_dir() for path in sorted(day.glob("*.json"))]

    def read_day(self, date: str) -> list[BookSnapshot]:
        """Every snapshot for one UTC day, in time order."""
        return [self.read(path) for path in self.list_snapshots(date)]

    def end_of_day_snapshots(self, limit: int | None = None) -> list[BookSnapshot]:
        """The last snapshot of each stored UTC day, oldest first.

        The closest thing an intraday sync has to an official close, matching
        :class:`~optitrade.backtest.market_replay.StoreReplay`'s convention so
        the two histories line up on the same dates. ``limit`` keeps only the
        most recent days.
        """
        dates = self.dates()
        if limit is not None:
            # dates[-0:] is the whole list, so an explicit 0 must short-circuit
            # or "give me none" would load the entire history.
            if limit <= 0:
                return []
            dates = dates[-limit:]
        snapshots = []
        for date in dates:
            paths = self.list_snapshots(date)
            if not paths:
                continue
            try:
                snapshots.append(self.read(paths[-1]))
            except (ValueError, OSError, KeyError, TypeError, json.JSONDecodeError):
                # Skip-and-warn: one unreadable day must not blind the panel.
                # TypeError included because a null field turns int()/float()
                # into a TypeError, not a ValueError.
                logger.warning("Unreadable book snapshot for %s; skipped", date, exc_info=True)
        return snapshots

    def intraday_spots(self, start: float, stop: float) -> list[float]:
        """Spots recorded in ``[start, stop]``, in time order.

        Feeds the realized-variance estimate the gamma bucket marks against;
        the whole point of that bucket is that gamma accrues along the path,
        not against one close-to-close move.

        ``start`` is *inclusive*. The caller passes the previous end-of-day
        snapshot's own timestamp, and for an Indian index that first return is
        the overnight gap — routinely the largest single move of the period.
        Excluding it would drop it from the realized variance entirely.

        Only the date directories spanning the window are read. Scanning the
        whole store and filtering afterwards meant deserialising every leg of
        every snapshot ever taken to recover one float each — minutes of work
        once a few months of history had accumulated.
        """
        if stop < start:
            return []
        first = datetime.fromtimestamp(start, tz=UTC).strftime("%Y-%m-%d")
        last = datetime.fromtimestamp(stop, tz=UTC).strftime("%Y-%m-%d")
        spots: list[float] = []
        for date in self.dates():
            if not first <= date <= last:
                continue
            for path in self.list_snapshots(date):
                try:
                    snapshot = self.read(path)
                except (ValueError, OSError, KeyError, TypeError, json.JSONDecodeError):
                    continue
                if start <= snapshot.timestamp <= stop:
                    spots.append(snapshot.spot)
        return spots

    def prune(self, keep_days: int) -> int:
        """Delete all but the most recent ``keep_days`` UTC dates.

        Without this the store grows without bound: a 60s sync writes 1,440
        files a day, so a 30-leg book reaches ~4.7 GB and half a million files
        in a year. Only the last two end-of-day snapshots are ever read for
        the P&L panel, so retention costs nothing the product uses.

        Returns the number of dates removed.
        """
        if keep_days < 1:
            raise ValueError(f"keep_days must be >= 1, got {keep_days}")
        stale = self.dates()[:-keep_days]
        removed = 0
        for date in stale:
            try:
                shutil.rmtree(self._root / date)
                removed += 1
            except OSError:
                logger.warning("Could not prune book snapshots for %s", date, exc_info=True)
        if removed:
            logger.info(
                "Pruned %d day(s) of book snapshots beyond %d-day retention", removed, keep_days
            )
        return removed


__all__ = [
    "SCHEMA_VERSION",
    "BookSnapshot",
    "BookSnapshotStore",
    "LegSnapshot",
    "snapshot_from_priced_book",
]

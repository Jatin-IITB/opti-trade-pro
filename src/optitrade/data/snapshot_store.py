"""Parquet persistence for raw option-chain snapshots.

Layout: ``root/{underlying}/{YYYY-MM-DD}/{HHMMSS}.parquet`` (UTC, derived from
the chain's timestamp), one row per quote with the chain-level fields repeated
as columns plus a ``schema_version`` column. Because directory and file names
are zero-padded UTC dates/times, lexicographic path order equals chronological
order, which ``list_snapshots`` relies on.

Round-trips preserve every value to float64 precision (Parquet doubles are
exact for float64).
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pandas as pd

from optitrade.core.types import OptionType
from optitrade.data.models import RawChain, RawQuote

SCHEMA_VERSION = 1


class SnapshotStore:
    """Writes and reads raw chains as Parquet files under a root directory."""

    def __init__(self, root: Path) -> None:
        self._root = Path(root)

    def write(self, chain: RawChain) -> Path:
        """Persist ``chain`` and return the path of the Parquet file written."""
        if not chain.quotes:
            raise ValueError(
                f"refusing to persist a chain for {chain.underlying} with 0 quotes; "
                "an empty snapshot cannot be reconstructed"
            )
        stamp = datetime.fromtimestamp(chain.timestamp, tz=UTC)
        path = (
            self._root
            / chain.underlying
            / stamp.strftime("%Y-%m-%d")
            / f"{stamp.strftime('%H%M%S')}.parquet"
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        n = len(chain.quotes)
        frame = pd.DataFrame(
            {
                "schema_version": [SCHEMA_VERSION] * n,
                "underlying": [chain.underlying] * n,
                "spot": [chain.spot] * n,
                "rate": [chain.rate] * n,
                "timestamp": [chain.timestamp] * n,
                "dividend_yield": [chain.dividend_yield] * n,
                "strike": [q.strike for q in chain.quotes],
                "expiry": [q.expiry for q in chain.quotes],
                "option_type": [q.option_type.value for q in chain.quotes],
                "bid": [q.bid for q in chain.quotes],
                "ask": [q.ask for q in chain.quotes],
                "ltp": [q.ltp for q in chain.quotes],
                "volume": [q.volume for q in chain.quotes],
                "open_interest": [q.open_interest for q in chain.quotes],
                "bid_qty": [q.bid_qty for q in chain.quotes],
                "ask_qty": [q.ask_qty for q in chain.quotes],
                "ltp_age_seconds": [q.ltp_age_seconds for q in chain.quotes],
            }
        )
        frame.to_parquet(path, engine="pyarrow", index=False)
        return path

    def read(self, path: Path) -> RawChain:
        """Load one snapshot file back into a :class:`RawChain`."""
        frame = pd.read_parquet(path, engine="pyarrow")
        if frame.empty:
            raise ValueError(f"snapshot {path} contains no rows")
        version = int(frame["schema_version"].iloc[0])
        if version != SCHEMA_VERSION:
            raise ValueError(
                f"snapshot {path} has schema_version {version}; "
                f"this reader supports version {SCHEMA_VERSION}"
            )
        rows = frame.to_dict(orient="records")
        quotes = tuple(
            RawQuote(
                strike=float(row["strike"]),
                expiry=float(row["expiry"]),
                option_type=OptionType(str(row["option_type"])),
                bid=float(row["bid"]),
                ask=float(row["ask"]),
                ltp=float(row["ltp"]),
                volume=int(row["volume"]),
                open_interest=int(row["open_interest"]),
                bid_qty=int(row["bid_qty"]),
                ask_qty=int(row["ask_qty"]),
                ltp_age_seconds=float(row["ltp_age_seconds"]),
            )
            for row in rows
        )
        first = rows[0]
        return RawChain(
            underlying=str(first["underlying"]),
            spot=float(first["spot"]),
            rate=float(first["rate"]),
            timestamp=float(first["timestamp"]),
            quotes=quotes,
            dividend_yield=float(first["dividend_yield"]),
        )

    def list_snapshots(self, underlying: str, date: str | None = None) -> list[Path]:
        """All snapshot paths for ``underlying``, chronologically sorted.

        ``date`` (``YYYY-MM-DD``) restricts the listing to a single UTC day.
        Missing directories yield an empty list.
        """
        base = self._root / underlying
        if not base.is_dir():
            return []
        if date is not None:
            day_dirs = [base / date] if (base / date).is_dir() else []
        else:
            day_dirs = sorted(d for d in base.iterdir() if d.is_dir())
        return [path for day in day_dirs for path in sorted(day.glob("*.parquet"))]

    def read_day(self, underlying: str, date: str) -> list[RawChain]:
        """Load every snapshot for one underlying and UTC day, in time order."""
        return [self.read(path) for path in self.list_snapshots(underlying, date)]


__all__ = ["SCHEMA_VERSION", "SnapshotStore"]

"""Event record for the append-only run journal.

One :class:`Event` per JSONL line. Sequence numbers are assigned by the
:class:`~optitrade.journal.event_log.EventLog` and are monotonically
increasing within a run; correlation ids tie together every event produced
while handling one request (a risk review, a debate, ...).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from optitrade.core.errors import JournalError


@dataclass(frozen=True, slots=True)
class Event:
    """A single immutable journal entry."""

    sequence: int
    event_type: str
    timestamp: float  # unix epoch seconds
    correlation_id: str
    data: dict[str, Any]

    def to_json_line(self) -> str:
        """Serialise to a single JSON line (no trailing newline)."""
        try:
            return json.dumps(
                {
                    "sequence": self.sequence,
                    "event_type": self.event_type,
                    "timestamp": self.timestamp,
                    "correlation_id": self.correlation_id,
                    "data": self.data,
                },
                separators=(",", ":"),
            )
        except (TypeError, ValueError) as exc:
            raise JournalError(f"event data is not JSON-serialisable: {exc}") from exc

    @classmethod
    def from_json_line(cls, line: str) -> Event:
        """Parse one JSONL line back into an :class:`Event`.

        Raises :class:`JournalError` on malformed input; the caller
        (``EventLog.replay``) adds the line number.
        """
        try:
            raw = json.loads(line)
        except json.JSONDecodeError as exc:
            raise JournalError(f"invalid JSON: {exc}") from exc
        if not isinstance(raw, dict):
            raise JournalError(f"expected a JSON object, got {type(raw).__name__}")
        try:
            return cls(
                sequence=int(raw["sequence"]),
                event_type=str(raw["event_type"]),
                timestamp=float(raw["timestamp"]),
                correlation_id=str(raw["correlation_id"]),
                data=dict(raw["data"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise JournalError(f"malformed event record: {exc!r}") from exc


__all__ = ["Event"]

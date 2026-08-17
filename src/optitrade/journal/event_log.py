"""Append-only JSONL event log (ported from the Prism/aroha runtime).

Design choices, in order of priority:

- Crash safety over throughput: every ``append`` opens the file, writes one
  line, flushes and fsyncs. A power cut loses at most the line being written.
- Recoverability: constructing an :class:`EventLog` over an existing file
  resumes sequence numbering from the max sequence found on disk, tolerating
  a torn final line (the aroha recovery pattern).
- Strict replay: ``replay`` refuses to skip corruption silently — a bad line
  raises :class:`JournalError` naming the line number, because a journal you
  cannot trust end-to-end is worse than no journal.
"""

from __future__ import annotations

import json
import os
import time
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from optitrade.core.errors import JournalError
from optitrade.journal.events import Event


class EventLog:
    """Append-only event journal writing to ``{directory}/{run_id}.jsonl``."""

    def __init__(self, directory: Path, run_id: str) -> None:
        self._directory = directory
        self._run_id = run_id
        directory.mkdir(parents=True, exist_ok=True)
        self._path = directory / f"{run_id}.jsonl"
        self._sequence = self._recover_sequence()

    @property
    def path(self) -> Path:
        return self._path

    @property
    def run_id(self) -> str:
        return self._run_id

    def _recover_sequence(self) -> int:
        """Return the max sequence already on disk (0 for a fresh run).

        Recovery is lenient where replay is strict: a crash mid-write can
        leave a torn final line, and recovery must still succeed so the run
        can continue appending.
        """
        if not self._path.exists():
            return 0
        max_seq = 0
        for line in self._path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                raw = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(raw, dict) and isinstance(raw.get("sequence"), int):
                max_seq = max(max_seq, raw["sequence"])
        return max_seq

    def append(
        self,
        event_type: str,
        data: dict[str, Any],
        correlation_id: str | None = None,
    ) -> Event:
        """Append one event and durably persist it before returning."""
        event = Event(
            sequence=self._sequence + 1,
            event_type=event_type,
            timestamp=time.time(),
            correlation_id=correlation_id or str(uuid.uuid4()),
            data=data,
        )
        line = event.to_json_line()  # serialise before touching the file
        try:
            with self._path.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")
                fh.flush()
                os.fsync(fh.fileno())
        except OSError as exc:
            raise JournalError(f"could not append to {self._path}: {exc}") from exc
        self._sequence = event.sequence
        return event

    def replay(self) -> Iterator[Event]:
        """Yield every event in write order; strict about corruption."""
        if not self._path.exists():
            return
        with self._path.open("r", encoding="utf-8") as fh:
            for lineno, line in enumerate(fh, start=1):
                if not line.strip():
                    continue
                try:
                    yield Event.from_json_line(line)
                except JournalError as exc:
                    raise JournalError(
                        f"{self._path.name}: corrupt event at line {lineno}: {exc}"
                    ) from exc

    def events_by_correlation(self, correlation_id: str) -> list[Event]:
        """All events sharing one correlation id, in write order."""
        return [e for e in self.replay() if e.correlation_id == correlation_id]


__all__ = ["EventLog"]

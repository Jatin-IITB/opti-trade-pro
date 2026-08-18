"""File-based kill switch for the daily desk cycle.

The switch is deliberately low-tech: engaged if and only if a marker file
(e.g. ``runtime_data/HALT``) exists on disk. That choice buys three
properties a fancier mechanism would have to re-earn:

- **Any process can halt the desk** — the risk engine, a monitoring script,
  or a human with ``touch runtime_data/HALT``. No handle to this object, no
  RPC, no import of this module is required.
- **The halt survives restarts.** State lives outside the process, so a
  crashed-and-relaunched desk stays halted until a human removes the file.
- **Resuming is an explicit, auditable act** (``reset`` / deleting the
  file), never an accidental side effect of a redeploy.

``engage`` records the reason and a UTC timestamp inside the file so the
next operator to find it knows why the desk stopped.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path


class KillSwitch:
    """Halt latch over a marker file; engaged iff the file exists."""

    def __init__(self, path: Path) -> None:
        self._path = path

    @property
    def path(self) -> Path:
        return self._path

    def engage(self, reason: str) -> None:
        """Create the marker file with the reason and a UTC timestamp line."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(tz=UTC).isoformat()
        self._path.write_text(f"engaged {stamp}: {reason}\n", encoding="utf-8")

    def is_engaged(self) -> bool:
        return self._path.exists()

    def reason(self) -> str | None:
        """Contents of the marker file, or ``None`` when not engaged.

        A bare ``touch``-created file returns an empty string: engaged, no
        stated reason.
        """
        if not self._path.exists():
            return None
        return self._path.read_text(encoding="utf-8").strip()

    def reset(self) -> None:
        """Remove the marker file (no-op when not engaged)."""
        self._path.unlink(missing_ok=True)


__all__ = ["KillSwitch"]

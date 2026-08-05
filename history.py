"""Persistent per-satellite usage history.

Lifetime tally of our own link's relationship with each satellite: how many
dwells, how many confirmed 15 s slots, and how much data moved. Stored as
one human-readable JSON file next to this module, rewritten atomically on
each dwell close (a few times per minute at worst).
"""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_PATH = Path(__file__).resolve().parent / "sat_history.json"


class SatHistory:
    def __init__(self, path=DEFAULT_PATH):
        self.path = Path(path)
        self.data = {}
        if self.path.exists():
            try:
                self.data = json.loads(self.path.read_text())
            except ValueError as e:
                # never silently wipe a damaged file; move it aside
                bad = self.path.with_suffix(".corrupt")
                self.path.rename(bad)
                logger.warning("unreadable %s moved to %s: %s",
                               self.path, bad, e)

    def get(self, norad):
        return self.data.get(str(norad))

    def record_dwell(self, norad, name, slots, down_bytes, up_bytes,
                     seconds=0.0, when=None):
        """Fold one closed dwell into the tally; returns the updated entry."""
        e = self.data.setdefault(str(norad), {
            "name": name, "dwells": 0, "slots": 0,
            "down_bytes": 0.0, "up_bytes": 0.0, "seconds": 0.0})
        e.setdefault("seconds", 0.0)   # entries written before this field
        e["name"] = name   # keep current ([DTC] tag can change)
        e["dwells"] += 1
        e["slots"] += slots
        e["down_bytes"] += down_bytes
        e["up_bytes"] += up_bytes
        e["seconds"] += seconds
        e["last_seen"] = (when or datetime.now(timezone.utc)).isoformat(
            timespec="seconds")
        self._save()
        return e

    def _save(self):
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, indent=1, sort_keys=True))
        tmp.replace(self.path)

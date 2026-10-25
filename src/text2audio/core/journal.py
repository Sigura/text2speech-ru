"""Append-only journals in `_work/` that make synthesis resumable (SPEC §6.4.2, FR-10).

Every finished unit of work appends one JSON line and fsyncs it, so killing the job
loses at most the units in flight. A torn last line (the process died mid-write) is
ignored on reading. Later lines for the same key win.
"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any


class Journal:
    def __init__(self, path: Path, key: str = "hash") -> None:
        self.path = path
        self.key = key
        self._lock = threading.Lock()
        self.entries: dict[str, dict[str, Any]] = {}
        if path.is_file():
            for line in path.read_text(encoding="utf-8").splitlines():
                try:
                    entry = json.loads(line)
                except ValueError:
                    continue  # torn write from an interrupted run
                if isinstance(entry, dict) and isinstance(entry.get(key), str):
                    self.entries[entry[key]] = entry

    def get(self, key: str) -> dict[str, Any] | None:
        return self.entries.get(key)

    def __contains__(self, key: str) -> bool:
        return key in self.entries

    def add(self, entry: dict[str, Any]) -> None:
        line = json.dumps(entry, ensure_ascii=False) + "\n"
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as f:
                f.write(line)
                f.flush()
                os.fsync(f.fileno())
            self.entries[entry[self.key]] = entry

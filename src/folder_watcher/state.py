"""The processed-file ledger (SPEC.md section 7.1).

It only answers "have we already taken this exact file?", so duplicate filesystem
events never start a second job. It is never read to find work: it is not a backlog.
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path


def file_key(path: Path) -> str | None:
    """Name + size + modification time. Copying the file again changes the mtime, so a
    re-copied file counts as a new arrival; a duplicate event for the same copy does not."""
    try:
        st = path.stat()
    except OSError:
        return None
    return f"{path.name}|{st.st_size}|{st.st_mtime_ns}"


class Ledger:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.Lock()
        self._status: dict[str, str] = {}
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            for line in path.read_text(encoding="utf-8").splitlines():
                try:
                    entry = json.loads(line)
                    self._status[entry["key"]] = entry["status"]
                except (ValueError, KeyError):
                    continue  # a damaged line must not stop the service

    def claim(self, key: str, file: str) -> bool:
        """Record a file as queued. False if this exact file was already taken."""
        with self._lock:
            if key in self._status:
                return False
            self._write(key, file, "queued")
            return True

    def record(self, key: str, file: str, status: str) -> None:
        with self._lock:
            self._write(key, file, status)

    def status(self, key: str) -> str | None:
        with self._lock:
            return self._status.get(key)

    def _write(self, key: str, file: str, status: str) -> None:
        self._status[key] = status
        entry = {"time": time.strftime("%Y-%m-%dT%H:%M:%S"), "file": file, "key": key, "status": status}
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

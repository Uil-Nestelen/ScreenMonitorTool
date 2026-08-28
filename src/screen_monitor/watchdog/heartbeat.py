"""Heartbeat status file.

The main process periodically writes a small status file. The watchdog
reads it independently and never imports the main application's modules,
so a bug in the main loop cannot take the watchdog down with it.

The write is atomic (write to a temp file, then os.replace) so the
watchdog never reads a half-written file.
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional


@dataclass
class HeartbeatData:
    process_id: int
    last_loop_time: float
    last_frame_time: Optional[float]
    last_status_change: float
    current_state: str
    sequence_number: int


class HeartbeatWriter:
    def __init__(self, status_path: Path) -> None:
        self._status_path = Path(status_path)
        self._status_path.parent.mkdir(parents=True, exist_ok=True)
        self._sequence_number = 0

    def write(
        self,
        current_state: str,
        last_loop_time: float,
        last_frame_time: Optional[float],
        last_status_change: float,
    ) -> None:
        self._sequence_number += 1
        data = HeartbeatData(
            process_id=os.getpid(),
            last_loop_time=last_loop_time,
            last_frame_time=last_frame_time,
            last_status_change=last_status_change,
            current_state=current_state,
            sequence_number=self._sequence_number,
        )

        directory = self._status_path.parent
        fd, tmp_path = tempfile.mkstemp(dir=directory, prefix=".heartbeat_", suffix=".tmp")
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(asdict(data), f)

            # On Windows, os.replace() can raise PermissionError if another
            # process (the watchdog reading the file, antivirus, Windows
            # Search indexing, etc.) transiently holds a lock on the
            # destination. This basically never happens on Linux/macOS.
            # Retry briefly instead of treating it as a hard failure.
            last_error: Optional[OSError] = None
            for attempt in range(5):
                try:
                    os.replace(tmp_path, self._status_path)
                    return
                except PermissionError as exc:
                    last_error = exc
                    time.sleep(0.05 * (attempt + 1))
            raise last_error  # all retries exhausted
        except Exception:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
            raise


class HeartbeatReader:
    def __init__(self, status_path: Path) -> None:
        self._status_path = Path(status_path)

    def read(self) -> Optional[HeartbeatData]:
        if not self._status_path.exists():
            return None
        try:
            with open(self._status_path, "r") as f:
                raw = json.load(f)
            return HeartbeatData(**raw)
        except (json.JSONDecodeError, TypeError, ValueError, OSError):
            # A corrupted or partially-written file is treated as "no data",
            # not as a crash - the watchdog decides what that means.
            return None

    def file_age_seconds(self, now: float) -> Optional[float]:
        if not self._status_path.exists():
            return None
        return now - self._status_path.stat().st_mtime

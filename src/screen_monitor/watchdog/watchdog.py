"""Independent watchdog process.

Run with:

    python -m screen_monitor.watchdog.watchdog [--status-path PATH] [--timeout SECONDS]

This module deliberately imports as little of the main application as
possible (only the heartbeat file format, which is a plain dataclass/JSON
contract, not code). If the main process has a bug elsewhere, the watchdog
must still be able to start and report the failure.

This initial version detects and logs a stale/missing heartbeat. Restart
and escalating-notification behavior (recovery.py, process_monitor.py) are
left for a later milestone, per the plan's "do not build everything at
once" guidance.
"""

from __future__ import annotations

import argparse
import logging
import time
from pathlib import Path

from screen_monitor.watchdog.heartbeat import HeartbeatReader

logger = logging.getLogger("screen_monitor.watchdog")


class Watchdog:
    def __init__(
        self,
        status_path: Path,
        heartbeat_timeout_seconds: float = 10.0,
        poll_interval_seconds: float = 2.0,
    ) -> None:
        self._reader = HeartbeatReader(status_path)
        self._timeout = heartbeat_timeout_seconds
        self._poll_interval = poll_interval_seconds
        self._was_stale = False

    def check_once(self) -> bool:
        """Return True if the main process appears healthy, False otherwise."""
        age = self._reader.file_age_seconds(time.time())

        if age is None:
            if not self._was_stale:
                logger.warning("No heartbeat file found yet - main process may not have started")
            self._was_stale = True
            return False

        if age > self._timeout:
            if not self._was_stale:
                logger.error(
                    "Heartbeat is stale (%.1fs old, limit %.1fs). Main process may be frozen or dead.",
                    age,
                    self._timeout,
                )
            self._was_stale = True
            return False

        data = self._reader.read()
        if data is None:
            logger.warning("Heartbeat file exists but could not be parsed")
            self._was_stale = True
            return False

        if self._was_stale:
            logger.info("Heartbeat recovered (sequence %s)", data.sequence_number)
        self._was_stale = False
        return True

    def run_forever(self) -> None:
        logger.info(
            "Watchdog started (timeout=%.1fs, poll_interval=%.1fs)",
            self._timeout,
            self._poll_interval,
        )
        while True:
            self.check_once()
            time.sleep(self._poll_interval)


def main() -> None:
    parser = argparse.ArgumentParser(description="Screen monitor watchdog")
    parser.add_argument(
        "--status-path",
        default="data/status/heartbeat.json",
        help="Path to the heartbeat status file written by the main process",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=10.0,
        help="Seconds after which a missing heartbeat update is considered stale",
    )
    parser.add_argument(
        "--poll-interval",
        type=float,
        default=2.0,
        help="How often to check the heartbeat file, in seconds",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    watchdog = Watchdog(
        status_path=Path(args.status_path),
        heartbeat_timeout_seconds=args.timeout,
        poll_interval_seconds=args.poll_interval,
    )
    watchdog.run_forever()


if __name__ == "__main__":
    main()

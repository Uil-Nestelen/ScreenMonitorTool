"""Unit tests for the heartbeat/watchdog contract.

Verifies the write side (HeartbeatWriter), the read side (HeartbeatReader),
and that Watchdog.check_once() correctly classifies a fresh vs. stale vs.
missing heartbeat - without needing to spawn a second real process.
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from screen_monitor.watchdog.heartbeat import HeartbeatReader, HeartbeatWriter
from screen_monitor.watchdog.watchdog import Watchdog


def test_heartbeat_round_trip(tmp_path):
    status_path = tmp_path / "heartbeat.json"
    writer = HeartbeatWriter(status_path)
    reader = HeartbeatReader(status_path)

    writer.write(
        current_state="MONITORING",
        last_loop_time=123.0,
        last_frame_time=122.5,
        last_status_change=100.0,
    )

    data = reader.read()
    assert data is not None
    assert data.current_state == "MONITORING"
    assert data.sequence_number == 1


def test_heartbeat_sequence_number_increments(tmp_path):
    status_path = tmp_path / "heartbeat.json"
    writer = HeartbeatWriter(status_path)
    reader = HeartbeatReader(status_path)

    for i in range(3):
        writer.write(
            current_state="MONITORING",
            last_loop_time=float(i),
            last_frame_time=float(i),
            last_status_change=0.0,
        )

    data = reader.read()
    assert data.sequence_number == 3


def test_watchdog_reports_missing_heartbeat_as_unhealthy(tmp_path):
    status_path = tmp_path / "does_not_exist.json"
    watchdog = Watchdog(status_path, heartbeat_timeout_seconds=10.0)

    assert watchdog.check_once() is False


def test_watchdog_reports_fresh_heartbeat_as_healthy(tmp_path):
    status_path = tmp_path / "heartbeat.json"
    writer = HeartbeatWriter(status_path)
    writer.write(
        current_state="MONITORING",
        last_loop_time=time.time(),
        last_frame_time=time.time(),
        last_status_change=time.time(),
    )

    watchdog = Watchdog(status_path, heartbeat_timeout_seconds=10.0)

    assert watchdog.check_once() is True


def test_watchdog_reports_stale_heartbeat_as_unhealthy(tmp_path):
    status_path = tmp_path / "heartbeat.json"
    writer = HeartbeatWriter(status_path)
    writer.write(
        current_state="MONITORING",
        last_loop_time=time.time(),
        last_frame_time=time.time(),
        last_status_change=time.time(),
    )

    # Force the file's mtime into the past to simulate a frozen main process.
    old_time = time.time() - 30
    import os

    os.utime(status_path, (old_time, old_time))

    watchdog = Watchdog(status_path, heartbeat_timeout_seconds=10.0)

    assert watchdog.check_once() is False

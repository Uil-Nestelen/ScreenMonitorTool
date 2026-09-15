"""Unit tests for RegionTimer, using FakeClock for deterministic timing."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from screen_monitor.common.clock import FakeClock
from screen_monitor.monitoring.timer import RegionTimer


def test_never_started_has_zero_elapsed_and_never_reached():
    clock = FakeClock(start=100.0)
    timer = RegionTimer(clock)

    assert timer.elapsed_since(None) == 0.0
    assert timer.has_reached(None, duration_seconds=0.0) is False


def test_elapsed_since_tracks_clock_advancement():
    clock = FakeClock(start=10.0)
    timer = RegionTimer(clock)

    started_at = clock.now()
    clock.advance(5.0)

    assert timer.elapsed_since(started_at) == 5.0


def test_has_reached_is_false_before_duration_and_true_at_or_after():
    clock = FakeClock(start=0.0)
    timer = RegionTimer(clock)
    started_at = clock.now()

    clock.advance(1.9)
    assert timer.has_reached(started_at, duration_seconds=2.0) is False

    clock.advance(0.1)  # now exactly at 2.0
    assert timer.has_reached(started_at, duration_seconds=2.0) is True

    clock.advance(100.0)  # well past
    assert timer.has_reached(started_at, duration_seconds=2.0) is True

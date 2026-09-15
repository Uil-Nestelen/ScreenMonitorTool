"""Unit tests for AlarmManager, using FakeClock and a spy AudioBackend so
alarm playback is deterministic and doesn't actually make noise."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from screen_monitor.alarms.alarm_manager import AlarmManager
from screen_monitor.common.clock import FakeClock
from screen_monitor.monitoring.events import Event, EventType


class SpyAudioBackend:
    def __init__(self):
        self.play_count = 0

    def play(self):
        self.play_count += 1


def make_event(event_type, region_id="r1", at=0.0):
    return Event(type=event_type, region_id=region_id, occurred_at=at, detail="test")


def test_alarm_triggered_plays_immediately():
    clock = FakeClock(start=0.0)
    audio = SpyAudioBackend()
    manager = AlarmManager(clock, audio_backend=audio, repeat_interval_seconds=5.0)

    manager.handle_event(make_event(EventType.REGION_ALARM_TRIGGERED))

    assert audio.play_count == 1
    assert manager.is_alarming("r1") is True


def test_tick_does_not_replay_before_interval():
    clock = FakeClock(start=0.0)
    audio = SpyAudioBackend()
    manager = AlarmManager(clock, audio_backend=audio, repeat_interval_seconds=5.0)
    manager.handle_event(make_event(EventType.REGION_ALARM_TRIGGERED))

    clock.advance(2.0)
    manager.tick()

    assert audio.play_count == 1  # still just the initial play


def test_tick_replays_after_interval_elapses():
    clock = FakeClock(start=0.0)
    audio = SpyAudioBackend()
    manager = AlarmManager(clock, audio_backend=audio, repeat_interval_seconds=5.0)
    manager.handle_event(make_event(EventType.REGION_ALARM_TRIGGERED))

    clock.advance(5.0)
    manager.tick()

    assert audio.play_count == 2


def test_tick_replays_repeatedly_over_multiple_intervals():
    clock = FakeClock(start=0.0)
    audio = SpyAudioBackend()
    manager = AlarmManager(clock, audio_backend=audio, repeat_interval_seconds=5.0)
    manager.handle_event(make_event(EventType.REGION_ALARM_TRIGGERED))

    for _ in range(3):
        clock.advance(5.0)
        manager.tick()

    assert audio.play_count == 4  # 1 initial + 3 repeats


def test_acknowledge_stops_repeats():
    clock = FakeClock(start=0.0)
    audio = SpyAudioBackend()
    manager = AlarmManager(clock, audio_backend=audio, repeat_interval_seconds=5.0)
    manager.handle_event(make_event(EventType.REGION_ALARM_TRIGGERED))

    manager.handle_event(make_event(EventType.ALARM_ACKNOWLEDGED))
    assert manager.is_alarming("r1") is False

    clock.advance(100.0)
    manager.tick()

    assert audio.play_count == 1  # no further plays after acknowledgment


def test_returned_normal_stops_repeats():
    clock = FakeClock(start=0.0)
    audio = SpyAudioBackend()
    manager = AlarmManager(clock, audio_backend=audio, repeat_interval_seconds=5.0)
    manager.handle_event(make_event(EventType.REGION_ALARM_TRIGGERED))

    manager.handle_event(make_event(EventType.REGION_RETURNED_NORMAL))

    clock.advance(100.0)
    manager.tick()

    assert audio.play_count == 1
    assert manager.is_alarming("r1") is False


def test_unrelated_event_types_are_ignored():
    clock = FakeClock(start=0.0)
    audio = SpyAudioBackend()
    manager = AlarmManager(clock, audio_backend=audio, repeat_interval_seconds=5.0)

    manager.handle_event(make_event(EventType.REGION_RED_CONFIRMED))

    assert audio.play_count == 0
    assert manager.is_alarming("r1") is False


def test_multiple_regions_track_independently():
    clock = FakeClock(start=0.0)
    audio = SpyAudioBackend()
    manager = AlarmManager(clock, audio_backend=audio, repeat_interval_seconds=5.0)

    manager.handle_event(make_event(EventType.REGION_ALARM_TRIGGERED, region_id="r1"))
    manager.handle_event(make_event(EventType.REGION_ALARM_TRIGGERED, region_id="r2"))
    assert audio.play_count == 2

    manager.handle_event(make_event(EventType.ALARM_ACKNOWLEDGED, region_id="r1"))

    clock.advance(5.0)
    manager.tick()

    # Only r2 should have repeated (r1 acknowledged, r2 still active).
    assert audio.play_count == 3
    assert manager.is_alarming("r1") is False
    assert manager.is_alarming("r2") is True

"""Integration-ish tests for RegionMonitor: wiring detections through the
state machine and into the (stub) alarm hook."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from screen_monitor.common.clock import FakeClock
from screen_monitor.common.enums import DetectionStatus, RegionStatus
from screen_monitor.detection.detector import DetectionResult
from screen_monitor.detection.region import Region
from screen_monitor.monitoring.events import EventType
from screen_monitor.monitoring.monitor import RegionMonitor


def make_region(**overrides):
    defaults = dict(
        id="r1",
        name="Test Region",
        x=0,
        y=0,
        width=100,
        height=100,
        confirmation_seconds=2.0,
        alarm_seconds=5.0,
    )
    defaults.update(overrides)
    return Region(**defaults)


def make_detection(status, at, region_id="r1"):
    return DetectionResult(
        status=status,
        confidence=1.0,
        red_percentage=0.9,
        evaluated_at=at,
        region_id=region_id,
        reason="test",
    )


def test_alarm_hook_is_invoked_when_alarm_triggers():
    triggered = []
    clock = FakeClock(start=0.0)
    monitor = RegionMonitor([make_region()], clock, alarm_hook=triggered.append)

    # RED -> RED_PENDING (t=0)
    monitor.process([make_detection(DetectionStatus.RED, clock.now())])
    clock.advance(2.0)
    # RED_PENDING -> RED_ACTIVE (confirmed at t=2)
    monitor.process([make_detection(DetectionStatus.RED, clock.now())])
    assert triggered == []  # not alarming yet

    clock.advance(5.0)
    # RED_ACTIVE -> ALARM_ACTIVE (alarm_seconds=5 reached)
    monitor.process([make_detection(DetectionStatus.RED, clock.now())])

    assert len(triggered) == 1
    assert triggered[0].type == EventType.REGION_ALARM_TRIGGERED
    assert monitor.get_state("r1").status == RegionStatus.ALARM_ACTIVE


def test_default_alarm_hook_does_not_raise():
    clock = FakeClock(start=0.0)
    monitor = RegionMonitor([make_region(confirmation_seconds=0.0, alarm_seconds=0.0)], clock)

    # With zero-second thresholds, each state machine step advances one
    # transition per cycle: NORMAL->PENDING, PENDING->RED_ACTIVE (confirmed),
    # RED_ACTIVE->ALARM_ACTIVE. Three RED readings walk through all of them
    # without the default (logging) hook raising.
    monitor.process([make_detection(DetectionStatus.RED, clock.now())])
    monitor.process([make_detection(DetectionStatus.RED, clock.now())])
    events = monitor.process([make_detection(DetectionStatus.RED, clock.now())])

    assert any(e.type == EventType.REGION_ALARM_TRIGGERED for e in events)


def test_acknowledge_via_monitor():
    clock = FakeClock(start=0.0)
    monitor = RegionMonitor(
        [make_region(confirmation_seconds=0.0, alarm_seconds=0.0)], clock
    )

    monitor.process([make_detection(DetectionStatus.RED, clock.now())])
    monitor.process([make_detection(DetectionStatus.RED, clock.now())])
    monitor.process([make_detection(DetectionStatus.RED, clock.now())])
    assert monitor.get_state("r1").status == RegionStatus.ALARM_ACTIVE

    event = monitor.acknowledge("r1")

    assert event is not None
    assert event.type == EventType.ALARM_ACKNOWLEDGED
    assert monitor.get_state("r1").status == RegionStatus.ALARM_ACKNOWLEDGED


def test_process_ignores_detections_for_unconfigured_regions():
    clock = FakeClock(start=0.0)
    monitor = RegionMonitor([make_region(id="r1")], clock)

    # Should not raise, and should not affect r1's state.
    events = monitor.process([make_detection(DetectionStatus.RED, clock.now(), region_id="unknown")])

    assert events == []
    assert monitor.get_state("r1").status == RegionStatus.NORMAL
    assert monitor.get_state("unknown") is None


def test_acknowledge_unconfigured_region_returns_none():
    clock = FakeClock(start=0.0)
    monitor = RegionMonitor([make_region()], clock)

    assert monitor.acknowledge("does-not-exist") is None

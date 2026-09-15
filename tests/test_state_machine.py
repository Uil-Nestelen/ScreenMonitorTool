"""Unit tests for RegionStateMachine.

Uses FakeClock and hand-built DetectionResults (no camera, no real
RedDetector) so every transition - including the UNKNOWN fail-safe
policy - can be exercised deterministically. Per Rule 5 in the plan.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from screen_monitor.common.clock import FakeClock
from screen_monitor.common.enums import DetectionStatus, RegionStatus
from screen_monitor.detection.detector import DetectionResult
from screen_monitor.detection.region import Region
from screen_monitor.monitoring.events import EventType
from screen_monitor.monitoring.region_state import RegionState
from screen_monitor.monitoring.state_machine import RegionStateMachine
from screen_monitor.monitoring.timer import RegionTimer


def make_region(**overrides):
    defaults = dict(
        id="r1",
        name="Test Region",
        x=0,
        y=0,
        width=100,
        height=100,
        confirmation_seconds=2.0,
        alarm_seconds=10.0,
    )
    defaults.update(overrides)
    return Region(**defaults)


def make_detection(status, at, reason="", region_id="r1", confidence=1.0, red_percentage=0.5):
    return DetectionResult(
        status=status,
        confidence=confidence,
        red_percentage=red_percentage,
        evaluated_at=at,
        region_id=region_id,
        reason=reason,
    )


def make_machine(region=None, clock=None):
    region = region or make_region()
    clock = clock or FakeClock(start=0.0)
    return RegionStateMachine(region, RegionTimer(clock)), clock


# -- NORMAL --------------------------------------------------------------


def test_normal_stays_normal_on_normal_reading():
    machine, clock = make_machine()
    state = RegionState(region_id="r1")

    new_state, events = machine.update(state, make_detection(DetectionStatus.NORMAL, clock.now()))

    assert new_state.status == RegionStatus.NORMAL
    assert events == []


def test_normal_does_not_start_timer_on_unknown_reading():
    """An UNKNOWN reading alone must not originate a red condition -
    only an actual RED reading does."""
    machine, clock = make_machine()
    state = RegionState(region_id="r1")

    new_state, events = machine.update(state, make_detection(DetectionStatus.UNKNOWN, clock.now()))

    assert new_state.status == RegionStatus.NORMAL
    assert new_state.red_started_at is None
    assert events == []


def test_normal_moves_to_red_pending_on_red_reading():
    machine, clock = make_machine()
    state = RegionState(region_id="r1")

    new_state, events = machine.update(state, make_detection(DetectionStatus.RED, clock.now()))

    assert new_state.status == RegionStatus.RED_PENDING
    assert new_state.red_started_at == clock.now()
    assert events == []


# -- RED_PENDING -----------------------------------------------------------


def test_red_pending_confirms_after_confirmation_seconds():
    region = make_region(confirmation_seconds=2.0)
    machine, clock = make_machine(region=region)
    state = RegionState(region_id="r1", status=RegionStatus.RED_PENDING, red_started_at=0.0)

    clock.advance(2.0)
    new_state, events = machine.update(state, make_detection(DetectionStatus.RED, clock.now()))

    assert new_state.status == RegionStatus.RED_ACTIVE
    assert new_state.confirmed_at == 2.0
    assert len(events) == 1
    assert events[0].type == EventType.REGION_RED_CONFIRMED


def test_red_pending_stays_pending_before_confirmation_seconds():
    region = make_region(confirmation_seconds=2.0)
    machine, clock = make_machine(region=region)
    state = RegionState(region_id="r1", status=RegionStatus.RED_PENDING, red_started_at=0.0)

    clock.advance(1.0)
    new_state, events = machine.update(state, make_detection(DetectionStatus.RED, clock.now()))

    assert new_state.status == RegionStatus.RED_PENDING
    assert events == []


def test_red_pending_cancels_to_normal_on_normal_reading():
    machine, clock = make_machine()
    state = RegionState(region_id="r1", status=RegionStatus.RED_PENDING, red_started_at=0.0)

    clock.advance(1.0)
    new_state, events = machine.update(state, make_detection(DetectionStatus.NORMAL, clock.now()))

    assert new_state.status == RegionStatus.NORMAL
    assert new_state.red_started_at is None
    # Never confirmed, so no event - nothing alarm-worthy happened.
    assert events == []


def test_red_pending_unknown_reading_still_counts_toward_confirmation():
    """Fail-safe: UNKNOWN continues an already-running pending timer."""
    region = make_region(confirmation_seconds=2.0)
    machine, clock = make_machine(region=region)
    state = RegionState(region_id="r1", status=RegionStatus.RED_PENDING, red_started_at=0.0)

    clock.advance(2.0)
    new_state, events = machine.update(state, make_detection(DetectionStatus.UNKNOWN, clock.now()))

    assert new_state.status == RegionStatus.RED_ACTIVE
    assert len(events) == 1
    assert events[0].type == EventType.REGION_RED_CONFIRMED


# -- RED_ACTIVE --------------------------------------------------------------


def test_red_active_triggers_alarm_after_alarm_seconds():
    region = make_region(alarm_seconds=10.0)
    machine, clock = make_machine(region=region, clock=FakeClock(start=5.0))
    state = RegionState(
        region_id="r1", status=RegionStatus.RED_ACTIVE, red_started_at=0.0, confirmed_at=5.0
    )

    clock.advance(10.0)  # confirmed_at=5.0 -> now=15.0, elapsed=10.0
    new_state, events = machine.update(state, make_detection(DetectionStatus.RED, clock.now()))

    assert new_state.status == RegionStatus.ALARM_ACTIVE
    assert new_state.alarm_triggered is True
    assert len(events) == 1
    assert events[0].type == EventType.REGION_ALARM_TRIGGERED


def test_red_active_stays_active_before_alarm_seconds():
    region = make_region(alarm_seconds=10.0)
    machine, clock = make_machine(region=region, clock=FakeClock(start=0.0))
    state = RegionState(
        region_id="r1", status=RegionStatus.RED_ACTIVE, red_started_at=0.0, confirmed_at=0.0
    )

    clock.advance(5.0)
    new_state, events = machine.update(state, make_detection(DetectionStatus.RED, clock.now()))

    assert new_state.status == RegionStatus.RED_ACTIVE
    assert events == []


def test_red_active_returns_to_normal_on_normal_reading():
    machine, clock = make_machine()
    state = RegionState(
        region_id="r1", status=RegionStatus.RED_ACTIVE, red_started_at=0.0, confirmed_at=0.0
    )

    clock.advance(1.0)
    new_state, events = machine.update(state, make_detection(DetectionStatus.NORMAL, clock.now()))

    assert new_state.status == RegionStatus.NORMAL
    assert new_state.confirmed_at is None
    assert len(events) == 1
    assert events[0].type == EventType.REGION_RETURNED_NORMAL


def test_red_active_unknown_reading_still_counts_toward_alarm():
    """Fail-safe: UNKNOWN continues an already-running alarm timer -
    a camera glitch must not delay or hide a real alarm."""
    region = make_region(alarm_seconds=10.0)
    machine, clock = make_machine(region=region, clock=FakeClock(start=0.0))
    state = RegionState(
        region_id="r1", status=RegionStatus.RED_ACTIVE, red_started_at=0.0, confirmed_at=0.0
    )

    clock.advance(10.0)
    new_state, events = machine.update(state, make_detection(DetectionStatus.UNKNOWN, clock.now()))

    assert new_state.status == RegionStatus.ALARM_ACTIVE
    assert len(events) == 1
    assert events[0].type == EventType.REGION_ALARM_TRIGGERED


# -- ALARM_ACTIVE --------------------------------------------------------


def test_alarm_active_does_not_clear_on_normal_reading():
    """Safety gate: an unacknowledged alarm cannot clear itself just
    because the detector currently reads NORMAL."""
    machine, clock = make_machine()
    state = RegionState(region_id="r1", status=RegionStatus.ALARM_ACTIVE, alarm_triggered=True)

    new_state, events = machine.update(state, make_detection(DetectionStatus.NORMAL, clock.now()))

    assert new_state.status == RegionStatus.ALARM_ACTIVE
    assert events == []


def test_alarm_active_does_not_clear_on_unknown_reading():
    machine, clock = make_machine()
    state = RegionState(region_id="r1", status=RegionStatus.ALARM_ACTIVE, alarm_triggered=True)

    new_state, events = machine.update(state, make_detection(DetectionStatus.UNKNOWN, clock.now()))

    assert new_state.status == RegionStatus.ALARM_ACTIVE
    assert events == []


def test_alarm_active_stays_active_on_continued_red():
    machine, clock = make_machine()
    state = RegionState(region_id="r1", status=RegionStatus.ALARM_ACTIVE, alarm_triggered=True)

    new_state, events = machine.update(state, make_detection(DetectionStatus.RED, clock.now()))

    assert new_state.status == RegionStatus.ALARM_ACTIVE
    assert events == []


# -- acknowledge() ---------------------------------------------------------


def test_acknowledge_moves_alarm_active_to_acknowledged():
    machine, clock = make_machine()
    state = RegionState(region_id="r1", status=RegionStatus.ALARM_ACTIVE, alarm_triggered=True)

    new_state, event = machine.acknowledge(state, clock.now())

    assert new_state.status == RegionStatus.ALARM_ACKNOWLEDGED
    assert new_state.acknowledged is True
    assert event is not None
    assert event.type == EventType.ALARM_ACKNOWLEDGED


def test_acknowledge_is_a_no_op_outside_alarm_active():
    machine, clock = make_machine()
    state = RegionState(region_id="r1", status=RegionStatus.RED_ACTIVE)

    new_state, event = machine.acknowledge(state, clock.now())

    assert new_state.status == RegionStatus.RED_ACTIVE
    assert event is None


# -- ALARM_ACKNOWLEDGED ----------------------------------------------------


def test_acknowledged_returns_to_normal_on_normal_reading():
    machine, clock = make_machine()
    state = RegionState(
        region_id="r1",
        status=RegionStatus.ALARM_ACKNOWLEDGED,
        alarm_triggered=True,
        acknowledged=True,
        red_started_at=0.0,
        confirmed_at=0.0,
    )

    new_state, events = machine.update(state, make_detection(DetectionStatus.NORMAL, clock.now()))

    assert new_state.status == RegionStatus.NORMAL
    assert new_state.alarm_triggered is False
    assert new_state.acknowledged is False
    assert new_state.red_started_at is None
    assert new_state.confirmed_at is None
    assert len(events) == 1
    assert events[0].type == EventType.REGION_RETURNED_NORMAL


def test_acknowledged_stays_acknowledged_on_continued_red_or_unknown():
    machine, clock = make_machine()
    state = RegionState(
        region_id="r1", status=RegionStatus.ALARM_ACKNOWLEDGED, alarm_triggered=True, acknowledged=True
    )

    new_state, events = machine.update(state, make_detection(DetectionStatus.RED, clock.now()))
    assert new_state.status == RegionStatus.ALARM_ACKNOWLEDGED
    assert events == []

    new_state, events = machine.update(state, make_detection(DetectionStatus.UNKNOWN, clock.now()))
    assert new_state.status == RegionStatus.ALARM_ACKNOWLEDGED
    assert events == []


# -- misc --------------------------------------------------------------


def test_mismatched_region_id_raises():
    machine, clock = make_machine()
    state = RegionState(region_id="r1")

    try:
        machine.update(state, make_detection(DetectionStatus.RED, clock.now(), region_id="other"))
        assert False, "expected ValueError"
    except ValueError:
        pass

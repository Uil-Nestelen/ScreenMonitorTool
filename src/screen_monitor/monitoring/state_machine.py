"""Region state machine (plan sections 4.4 and 12, Rule 4).

Valid transitions, per the plan:

    NORMAL
      v confirmed red
    RED_PENDING
      v red remains confirmed
    RED_ACTIVE
      v threshold reached
    ALARM_ACTIVE
      v user acknowledges
    ALARM_ACKNOWLEDGED
      v region returns normal
    NORMAL

Fail-safe policy for UNKNOWN (agreed for this milestone): once a red
condition is already in progress, an UNKNOWN reading (camera glitch,
temporary image-quality fault) is treated the same as RED - it keeps a
running confirmation/alarm timer moving and never pauses, resets, or
clears it. This is a deliberate extension of Rule 4 ("UNKNOWN must never
silently become NORMAL") to region timers and alarms: a flaky camera
must not be usable, even accidentally, to hide or delay a real alarm.

UNKNOWN behaves differently when nothing is in progress yet: from
NORMAL, an UNKNOWN reading alone does not *start* a red timer - only an
actual RED reading does. UNKNOWN only ever continues an existing red
condition, never originates one.

ALARM_ACTIVE only ever leaves via `acknowledge()`, regardless of what
the detector reads afterwards (RED, NORMAL, or UNKNOWN) - an
unacknowledged alarm must not be able to clear itself just because the
red condition or the camera view briefly changes.
"""

from __future__ import annotations

from dataclasses import replace
from typing import List, Tuple, TYPE_CHECKING

from screen_monitor.common.enums import DetectionStatus, RegionStatus
from screen_monitor.monitoring.events import Event, EventType
from screen_monitor.monitoring.region_state import RegionState
from screen_monitor.monitoring.timer import RegionTimer

if TYPE_CHECKING:
    from screen_monitor.detection.detector import DetectionResult
    from screen_monitor.detection.region import Region

# Readings that continue an already-running red condition without
# resetting or pausing it. RED obviously; UNKNOWN per the fail-safe
# policy above (but only once a red condition is already in progress -
# see _from_normal).
_CONTINUES_RED = (DetectionStatus.RED, DetectionStatus.UNKNOWN)


class RegionStateMachine:
    """Applies one detection result at a time to a single region's state."""

    def __init__(self, region: "Region", timer: RegionTimer) -> None:
        self._region = region
        self._timer = timer

    def update(
        self, state: RegionState, detection: "DetectionResult"
    ) -> Tuple[RegionState, List[Event]]:
        if state.region_id != detection.region_id:
            raise ValueError(
                f"Region id mismatch: state is for '{state.region_id}', "
                f"detection is for '{detection.region_id}'"
            )

        handler = {
            RegionStatus.NORMAL: self._from_normal,
            RegionStatus.RED_PENDING: self._from_red_pending,
            RegionStatus.RED_ACTIVE: self._from_red_active,
            RegionStatus.ALARM_ACTIVE: self._from_alarm_active,
            RegionStatus.ALARM_ACKNOWLEDGED: self._from_alarm_acknowledged,
        }[state.status]

        new_state, events = handler(state, detection)
        new_state = replace(
            new_state,
            confidence=detection.confidence,
            last_detection_at=detection.evaluated_at,
            last_reason=detection.reason,
        )
        return new_state, events

    def acknowledge(self, state: RegionState, now: float) -> Tuple[RegionState, "Event | None"]:
        """Acknowledge an active alarm. No-op (with a returned None event)
        if the region isn't currently alarming - acknowledgment only makes
        sense from ALARM_ACTIVE.
        """
        if state.status != RegionStatus.ALARM_ACTIVE:
            return state, None

        new_state = replace(state, status=RegionStatus.ALARM_ACKNOWLEDGED, acknowledged=True)
        event = Event(
            type=EventType.ALARM_ACKNOWLEDGED,
            region_id=state.region_id,
            occurred_at=now,
            detail=f"Alarm for region '{state.region_id}' acknowledged",
        )
        return new_state, event

    # -- per-current-state handlers -----------------------------------

    def _from_normal(
        self, state: RegionState, detection: "DetectionResult"
    ) -> Tuple[RegionState, List[Event]]:
        if detection.status == DetectionStatus.RED:
            new_state = replace(
                state,
                status=RegionStatus.RED_PENDING,
                red_started_at=detection.evaluated_at,
            )
            return new_state, []
        # NORMAL or UNKNOWN: nothing in progress yet, so UNKNOWN cannot
        # originate a red timer - stay NORMAL.
        return replace(state, status=RegionStatus.NORMAL), []

    def _from_red_pending(
        self, state: RegionState, detection: "DetectionResult"
    ) -> Tuple[RegionState, List[Event]]:
        if detection.status not in _CONTINUES_RED:
            # Went back to NORMAL before confirmation - cancel. Nothing
            # was ever confirmed, so no REGION_RETURNED_NORMAL event.
            new_state = replace(
                state,
                status=RegionStatus.NORMAL,
                red_started_at=None,
                confirmed_at=None,
            )
            return new_state, []

        if self._timer.has_reached(state.red_started_at, self._region.confirmation_seconds):
            new_state = replace(
                state,
                status=RegionStatus.RED_ACTIVE,
                confirmed_at=detection.evaluated_at,
            )
            event = Event(
                type=EventType.REGION_RED_CONFIRMED,
                region_id=state.region_id,
                occurred_at=detection.evaluated_at,
                detail=detection.reason,
            )
            return new_state, [event]

        return replace(state, status=RegionStatus.RED_PENDING), []

    def _from_red_active(
        self, state: RegionState, detection: "DetectionResult"
    ) -> Tuple[RegionState, List[Event]]:
        if detection.status not in _CONTINUES_RED:
            new_state = replace(
                state,
                status=RegionStatus.NORMAL,
                red_started_at=None,
                confirmed_at=None,
            )
            event = Event(
                type=EventType.REGION_RETURNED_NORMAL,
                region_id=state.region_id,
                occurred_at=detection.evaluated_at,
                detail="Region returned to normal before an alarm was triggered",
            )
            return new_state, [event]

        if self._timer.has_reached(state.confirmed_at, self._region.alarm_seconds):
            new_state = replace(
                state,
                status=RegionStatus.ALARM_ACTIVE,
                alarm_triggered=True,
            )
            event = Event(
                type=EventType.REGION_ALARM_TRIGGERED,
                region_id=state.region_id,
                occurred_at=detection.evaluated_at,
                detail=(
                    f"Region '{state.region_id}' has been continuously red "
                    f"for at least {self._region.alarm_seconds:.0f}s"
                ),
            )
            return new_state, [event]

        return replace(state, status=RegionStatus.RED_ACTIVE), []

    def _from_alarm_active(
        self, state: RegionState, detection: "DetectionResult"
    ) -> Tuple[RegionState, List[Event]]:
        # Safety gate: stays ALARM_ACTIVE no matter what the detector
        # reads next. Only acknowledge() can move it forward.
        return replace(state, status=RegionStatus.ALARM_ACTIVE), []

    def _from_alarm_acknowledged(
        self, state: RegionState, detection: "DetectionResult"
    ) -> Tuple[RegionState, List[Event]]:
        if detection.status == DetectionStatus.NORMAL:
            new_state = replace(
                state,
                status=RegionStatus.NORMAL,
                red_started_at=None,
                confirmed_at=None,
                alarm_triggered=False,
                acknowledged=False,
            )
            event = Event(
                type=EventType.REGION_RETURNED_NORMAL,
                region_id=state.region_id,
                occurred_at=detection.evaluated_at,
                detail="Acknowledged region returned to normal",
            )
            return new_state, [event]

        # RED or UNKNOWN: fail-safe - stays acknowledged. It's already
        # been acted on by a human; re-alarming on every still-red cycle
        # would be noise, and UNKNOWN must not be able to clear it.
        return replace(state, status=RegionStatus.ALARM_ACKNOWLEDGED), []

"""Coordinates detection results into region state + timers + events.

Per Rule 1 in the plan: `RegionMonitor` decides "what does this mean over
time?" (confirm red, trigger alarm) - it does not decide "what should be
activated?" That's `alarms/alarm_manager.py`'s job, a later milestone.
For now, `RegionMonitor` calls an injected `alarm_hook` callback when a
REGION_ALARM_TRIGGERED event fires. This is a placeholder seam (Rule 5:
dependency injection / test doubles) that `alarm_manager.py` will
eventually replace or sit behind - it lets alarm-worthy events be acted
on now without this milestone needing to implement real alarm output
(local_audio.py, escalation.py) itself.
"""

from __future__ import annotations

import logging
from typing import Callable, Dict, List, Optional, Sequence

from screen_monitor.common.clock import Clock
from screen_monitor.common.enums import RegionStatus
from screen_monitor.detection.detector import DetectionResult
from screen_monitor.detection.region import Region
from screen_monitor.monitoring.events import Event, EventType
from screen_monitor.monitoring.region_state import RegionState
from screen_monitor.monitoring.state_machine import RegionStateMachine
from screen_monitor.monitoring.timer import RegionTimer

logger = logging.getLogger(__name__)

AlarmHook = Callable[[Event], None]


def default_alarm_hook(event: Event) -> None:
    """Placeholder for `alarms/alarm_manager.py` (a later milestone).

    Logs loudly at WARNING so an alarm-worthy event is visible without
    having to grep through per-cycle INFO detection logs. Replace by
    passing a real `alarm_hook` into `RegionMonitor` once local audio /
    escalation exists.
    """
    logger.warning(
        "ALARM: region '%s' - %s",
        event.region_id,
        event.detail,
    )


class RegionMonitor:
    """Coordinates the monitoring cycle described in plan section 4.4:

        analyze regions -> update region states -> update timers ->
        generate events -> send events to alarms

    (Notifications are still a later milestone.)
    """

    def __init__(
        self,
        regions: Sequence[Region],
        clock: Clock,
        alarm_hook: Optional[AlarmHook] = None,
    ) -> None:
        timer = RegionTimer(clock)
        self._timer = timer
        self._clock = clock
        self._state_machines: Dict[str, RegionStateMachine] = {
            region.id: RegionStateMachine(region, timer) for region in regions
        }
        self._states: Dict[str, RegionState] = {
            region.id: RegionState(region_id=region.id) for region in regions
        }
        self._alarm_hook = alarm_hook or default_alarm_hook

    def process(self, detections: Sequence[DetectionResult]) -> List[Event]:
        """Apply one cycle's detection results and return the events raised."""
        all_events: List[Event] = []

        for detection in detections:
            machine = self._state_machines.get(detection.region_id)
            if machine is None:
                logger.warning(
                    "Ignoring detection for unconfigured region '%s'",
                    detection.region_id,
                )
                continue

            state = self._states[detection.region_id]
            new_state, events = machine.update(state, detection)
            self._states[detection.region_id] = new_state

            for event in events:
                logger.info(
                    "Event: %s (region='%s') - %s",
                    event.type.value,
                    event.region_id,
                    event.detail,
                )
                if event.type == EventType.REGION_ALARM_TRIGGERED:
                    self._alarm_hook(event)

            all_events.extend(events)

        return all_events

    def acknowledge(self, region_id: str) -> Optional[Event]:
        """Acknowledge an active alarm for a region. Returns the resulting
        event, or None if the region is unknown or isn't currently
        alarming (acknowledgment only applies to ALARM_ACTIVE).
        """
        machine = self._state_machines.get(region_id)
        if machine is None:
            logger.warning("Cannot acknowledge unconfigured region '%s'", region_id)
            return None

        state = self._states[region_id]
        new_state, event = machine.acknowledge(state, self._clock.now())
        self._states[region_id] = new_state

        if event is not None:
            logger.info("Event: %s (region='%s')", event.type.value, event.region_id)

        return event

    def replace_region(self, region: Region) -> bool:
        """Swap in an updated definition (e.g. a redrawn box) for an
        already-configured region, resetting its state to NORMAL.

        Refused (returns False) if the region is unknown or currently
        ALARM_ACTIVE: an unacknowledged alarm must never be able to
        disappear as a side effect of editing, per the state machine's
        safety gate. Acknowledge it first.
        """
        state = self._states.get(region.id)
        if state is None:
            logger.warning("Cannot replace unconfigured region '%s'", region.id)
            return False
        if state.status == RegionStatus.ALARM_ACTIVE:
            logger.warning(
                "Refusing to replace region '%s' while its alarm is active", region.id
            )
            return False

        self._state_machines[region.id] = RegionStateMachine(region, self._timer)
        self._states[region.id] = RegionState(region_id=region.id)
        logger.info("Region '%s' updated - state reset to NORMAL", region.id)
        return True

    def add_region(self, region: Region) -> bool:
        """Start monitoring a new region. Returns False if the id is taken."""
        if region.id in self._states:
            logger.warning("Cannot add region '%s': id already in use", region.id)
            return False
        self._state_machines[region.id] = RegionStateMachine(region, self._timer)
        self._states[region.id] = RegionState(region_id=region.id)
        logger.info("Region '%s' added", region.id)
        return True

    def get_state(self, region_id: str) -> Optional[RegionState]:
        return self._states.get(region_id)

    def all_states(self) -> Dict[str, RegionState]:
        return dict(self._states)

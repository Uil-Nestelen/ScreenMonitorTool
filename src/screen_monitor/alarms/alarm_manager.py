"""Turns monitoring events into repeated local noise (plan section 10,
the "local alarm" step after the region state machine).

Per Rule 1: `RegionStateMachine`/`RegionMonitor` decide *whether* a
region is alarming - they know nothing about sound. `AlarmManager` only
decides *how often to make noise* while a region is `ALARM_ACTIVE`, and
`AudioBackend` only knows how to make one noise. This is the real
replacement for `RegionMonitor`'s `default_alarm_hook` stub, though it
plugs in alongside it rather than through it - see main.py for why.

Per Rule 5, all repeat-interval timing goes through the injected `Clock`,
so this is deterministically testable with `FakeClock` exactly like the
region state machine's timers.
"""

from __future__ import annotations

import logging
from typing import Dict, Optional, Set

from screen_monitor.common.clock import Clock
from screen_monitor.monitoring.events import Event, EventType
from screen_monitor.alarms.local_audio import AudioBackend, default_audio_backend

logger = logging.getLogger(__name__)

DEFAULT_REPEAT_INTERVAL_SECONDS = 5.0


class AlarmManager:
    """Repeats an audio cue for each region that's ALARM_ACTIVE, on
    `repeat_interval_seconds`, until that region is acknowledged or
    returns to normal.

    Usage: forward every `Event` that `RegionMonitor.process()` /
    `RegionMonitor.acknowledge()` returns into `handle_event()`, and call
    `tick()` once per monitoring loop cycle (independent of whether new
    events came in that cycle - a repeat can be due on a cycle with no
    new detections at all).
    """

    def __init__(
        self,
        clock: Clock,
        audio_backend: Optional[AudioBackend] = None,
        repeat_interval_seconds: float = DEFAULT_REPEAT_INTERVAL_SECONDS,
    ) -> None:
        self._clock = clock
        self._audio = audio_backend or default_audio_backend()
        logger.info("Alarm audio backend: %s", type(self._audio).__name__)
        self._repeat_interval = repeat_interval_seconds
        self._active_regions: Set[str] = set()
        self._last_played_at: Dict[str, float] = {}

    def handle_event(self, event: Event) -> None:
        """Feed in one event from RegionMonitor. Starts, stops, or ignores
        repeating noise for that event's region accordingly.
        """
        if event.region_id is None:
            return

        if event.type == EventType.REGION_ALARM_TRIGGERED:
            self._active_regions.add(event.region_id)
            self._play(event.region_id)
        elif event.type in (EventType.ALARM_ACKNOWLEDGED, EventType.REGION_RETURNED_NORMAL):
            # REGION_RETURNED_NORMAL is included defensively: an alarm
            # should only ever clear via acknowledgment per the state
            # machine's safety gate, but stopping the noise here too
            # means a bug or a future relaxed policy can't leave a
            # region beeping forever with nothing left to acknowledge.
            self._active_regions.discard(event.region_id)
            self._last_played_at.pop(event.region_id, None)

    def tick(self) -> None:
        """Call once per monitoring loop cycle. Re-plays the alarm cue for
        any region still active and due for a repeat.
        """
        now = self._clock.now()
        for region_id in list(self._active_regions):
            last_played_at = self._last_played_at.get(region_id)
            if last_played_at is None or (now - last_played_at) >= self._repeat_interval:
                self._play(region_id)

    def is_alarming(self, region_id: str) -> bool:
        return region_id in self._active_regions

    def _play(self, region_id: str) -> None:
        self._last_played_at[region_id] = self._clock.now()
        logger.info("Playing alarm sound for region '%s'", region_id)
        self._audio.play()

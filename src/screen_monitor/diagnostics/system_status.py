"""System status snapshot.

A scaled-down version of the SystemStatus described in section 4.9 of the
plan, covering camera + frame health + stream health + watchdog. Fields
for detection/alarm/notification status are added as those subsystems
warrant surfacing them here too.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Set

from screen_monitor.common.enums import FrameHealthReason, HealthStatus, SystemState


# Below this (but above zero) a stream that is otherwise healthy is
# reported as a "low frame rate" fault. The main loop runs at ~5 fps by
# default, so a healthy stream sits well above this.
LOW_FPS_THRESHOLD = 1.0

FAULT_WATCHDOG = "watchdog"
FAULT_STREAM = "stream"
FAULT_FPS = "fps"


@dataclass
class SystemStatus:
    overall_state: SystemState
    camera_connected: bool
    camera_status: HealthStatus
    last_frame_reason: FrameHealthReason
    last_frame_age_seconds: Optional[float]
    watchdog_visible: bool
    stream_frame_rate: float = 0.0
    stream_frozen: bool = False
    stream_timed_out: bool = False

    def active_faults(self) -> Set[str]:
        """Which system-level faults are currently active.

        Shared by the Application (to know when an acknowledged fault has
        cleared) and the dashboard view model (to know what to display),
        so there is exactly one definition of "the stream is faulted".
        """
        faults: Set[str] = set()

        if not self.watchdog_visible:
            faults.add(FAULT_WATCHDOG)

        stream_fault = (
            not self.camera_connected
            or self.stream_frozen
            or self.stream_timed_out
            or self.last_frame_age_seconds is None
        )
        if stream_fault:
            faults.add(FAULT_STREAM)
        elif 0.0 < self.stream_frame_rate < LOW_FPS_THRESHOLD:
            faults.add(FAULT_FPS)

        return faults


def build_status(
    overall_state: SystemState,
    camera_connected: bool,
    last_frame_reason: FrameHealthReason,
    last_frame_age_seconds: Optional[float],
    watchdog_visible: bool,
    stream_frame_rate: float = 0.0,
    stream_frozen: bool = False,
    stream_timed_out: bool = False,
) -> SystemStatus:
    if not camera_connected:
        camera_status = HealthStatus.FAULT
    elif stream_frozen or stream_timed_out:
        # A frozen or timed-out stream is a real fault even though the
        # individual frames themselves may still pass frame_health.py's
        # per-frame checks (a stuck driver can keep returning a
        # perfectly well-formed, non-stale, identical frame).
        camera_status = HealthStatus.FAULT
    elif last_frame_reason == FrameHealthReason.VALID:
        camera_status = HealthStatus.OK
    elif last_frame_reason in (
        FrameHealthReason.STALE,
        FrameHealthReason.INVALID_DIMENSIONS,
    ):
        camera_status = HealthStatus.DEGRADED
    else:
        camera_status = HealthStatus.UNKNOWN

    return SystemStatus(
        overall_state=overall_state,
        camera_connected=camera_connected,
        camera_status=camera_status,
        last_frame_reason=last_frame_reason,
        last_frame_age_seconds=last_frame_age_seconds,
        watchdog_visible=watchdog_visible,
        stream_frame_rate=stream_frame_rate,
        stream_frozen=stream_frozen,
        stream_timed_out=stream_timed_out,
    )
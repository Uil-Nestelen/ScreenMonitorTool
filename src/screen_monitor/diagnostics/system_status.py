"""System status snapshot.

A scaled-down version of the SystemStatus described in section 4.9 of the
plan, covering only what the initial pipeline (camera + frame health +
watchdog) can actually report. Fields for detection/alarm/notification
status are added once those subsystems exist.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from screen_monitor.common.enums import FrameHealthReason, HealthStatus, SystemState


@dataclass
class SystemStatus:
    overall_state: SystemState
    camera_connected: bool
    camera_status: HealthStatus
    last_frame_reason: FrameHealthReason
    last_frame_age_seconds: Optional[float]
    watchdog_visible: bool


def build_status(
    overall_state: SystemState,
    camera_connected: bool,
    last_frame_reason: FrameHealthReason,
    last_frame_age_seconds: Optional[float],
    watchdog_visible: bool,
) -> SystemStatus:
    if not camera_connected:
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
    )

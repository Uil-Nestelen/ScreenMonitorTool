"""System status snapshot.

A scaled-down version of the SystemStatus described in section 4.9 of the
plan, covering camera + frame health + stream health + watchdog. Fields
for detection/alarm/notification status are added as those subsystems
warrant surfacing them here too.
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
    stream_frame_rate: float = 0.0
    stream_frozen: bool = False
    stream_timed_out: bool = False


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
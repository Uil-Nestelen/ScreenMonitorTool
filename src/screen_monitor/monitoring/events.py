"""Structured monitoring events (plan section 4.4 / Rule 3).

Explicit events make logging, testing, and future notification routing
easier than inferring "what happened" from raw state diffs. Only the
region-level event types are used by this milestone; the remaining ones
from the plan's list are declared now so `monitoring/health.py`, the
watchdog, and `notifications/` can reuse this same `Event` shape without
inventing a second event system later.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional


class EventType(Enum):
    # Region-level events - produced by this milestone.
    REGION_RED_CONFIRMED = "REGION_RED_CONFIRMED"
    REGION_RETURNED_NORMAL = "REGION_RETURNED_NORMAL"
    REGION_ALARM_TRIGGERED = "REGION_ALARM_TRIGGERED"
    ALARM_ACKNOWLEDGED = "ALARM_ACKNOWLEDGED"

    # Reserved for later milestones (stream/watchdog/notification
    # integration) - declared here so all events share one shape.
    FRAME_TIMEOUT = "FRAME_TIMEOUT"
    STREAM_FROZEN = "STREAM_FROZEN"
    CAMERA_DISCONNECTED = "CAMERA_DISCONNECTED"
    NOTIFICATION_FAILED = "NOTIFICATION_FAILED"
    WATCHDOG_HEARTBEAT_EXPIRED = "WATCHDOG_HEARTBEAT_EXPIRED"


@dataclass(frozen=True)
class Event:
    type: EventType
    region_id: Optional[str]
    occurred_at: float
    detail: str = ""

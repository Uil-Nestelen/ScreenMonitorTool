"""Shared state enums.

Rule 4 from the project plan: treat UNKNOWN as unsafe. UNKNOWN must never
silently become NORMAL/READY anywhere in the codebase.
"""

from enum import Enum


class SystemState(Enum):
    STARTING = "STARTING"
    SELF_TESTING = "SELF_TESTING"
    READY = "READY"
    MONITORING = "MONITORING"
    SYSTEM_FAULT = "SYSTEM_FAULT"
    SHUTTING_DOWN = "SHUTTING_DOWN"
    STOPPED = "STOPPED"


class HealthStatus(Enum):
    OK = "OK"
    DEGRADED = "DEGRADED"
    FAULT = "FAULT"
    UNKNOWN = "UNKNOWN"


class FrameHealthReason(Enum):
    VALID = "VALID"
    MISSING = "MISSING"
    INVALID_DIMENSIONS = "INVALID_DIMENSIONS"
    EMPTY = "EMPTY"
    CORRUPTED = "CORRUPTED"
    STALE = "STALE"


class DetectionStatus(Enum):
    """Rule 4: UNKNOWN must never silently become NORMAL."""

    RED = "RED"
    NORMAL = "NORMAL"
    UNKNOWN = "UNKNOWN"


class ImageQuality(Enum):
    GOOD = "GOOD"
    TOO_DARK = "TOO_DARK"
    OVEREXPOSED = "OVEREXPOSED"
    LOW_CONTRAST = "LOW_CONTRAST"
    UNKNOWN = "UNKNOWN"

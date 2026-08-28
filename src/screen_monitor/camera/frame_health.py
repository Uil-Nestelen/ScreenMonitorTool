"""Frame health validation.

Checks whether a frame is usable at all, before any detection logic ever
sees it. This module never decides anything about red/normal state - it
only answers "is this frame trustworthy?".
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from screen_monitor.camera.frame import Frame
from screen_monitor.common.clock import Clock, RealClock
from screen_monitor.common.enums import FrameHealthReason


@dataclass
class FrameHealthResult:
    is_valid: bool
    reason: FrameHealthReason
    detail: str = ""


class FrameHealthValidator:
    def __init__(
        self,
        expected_min_width: int = 1,
        expected_min_height: int = 1,
        max_age_seconds: float = 3.0,
        clock: Optional[Clock] = None,
    ) -> None:
        self._min_width = expected_min_width
        self._min_height = expected_min_height
        self._max_age_seconds = max_age_seconds
        self._clock = clock or RealClock()

    def validate(self, frame: Optional[Frame]) -> FrameHealthResult:
        if frame is None or frame.image is None:
            return FrameHealthResult(False, FrameHealthReason.MISSING, "No frame received")

        if frame.width < self._min_width or frame.height < self._min_height:
            return FrameHealthResult(
                False,
                FrameHealthReason.INVALID_DIMENSIONS,
                f"Frame dimensions {frame.width}x{frame.height} are too small",
            )

        try:
            if frame.image.size == 0:
                return FrameHealthResult(False, FrameHealthReason.EMPTY, "Frame has no data")
        except AttributeError:
            return FrameHealthResult(
                False, FrameHealthReason.CORRUPTED, "Frame image object is not array-like"
            )

        age = self._clock.now() - frame.received_at
        if age > self._max_age_seconds:
            return FrameHealthResult(
                False,
                FrameHealthReason.STALE,
                f"Frame is {age:.2f}s old, exceeds {self._max_age_seconds}s limit",
            )

        return FrameHealthResult(True, FrameHealthReason.VALID)

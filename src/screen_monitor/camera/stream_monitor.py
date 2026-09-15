"""Stream-level health tracking across multiple frames (plan section
4.2, step 4 of the growth order).

Where `frame_health.py` asks "is this one frame trustworthy?", this asks
"is the stream *behaving* like a live camera over time?" - frame rate,
whether the picture is actually changing (frozen-stream detection), and
whether frames have stopped arriving at all (timeout).

Per Rule 1: this module doesn't decide anything about red/alarm state,
or even about SystemState - it only reports stream health. What the
caller does with a frozen/timed-out reading (log it, fault the system,
alert the watchdog) is main.py's call, deliberately left for a separate
decision rather than baked in here.

Per Rule 5, all timing goes through the injected Clock, so frozen/
timeout detection and frame-rate calculation are deterministically
testable with FakeClock and synthetic frames - no real camera, no real
sleeps.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

import cv2
import numpy as np

from screen_monitor.camera.frame import Frame
from screen_monitor.common.clock import Clock, RealClock

# Frames are downsized to this before comparing, so frozen-stream
# detection is cheap every cycle regardless of the camera's real
# resolution. Small enough that sensor noise on a genuinely live feed
# still shows up as a pixel difference almost every frame, so an exact
# match across consecutive thumbnails is a strong "actually frozen"
# signal rather than a false positive from a very static scene.
_THUMBNAIL_SIZE = (32, 32)


@dataclass
class StreamStatus:
    connected: bool
    frozen: bool
    timed_out: bool
    frame_rate: float  # frames/sec over a rolling window; 0.0 if unknown
    last_valid_frame_age_seconds: Optional[float]
    last_changed_frame_age_seconds: Optional[float]


class StreamMonitor:
    def __init__(
        self,
        frozen_threshold_seconds: float = 5.0,
        timeout_seconds: float = 5.0,
        rate_window_size: int = 30,
        clock: Optional[Clock] = None,
    ) -> None:
        self._frozen_threshold = frozen_threshold_seconds
        self._timeout_threshold = timeout_seconds
        self._rate_window_size = rate_window_size
        self._clock = clock or RealClock()

        self._last_valid_frame_at: Optional[float] = None
        self._last_changed_frame_at: Optional[float] = None
        self._last_thumbnail: Optional[np.ndarray] = None
        self._frame_timestamps: List[float] = []

    def update(self, frame: Optional[Frame], is_valid: bool) -> StreamStatus:
        """Call once per monitoring loop cycle with the frame just read
        and whether frame_health.py considered it valid this cycle.
        """
        now = self._clock.now()

        if is_valid and frame is not None and frame.image is not None:
            self._last_valid_frame_at = now
            self._frame_timestamps.append(now)
            if len(self._frame_timestamps) > self._rate_window_size:
                self._frame_timestamps.pop(0)

            thumbnail = self._make_thumbnail(frame.image)
            if self._last_thumbnail is None or not np.array_equal(thumbnail, self._last_thumbnail):
                self._last_changed_frame_at = now
            self._last_thumbnail = thumbnail

        # "Never connected yet" is deliberately reported as connected=False
        # rather than timed_out=True - Rule 4's spirit applied here too:
        # don't claim a specific failure mode ("timed out") when what's
        # really true is just "nothing has arrived yet, e.g. at startup".
        connected = (
            self._last_valid_frame_at is not None
            and (now - self._last_valid_frame_at) <= self._timeout_threshold
        )
        timed_out = self._last_valid_frame_at is not None and not connected

        frozen = (
            self._last_changed_frame_at is not None
            and (now - self._last_changed_frame_at) > self._frozen_threshold
        )

        return StreamStatus(
            connected=connected,
            frozen=frozen,
            timed_out=timed_out,
            frame_rate=self._frame_rate(),
            last_valid_frame_age_seconds=(
                None if self._last_valid_frame_at is None else now - self._last_valid_frame_at
            ),
            last_changed_frame_age_seconds=(
                None if self._last_changed_frame_at is None else now - self._last_changed_frame_at
            ),
        )

    def _make_thumbnail(self, image: np.ndarray) -> np.ndarray:
        return cv2.resize(image, _THUMBNAIL_SIZE, interpolation=cv2.INTER_NEAREST)

    def _frame_rate(self) -> float:
        if len(self._frame_timestamps) < 2:
            return 0.0
        span = self._frame_timestamps[-1] - self._frame_timestamps[0]
        if span <= 0:
            return 0.0
        return (len(self._frame_timestamps) - 1) / span

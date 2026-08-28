"""Clock abstraction.

Rule 5 from the project plan: the camera, clock, alarm output, and
notification services should be replaceable with test doubles. This module
provides a real clock for production and a fake, manually-advanced clock
for deterministic tests (used later for timers, frame staleness, etc.).
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod


class Clock(ABC):
    """Common interface used anywhere the code needs "now"."""

    @abstractmethod
    def now(self) -> float:
        """Return the current time as a monotonic float, in seconds."""
        raise NotImplementedError


class RealClock(Clock):
    """Wraps time.monotonic() so real timing code has one seam to mock."""

    def now(self) -> float:
        return time.monotonic()


class FakeClock(Clock):
    """A manually advanced clock for deterministic unit tests."""

    def __init__(self, start: float = 0.0) -> None:
        self._current = start

    def now(self) -> float:
        return self._current

    def advance(self, seconds: float) -> None:
        if seconds < 0:
            raise ValueError("Cannot advance a clock backwards")
        self._current += seconds

    def set(self, value: float) -> None:
        self._current = value

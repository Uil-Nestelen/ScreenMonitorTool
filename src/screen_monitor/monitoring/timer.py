"""Elapsed-time helper for red-condition timers (plan section 4.4).

Per the plan: "It should not use scattered calls to time.time() throughout
the application. Use a clock abstraction so timers can be tested
deterministically." All elapsed-time math for the state machine goes
through this one class, wrapping the injected `Clock` (Rule 5).
"""

from __future__ import annotations

from typing import Optional

from screen_monitor.common.clock import Clock


class RegionTimer:
    def __init__(self, clock: Clock) -> None:
        self._clock = clock

    def elapsed_since(self, started_at: Optional[float]) -> float:
        """Seconds elapsed since `started_at`, or 0.0 if it hasn't started."""
        if started_at is None:
            return 0.0
        return self._clock.now() - started_at

    def has_reached(self, started_at: Optional[float], duration_seconds: float) -> bool:
        """True once `duration_seconds` have elapsed since `started_at`.

        A `started_at` of None (timer never started) never reads as
        reached, regardless of `duration_seconds`.
        """
        if started_at is None:
            return False
        return self.elapsed_since(started_at) >= duration_seconds

"""Per-region monitoring state (plan section 4.4).

This is plain data - the transition rules that mutate it live in
`state_machine.py`, per Rule 1 (keep detection/decisions/state separate).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from screen_monitor.common.enums import RegionStatus


@dataclass
class RegionState:
    region_id: str
    status: RegionStatus = RegionStatus.NORMAL
    confidence: float = 0.0
    red_started_at: Optional[float] = None
    confirmed_at: Optional[float] = None
    alarm_triggered: bool = False
    acknowledged: bool = False
    last_detection_at: Optional[float] = None
    last_reason: str = ""

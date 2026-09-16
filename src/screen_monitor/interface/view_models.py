"""Converts Application's internal status objects into display-ready
data for gui.py (plan section 4.10).

Per Rule 1 ("The GUI should ... not ... decide whether an alarm is
active"), all the deciding already happened elsewhere - RegionMonitor's
state machine, AlarmManager, StreamMonitor. This module adds no new
logic of its own; it only reformats their already-computed output into
something a view can render directly, so gui.py itself never has to
interpret a RegionStatus or a SystemStatus field.

Deliberately has no Tkinter (or any UI framework) import, so it's fully
unit-testable without a display.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

from screen_monitor.common.enums import RegionStatus, SystemState
from screen_monitor.diagnostics.system_status import SystemStatus
from screen_monitor.monitoring.region_state import RegionState

# Only these statuses make sense to acknowledge - matches
# RegionStateMachine.acknowledge()'s own no-op-elsewhere behavior, so the
# button is only ever enabled when clicking it would actually do something.
_ACKNOWLEDGEABLE_STATUSES = (RegionStatus.ALARM_ACTIVE,)


@dataclass
class RegionViewModel:
    region_id: str
    status_label: str
    is_alarming: bool
    can_acknowledge: bool
    detail: str


@dataclass
class DashboardViewModel:
    overall_state: str
    camera_connected: bool
    camera_status: str
    stream_frame_rate: float
    stream_frozen: bool
    stream_timed_out: bool
    watchdog_visible: bool
    last_frame_age_seconds: Optional[float]
    regions: List[RegionViewModel] = field(default_factory=list)
    has_active_alarm: bool = False
    fault_message: Optional[str] = None


def build_region_view_model(state: RegionState, is_alarming: bool) -> RegionViewModel:
    return RegionViewModel(
        region_id=state.region_id,
        status_label=state.status.value,
        is_alarming=is_alarming,
        can_acknowledge=state.status in _ACKNOWLEDGEABLE_STATUSES,
        detail=state.last_reason,
    )


def _fault_message(system_status: Optional[SystemStatus]) -> Optional[str]:
    if system_status is None:
        return None
    if not system_status.camera_connected:
        return "Camera disconnected"
    if system_status.stream_frozen:
        return "Stream appears frozen - picture hasn't changed"
    if system_status.stream_timed_out:
        return "Stream timed out - no valid frame received recently"
    return None


def build_dashboard_view_model(
    system_status: Optional[SystemStatus],
    region_states: Dict[str, RegionState],
    is_alarming_fn,
) -> DashboardViewModel:
    """Build the full dashboard view model.

    `is_alarming_fn` is a `Callable[[str], bool]` (e.g.
    `AlarmManager.is_alarming`) rather than the manager itself, so this
    stays testable with a plain function/lambda and doesn't need to know
    anything about AlarmManager beyond that one query.
    """
    regions = []
    has_active_alarm = False
    for region_id, state in region_states.items():
        is_alarming = is_alarming_fn(region_id) if is_alarming_fn is not None else False
        has_active_alarm = has_active_alarm or is_alarming
        regions.append(build_region_view_model(state, is_alarming))

    regions.sort(key=lambda r: r.region_id)

    if system_status is None:
        return DashboardViewModel(
            overall_state=SystemState.STARTING.value,
            camera_connected=False,
            camera_status="UNKNOWN",
            stream_frame_rate=0.0,
            stream_frozen=False,
            stream_timed_out=False,
            watchdog_visible=False,
            last_frame_age_seconds=None,
            regions=regions,
            has_active_alarm=has_active_alarm,
            fault_message=None,
        )

    return DashboardViewModel(
        overall_state=system_status.overall_state.value,
        camera_connected=system_status.camera_connected,
        camera_status=system_status.camera_status.value,
        stream_frame_rate=system_status.stream_frame_rate,
        stream_frozen=system_status.stream_frozen,
        stream_timed_out=system_status.stream_timed_out,
        watchdog_visible=system_status.watchdog_visible,
        last_frame_age_seconds=system_status.last_frame_age_seconds,
        regions=regions,
        has_active_alarm=has_active_alarm,
        fault_message=_fault_message(system_status),
    )

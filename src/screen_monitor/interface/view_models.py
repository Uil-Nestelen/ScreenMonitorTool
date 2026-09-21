"""Converts Application's internal status objects into display-ready
data for gui.py (plan section 4.10).

Per Rule 1 ("The GUI should ... not ... decide whether an alarm is
active"), all the deciding already happened elsewhere - RegionMonitor's
state machine, AlarmManager, StreamMonitor. This module adds no new
logic of its own; it only reformats their already-computed output into
something a view can render directly, so gui.py itself never has to
interpret a RegionStatus, a DetectionStatus or a SystemStatus field.

Deliberately has no Tkinter (or any UI framework) import, so it's fully
unit-testable without a display.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Sequence

from screen_monitor.common.enums import DetectionStatus, RegionStatus, SystemState
from screen_monitor.detection.detector import DetectionResult
from screen_monitor.detection.region import Region
from screen_monitor.diagnostics.system_status import (
    FAULT_FPS,
    FAULT_STREAM,
    FAULT_WATCHDOG,
    SystemStatus,
)
from screen_monitor.monitoring.region_state import RegionState

# Only these statuses make sense to acknowledge - matches
# RegionStateMachine.acknowledge()'s own no-op-elsewhere behavior, so the
# button is only ever enabled when clicking it would actually do something.
_ACKNOWLEDGEABLE_STATUSES = (RegionStatus.ALARM_ACTIVE,)

# A region can't be redrawn while its alarm is unacknowledged - see
# RegionMonitor.replace_region().
_LOCKED_STATUSES = (RegionStatus.ALARM_ACTIVE,)


@dataclass
class RegionViewModel:
    region_id: str
    status_label: str
    is_alarming: bool
    can_acknowledge: bool
    detail: str
    # -- added for the redesigned dashboard (all defaulted so existing
    # positional construction keeps working) --
    name: str = ""
    red_threshold: float = 0.0
    detecting_color: str = "RED"
    can_edit: bool = True
    red_percentage: Optional[float] = None
    reading_unknown: bool = False
    # Coarse display bucket so the view can pick a color without
    # interpreting RegionStatus itself: "ok" | "unknown" | "pending" |
    # "alarm" | "acknowledged".
    severity: str = "ok"

    @property
    def display_name(self) -> str:
        return self.name or self.region_id


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
    # -- top status bar. `*_ok`: True = healthy, False = faulted,
    # None = neutral (e.g. still starting up). --
    watchdog_label: str = "Starting"
    watchdog_ok: Optional[bool] = None
    watchdog_can_acknowledge: bool = False
    stream_label: str = "Starting"
    stream_ok: Optional[bool] = None
    stream_can_acknowledge: bool = False
    fps_text: str = "0fps"
    fps_ok: Optional[bool] = None
    fps_can_acknowledge: bool = False
    alarming_region_names: List[str] = field(default_factory=list)


_SEVERITY_BY_STATUS = {
    RegionStatus.NORMAL: "ok",
    RegionStatus.RED_PENDING: "pending",
    RegionStatus.RED_ACTIVE: "pending",
    RegionStatus.ALARM_ACTIVE: "alarm",
    RegionStatus.ALARM_ACKNOWLEDGED: "acknowledged",
}


def _severity(state: RegionState, reading_unknown: bool) -> str:
    severity = _SEVERITY_BY_STATUS[state.status]
    # An unreadable region that isn't otherwise in trouble is worth
    # flagging (camera blocked, region off-frame, too dark...) rather
    # than showing a reassuring "ok".
    if severity == "ok" and reading_unknown:
        return "unknown"
    return severity


def build_region_view_model(
    state: RegionState,
    is_alarming: bool,
    region: Optional[Region] = None,
    detection: Optional[DetectionResult] = None,
) -> RegionViewModel:
    reading_unknown = detection is not None and detection.status == DetectionStatus.UNKNOWN
    return RegionViewModel(
        region_id=state.region_id,
        status_label=state.status.value,
        is_alarming=is_alarming,
        can_acknowledge=state.status in _ACKNOWLEDGEABLE_STATUSES,
        detail=state.last_reason,
        name=region.name if region is not None else "",
        red_threshold=region.red_percentage_threshold if region is not None else 0.0,
        can_edit=state.status not in _LOCKED_STATUSES,
        red_percentage=detection.red_percentage if detection is not None else None,
        reading_unknown=reading_unknown,
        severity=_severity(state, reading_unknown),
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


def _stream_label(system_status: SystemStatus) -> str:
    if not system_status.camera_connected:
        return "Missing"
    if system_status.stream_frozen:
        return "Frozen"
    if system_status.stream_timed_out:
        return "Timed out"
    if system_status.last_frame_age_seconds is None:
        return "Missing"
    return "Healthy"


def _fps_text(frame_rate: float) -> str:
    if frame_rate <= 0.0:
        return "0fps"
    return f"{frame_rate:.1f}fps"


def _with_ack(label: str, fault_active: bool, acknowledged: bool) -> str:
    return f"{label} (acknowledged)" if fault_active and acknowledged else label


def build_dashboard_view_model(
    system_status: Optional[SystemStatus],
    region_states: Dict[str, RegionState],
    is_alarming_fn,
    regions: Sequence[Region] = (),
    detections: Iterable[DetectionResult] = (),
    acknowledged_faults: Iterable[str] = (),
) -> DashboardViewModel:
    """Build the full dashboard view model.

    `is_alarming_fn` is a `Callable[[str], bool]` (e.g.
    `AlarmManager.is_alarming`) rather than the manager itself, so this
    stays testable with a plain function/lambda and doesn't need to know
    anything about AlarmManager beyond that one query.

    `regions` (the configured Region objects) supplies each row's name,
    threshold and display order; without it rows are sorted by id and
    show the id as their name. `detections` is this cycle's raw
    detector output, used for the live red-percentage readout.
    """
    region_by_id = {r.id: r for r in regions}
    detection_by_id = {d.region_id: d for d in detections}

    region_vms: List[RegionViewModel] = []
    alarming_names: List[str] = []
    has_active_alarm = False
    for region_id, state in region_states.items():
        is_alarming = is_alarming_fn(region_id) if is_alarming_fn is not None else False
        region_vm = build_region_view_model(
            state,
            is_alarming,
            region=region_by_id.get(region_id),
            detection=detection_by_id.get(region_id),
        )
        has_active_alarm = has_active_alarm or is_alarming
        if is_alarming:
            alarming_names.append(region_vm.display_name)
        region_vms.append(region_vm)

    if region_by_id:
        order = {r.id: index for index, r in enumerate(regions)}
        region_vms.sort(key=lambda r: (order.get(r.region_id, len(order)), r.region_id))
    else:
        region_vms.sort(key=lambda r: r.region_id)

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
            regions=region_vms,
            has_active_alarm=has_active_alarm,
            fault_message=None,
            alarming_region_names=alarming_names,
        )

    faults = system_status.active_faults()
    acknowledged = set(acknowledged_faults)

    watchdog_faulted = FAULT_WATCHDOG in faults
    stream_faulted = FAULT_STREAM in faults
    fps_faulted = FAULT_FPS in faults or stream_faulted

    stream_label = _stream_label(system_status)

    return DashboardViewModel(
        overall_state=system_status.overall_state.value,
        camera_connected=system_status.camera_connected,
        camera_status=system_status.camera_status.value,
        stream_frame_rate=system_status.stream_frame_rate,
        stream_frozen=system_status.stream_frozen,
        stream_timed_out=system_status.stream_timed_out,
        watchdog_visible=system_status.watchdog_visible,
        last_frame_age_seconds=system_status.last_frame_age_seconds,
        regions=region_vms,
        has_active_alarm=has_active_alarm,
        fault_message=_fault_message(system_status),
        watchdog_label=_with_ack(
            "Heartbeat Missing" if watchdog_faulted else "Heartbeat Published",
            watchdog_faulted,
            FAULT_WATCHDOG in acknowledged,
        ),
        watchdog_ok=not watchdog_faulted,
        watchdog_can_acknowledge=watchdog_faulted and FAULT_WATCHDOG not in acknowledged,
        stream_label=_with_ack(stream_label, stream_faulted, FAULT_STREAM in acknowledged),
        stream_ok=not stream_faulted,
        stream_can_acknowledge=stream_faulted and FAULT_STREAM not in acknowledged,
        fps_text=_fps_text(system_status.stream_frame_rate),
        fps_ok=not fps_faulted,
        # A dead stream is acknowledged through the Stream Status button;
        # this one is only for the "stream is alive but slow" case.
        fps_can_acknowledge=(FAULT_FPS in faults) and FAULT_FPS not in acknowledged,
        alarming_region_names=alarming_names,
    )

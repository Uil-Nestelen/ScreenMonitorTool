"""Unit tests for the dashboard view models (no display needed)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from screen_monitor.common.enums import (
    DetectionStatus,
    FrameHealthReason,
    HealthStatus,
    RegionStatus,
    SystemState,
)
from screen_monitor.detection.detector import DetectionResult
from screen_monitor.detection.region import Region
from screen_monitor.diagnostics.system_status import SystemStatus
from screen_monitor.interface.view_models import build_dashboard_view_model
from screen_monitor.monitoring.region_state import RegionState


def make_status(**overrides):
    defaults = dict(
        overall_state=SystemState.MONITORING,
        camera_connected=True,
        camera_status=HealthStatus.OK,
        last_frame_reason=FrameHealthReason.VALID,
        last_frame_age_seconds=0.1,
        watchdog_visible=True,
        stream_frame_rate=4.6,
        stream_frozen=False,
        stream_timed_out=False,
    )
    defaults.update(overrides)
    return SystemStatus(**defaults)


def make_region(rid, name=None, **kw):
    return Region(id=rid, name=name or rid, x=0, y=0, width=50, height=50, **kw)


def build(status, states=None, regions=(), detections=(), acked=(), alarming=()):
    states = states or {}
    return build_dashboard_view_model(
        status, states, lambda rid: rid in alarming,
        regions=regions, detections=detections, acknowledged_faults=acked,
    )


def test_healthy_system_matches_normal_mode_mockup():
    vm = build(make_status())
    assert vm.watchdog_label == "Heartbeat Published" and vm.watchdog_ok is True
    assert vm.stream_label == "Healthy" and vm.stream_ok is True
    assert vm.fps_text == "4.6fps" and vm.fps_ok is True
    assert not (vm.watchdog_can_acknowledge or vm.stream_can_acknowledge or vm.fps_can_acknowledge)


def test_failing_system_matches_editor_mode_mockup():
    vm = build(make_status(
        camera_connected=False, camera_status=HealthStatus.FAULT,
        watchdog_visible=False, stream_frame_rate=0.0, last_frame_age_seconds=None,
    ))
    assert vm.watchdog_label == "Heartbeat Missing" and vm.watchdog_ok is False
    assert vm.stream_label == "Missing" and vm.stream_ok is False
    assert vm.fps_text == "0fps" and vm.fps_ok is False
    assert vm.watchdog_can_acknowledge and vm.stream_can_acknowledge
    # a dead stream is acknowledged via Stream Status, not the fps button
    assert not vm.fps_can_acknowledge


def test_frozen_and_timed_out_labels():
    assert build(make_status(stream_frozen=True)).stream_label == "Frozen"
    assert build(make_status(stream_timed_out=True)).stream_label == "Timed out"


def test_acknowledged_fault_disables_button_and_says_so():
    vm = build(make_status(stream_frozen=True), acked={"stream"})
    assert vm.stream_label == "Frozen (acknowledged)"
    assert vm.stream_ok is False  # still a fault, just acknowledged
    assert not vm.stream_can_acknowledge


def test_low_fps_is_its_own_acknowledgeable_fault():
    vm = build(make_status(stream_frame_rate=0.4))
    assert vm.stream_ok is True
    assert vm.fps_ok is False and vm.fps_can_acknowledge


def test_no_status_yet_renders_neutral_starting_state():
    vm = build(None)
    assert vm.overall_state == SystemState.STARTING.value
    assert vm.watchdog_ok is None and vm.stream_ok is None


def test_regions_keep_config_order_not_alphabetical():
    regions = [make_region("region_2"), make_region("region_10"), make_region("region_1")]
    states = {r.id: RegionState(region_id=r.id) for r in regions}
    vm = build(make_status(), states, regions=regions)
    assert [r.region_id for r in vm.regions] == ["region_2", "region_10", "region_1"]


def test_without_config_regions_fall_back_to_id_order():
    states = {rid: RegionState(region_id=rid) for rid in ("b", "a")}
    vm = build(make_status(), states)
    assert [r.region_id for r in vm.regions] == ["a", "b"]


def test_region_row_uses_name_threshold_and_detection():
    region = make_region("z1", name="bigzone", red_percentage_threshold=0.3)
    det = DetectionResult(DetectionStatus.NORMAL, 0.9, 0.12, 0.0, "z1")
    vm = build(make_status(), {"z1": RegionState(region_id="z1")}, regions=[region], detections=[det])
    row = vm.regions[0]
    assert row.display_name == "bigzone"
    assert row.red_threshold == 0.3
    assert row.red_percentage == 0.12
    assert row.severity == "ok" and row.can_edit


def test_unknown_reading_flags_region_but_alarm_wins():
    region = make_region("z1")
    unknown = DetectionResult(DetectionStatus.UNKNOWN, 0.0, 0.0, 0.0, "z1")
    vm = build(make_status(), {"z1": RegionState(region_id="z1")}, regions=[region], detections=[unknown])
    assert vm.regions[0].severity == "unknown"

    alarm_state = RegionState(region_id="z1", status=RegionStatus.ALARM_ACTIVE)
    vm = build(make_status(), {"z1": alarm_state}, regions=[region], detections=[unknown], alarming={"z1"})
    row = vm.regions[0]
    assert row.severity == "alarm"
    assert row.can_acknowledge
    assert not row.can_edit  # can't redraw an unacknowledged alarm
    assert vm.has_active_alarm and vm.alarming_region_names == ["z1"]


def test_severity_by_status():
    region = make_region("z1")
    for status, expected in [
        (RegionStatus.RED_PENDING, "pending"),
        (RegionStatus.RED_ACTIVE, "pending"),
        (RegionStatus.ALARM_ACKNOWLEDGED, "acknowledged"),
    ]:
        vm = build(make_status(), {"z1": RegionState(region_id="z1", status=status)}, regions=[region])
        assert vm.regions[0].severity == expected

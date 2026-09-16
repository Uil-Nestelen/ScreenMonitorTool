"""Unit tests for interface/view_models.py - pure logic, no Tkinter/UI
needed, so these run the same everywhere."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from screen_monitor.common.enums import FrameHealthReason, HealthStatus, RegionStatus, SystemState
from screen_monitor.diagnostics.system_status import SystemStatus
from screen_monitor.interface.view_models import build_dashboard_view_model, build_region_view_model
from screen_monitor.monitoring.region_state import RegionState


def make_system_status(**overrides):
    defaults = dict(
        overall_state=SystemState.MONITORING,
        camera_connected=True,
        camera_status=HealthStatus.OK,
        last_frame_reason=FrameHealthReason.VALID,
        last_frame_age_seconds=0.1,
        watchdog_visible=True,
        stream_frame_rate=5.0,
        stream_frozen=False,
        stream_timed_out=False,
    )
    defaults.update(overrides)
    return SystemStatus(**defaults)


# -- build_region_view_model -----------------------------------------------


def test_region_view_model_reflects_status_and_alarm():
    state = RegionState(region_id="r1", status=RegionStatus.ALARM_ACTIVE, last_reason="red confirmed")

    vm = build_region_view_model(state, is_alarming=True)

    assert vm.region_id == "r1"
    assert vm.status_label == "ALARM_ACTIVE"
    assert vm.is_alarming is True
    assert vm.can_acknowledge is True
    assert vm.detail == "red confirmed"


def test_region_view_model_can_acknowledge_only_when_alarm_active():
    for status in (RegionStatus.NORMAL, RegionStatus.RED_PENDING, RegionStatus.RED_ACTIVE, RegionStatus.ALARM_ACKNOWLEDGED):
        state = RegionState(region_id="r1", status=status)
        vm = build_region_view_model(state, is_alarming=False)
        assert vm.can_acknowledge is False, f"{status} should not be acknowledgeable"

    state = RegionState(region_id="r1", status=RegionStatus.ALARM_ACTIVE)
    vm = build_region_view_model(state, is_alarming=True)
    assert vm.can_acknowledge is True


# -- build_dashboard_view_model ---------------------------------------------


def test_dashboard_view_model_with_no_status_yet():
    """Before the first loop cycle, system_status is None - the dashboard
    should render a sensible default rather than crash."""
    vm = build_dashboard_view_model(system_status=None, region_states={}, is_alarming_fn=None)

    assert vm.overall_state == SystemState.STARTING.value
    assert vm.camera_connected is False
    assert vm.regions == []
    assert vm.has_active_alarm is False
    assert vm.fault_message is None


def test_dashboard_view_model_maps_system_status_fields():
    status = make_system_status(
        overall_state=SystemState.MONITORING,
        camera_connected=True,
        camera_status=HealthStatus.OK,
        stream_frame_rate=3.5,
    )

    vm = build_dashboard_view_model(status, region_states={}, is_alarming_fn=None)

    assert vm.overall_state == "MONITORING"
    assert vm.camera_connected is True
    assert vm.camera_status == "OK"
    assert vm.stream_frame_rate == 3.5


def test_dashboard_view_model_aggregates_regions_and_alarm_flag():
    region_states = {
        "r1": RegionState(region_id="r1", status=RegionStatus.NORMAL),
        "r2": RegionState(region_id="r2", status=RegionStatus.ALARM_ACTIVE),
    }

    def is_alarming(region_id):
        return region_id == "r2"

    vm = build_dashboard_view_model(make_system_status(), region_states, is_alarming)

    assert len(vm.regions) == 2
    assert vm.has_active_alarm is True
    r1 = next(r for r in vm.regions if r.region_id == "r1")
    r2 = next(r for r in vm.regions if r.region_id == "r2")
    assert r1.is_alarming is False
    assert r2.is_alarming is True


def test_dashboard_view_model_no_alarm_when_nothing_alarming():
    region_states = {"r1": RegionState(region_id="r1", status=RegionStatus.RED_ACTIVE)}

    vm = build_dashboard_view_model(make_system_status(), region_states, lambda rid: False)

    assert vm.has_active_alarm is False


def test_dashboard_view_model_regions_sorted_by_id():
    region_states = {
        "zebra": RegionState(region_id="zebra", status=RegionStatus.NORMAL),
        "alpha": RegionState(region_id="alpha", status=RegionStatus.NORMAL),
    }

    vm = build_dashboard_view_model(make_system_status(), region_states, lambda rid: False)

    assert [r.region_id for r in vm.regions] == ["alpha", "zebra"]


# -- fault_message -----------------------------------------------------


def test_fault_message_none_when_healthy():
    vm = build_dashboard_view_model(make_system_status(), {}, None)
    assert vm.fault_message is None


def test_fault_message_camera_disconnected():
    status = make_system_status(camera_connected=False, camera_status=HealthStatus.FAULT)
    vm = build_dashboard_view_model(status, {}, None)
    assert vm.fault_message == "Camera disconnected"


def test_fault_message_stream_frozen():
    status = make_system_status(stream_frozen=True)
    vm = build_dashboard_view_model(status, {}, None)
    assert "frozen" in vm.fault_message.lower()


def test_fault_message_stream_timed_out():
    status = make_system_status(stream_timed_out=True)
    vm = build_dashboard_view_model(status, {}, None)
    assert "timed out" in vm.fault_message.lower()


def test_fault_message_camera_disconnect_takes_priority_over_stream_issues():
    status = make_system_status(camera_connected=False, stream_frozen=True, stream_timed_out=True)
    vm = build_dashboard_view_model(status, {}, None)
    assert vm.fault_message == "Camera disconnected"

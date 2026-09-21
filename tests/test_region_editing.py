"""Tests for the live region add/redraw path: RegionMonitor, config
persistence, SystemStatus faults, and the Application API the dashboard uses."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import numpy as np

from screen_monitor.camera.frame import Frame
from screen_monitor.common.clock import FakeClock
from screen_monitor.common.enums import (
    DetectionStatus,
    FrameHealthReason,
    HealthStatus,
    RegionStatus,
    SystemState,
)
from screen_monitor.configuration.loader import load_config, load_regions, save_region
from screen_monitor.detection.detector import DetectionResult
from screen_monitor.detection.region import Region
from screen_monitor.diagnostics.system_status import SystemStatus, build_status
from screen_monitor.main import MAX_REGIONS, Application
from screen_monitor.monitoring.monitor import RegionMonitor


def make_region(rid="r1", **kw):
    defaults = dict(id=rid, name=rid, x=0, y=0, width=50, height=50,
                    confirmation_seconds=0.0, alarm_seconds=0.0)
    defaults.update(kw)
    return Region(**defaults)


def red(region_id, at):
    return DetectionResult(DetectionStatus.RED, 1.0, 0.9, at, region_id)


def drive_to_alarm(monitor, clock, rid="r1"):
    for _ in range(3):
        monitor.process([red(rid, clock.now())])
    assert monitor.get_state(rid).status == RegionStatus.ALARM_ACTIVE


# -- RegionMonitor -------------------------------------------------------

def test_replace_region_resets_state_and_uses_new_definition():
    clock = FakeClock()
    monitor = RegionMonitor([make_region(confirmation_seconds=2.0)], clock)
    monitor.process([red("r1", clock.now())])
    assert monitor.get_state("r1").status == RegionStatus.RED_PENDING

    assert monitor.replace_region(make_region(confirmation_seconds=0.0, x=10)) is True
    assert monitor.get_state("r1").status == RegionStatus.NORMAL

    # New definition's 0s confirmation is in effect: pending -> active in one more cycle.
    monitor.process([red("r1", clock.now())])
    monitor.process([red("r1", clock.now())])
    assert monitor.get_state("r1").status == RegionStatus.RED_ACTIVE


def test_replace_region_is_refused_while_alarm_active():
    clock = FakeClock()
    monitor = RegionMonitor([make_region()], clock)
    drive_to_alarm(monitor, clock)

    assert monitor.replace_region(make_region(x=99)) is False
    assert monitor.get_state("r1").status == RegionStatus.ALARM_ACTIVE

    monitor.acknowledge("r1")
    assert monitor.replace_region(make_region(x=99)) is True


def test_replace_unknown_region_is_refused():
    monitor = RegionMonitor([make_region()], FakeClock())
    assert monitor.replace_region(make_region("nope")) is False


def test_add_region_starts_normal_and_rejects_duplicates():
    monitor = RegionMonitor([make_region()], FakeClock())
    assert monitor.add_region(make_region("r2")) is True
    assert monitor.get_state("r2").status == RegionStatus.NORMAL
    assert monitor.add_region(make_region("r2")) is False


# -- config persistence --------------------------------------------------

def test_save_region_updates_in_place_and_keeps_other_config(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({
        "regions": [make_region("a").to_dict(), make_region("b").to_dict()],
        "detection": {"saturation_min": 70, "value_min": 50},
    }))

    save_region(path, make_region("a", x=123))

    config = load_config(path)
    assert [r["id"] for r in config["regions"]] == ["a", "b"]  # order kept
    assert config["regions"][0]["x"] == 123
    assert config["detection"] == {"saturation_min": 70, "value_min": 50}
    assert not list(tmp_path.glob(".config_*.tmp"))


def test_save_region_appends_new_and_creates_missing_file(tmp_path):
    path = tmp_path / "sub" / "config.json"
    save_region(path, make_region("a"))
    save_region(path, make_region("b"))
    assert [r.id for r in load_regions(load_config(path))] == ["a", "b"]


# -- system faults ---------------------------------------------------------

def status(**kw):
    defaults = dict(
        overall_state=SystemState.MONITORING, camera_connected=True,
        camera_status=HealthStatus.OK, last_frame_reason=FrameHealthReason.VALID,
        last_frame_age_seconds=0.1, watchdog_visible=True, stream_frame_rate=5.0,
    )
    defaults.update(kw)
    return SystemStatus(**defaults)


def test_active_faults():
    assert status().active_faults() == set()
    assert status(watchdog_visible=False).active_faults() == {"watchdog"}
    assert status(stream_frozen=True).active_faults() == {"stream"}
    assert status(last_frame_age_seconds=None).active_faults() == {"stream"}
    assert status(camera_connected=False).active_faults() == {"stream"}
    assert status(stream_frame_rate=0.5).active_faults() == {"fps"}
    # a startup 0 fps reading is not a low-fps fault
    assert status(stream_frame_rate=0.0).active_faults() == set()


# -- Application API used by the dashboard ---------------------------------

def make_app(tmp_path, regions=None, config=True):
    return Application(
        device_index=0,
        status_path=tmp_path / "hb.json",
        frame_timeout_seconds=3.0,
        regions=regions,
        config_path=(tmp_path / "config.json") if config else None,
    )


def give_frame(app, w=640, h=480):
    app._last_frame = Frame.from_image(np.zeros((h, w, 3), dtype=np.uint8), received_at=0.0)


def test_app_works_with_zero_regions_and_can_add_the_first(tmp_path):
    app = make_app(tmp_path)
    give_frame(app)
    assert app.regions == []

    result = app.add_region(make_region("first"))

    assert result.ok and result.persisted
    assert [r.id for r in app.regions] == ["first"]
    assert "first" in app.region_states()
    assert [r.id for r in load_regions(load_config(tmp_path / "config.json"))] == ["first"]


def test_app_redraw_applies_live_and_persists(tmp_path):
    app = make_app(tmp_path, regions=[make_region("r1")])
    give_frame(app)

    result = app.replace_region(make_region("r1", x=200, y=100))

    assert result.ok and result.persisted
    assert app.regions[0].x == 200
    assert load_regions(load_config(tmp_path / "config.json"))[0].x == 200


def test_app_edit_outside_frame_is_refused(tmp_path):
    app = make_app(tmp_path, regions=[make_region("r1")])
    give_frame(app, w=100, h=100)

    result = app.replace_region(make_region("r1", x=90))  # 90+50 > 100

    assert not result.ok
    assert app.regions[0].x == 0
    assert not (tmp_path / "config.json").exists()


def test_app_edit_without_config_path_applies_but_warns(tmp_path):
    app = make_app(tmp_path, regions=[make_region("r1")], config=False)
    give_frame(app)

    result = app.replace_region(make_region("r1", x=5))

    assert result.ok and not result.persisted
    assert "not saved" in result.message


def test_app_refuses_redraw_during_alarm(tmp_path):
    app = make_app(tmp_path, regions=[make_region("r1")])
    give_frame(app)
    for _ in range(3):
        events = app._region_monitor.process([red("r1", app._clock.now())])
        for e in events:
            app._alarm_manager.handle_event(e)
    assert app.is_alarming("r1")

    result = app.replace_region(make_region("r1", x=5))

    assert not result.ok
    assert "acknowledge" in result.message.lower()
    assert app.regions[0].x == 0


def test_app_region_limit(tmp_path):
    app = make_app(tmp_path, regions=[make_region(f"r{i}") for i in range(MAX_REGIONS)])
    give_frame(app)
    assert not app.add_region(make_region("extra")).ok


def test_acknowledge_fault_only_while_active_and_clears_on_recovery(tmp_path):
    app = make_app(tmp_path)
    app._last_system_status = status(stream_frozen=True)
    assert app.acknowledge_fault("stream") is True
    assert app.acknowledged_faults() == {"stream"}

    assert app.acknowledge_fault("watchdog") is False  # not faulted
    assert app.acknowledge_fault("bogus") is False

    # simulate the loop's cleanup once the stream recovers
    app._last_system_status = status()
    app._acknowledged_faults &= app._last_system_status.active_faults()
    assert app.acknowledged_faults() == set()

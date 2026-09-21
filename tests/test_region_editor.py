"""Tests for the editor's form parsing, countdown text, name-only edits,
and the alarm sound generation."""

import sys
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import numpy as np

from screen_monitor.alarms.local_audio import _build_siren_wav
from screen_monitor.camera.frame import Frame
from screen_monitor.common.enums import RegionStatus
from screen_monitor.detection.region import Region
from screen_monitor.interface.region_form import form_texts, parse_region_form
from screen_monitor.interface.view_models import build_region_view_model
from screen_monitor.main import Application
from screen_monitor.monitoring.region_state import RegionState


def region(**kw):
    d = dict(id="r1", name="Zone", x=0, y=0, width=50, height=50,
             confirmation_seconds=2.0, alarm_seconds=300.0)
    d.update(kw)
    return Region(**d)


# -- form parsing ------------------------------------------------------

def test_form_round_trip_and_formatting():
    assert form_texts("Zone", 0.3, 2.0, 300.0) == ("Zone", "0.30", "2", "300")


def test_parse_applies_all_fields():
    new = parse_region_form(region(), "Big", "0.45", "1.5", "10")
    assert (new.name, new.red_percentage_threshold, new.confirmation_seconds, new.alarm_seconds) == ("Big", 0.45, 1.5, 10.0)
    assert (new.x, new.width, new.id) == (0, 50, "r1")  # everything else untouched


def test_parse_accepts_comma_decimal_and_percent():
    assert parse_region_form(region(), "Z", "0,4", "2", "5").red_percentage_threshold == 0.4
    assert parse_region_form(region(), "Z", "35%", "2", "5").red_percentage_threshold == 0.35


def test_parse_rejects_bad_input_with_readable_messages():
    for args, fragment in [
        (("", "0.3", "2", "5"), "Name"),
        (("Z", "abc", "2", "5"), "Threshold"),
        (("Z", "1.5", "2", "5"), "Threshold"),
        (("Z", "0.3", "-1", "5"), "Confirm"),
        (("Z", "0.3", "2", "soon"), "Alarm"),
        (("Z", "0.3", "2", "nan"), "Alarm"),
    ]:
        try:
            parse_region_form(region(), *args)
        except ValueError as exc:
            assert fragment in str(exc), (args, str(exc))
        else:
            raise AssertionError(f"expected ValueError for {args}")


# -- countdown / subtitle ---------------------------------------------

def vm_for(status, now, **state_kw):
    state = RegionState(region_id="r1", status=status, **state_kw)
    return build_region_view_model(state, False, region=region(), now=now)


def test_red_active_shows_time_until_alarm():
    vm = vm_for(RegionStatus.RED_ACTIVE, now=45.0, confirmed_at=0.0)
    assert vm.countdown_text == "alarm in 4m 15s"
    assert "Red confirmed" in vm.subtitle and "alarm in 4m 15s" in vm.subtitle


def test_red_pending_shows_confirmation_countdown():
    vm = vm_for(RegionStatus.RED_PENDING, now=0.5, red_started_at=0.0)
    assert vm.countdown_text == "confirming 2s"  # 1.5s left, rounded up


def test_no_countdown_when_normal_or_alarming_or_no_clock():
    assert vm_for(RegionStatus.NORMAL, now=10.0).countdown_text == ""
    assert vm_for(RegionStatus.ALARM_ACTIVE, now=10.0).countdown_text == ""
    assert vm_for(RegionStatus.RED_ACTIVE, now=None, confirmed_at=0.0).countdown_text == ""


def test_view_model_carries_editable_settings():
    vm = vm_for(RegionStatus.NORMAL, now=0.0)
    assert (vm.confirmation_seconds, vm.alarm_seconds, vm.red_threshold) == (2.0, 300.0, 0.30)


# -- name-only edits keep timers running -------------------------------

def make_app(tmp_path):
    app = Application(0, tmp_path / "hb.json", 3.0, regions=[region(confirmation_seconds=0.0, alarm_seconds=0.0)],
                      config_path=tmp_path / "config.json")
    app._last_frame = Frame.from_image(np.zeros((480, 640, 3), dtype=np.uint8), received_at=0.0)
    return app


def test_rename_does_not_reset_running_timer(tmp_path):
    app = make_app(tmp_path)
    app._region_monitor._states["r1"] = RegionState(
        region_id="r1", status=RegionStatus.RED_ACTIVE, red_started_at=0.0, confirmed_at=0.0)

    result = app.replace_region(region(name="Renamed", confirmation_seconds=0.0, alarm_seconds=0.0))

    assert result.ok and app.regions[0].name == "Renamed"
    assert app.region_states()["r1"].status == RegionStatus.RED_ACTIVE


def test_rename_is_allowed_during_alarm_but_threshold_change_is_not(tmp_path):
    app = make_app(tmp_path)
    app._region_monitor._states["r1"] = RegionState(region_id="r1", status=RegionStatus.ALARM_ACTIVE)
    base = dict(confirmation_seconds=0.0, alarm_seconds=0.0)

    assert app.replace_region(region(name="Renamed", **base)).ok
    refused = app.replace_region(region(name="Renamed", red_percentage_threshold=0.9, **base))

    assert not refused.ok
    assert app.regions[0].red_percentage_threshold == 0.30
    assert app.region_states()["r1"].status == RegionStatus.ALARM_ACTIVE


def test_changing_alarm_seconds_takes_effect_and_persists(tmp_path):
    app = make_app(tmp_path)
    result = app.replace_region(region(confirmation_seconds=0.0, alarm_seconds=12.0))
    assert result.ok and result.persisted
    assert app.regions[0].alarm_seconds == 12.0


# -- alarm sound ------------------------------------------------------

def test_siren_wav_is_valid_and_not_silent(tmp_path):
    path = tmp_path / "siren.wav"
    _build_siren_wav(path)
    with wave.open(str(path)) as w:
        assert w.getnchannels() == 1 and w.getsampwidth() == 2
        seconds = w.getnframes() / w.getframerate()
        samples = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
    assert 1.0 < seconds < 3.0
    assert np.abs(samples).max() > 20000

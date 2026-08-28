"""Unit tests for frame health validation.

These tests use a fake CameraSource / raw arrays instead of a real webcam,
per Rule 5 in the plan (dependency injection: camera and clock should be
replaceable with test doubles).
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import numpy as np

from screen_monitor.camera.frame import Frame
from screen_monitor.camera.frame_health import FrameHealthValidator
from screen_monitor.common.clock import FakeClock
from screen_monitor.common.enums import FrameHealthReason


def make_image(width=100, height=100):
    return np.zeros((height, width, 3), dtype=np.uint8)


def test_missing_frame_is_invalid():
    validator = FrameHealthValidator()
    result = validator.validate(None)
    assert not result.is_valid
    assert result.reason == FrameHealthReason.MISSING


def test_valid_frame_passes():
    clock = FakeClock(start=100.0)
    validator = FrameHealthValidator(max_age_seconds=3.0, clock=clock)
    frame = Frame.from_image(make_image(), received_at=100.0)

    result = validator.validate(frame)

    assert result.is_valid
    assert result.reason == FrameHealthReason.VALID


def test_stale_frame_is_rejected():
    clock = FakeClock(start=100.0)
    validator = FrameHealthValidator(max_age_seconds=3.0, clock=clock)
    frame = Frame.from_image(make_image(), received_at=100.0)

    clock.advance(5.0)  # frame is now 5s old, limit is 3s
    result = validator.validate(frame)

    assert not result.is_valid
    assert result.reason == FrameHealthReason.STALE


def test_undersized_frame_is_rejected():
    clock = FakeClock(start=0.0)
    validator = FrameHealthValidator(
        expected_min_width=50, expected_min_height=50, clock=clock
    )
    frame = Frame.from_image(make_image(width=10, height=10), received_at=0.0)

    result = validator.validate(frame)

    assert not result.is_valid
    assert result.reason == FrameHealthReason.INVALID_DIMENSIONS


def test_empty_frame_is_rejected():
    clock = FakeClock(start=0.0)
    validator = FrameHealthValidator(clock=clock)
    empty_image = np.zeros((0, 0, 3), dtype=np.uint8)
    frame = Frame.from_image(empty_image, received_at=0.0)

    result = validator.validate(frame)

    assert not result.is_valid
    assert result.reason in (FrameHealthReason.INVALID_DIMENSIONS, FrameHealthReason.EMPTY)

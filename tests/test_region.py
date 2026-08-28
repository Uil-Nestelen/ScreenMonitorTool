"""Unit tests for Region validation and bounds checking."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import pytest

from screen_monitor.detection.region import Region


def make_region(**overrides):
    defaults = dict(id="r1", name="Test Region", x=10, y=20, width=100, height=50)
    defaults.update(overrides)
    return Region(**defaults)


def test_valid_region_is_within_frame():
    region = make_region(x=0, y=0, width=100, height=100)
    assert region.is_within_frame(frame_width=200, frame_height=200)


def test_region_extending_past_frame_edge_is_not_within_frame():
    region = make_region(x=150, y=0, width=100, height=100)
    assert not region.is_within_frame(frame_width=200, frame_height=200)


def test_region_with_negative_width_is_rejected():
    with pytest.raises(ValueError):
        make_region(width=-10)


def test_region_with_negative_position_is_rejected():
    with pytest.raises(ValueError):
        make_region(x=-5)


def test_region_with_out_of_range_threshold_is_rejected():
    with pytest.raises(ValueError):
        make_region(red_percentage_threshold=1.5)


def test_region_round_trips_through_dict():
    region = make_region(red_percentage_threshold=0.4, confidence_threshold=0.9)
    restored = Region.from_dict(region.to_dict())
    assert restored == region


def test_region_from_dict_ignores_unknown_keys():
    data = make_region().to_dict()
    data["some_future_field"] = "ignored"
    region = Region.from_dict(data)
    assert region.id == "r1"

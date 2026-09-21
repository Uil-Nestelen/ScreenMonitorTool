"""Unit tests for canvas <-> frame coordinate mapping."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from screen_monitor.interface.stream_geometry import (
    canvas_to_frame,
    compute_viewport,
    frame_to_canvas,
    rect_from_drag,
)


def test_frame_is_letterboxed_and_centered():
    vp = compute_viewport(640, 480, 1000, 400)  # canvas is wider than the frame
    assert vp.scale == 400 / 480
    assert vp.offset_y == 0
    assert abs(vp.offset_x - (1000 - 640 * vp.scale) / 2) < 1e-9


def test_degenerate_sizes_give_no_viewport():
    assert compute_viewport(0, 480, 100, 100) is None
    assert compute_viewport(640, 480, 1, 0) is None


def test_round_trip_frame_canvas_frame():
    vp = compute_viewport(1280, 720, 900, 700)
    for x, y in [(0, 0), (100, 200), (1279, 719)]:
        cx, cy = frame_to_canvas(vp, x, y)
        assert canvas_to_frame(vp, cx, cy) == (x, y)


def test_points_on_letterbox_bars_clamp_into_frame():
    vp = compute_viewport(640, 480, 1000, 400)
    assert canvas_to_frame(vp, -50, -50) == (0, 0)
    assert canvas_to_frame(vp, 5000, 5000) == (640, 480)


def test_drag_becomes_frame_rect_in_any_direction():
    vp = compute_viewport(640, 480, 640, 480)  # scale 1, no offset
    assert rect_from_drag(vp, (100, 50), (200, 150)) == (100, 50, 100, 100)
    assert rect_from_drag(vp, (200, 150), (100, 50)) == (100, 50, 100, 100)


def test_drag_scaled_view_maps_back_to_frame_pixels():
    vp = compute_viewport(1280, 720, 640, 360)  # half scale
    assert rect_from_drag(vp, (100, 100), (200, 150)) == (200, 200, 200, 100)


def test_tiny_drag_is_rejected_as_accidental_click():
    vp = compute_viewport(640, 480, 640, 480)
    assert rect_from_drag(vp, (10, 10), (12, 13)) is None


def test_dragged_rect_never_leaves_frame():
    vp = compute_viewport(640, 480, 640, 480)
    x, y, w, h = rect_from_drag(vp, (600, 400), (900, 900))
    assert x + w <= 640 and y + h <= 480

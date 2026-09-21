"""Pure geometry for mapping between camera-frame pixels and the
dashboard's stream canvas (letterboxed, scaled to fit).

No Tkinter import, so it's unit-testable without a display. Region
coordinates are always stored in *frame* pixels (what the detector
crops); the canvas is only ever a scaled view of that frame.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple

MIN_REGION_SIZE = 8  # frame pixels; smaller drags are treated as accidental clicks


@dataclass(frozen=True)
class Viewport:
    frame_width: int
    frame_height: int
    scale: float
    offset_x: float
    offset_y: float

    @property
    def draw_width(self) -> int:
        return max(1, int(self.frame_width * self.scale))

    @property
    def draw_height(self) -> int:
        return max(1, int(self.frame_height * self.scale))


def compute_viewport(
    frame_width: int, frame_height: int, canvas_width: int, canvas_height: int
) -> Optional[Viewport]:
    """Fit the frame inside the canvas, centered, preserving aspect ratio."""
    if min(frame_width, frame_height, canvas_width, canvas_height) <= 0:
        return None
    scale = min(canvas_width / frame_width, canvas_height / frame_height)
    return Viewport(
        frame_width=frame_width,
        frame_height=frame_height,
        scale=scale,
        offset_x=(canvas_width - frame_width * scale) / 2,
        offset_y=(canvas_height - frame_height * scale) / 2,
    )


def frame_to_canvas(viewport: Viewport, x: float, y: float) -> Tuple[float, float]:
    return viewport.offset_x + x * viewport.scale, viewport.offset_y + y * viewport.scale


def canvas_to_frame(viewport: Viewport, cx: float, cy: float) -> Tuple[int, int]:
    """Canvas pixel -> frame pixel, clamped to the frame's bounds (so a
    drag that strays onto the letterbox bars still yields a valid point)."""
    x = int(round((cx - viewport.offset_x) / viewport.scale))
    y = int(round((cy - viewport.offset_y) / viewport.scale))
    x = max(0, min(viewport.frame_width, x))
    y = max(0, min(viewport.frame_height, y))
    return x, y


def rect_from_drag(
    viewport: Viewport, start: Tuple[float, float], end: Tuple[float, float]
) -> Optional[Tuple[int, int, int, int]]:
    """Canvas drag -> (x, y, width, height) in frame pixels, or None if
    the drag is too small to be a deliberate region."""
    x1, y1 = canvas_to_frame(viewport, *start)
    x2, y2 = canvas_to_frame(viewport, *end)
    x, y = min(x1, x2), min(y1, y2)
    width, height = abs(x2 - x1), abs(y2 - y1)
    if width < MIN_REGION_SIZE or height < MIN_REGION_SIZE:
        return None
    return x, y, width, height

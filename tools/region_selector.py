"""Interactive region selector and live calibration tool.

Three things in one, because testing detection "blind" is hard in
practice:

1. Draw a new region by dragging on the live feed, then press 's' to
   save it.
2. Any region(s) already saved in the output config file are loaded on
   startup and drawn as an overlay automatically - with a LIVE readout
   of red percentage / confidence / status updating every frame.
3. A "mask debug view" (press 'm') shows exactly which pixels the
   detector currently counts as red: background dimmed to grayscale,
   matching pixels lit up in solid red. This answers the question
   "is my region misplaced, or is the color just not being picked up?"
   at a glance - if the red thing is visible in normal view but nothing
   lights up in mask view, it's a color/threshold problem, not
   placement. Saturation/value thresholds (the two things that
   typically wash out at distance/angle) can be tuned live with the
   keys below and saved into the config for the main app to use too.

Controls:
    Click and drag  - draw/redraw a new region rectangle (shown in green)
    s               - save the current green rectangle as a region
                      (prompts in the terminal for an id/name/threshold),
                      also saves the current saturation/value thresholds
    r               - clear the current (unsaved) rectangle
    m               - toggle the red-pixel mask debug view
    [ / ]           - decrease / increase saturation_min by 5
    - / =           - decrease / increase value_min by 5
    q               - quit

Run with:

    python tools/region_selector.py --device-index 0 [--output config/config.json]

Saved regions are drawn with a live label like:

    screen_1: 42.3% RED (conf 0.71)
    screen_1: 2.1% NORMAL (conf 0.53)
    screen_1: TOO_DARK (UNKNOWN)
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from screen_monitor.camera.usb_camera import UsbCamera, UsbCameraError
from screen_monitor.detection.red_detector import (
    DEFAULT_SATURATION_MIN,
    DEFAULT_VALUE_MIN,
    RedDetector,
)
from screen_monitor.detection.region import Region
from screen_monitor.common.enums import DetectionStatus

WINDOW_NAME = "Screen Monitor - Region Selector / Calibration"

NEW_REGION_COLOR = (0, 255, 0)  # green - the box you're currently drawing
SAVED_RED_COLOR = (0, 0, 255)  # red - saved region currently reading RED
SAVED_NORMAL_COLOR = (0, 200, 200)  # yellow - saved region currently reading NORMAL
SAVED_UNKNOWN_COLOR = (128, 128, 128)  # gray - saved region currently UNKNOWN


class RectangleSelector:
    def __init__(self) -> None:
        self.dragging = False
        self.start = None
        self.end = None

    def on_mouse(self, event, x, y, flags, param):
        if event == cv2.EVENT_LBUTTONDOWN:
            self.dragging = True
            self.start = (x, y)
            self.end = (x, y)
        elif event == cv2.EVENT_MOUSEMOVE and self.dragging:
            self.end = (x, y)
        elif event == cv2.EVENT_LBUTTONUP:
            self.dragging = False
            self.end = (x, y)

    def get_rect(self):
        if self.start is None or self.end is None:
            return None
        x1, y1 = self.start
        x2, y2 = self.end
        x, y = min(x1, x2), min(y1, y2)
        width, height = abs(x2 - x1), abs(y2 - y1)
        if width == 0 or height == 0:
            return None
        return x, y, width, height


def load_existing_config(output_path: Path) -> dict:
    if output_path.exists():
        with open(output_path, "r") as f:
            return json.load(f)
    return {"regions": []}


def load_saved_regions(output_path: Path) -> list:
    config = load_existing_config(output_path)
    regions = []
    for raw in config.get("regions", []):
        try:
            regions.append(Region.from_dict(raw))
        except (TypeError, ValueError) as exc:
            print(f"Skipping invalid saved region {raw.get('id', '?')}: {exc}")
    return regions


def load_saved_thresholds(output_path: Path) -> tuple:
    config = load_existing_config(output_path)
    detection = config.get("detection", {})
    return (
        int(detection.get("saturation_min", DEFAULT_SATURATION_MIN)),
        int(detection.get("value_min", DEFAULT_VALUE_MIN)),
    )


def save_region(output_path: Path, rect, detector: RedDetector) -> None:
    x, y, width, height = rect
    print(f"\nRectangle: x={x}, y={y}, width={width}, height={height}")
    region_id = input("Region id (e.g. 'screen_1'): ").strip() or "screen_1"
    name = input("Region name (e.g. 'Server Monitor'): ").strip() or region_id
    threshold_raw = input("Red percentage threshold [0.30]: ").strip()
    threshold = float(threshold_raw) if threshold_raw else 0.30

    region = Region(
        id=region_id,
        name=name,
        x=x,
        y=y,
        width=width,
        height=height,
        red_percentage_threshold=threshold,
    )

    config = load_existing_config(output_path)
    regions = [r for r in config.get("regions", []) if r.get("id") != region.id]
    regions.append(region.to_dict())
    config["regions"] = regions

    saturation_min, value_min = detector.get_thresholds()
    config["detection"] = {"saturation_min": saturation_min, "value_min": value_min}

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w") as f:
        json.dump(config, f, indent=2)

    print(
        f"Saved region '{region.id}' and detection thresholds "
        f"(saturation_min={saturation_min}, value_min={value_min}) to {output_path}"
    )


def draw_saved_region_overlay(display, region: Region, frame_image, detector: RedDetector) -> None:
    """Crop, detect, and draw a saved region with a live status label."""
    if not region.is_within_frame(frame_image.shape[1], frame_image.shape[0]):
        cv2.rectangle(
            display,
            (region.x, region.y),
            (region.x + region.width, region.y + region.height),
            SAVED_UNKNOWN_COLOR,
            2,
        )
        cv2.putText(
            display,
            f"{region.id}: OUT OF FRAME BOUNDS",
            (region.x, max(0, region.y - 10)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.55,
            SAVED_UNKNOWN_COLOR,
            2,
        )
        return

    cropped = frame_image[
        region.y : region.y + region.height,
        region.x : region.x + region.width,
    ]
    result = detector.detect(cropped, region, evaluated_at=time.monotonic())

    if result.status == DetectionStatus.RED:
        color = SAVED_RED_COLOR
        label = f"{region.id}: {result.red_percentage:.1%} RED (conf {result.confidence:.2f})"
    elif result.status == DetectionStatus.NORMAL:
        color = SAVED_NORMAL_COLOR
        label = f"{region.id}: {result.red_percentage:.1%} NORMAL (conf {result.confidence:.2f})"
    else:
        color = SAVED_UNKNOWN_COLOR
        label = f"{region.id}: {result.reason}"

    cv2.rectangle(
        display,
        (region.x, region.y),
        (region.x + region.width, region.y + region.height),
        color,
        2,
    )
    cv2.putText(
        display,
        label,
        (region.x, max(0, region.y - 10)),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.55,
        color,
        2,
    )


def make_mask_debug_view(frame_bgr: np.ndarray, detector: RedDetector) -> np.ndarray:
    """Background dimmed to grayscale; pixels the detector counts as red are
    lit up in solid red. Answers "is the color actually being picked up?"
    at a glance, independent of any region box.
    """
    mask = detector.compute_mask(frame_bgr)
    gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
    # Dim the grayscale background so the highlighted pixels stand out clearly.
    dimmed = (gray.astype(np.float32) * 0.4).astype(np.uint8)
    debug = cv2.cvtColor(dimmed, cv2.COLOR_GRAY2BGR)
    debug[mask > 0] = (0, 0, 255)
    return debug


def main() -> None:
    parser = argparse.ArgumentParser(description="Interactively select and calibrate a detection region")
    parser.add_argument("--device-index", type=int, default=0)
    parser.add_argument("--output", default="config/config.json")
    args = parser.parse_args()

    output_path = Path(args.output)
    camera = UsbCamera(device_index=args.device_index)
    selector = RectangleSelector()
    window_name = f"{WINDOW_NAME} - device {args.device_index} - {output_path}"

    saturation_min, value_min = load_saved_thresholds(output_path)
    detector = RedDetector(saturation_min=saturation_min, value_min=value_min)
    mask_view_enabled = False

    try:
        camera.connect()
    except UsbCameraError as exc:
        print(f"Failed to open camera: {exc}")
        return

    cv2.namedWindow(window_name)
    cv2.setMouseCallback(window_name, selector.on_mouse)

    saved_regions = load_saved_regions(output_path)
    if saved_regions:
        print(f"Loaded {len(saved_regions)} saved region(s) from {output_path}: "
              f"{[r.id for r in saved_regions]}")
    print(f"Detection thresholds: saturation_min={saturation_min}, value_min={value_min}")
    print("Drag to draw a NEW region. 's' to save it, 'r' to reset, 'q' to quit.")
    print("'m' toggles the red-pixel mask debug view.")
    print("'[' / ']' adjust saturation_min, '-' / '=' adjust value_min, live.")

    try:
        while True:
            frame = camera.read_frame()
            if frame is None or frame.image is None:
                continue

            if mask_view_enabled:
                display = make_mask_debug_view(frame.image, detector)
                cv2.putText(
                    display,
                    "MASK VIEW - red = pixels detector counts as red",
                    (10, 25),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.6,
                    (255, 255, 255),
                    2,
                )
            else:
                display = frame.image.copy()
                # Live overlay for every already-saved region.
                for region in saved_regions:
                    draw_saved_region_overlay(display, region, frame.image, detector)

            sat_min, val_min = detector.get_thresholds()
            cv2.putText(
                display,
                f"saturation_min={sat_min}  value_min={val_min}  ('m' mask view)",
                (10, display.shape[0] - 15),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.5,
                (255, 255, 255),
                1,
            )

            # The rectangle currently being drawn (not yet saved).
            rect = selector.get_rect()
            if rect and not mask_view_enabled:
                x, y, w, h = rect
                cv2.rectangle(display, (x, y), (x + w, y + h), NEW_REGION_COLOR, 2)
                cv2.putText(
                    display,
                    f"NEW: {w}x{h} @ ({x},{y})",
                    (x, max(0, y - 10)),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.6,
                    NEW_REGION_COLOR,
                    2,
                )

            cv2.imshow(window_name, display)
            key = cv2.waitKey(1) & 0xFF

            if key == ord("q"):
                break
            elif key == ord("r"):
                selector.start = None
                selector.end = None
            elif key == ord("m"):
                mask_view_enabled = not mask_view_enabled
            elif key == ord("["):
                sat_min, val_min = detector.get_thresholds()
                detector.set_thresholds(sat_min - 5, val_min)
            elif key == ord("]"):
                sat_min, val_min = detector.get_thresholds()
                detector.set_thresholds(sat_min + 5, val_min)
            elif key == ord("-"):
                sat_min, val_min = detector.get_thresholds()
                detector.set_thresholds(sat_min, val_min - 5)
            elif key == ord("="):
                sat_min, val_min = detector.get_thresholds()
                detector.set_thresholds(sat_min, val_min + 5)
            elif key == ord("s"):
                rect = selector.get_rect()
                if rect is None:
                    print("No rectangle drawn yet - drag on the video window first.")
                else:
                    save_region(output_path, rect, detector)
                    # Reload so the newly-saved region immediately shows its
                    # own live overlay too.
                    saved_regions = load_saved_regions(output_path)
                    selector.start = None
                    selector.end = None
    finally:
        camera.disconnect()
        cv2.destroyAllWindows()


if __name__ == "__main__":
    main()


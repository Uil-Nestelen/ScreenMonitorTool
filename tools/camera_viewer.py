"""Standalone camera viewer.

Lets you check that OpenCV can see your webcam and that frame_health
reports VALID, without running the full application or the watchdog.
Press 'q' to quit.

Run with:

    python tools/camera_viewer.py [--device-index N]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import cv2

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from screen_monitor.camera.frame_health import FrameHealthValidator
from screen_monitor.camera.usb_camera import UsbCamera, UsbCameraError


def main() -> None:
    parser = argparse.ArgumentParser(description="Preview a USB camera with frame health overlay")
    parser.add_argument("--device-index", type=int, default=0)
    args = parser.parse_args()

    camera = UsbCamera(device_index=args.device_index)
    validator = FrameHealthValidator()

    try:
        camera.connect()
    except UsbCameraError as exc:
        print(f"Failed to open camera: {exc}")
        return

    print("Press 'q' in the video window to quit.")
    try:
        while True:
            frame = camera.read_frame()
            result = validator.validate(frame)

            if frame is not None and frame.image is not None:
                display = frame.image.copy()
                label = f"{result.reason.value}"
                color = (0, 200, 0) if result.is_valid else (0, 0, 255)
                cv2.putText(
                    display, label, (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 1, color, 2
                )
                cv2.imshow("Screen Monitor - Camera Viewer", display)

            if cv2.waitKey(1) & 0xFF == ord("q"):
                break
    finally:
        camera.disconnect()
        cv2.destroyAllWindows()


if __name__ == "__main__":
    main()

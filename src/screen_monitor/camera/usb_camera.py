"""USB webcam camera source, implemented with OpenCV's VideoCapture.

This is the camera implementation selected for the initial version of the
project (section 9 of the plan uses ip_camera.py as the example; here we
use a local USB webcam instead, behind the same CameraSource interface,
per section 4.2's note that additional implementations can be swapped in).
"""

from __future__ import annotations

import logging
from typing import Optional

import cv2

from screen_monitor.camera.frame import Frame
from screen_monitor.camera.source import CameraSource
from screen_monitor.common.clock import Clock, RealClock

logger = logging.getLogger(__name__)


class UsbCameraError(RuntimeError):
    pass


class UsbCamera(CameraSource):
    def __init__(
        self,
        device_index: int = 0,
        expected_width: Optional[int] = None,
        expected_height: Optional[int] = None,
        clock: Optional[Clock] = None,
    ) -> None:
        self._device_index = device_index
        self._expected_width = expected_width
        self._expected_height = expected_height
        self._clock = clock or RealClock()
        self._capture: Optional["cv2.VideoCapture"] = None
        self._sequence_number = 0

    def connect(self) -> None:
        capture = cv2.VideoCapture(self._device_index)
        if self._expected_width:
            capture.set(cv2.CAP_PROP_FRAME_WIDTH, self._expected_width)
        if self._expected_height:
            capture.set(cv2.CAP_PROP_FRAME_HEIGHT, self._expected_height)

        if not capture.isOpened():
            capture.release()
            raise UsbCameraError(
                f"Could not open USB camera at device index {self._device_index}"
            )

        self._capture = capture
        logger.info("USB camera %s connected", self._device_index)

    def disconnect(self) -> None:
        if self._capture is not None:
            self._capture.release()
            self._capture = None
            logger.info("USB camera %s disconnected", self._device_index)

    def is_connected(self) -> bool:
        return self._capture is not None and self._capture.isOpened()

    def read_frame(self) -> Optional[Frame]:
        if not self.is_connected():
            return None

        ok, image = self._capture.read()
        received_at = self._clock.now()

        if not ok or image is None:
            logger.warning("USB camera %s: failed to read a frame", self._device_index)
            return None

        self._sequence_number += 1
        return Frame.from_image(
            image=image,
            received_at=received_at,
            sequence_number=self._sequence_number,
            source_timestamp=None,  # USB webcams generally don't expose this
            metadata={"device_index": self._device_index},
        )

    def get_metadata(self) -> dict:
        info = {"type": "usb_camera", "device_index": self._device_index}
        if self._capture is not None and self._capture.isOpened():
            info["actual_width"] = int(self._capture.get(cv2.CAP_PROP_FRAME_WIDTH))
            info["actual_height"] = int(self._capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
            info["fps"] = self._capture.get(cv2.CAP_PROP_FPS)
        return info

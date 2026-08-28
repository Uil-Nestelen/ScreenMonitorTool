"""HSV-based red detector.

Red wraps around the hue circle in HSV (it sits at both ends: ~0 and
~180 in OpenCV's 0-180 hue range), so two hue ranges are combined into
one mask. Saturation and value minimums filter out dark, washed-out, or
grayish pixels that happen to fall in the red hue range but wouldn't
look "red" to a person.

Saturation and value minimums are the two knobs worth tuning in
practice: a red screen viewed from further away, at an angle, or under
different lighting often loses saturation (looks more washed-out/pink)
or value (looks darker) well before its hue actually changes. Hue
itself rarely needs adjusting.

Per Rule 1 in the plan: this module only answers "what does the image
appear to show?" - it does not start timers, decide alarms, or send
notifications.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import cv2
import numpy as np

from screen_monitor.common.enums import DetectionStatus, ImageQuality
from screen_monitor.detection.confidence import calculate_confidence
from screen_monitor.detection.detector import DetectionResult, Detector
from screen_monitor.detection.image_quality import assess_image_quality

if TYPE_CHECKING:
    from screen_monitor.detection.region import Region

DEFAULT_SATURATION_MIN = 70
DEFAULT_VALUE_MIN = 50


class RedDetector(Detector):
    def __init__(
        self,
        saturation_min: int = DEFAULT_SATURATION_MIN,
        value_min: int = DEFAULT_VALUE_MIN,
    ) -> None:
        self.set_thresholds(saturation_min, value_min)

    def set_thresholds(self, saturation_min: int, value_min: int) -> None:
        saturation_min = int(max(0, min(255, saturation_min)))
        value_min = int(max(0, min(255, value_min)))
        self._saturation_min = saturation_min
        self._value_min = value_min
        # Two hue ranges because red wraps around 0/180 in OpenCV's HSV hue space.
        self._lower_red_1 = np.array([0, saturation_min, value_min])
        self._upper_red_1 = np.array([10, 255, 255])
        self._lower_red_2 = np.array([170, saturation_min, value_min])
        self._upper_red_2 = np.array([180, 255, 255])

    def get_thresholds(self) -> tuple:
        """Return (saturation_min, value_min) - useful for live calibration UIs."""
        return self._saturation_min, self._value_min

    def compute_mask(self, image: np.ndarray) -> np.ndarray:
        """Return the binary red mask for an image - exposed for debug/calibration views."""
        hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
        mask1 = cv2.inRange(hsv, self._lower_red_1, self._upper_red_1)
        mask2 = cv2.inRange(hsv, self._lower_red_2, self._upper_red_2)
        return cv2.bitwise_or(mask1, mask2)

    def _red_percentage(self, image: np.ndarray) -> float:
        mask = self.compute_mask(image)
        red_pixels = int(np.count_nonzero(mask))
        total_pixels = mask.shape[0] * mask.shape[1]
        if total_pixels == 0:
            return 0.0
        return red_pixels / total_pixels

    def detect(
        self,
        image: "np.ndarray",
        region: "Region",
        evaluated_at: float,
    ) -> DetectionResult:
        if image is None or image.size == 0:
            return DetectionResult(
                status=DetectionStatus.UNKNOWN,
                confidence=0.0,
                red_percentage=0.0,
                evaluated_at=evaluated_at,
                region_id=region.id,
                reason="Empty region image",
            )

        quality = assess_image_quality(image)
        if quality != ImageQuality.GOOD:
            # Rule 4: unreliable image quality must produce UNKNOWN, never
            # a guessed NORMAL/RED.
            return DetectionResult(
                status=DetectionStatus.UNKNOWN,
                confidence=0.0,
                red_percentage=0.0,
                evaluated_at=evaluated_at,
                region_id=region.id,
                reason=f"Image quality is {quality.value}, cannot evaluate reliably",
            )

        red_percentage = self._red_percentage(image)
        confidence = calculate_confidence(red_percentage, region.red_percentage_threshold)

        if red_percentage >= region.red_percentage_threshold:
            status = DetectionStatus.RED
            reason = (
                f"Red pixel percentage {red_percentage:.1%} meets or exceeds "
                f"threshold {region.red_percentage_threshold:.1%}"
            )
        else:
            status = DetectionStatus.NORMAL
            reason = (
                f"Red pixel percentage {red_percentage:.1%} is below "
                f"threshold {region.red_percentage_threshold:.1%}"
            )

        return DetectionResult(
            status=status,
            confidence=confidence,
            red_percentage=red_percentage,
            evaluated_at=evaluated_at,
            region_id=region.id,
            reason=reason,
        )

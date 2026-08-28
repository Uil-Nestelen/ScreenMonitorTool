"""Checks whether a region image can be evaluated reliably.

This runs before color analysis. A region that is too dark, too bright,
or genuinely flat/colorless cannot be trusted to give a meaningful red
percentage, so the detector should report UNKNOWN rather than guessing.

Note: grayscale standard deviation alone is NOT used to detect low
contrast, because a real red alert screen is often a uniform solid red -
which has near-zero grayscale variance despite being perfectly valid,
highly saturated content. Instead, "low contrast" here means low
saturation as well as low grayscale variance: a flat, colorless view
(e.g. a lens cap, a blank wall, a dead/frozen feed showing gray) rather
than a flat, colorful one.

OBSTRUCTED detection (e.g. something physically blocking the camera) is
not implemented in this milestone - it needs a reference/baseline image
to compare against, which doesn't exist yet.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import cv2
import numpy as np

from screen_monitor.common.enums import ImageQuality

if TYPE_CHECKING:
    pass

TOO_DARK_MEAN_THRESHOLD = 25.0
OVEREXPOSED_MEAN_THRESHOLD = 230.0
LOW_CONTRAST_STDDEV_THRESHOLD = 12.0
LOW_CONTRAST_SATURATION_THRESHOLD = 20.0  # out of 255


def assess_image_quality(image: "np.ndarray") -> ImageQuality:
    if image is None or image.size == 0:
        return ImageQuality.UNKNOWN

    try:
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    except cv2.error:
        return ImageQuality.UNKNOWN

    mean_brightness = float(np.mean(gray))
    stddev = float(np.std(gray))
    mean_saturation = float(np.mean(hsv[:, :, 1]))

    if mean_brightness < TOO_DARK_MEAN_THRESHOLD:
        return ImageQuality.TOO_DARK
    if mean_brightness > OVEREXPOSED_MEAN_THRESHOLD:
        return ImageQuality.OVEREXPOSED
    if stddev < LOW_CONTRAST_STDDEV_THRESHOLD and mean_saturation < LOW_CONTRAST_SATURATION_THRESHOLD:
        return ImageQuality.LOW_CONTRAST

    return ImageQuality.GOOD

"""Unit tests for RedDetector.

Uses synthetic images (solid colors) instead of a real camera, per
Rule 5 in the plan (camera/clock/etc. should be replaceable with test
doubles - here we just skip the camera dependency entirely since the
detector only needs an image array).
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import numpy as np

from screen_monitor.common.enums import DetectionStatus
from screen_monitor.detection.red_detector import RedDetector
from screen_monitor.detection.region import Region


def make_region(**overrides):
    defaults = dict(
        id="r1",
        name="Test Region",
        x=0,
        y=0,
        width=100,
        height=100,
        red_percentage_threshold=0.30,
        confidence_threshold=0.80,
    )
    defaults.update(overrides)
    return Region(**defaults)


def solid_bgr_image(bgr_color, width=100, height=100):
    image = np.zeros((height, width, 3), dtype=np.uint8)
    image[:, :] = bgr_color
    return image


def test_solid_red_image_is_detected_as_red():
    detector = RedDetector()
    region = make_region(red_percentage_threshold=0.30)
    image = solid_bgr_image((0, 0, 255))  # pure red in BGR

    result = detector.detect(image, region, evaluated_at=1.0)

    assert result.status == DetectionStatus.RED
    assert result.red_percentage > 0.9
    assert result.region_id == "r1"


def test_solid_blue_image_is_detected_as_normal():
    detector = RedDetector()
    region = make_region(red_percentage_threshold=0.30)
    image = solid_bgr_image((255, 0, 0))  # pure blue in BGR

    result = detector.detect(image, region, evaluated_at=1.0)

    assert result.status == DetectionStatus.NORMAL
    assert result.red_percentage < 0.1


def test_too_dark_image_is_unknown_not_normal():
    detector = RedDetector()
    region = make_region()
    image = solid_bgr_image((2, 2, 2))  # near-black

    result = detector.detect(image, region, evaluated_at=1.0)

    # Rule 4: unreliable image quality must never resolve to NORMAL/RED.
    assert result.status == DetectionStatus.UNKNOWN


def test_overexposed_image_is_unknown_not_normal():
    detector = RedDetector()
    region = make_region()
    image = solid_bgr_image((250, 250, 250))  # near-white

    result = detector.detect(image, region, evaluated_at=1.0)

    assert result.status == DetectionStatus.UNKNOWN


def test_blank_gray_image_is_unknown_low_contrast():
    detector = RedDetector()
    region = make_region()
    image = solid_bgr_image((120, 120, 120))  # flat mid-gray, colorless

    result = detector.detect(image, region, evaluated_at=1.0)

    assert result.status == DetectionStatus.UNKNOWN


def test_empty_image_is_unknown():
    detector = RedDetector()
    region = make_region()
    empty = np.zeros((0, 0, 3), dtype=np.uint8)

    result = detector.detect(empty, region, evaluated_at=1.0)

    assert result.status == DetectionStatus.UNKNOWN


def test_mixed_image_respects_threshold():
    detector = RedDetector()
    region = make_region(red_percentage_threshold=0.5)

    # Half the image red, half blue - well above and below different thresholds.
    image = np.zeros((100, 100, 3), dtype=np.uint8)
    image[:, :50] = (0, 0, 255)  # red half
    image[:, 50:] = (255, 0, 0)  # blue half

    result = detector.detect(image, region, evaluated_at=1.0)

    assert 0.4 < result.red_percentage < 0.6
    # Right around the 0.5 threshold - could go either way, but must not
    # be UNKNOWN, since image quality here is fine (high saturation).
    assert result.status in (DetectionStatus.RED, DetectionStatus.NORMAL)

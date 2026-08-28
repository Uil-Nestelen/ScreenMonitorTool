"""Applies a Detector to every configured region of a frame.

Crops each region out of the frame, checks that the region actually fits
inside the frame's current dimensions (it might not, e.g. after a camera
resolution change), and reports out-of-bounds regions as UNKNOWN faults
rather than crashing or silently skipping them.
"""

from __future__ import annotations

import logging
from typing import List, Sequence

from screen_monitor.camera.frame import Frame
from screen_monitor.common.enums import DetectionStatus
from screen_monitor.detection.detector import DetectionResult, Detector
from screen_monitor.detection.region import Region

logger = logging.getLogger(__name__)


class RegionDetector:
    def __init__(self, detector: Detector) -> None:
        self._detector = detector

    def analyze(self, frame: Frame, regions: Sequence[Region]) -> List[DetectionResult]:
        results: List[DetectionResult] = []

        if frame is None or frame.image is None:
            for region in regions:
                results.append(
                    DetectionResult(
                        status=DetectionStatus.UNKNOWN,
                        confidence=0.0,
                        red_percentage=0.0,
                        evaluated_at=0.0,
                        region_id=region.id,
                        reason="No frame available",
                    )
                )
            return results

        for region in regions:
            if not region.is_within_frame(frame.width, frame.height):
                logger.error(
                    "Region '%s' (%s,%s,%sx%s) does not fit inside frame %sx%s",
                    region.id,
                    region.x,
                    region.y,
                    region.width,
                    region.height,
                    frame.width,
                    frame.height,
                )
                results.append(
                    DetectionResult(
                        status=DetectionStatus.UNKNOWN,
                        confidence=0.0,
                        red_percentage=0.0,
                        evaluated_at=frame.received_at,
                        region_id=region.id,
                        reason=(
                            f"Region does not fit inside frame "
                            f"({frame.width}x{frame.height})"
                        ),
                    )
                )
                continue

            cropped = frame.image[
                region.y : region.y + region.height,
                region.x : region.x + region.width,
            ]
            result = self._detector.detect(cropped, region, frame.received_at)
            results.append(result)

        return results

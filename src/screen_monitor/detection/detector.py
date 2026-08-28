"""Detector interface.

A detector answers only "what does this image appear to show?" — per
Rule 1 in the plan, it must not start timers, decide alarms, or send
notifications. That's the monitoring engine's job (a later milestone).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING, Optional

from screen_monitor.common.enums import DetectionStatus

if TYPE_CHECKING:
    import numpy as np

    from screen_monitor.detection.region import Region


@dataclass
class DetectionResult:
    status: DetectionStatus
    confidence: float
    red_percentage: float
    evaluated_at: float
    region_id: str
    reason: str = ""


class Detector(ABC):
    @abstractmethod
    def detect(
        self,
        image: "np.ndarray",
        region: "Region",
        evaluated_at: float,
    ) -> DetectionResult:
        """Analyze an already-cropped region image and return a result.

        Implementations must return UNKNOWN (never NORMAL) when the image
        cannot be evaluated reliably, per Rule 4 in the plan.
        """
        raise NotImplementedError

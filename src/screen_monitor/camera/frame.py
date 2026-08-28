"""Frame object.

A frame carries more than just image data: the plan is explicit that a
frame can be *received* now but still contain *old* video data, so the
timestamps are first-class fields, not an afterthought.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

try:
    import numpy as np
except ImportError:  # pragma: no cover - numpy ships with opencv-python
    np = None  # type: ignore


@dataclass
class Frame:
    image: Optional["np.ndarray"]
    received_at: float
    source_timestamp: Optional[float] = None
    sequence_number: int = 0
    width: int = 0
    height: int = 0
    metadata: dict = field(default_factory=dict)

    @classmethod
    def from_image(
        cls,
        image: Optional["np.ndarray"],
        received_at: float,
        sequence_number: int = 0,
        source_timestamp: Optional[float] = None,
        metadata: Optional[dict] = None,
    ) -> "Frame":
        height, width = 0, 0
        if image is not None and hasattr(image, "shape") and len(image.shape) >= 2:
            height, width = image.shape[0], image.shape[1]
        return cls(
            image=image,
            received_at=received_at,
            source_timestamp=source_timestamp,
            sequence_number=sequence_number,
            width=width,
            height=height,
            metadata=metadata or {},
        )

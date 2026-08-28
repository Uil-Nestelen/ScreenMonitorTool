"""A configured region of the frame to watch for a red condition.

Field names match the config file schema in the project plan (section 8)
directly, so loading from JSON is a straight dict unpack.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Region:
    id: str
    name: str
    x: int
    y: int
    width: int
    height: int
    red_percentage_threshold: float = 0.30
    confidence_threshold: float = 0.80
    confirmation_seconds: float = 2.0
    alarm_seconds: float = 300.0

    def __post_init__(self) -> None:
        if self.width <= 0 or self.height <= 0:
            raise ValueError(f"Region '{self.id}': width and height must be positive")
        if self.x < 0 or self.y < 0:
            raise ValueError(f"Region '{self.id}': x and y must be non-negative")
        if not (0.0 <= self.red_percentage_threshold <= 1.0):
            raise ValueError(f"Region '{self.id}': red_percentage_threshold must be in [0, 1]")
        if not (0.0 <= self.confidence_threshold <= 1.0):
            raise ValueError(f"Region '{self.id}': confidence_threshold must be in [0, 1]")

    def is_within_frame(self, frame_width: int, frame_height: int) -> bool:
        return (
            self.x >= 0
            and self.y >= 0
            and self.x + self.width <= frame_width
            and self.y + self.height <= frame_height
        )

    @classmethod
    def from_dict(cls, data: dict) -> "Region":
        known_fields = {
            "id",
            "name",
            "x",
            "y",
            "width",
            "height",
            "red_percentage_threshold",
            "confidence_threshold",
            "confirmation_seconds",
            "alarm_seconds",
        }
        filtered = {k: v for k, v in data.items() if k in known_fields}
        return cls(**filtered)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "x": self.x,
            "y": self.y,
            "width": self.width,
            "height": self.height,
            "red_percentage_threshold": self.red_percentage_threshold,
            "confidence_threshold": self.confidence_threshold,
            "confirmation_seconds": self.confirmation_seconds,
            "alarm_seconds": self.alarm_seconds,
        }

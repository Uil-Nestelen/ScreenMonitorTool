"""Common camera interface.

Any camera implementation (USB webcam today, IP camera later) must satisfy
this interface so the rest of the application never depends on a concrete
capture backend. This also makes it easy to inject a fake camera in tests.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Optional

from screen_monitor.camera.frame import Frame


class CameraSource(ABC):
    @abstractmethod
    def connect(self) -> None:
        """Open the underlying camera/stream. Raise on failure."""
        raise NotImplementedError

    @abstractmethod
    def disconnect(self) -> None:
        """Release the underlying camera/stream. Must be safe to call twice."""
        raise NotImplementedError

    @abstractmethod
    def read_frame(self) -> Optional[Frame]:
        """Return the latest Frame, or None if no frame is currently available."""
        raise NotImplementedError

    @abstractmethod
    def is_connected(self) -> bool:
        raise NotImplementedError

    @abstractmethod
    def get_metadata(self) -> dict:
        """Return implementation-specific info (device index/url, resolution, etc.)."""
        raise NotImplementedError

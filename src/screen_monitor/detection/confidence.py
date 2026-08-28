"""Confidence calculations for red detection.

Confidence represents how far the measured red percentage is from the
region's threshold, not how "red" the region looks — a measurement right
at the threshold is genuinely ambiguous and should report low confidence
even if it ends up classified as RED.
"""

from __future__ import annotations


def clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def calculate_confidence(red_percentage: float, threshold: float) -> float:
    """Return a confidence score in [0, 1].

    Distance from the threshold, relative to the threshold itself, maps
    to confidence: a measurement far above or below the threshold is
    reported with high confidence; a measurement right at the threshold
    is reported with confidence near 0.5.
    """
    if threshold <= 0.0:
        # Degenerate config (any red at all counts) - confidence tracks
        # the measurement directly.
        return clamp(red_percentage)

    distance = abs(red_percentage - threshold)
    relative_distance = distance / threshold
    return clamp(0.5 + relative_distance * 0.5)


def meets_confidence(confidence: float, confidence_threshold: float) -> bool:
    return confidence >= confidence_threshold

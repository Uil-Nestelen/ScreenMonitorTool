"""Parsing/formatting for the region editor's form fields.

Pure functions, no Tkinter: the editor shows every field as text, and this
module turns that text back into a validated Region (or a message a person
can act on). Kept separate so it is unit-testable without a display.
"""

from __future__ import annotations

import math
from dataclasses import replace
from typing import Tuple

from screen_monitor.detection.region import Region

FORM_KEYS = ("name", "threshold", "confirmation", "alarm")


def _fmt_seconds(value: float) -> str:
    return f"{value:g}"


def form_texts(name: str, threshold: float, confirmation: float, alarm: float) -> Tuple[str, str, str, str]:
    """Format engine values as the text shown in the form fields."""
    return (name, f"{threshold:.2f}", _fmt_seconds(confirmation), _fmt_seconds(alarm))


def _parse_number(text: str) -> float:
    cleaned = text.strip().replace(",", ".")  # accept "0,3"
    if cleaned.endswith("%"):
        return float(cleaned[:-1]) / 100.0
    return float(cleaned)


def parse_region_form(old: Region, name: str, threshold: str, confirmation: str, alarm: str) -> Region:
    """Return `old` with the edited fields applied. Raises ValueError with
    a user-facing message if anything is invalid."""
    name = name.strip()
    if not name:
        raise ValueError("Name can't be empty.")

    try:
        threshold_value = _parse_number(threshold)
    except ValueError:
        raise ValueError("Threshold must be a number between 0 and 1 (e.g. 0.30 or 30%).") from None
    if not math.isfinite(threshold_value) or not (0.0 <= threshold_value <= 1.0):
        raise ValueError("Threshold must be between 0 and 1 (e.g. 0.30 or 30%).")

    seconds = []
    for label, text in (("Confirm time", confirmation), ("Alarm time", alarm)):
        try:
            value = _parse_number(text)
        except ValueError:
            raise ValueError(f"{label} must be a number of seconds.") from None
        if not math.isfinite(value) or value < 0:
            raise ValueError(f"{label} must be 0 seconds or more.")
        seconds.append(value)

    return replace(
        old,
        name=name,
        red_percentage_threshold=threshold_value,
        confirmation_seconds=seconds[0],
        alarm_seconds=seconds[1],
    )

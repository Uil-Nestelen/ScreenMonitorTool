"""Minimal configuration loading.

This is intentionally small: it loads the JSON config file and turns the
"regions" list into Region objects. The full configuration package
described in the plan (models.py, validator.py, defaults.py,
migration.py) is a later milestone - for now this just unblocks region
configuration + red detection without over-building ahead of need.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import List

from screen_monitor.detection.region import Region


class ConfigError(RuntimeError):
    pass


def load_config(path: Path) -> dict:
    path = Path(path)
    if not path.exists():
        raise ConfigError(f"Config file not found: {path}")
    try:
        with open(path, "r") as f:
            return json.load(f)
    except json.JSONDecodeError as exc:
        raise ConfigError(f"Config file {path} is not valid JSON: {exc}") from exc


def load_regions(config: dict) -> List[Region]:
    raw_regions = config.get("regions", [])
    regions: List[Region] = []
    seen_ids = set()

    for raw in raw_regions:
        try:
            region = Region.from_dict(raw)
        except (TypeError, ValueError) as exc:
            raise ConfigError(f"Invalid region in config: {exc}") from exc

        if region.id in seen_ids:
            raise ConfigError(f"Duplicate region id in config: '{region.id}'")
        seen_ids.add(region.id)
        regions.append(region)

    return regions


def load_detection_settings(config: dict) -> dict:
    """Return {'saturation_min': int, 'value_min': int}, with defaults if absent.

    These are the two red-detector thresholds worth tuning in practice
    (see red_detector.py) - saved by tools/region_selector.py once you've
    calibrated them live against the real camera position.
    """
    from screen_monitor.detection.red_detector import DEFAULT_SATURATION_MIN, DEFAULT_VALUE_MIN

    detection_config = config.get("detection", {})
    return {
        "saturation_min": int(detection_config.get("saturation_min", DEFAULT_SATURATION_MIN)),
        "value_min": int(detection_config.get("value_min", DEFAULT_VALUE_MIN)),
    }

"""Main application entry point.

Proves the pipeline described in section 9 of the plan, now extended
with region configuration + red detection (section 10, steps 8-9):

    USB camera -> frame reception -> frame health validation ->
    region detection (if --config given) -> heartbeat ->
    independent watchdog

Run with:

    python -m screen_monitor [--device-index N] [--status-path PATH] [--config PATH]

The monitoring state machine, alarms, notifications, and the GUI are not
part of this milestone - see the project plan's growth order (section 10)
for what comes next. Detection results are logged only; nothing acts on
them yet.
"""

from __future__ import annotations

import argparse
import logging
import signal
import threading
import time
from pathlib import Path
from typing import List, Optional

from screen_monitor.alarms.alarm_manager import DEFAULT_REPEAT_INTERVAL_SECONDS, AlarmManager
from screen_monitor.camera.frame_health import FrameHealthValidator
from screen_monitor.camera.usb_camera import UsbCamera, UsbCameraError
from screen_monitor.common.clock import RealClock
from screen_monitor.common.enums import FrameHealthReason, SystemState
from screen_monitor.configuration.loader import (
    ConfigError,
    load_config,
    load_detection_settings,
    load_regions,
)
from screen_monitor.detection.red_detector import DEFAULT_SATURATION_MIN, DEFAULT_VALUE_MIN, RedDetector
from screen_monitor.detection.region import Region
from screen_monitor.detection.region_detector import RegionDetector
from screen_monitor.diagnostics.system_status import build_status
from screen_monitor.monitoring.monitor import RegionMonitor
from screen_monitor.watchdog.heartbeat import HeartbeatWriter

logger = logging.getLogger("screen_monitor.main")


class Application:
    def __init__(
        self,
        device_index: int,
        status_path: Path,
        frame_timeout_seconds: float,
        regions: Optional[List[Region]] = None,
        saturation_min: int = DEFAULT_SATURATION_MIN,
        value_min: int = DEFAULT_VALUE_MIN,
        loop_interval_seconds: float = 0.2,
        interactive_ack: bool = False,
        alarm_repeat_interval_seconds: float = DEFAULT_REPEAT_INTERVAL_SECONDS,
    ) -> None:
        self._clock = RealClock()
        self._camera = UsbCamera(device_index=device_index, clock=self._clock)
        self._validator = FrameHealthValidator(
            max_age_seconds=frame_timeout_seconds, clock=self._clock
        )
        self._heartbeat = HeartbeatWriter(status_path)
        self._regions = regions or []
        self._region_detector = (
            RegionDetector(RedDetector(saturation_min=saturation_min, value_min=value_min))
            if self._regions
            else None
        )
        # The state machine + timers (confirmation/alarm) that turn raw
        # per-cycle detection results into region state over time.
        self._region_monitor = RegionMonitor(self._regions, self._clock) if self._regions else None
        # Real local alarm output (milestone 5): repeats a beep for every
        # ALARM_ACTIVE region until it's acknowledged. RegionMonitor's own
        # default_alarm_hook still fires once per trigger too (it's just a
        # log line) - AlarmManager is driven separately below since it also
        # needs ALARM_ACKNOWLEDGED/REGION_RETURNED_NORMAL events (to stop
        # repeating) and a per-cycle tick() (to repeat on a schedule), not
        # just the single REGION_ALARM_TRIGGERED event the hook seam covers.
        self._alarm_manager = (
            AlarmManager(self._clock, repeat_interval_seconds=alarm_repeat_interval_seconds)
            if self._regions
            else None
        )
        self._interactive_ack = interactive_ack
        self._loop_interval = loop_interval_seconds
        self._shutdown_requested = False
        self._state = SystemState.STARTING
        self._last_frame_time = None
        self._last_status_change = self._clock.now()

    def _set_state(self, state: SystemState) -> None:
        if state != self._state:
            logger.info("State transition: %s -> %s", self._state.value, state.value)
            self._state = state
            self._last_status_change = self._clock.now()

    def request_shutdown(self, *_args) -> None:
        logger.info("Shutdown requested")
        self._shutdown_requested = True

    def run(self) -> None:
        signal.signal(signal.SIGINT, self.request_shutdown)
        signal.signal(signal.SIGTERM, self.request_shutdown)

        self._set_state(SystemState.SELF_TESTING)
        try:
            self._camera.connect()
        except UsbCameraError as exc:
            logger.error("Camera self-test failed: %s", exc)
            self._set_state(SystemState.SYSTEM_FAULT)
            self._publish_heartbeat()
            return

        self._set_state(SystemState.MONITORING)

        if self._interactive_ack and self._region_monitor is not None:
            self._start_interactive_ack_listener()

        try:
            while not self._shutdown_requested:
                self._loop_once()
                time.sleep(self._loop_interval)
        except Exception:
            logger.exception("Unhandled exception in main loop")
            self._set_state(SystemState.SYSTEM_FAULT)
            self._publish_heartbeat()
            raise
        finally:
            self._set_state(SystemState.SHUTTING_DOWN)
            self._camera.disconnect()
            self._set_state(SystemState.STOPPED)
            self._publish_heartbeat()

    def _loop_once(self) -> None:
        frame = self._camera.read_frame()
        result = self._validator.validate(frame)

        if result.is_valid:
            self._last_frame_time = self._clock.now()
            if self._state != SystemState.MONITORING:
                self._set_state(SystemState.MONITORING)

            if self._region_detector is not None:
                detections = self._region_detector.analyze(frame, self._regions)
                for detection in detections:
                    logger.debug(
                        "Region '%s': %s (red=%.1f%%, confidence=%.2f) - %s",
                        detection.region_id,
                        detection.status.value,
                        detection.red_percentage * 100,
                        detection.confidence,
                        detection.reason,
                    )
                if self._region_monitor is not None:
                    # Events (confirmed red, alarm triggered, returned to
                    # normal, ...) are logged at INFO by RegionMonitor
                    # itself, so per-cycle raw detections above are
                    # deliberately dropped to DEBUG - this is what keeps
                    # the log readable once regions are actually being
                    # tracked over time instead of just printed each cycle.
                    events = self._region_monitor.process(detections)
                    if self._alarm_manager is not None:
                        for event in events:
                            self._alarm_manager.handle_event(event)
        else:
            logger.warning("Frame invalid: %s (%s)", result.reason.value, result.detail)
            if not self._camera.is_connected():
                self._set_state(SystemState.SYSTEM_FAULT)

        if self._alarm_manager is not None:
            # Runs every cycle regardless of frame validity or new
            # events - a repeat beep can be due on a cycle with nothing
            # new to report.
            self._alarm_manager.tick()

        status = build_status(
            overall_state=self._state,
            camera_connected=self._camera.is_connected(),
            last_frame_reason=result.reason,
            last_frame_age_seconds=(
                None
                if self._last_frame_time is None
                else self._clock.now() - self._last_frame_time
            ),
            watchdog_visible=True,
        )
        logger.debug("Status: %s", status)

        self._publish_heartbeat()

    def _start_interactive_ack_listener(self) -> None:
        """TEMPORARY test scaffolding for milestone 4: lets you type "ack"
        (or "ack <region_id>" with multiple regions) at the terminal to
        acknowledge an active alarm, since there's no GUI yet to do it for
        real. Remove this once the GUI milestone adds a real caller for
        RegionMonitor.acknowledge().
        """
        region_ids = [r.id for r in self._regions]
        print(
            f"[interactive-ack] Type 'ack' to acknowledge an alarm "
            f"(regions: {region_ids}). Use 'ack <region_id>' if there's more than one."
        )

        def _listen() -> None:
            for line in iter(input, ""):
                parts = line.strip().split()
                if not parts or parts[0] != "ack":
                    continue
                if len(parts) >= 2:
                    region_id = parts[1]
                elif len(region_ids) == 1:
                    region_id = region_ids[0]
                else:
                    print(f"Multiple regions configured - use: ack <region_id> ({region_ids})")
                    continue
                event = self._region_monitor.acknowledge(region_id)
                if event is None:
                    print(f"No-op: region '{region_id}' isn't currently ALARM_ACTIVE (or doesn't exist)")
                else:
                    if self._alarm_manager is not None:
                        self._alarm_manager.handle_event(event)
                    print(f"Acknowledged region '{region_id}'")

        thread = threading.Thread(target=_listen, daemon=True)
        thread.start()

    def _publish_heartbeat(self) -> None:
        # A heartbeat write failure (e.g. a transient file lock on Windows)
        # should never take down the camera/monitoring loop - it just means
        # the watchdog sees a slightly stale file for one cycle.
        try:
            self._heartbeat.write(
                current_state=self._state.value,
                last_loop_time=self._clock.now(),
                last_frame_time=self._last_frame_time,
                last_status_change=self._last_status_change,
            )
        except OSError:
            logger.exception("Failed to publish heartbeat this cycle")


def main() -> None:
    parser = argparse.ArgumentParser(description="Screen monitor")
    parser.add_argument("--device-index", type=int, default=0, help="USB camera device index")
    parser.add_argument(
        "--status-path",
        default="data/status/heartbeat.json",
        help="Path to write the heartbeat status file (read by the watchdog)",
    )
    parser.add_argument(
        "--frame-timeout",
        type=float,
        default=3.0,
        help="Seconds after which a received frame is considered stale",
    )
    parser.add_argument(
        "--config",
        default=None,
        help=(
            "Path to a config JSON file with a 'regions' list. If omitted, "
            "the app runs camera+watchdog only, with no region detection "
            "(same behavior as before this milestone)."
        ),
    )
    parser.add_argument(
        "--interactive-ack",
        action="store_true",
        help=(
            "TEMPORARY test flag for milestone 4: lets you type 'ack' in "
            "the terminal to acknowledge an active alarm, since there's no "
            "GUI yet. Will be removed once the GUI milestone exists."
        ),
    )
    parser.add_argument(
        "--alarm-repeat-seconds",
        type=float,
        default=DEFAULT_REPEAT_INTERVAL_SECONDS,
        help="Seconds between repeated alarm beeps while a region is ALARM_ACTIVE",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    regions: List[Region] = []
    saturation_min = DEFAULT_SATURATION_MIN
    value_min = DEFAULT_VALUE_MIN
    if args.config:
        try:
            config = load_config(Path(args.config))
            regions = load_regions(config)
            detection_settings = load_detection_settings(config)
            saturation_min = detection_settings["saturation_min"]
            value_min = detection_settings["value_min"]
        except ConfigError as exc:
            logger.error("Failed to load config: %s", exc)
            return

        if regions:
            logger.info(
                "Loaded %d region(s): %s (saturation_min=%d, value_min=%d)",
                len(regions),
                [r.id for r in regions],
                saturation_min,
                value_min,
            )
        else:
            logger.warning("Config file has no regions defined - detection will be skipped")

    app = Application(
        device_index=args.device_index,
        status_path=Path(args.status_path),
        frame_timeout_seconds=args.frame_timeout,
        regions=regions,
        saturation_min=saturation_min,
        value_min=value_min,
        interactive_ack=args.interactive_ack,
        alarm_repeat_interval_seconds=args.alarm_repeat_seconds,
    )
    app.run()


if __name__ == "__main__":
    main()

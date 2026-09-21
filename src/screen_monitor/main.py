"""Main application entry point.

Full pipeline through the GUI milestone (plan section 10, steps 1-12,
step 4, and 15):

    USB camera -> frame reception -> frame health validation ->
    stream health monitoring -> region detection (if --config given) ->
    region state machine -> local alarm -> heartbeat ->
    independent watchdog

Run headless:

    python -m screen_monitor [--device-index N] [--status-path PATH] [--config PATH]

Or with the live dashboard:

    python -m screen_monitor --config PATH --gui

Remote notifications (step 13) and watchdog failure notifications
(step 7) are deliberately skipped - see README. Step 14 (a dedicated
startup self-test module) is deferred until after step 17.
"""

from __future__ import annotations

import argparse
import logging
import signal
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Set

from screen_monitor.alarms.alarm_manager import DEFAULT_REPEAT_INTERVAL_SECONDS, AlarmManager
from screen_monitor.camera.frame_health import FrameHealthValidator
from screen_monitor.camera.stream_monitor import StreamMonitor
from screen_monitor.camera.usb_camera import UsbCamera, UsbCameraError
from screen_monitor.common.clock import RealClock
from screen_monitor.common.enums import FrameHealthReason, SystemState
from screen_monitor.configuration.loader import (
    ConfigError,
    load_config,
    load_detection_settings,
    load_regions,
    save_region,
)
from screen_monitor.detection.red_detector import DEFAULT_SATURATION_MIN, DEFAULT_VALUE_MIN, RedDetector
from screen_monitor.detection.region import Region
from screen_monitor.detection.region_detector import RegionDetector
from screen_monitor.diagnostics.system_status import FAULT_FPS, FAULT_STREAM, FAULT_WATCHDOG, build_status
from screen_monitor.monitoring.monitor import RegionMonitor
from screen_monitor.watchdog.heartbeat import HeartbeatWriter

logger = logging.getLogger("screen_monitor.main")

MAX_REGIONS = 12
_ACKNOWLEDGEABLE_FAULTS = (FAULT_WATCHDOG, FAULT_STREAM, FAULT_FPS)


@dataclass
class RegionEditResult:
    """Outcome of adding/redrawing a region from the dashboard.

    `ok` is False when the edit was refused (message says why).
    `persisted` is False when the change is live but wasn't written to a
    config file (no --config given, or the write failed) - it will be
    lost on restart, which the dashboard should tell the user.
    """

    ok: bool
    message: str = ""
    persisted: bool = False


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
        stream_frozen_threshold_seconds: float = 5.0,
        stream_timeout_seconds: Optional[float] = None,
        config_path: Optional[Path] = None,
    ) -> None:
        self._config_path = Path(config_path) if config_path else None
        self._clock = RealClock()
        self._camera = UsbCamera(device_index=device_index, clock=self._clock)
        self._validator = FrameHealthValidator(
            max_age_seconds=frame_timeout_seconds, clock=self._clock
        )
        self._heartbeat = HeartbeatWriter(status_path)
        # Stream-level health across many frames (growth-order step 4):
        # frame rate, whether the picture is actually changing (frozen
        # detection), and connection/timeout state. Reuses frame_timeout
        # for its own timeout window unless a distinct value is given -
        # frame_health.py's staleness check and this are related but
        # separate concerns (one frame vs. the stream over time).
        self._stream_monitor = StreamMonitor(
            frozen_threshold_seconds=stream_frozen_threshold_seconds,
            timeout_seconds=(
                stream_timeout_seconds if stream_timeout_seconds is not None else frame_timeout_seconds
            ),
            clock=self._clock,
        )
        self._regions = list(regions or [])
        # The detector, monitor and alarm manager exist even with zero
        # regions configured (they just have nothing to do) so the
        # dashboard can add the first region at runtime.
        self._region_detector = RegionDetector(
            RedDetector(saturation_min=saturation_min, value_min=value_min)
        )
        # The state machine + timers (confirmation/alarm) that turn raw
        # per-cycle detection results into region state over time.
        self._region_monitor = RegionMonitor(self._regions, self._clock)
        # Real local alarm output (milestone 5): repeats a beep for every
        # ALARM_ACTIVE region until it's acknowledged. RegionMonitor's own
        # default_alarm_hook still fires once per trigger too (it's just a
        # log line) - AlarmManager is driven separately below since it also
        # needs ALARM_ACKNOWLEDGED/REGION_RETURNED_NORMAL events (to stop
        # repeating) and a per-cycle tick() (to repeat on a schedule), not
        # just the single REGION_ALARM_TRIGGERED event the hook seam covers.
        self._alarm_manager = AlarmManager(
            self._clock, repeat_interval_seconds=alarm_repeat_interval_seconds
        )
        self._interactive_ack = interactive_ack
        self._loop_interval = loop_interval_seconds
        self._shutdown_requested = False
        self._state = SystemState.STARTING
        self._last_frame_time = None
        self._last_status_change = self._clock.now()
        self._stream_was_frozen = False
        self._stream_was_timed_out = False
        # None until the first _loop_once() completes - gui.py's view
        # model layer handles this (renders a "starting up" default).
        self._last_system_status = None
        self._last_stream_status = None
        # Latest valid frame + this cycle's detections, cached for the
        # dashboard's embedded stream view (same reasoning as above).
        # The frame is None whenever the current cycle's frame was invalid.
        self._last_frame = None
        self._last_detections: list = []
        # Whether the most recent heartbeat write succeeded. This is what
        # the dashboard's "Watchdog Status" reflects: the app can't see
        # the watchdog process itself, only whether it is publishing what
        # the watchdog reads.
        self._heartbeat_ok = False
        # System faults (see SystemStatus.active_faults) the user has
        # acknowledged. Purely a display/bookkeeping flag - it silences
        # nothing; an entry is dropped automatically once that fault
        # clears, so a recurrence is shown as new.
        self._acknowledged_faults: Set[str] = set()

    def _set_state(self, state: SystemState) -> None:
        if state != self._state:
            logger.info("State transition: %s -> %s", self._state.value, state.value)
            self._state = state
            self._last_status_change = self._clock.now()

    def request_shutdown(self, *_args) -> None:
        logger.info("Shutdown requested")
        self._shutdown_requested = True

    def start(self) -> bool:
        """Runs self-test + camera connect. Returns True if monitoring can
        proceed, False on a fatal startup fault (already logged, state set
        to SYSTEM_FAULT, heartbeat published). Split out from run() so
        gui.py can drive the same startup sequence before switching to its
        own Tkinter-scheduled tick loop instead of run()'s blocking one.
        """
        signal.signal(signal.SIGINT, self.request_shutdown)
        signal.signal(signal.SIGTERM, self.request_shutdown)

        self._set_state(SystemState.SELF_TESTING)
        try:
            self._camera.connect()
        except UsbCameraError as exc:
            logger.error("Camera self-test failed: %s", exc)
            self._set_state(SystemState.SYSTEM_FAULT)
            self._publish_heartbeat()
            return False

        self._set_state(SystemState.MONITORING)
        self._publish_heartbeat()

        if self._interactive_ack and self._region_monitor is not None:
            self._start_interactive_ack_listener()

        return True

    def shutdown(self) -> None:
        self._set_state(SystemState.SHUTTING_DOWN)
        self._camera.disconnect()
        self._set_state(SystemState.STOPPED)
        self._publish_heartbeat()

    def acknowledge(self, region_id: str):
        """Acknowledge an active alarm for a region. Shared by the
        interactive-ack CLI listener and gui.py's Acknowledge button, so
        there's exactly one place that knows acknowledging must also tell
        AlarmManager to stop repeating.
        """
        if self._region_monitor is None:
            return None
        event = self._region_monitor.acknowledge(region_id)
        if event is not None and self._alarm_manager is not None:
            self._alarm_manager.handle_event(event)
        return event

    # -- read-only accessors for the dashboard ---------------------------

    @property
    def regions(self) -> List[Region]:
        return list(self._regions)

    @property
    def shutdown_requested(self) -> bool:
        return self._shutdown_requested

    @property
    def loop_interval(self) -> float:
        return self._loop_interval

    def last_system_status(self):
        return self._last_system_status

    def latest_frame(self):
        return self._last_frame

    def latest_detections(self) -> list:
        return list(self._last_detections)

    def region_states(self) -> dict:
        return self._region_monitor.all_states()

    def is_alarming(self, region_id: str) -> bool:
        return self._alarm_manager.is_alarming(region_id)

    def acknowledged_faults(self) -> Set[str]:
        return set(self._acknowledged_faults)

    def acknowledge_fault(self, kind: str) -> bool:
        """Mark a system fault (watchdog / stream / fps) as acknowledged.
        Only applies while that fault is actually active.
        """
        status = self._last_system_status
        if kind not in _ACKNOWLEDGEABLE_FAULTS or status is None:
            return False
        if kind not in status.active_faults():
            return False
        self._acknowledged_faults.add(kind)
        logger.info("System fault '%s' acknowledged", kind)
        return True

    # -- region editing (called by the dashboard) -------------------------

    def replace_region(self, region: Region) -> RegionEditResult:
        """Apply a redrawn/edited region live and persist it."""
        index = next((i for i, r in enumerate(self._regions) if r.id == region.id), None)
        if index is None:
            return RegionEditResult(False, f"Region '{region.id}' doesn't exist")
        error = self._check_fits_frame(region)
        if error:
            return RegionEditResult(False, error)
        if not self._region_monitor.replace_region(region):
            return RegionEditResult(
                False,
                f"Region '{region.id}' can't be changed right now - acknowledge its alarm first.",
            )
        self._regions[index] = region
        return self._persist(region)

    def add_region(self, region: Region) -> RegionEditResult:
        """Start monitoring a new region live and persist it."""
        if len(self._regions) >= MAX_REGIONS:
            return RegionEditResult(False, f"At most {MAX_REGIONS} regions are supported")
        error = self._check_fits_frame(region)
        if error:
            return RegionEditResult(False, error)
        if not self._region_monitor.add_region(region):
            return RegionEditResult(False, f"A region with id '{region.id}' already exists")
        self._regions.append(region)
        return self._persist(region)

    def _check_fits_frame(self, region: Region) -> Optional[str]:
        frame = self._last_frame
        if frame is not None and not region.is_within_frame(frame.width, frame.height):
            return f"Region doesn't fit inside the {frame.width}x{frame.height} frame"
        return None

    def _persist(self, region: Region) -> RegionEditResult:
        if self._config_path is None:
            return RegionEditResult(
                True, "Applied, but not saved - the app was started without --config.", False
            )
        try:
            save_region(self._config_path, region)
        except (OSError, ConfigError) as exc:
            logger.exception("Failed to save region '%s' to %s", region.id, self._config_path)
            return RegionEditResult(True, f"Applied, but saving to the config failed: {exc}", False)
        return RegionEditResult(True, "Saved.", True)

    def run(self) -> None:
        if not self.start():
            return

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
            self.shutdown()

    def _loop_once(self) -> None:
        frame = self._camera.read_frame()
        result = self._validator.validate(frame)

        # Runs regardless of per-frame validity - StreamMonitor needs to
        # see invalid/stale cycles too, to correctly track timeout state.
        stream_status = self._stream_monitor.update(frame, result.is_valid)
        self._log_stream_transitions(stream_status)

        self._last_frame = frame if result.is_valid else None
        self._last_detections = []

        if result.is_valid:
            self._last_frame_time = self._clock.now()
            if self._state != SystemState.MONITORING:
                self._set_state(SystemState.MONITORING)

            if self._region_detector is not None:
                detections = self._region_detector.analyze(frame, self._regions)
                self._last_detections = detections
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
            watchdog_visible=self._heartbeat_ok,
            stream_frame_rate=stream_status.frame_rate,
            stream_frozen=stream_status.frozen,
            stream_timed_out=stream_status.timed_out,
        )
        logger.debug("Status: %s", status)
        # Cached for gui.py to read each tick, since it lives in the same
        # process and shouldn't need to recompute or re-derive this itself
        # (Rule 1: it displays state, it doesn't compute it).
        self._last_system_status = status
        self._last_stream_status = stream_status
        # A fault that has cleared is no longer "acknowledged" - if it
        # comes back it should show as a fresh, unacknowledged problem.
        self._acknowledged_faults &= status.active_faults()

        self._publish_heartbeat()

    def _log_stream_transitions(self, stream_status) -> None:
        # Logged on transitions only (not every cycle) to avoid spamming
        # the log every 0.2s while a fault condition persists - INFO
        # already covers every region state transition, so this stays
        # readable alongside that.
        if stream_status.frozen != self._stream_was_frozen:
            if stream_status.frozen:
                logger.warning(
                    "Stream appears frozen - no pixel change for over %.1fs",
                    stream_status.last_changed_frame_age_seconds or 0.0,
                )
            else:
                logger.info("Stream is no longer frozen - picture is changing again")
            self._stream_was_frozen = stream_status.frozen

        if stream_status.timed_out != self._stream_was_timed_out:
            if stream_status.timed_out:
                logger.warning(
                    "Stream timed out - no valid frame for over %.1fs",
                    stream_status.last_valid_frame_age_seconds or 0.0,
                )
            else:
                logger.info("Stream recovered from timeout - valid frames arriving again")
            self._stream_was_timed_out = stream_status.timed_out

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
                event = self.acknowledge(region_id)
                if event is None:
                    print(f"No-op: region '{region_id}' isn't currently ALARM_ACTIVE (or doesn't exist)")
                else:
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
            self._heartbeat_ok = True
        except OSError:
            self._heartbeat_ok = False
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
            "Terminal-based alternative to --gui: type 'ack' to acknowledge "
            "an active alarm. Ignored if --gui is also passed (the dashboard "
            "has a real Acknowledge button)."
        ),
    )
    parser.add_argument(
        "--alarm-repeat-seconds",
        type=float,
        default=DEFAULT_REPEAT_INTERVAL_SECONDS,
        help="Seconds between repeated alarm beeps while a region is ALARM_ACTIVE",
    )
    parser.add_argument(
        "--stream-frozen-threshold",
        type=float,
        default=5.0,
        help="Seconds of an unchanging picture before the stream is considered frozen",
    )
    parser.add_argument(
        "--stream-timeout",
        type=float,
        default=None,
        help=(
            "Seconds with no valid frame before the stream is considered timed "
            "out. Defaults to --frame-timeout if not given."
        ),
    )
    parser.add_argument(
        "--gui",
        action="store_true",
        help=(
            "Run the live Tkinter dashboard instead of the headless CLI loop. "
            "Runs in the same process, with an Acknowledge button per region "
            "instead of --interactive-ack."
        ),
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
        interactive_ack=(args.interactive_ack and not args.gui),
        alarm_repeat_interval_seconds=args.alarm_repeat_seconds,
        stream_frozen_threshold_seconds=args.stream_frozen_threshold,
        stream_timeout_seconds=args.stream_timeout,
        config_path=Path(args.config) if args.config else None,
    )

    if args.gui:
        # Lazy import: Tkinter needs OS-level Tk libraries that aren't
        # guaranteed present everywhere (e.g. minimal containers), so
        # headless (--gui not passed) usage must never require it.
        from screen_monitor.interface.gui import Dashboard

        dashboard = Dashboard(
            app,
            config_path=Path(args.config) if args.config else None,
            device_index=args.device_index,
        )
        dashboard.start()
    else:
        app.run()


if __name__ == "__main__":
    main()

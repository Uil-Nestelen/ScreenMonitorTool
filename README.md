# Screen Red-Alert Monitoring System

Milestones so far, per the project's development plan: camera capture → frame-health → heartbeat → independent watchdog (section 9); region configuration and HSV-based red detection (section 10, steps 8-9); the region state machine + confirmation/alarm timers (section 10, steps 10-11); local alarm output (section 10, step 12); stream-level health monitoring (section 10, step 4 — built out of order, after local alarm, by choice); and now the GUI (section 10, step 15).

**Deliberately skipped for now:** remote notifications (step 13) and watchdog failure notifications (step 7). Both exist in the plan for someone who isn't in the room with the machine — since the local alarm is always audible in person, these were dropped as unnecessary complexity rather than deferred by oversight. Revisit if this ever needs to run unattended.

**Deliberately deferred:** a dedicated startup self-test module (step 14) — `main.py` does a minimal inline version today. Will be picked up after step 17 (automatic recovery), by choice.

```
USB webcam
     ↓
Python (OpenCV VideoCapture)
     ↓
Frame reception
     ↓
Frame health validation (missing / undersized / empty / stale) - one frame at a time
     ↓
Stream health monitoring (frame rate, frozen-stream detection, timeout) - across frames, over time
     ↓
Region detection (if --config given): crop each region, check image
quality, run HSV red-percentage detection → RED / NORMAL / UNKNOWN
     ↓
Region monitoring: state machine turns per-cycle detections into
NORMAL → RED_PENDING → RED_ACTIVE → ALARM_ACTIVE → ALARM_ACKNOWLEDGED,
using each region's confirmation_seconds / alarm_seconds
     ↓
Local alarm: AlarmManager plays a repeating beep for every ALARM_ACTIVE
region until it's acknowledged (--alarm-repeat-seconds, default 5s)
     ↓
Heartbeat file (atomic write)          Live dashboard (--gui): same
     ↓                                 process, polls this same state
Independent watchdog process           each tick and can Acknowledge
(separate from the main process)       directly - see below
```

Region state transitions are logged at INFO (raw per-cycle detections are DEBUG). Stream health transitions (frozen/timeout starting or clearing) log at WARNING/INFO too, but only on the transition itself, not every cycle.

## Running the GUI

```bash
python -m screen_monitor --config config/config.json --gui
```

- Shows system state, camera status, stream frame rate/frozen/timeout, watchdog heartbeat status, and one row per region with a live status and an **Acknowledge** button (enabled only while that region is `ALARM_ACTIVE`, matching exactly when acknowledging would actually do something).
- A red banner across the top replaces "All systems normal" whenever the camera is disconnected, or the stream is frozen/timed out.
- **"Configure Regions..."** launches `interface/setup_wizard.py` (the old `region_selector.py` calibration tool, relocated) as a separate process. It needs exclusive camera access, same as the monitoring engine — **you can't run both against the same device at once.** Close the dashboard first if you need to recalibrate, then relaunch.
- The GUI runs in the *same process* as the monitoring engine (driven by Tkinter's `.after()` instead of the headless loop's blocking `while` + `sleep`), specifically so Acknowledge can call `RegionMonitor.acknowledge()` directly with no IPC. `--interactive-ack` still exists for terminal-only testing, but is ignored when `--gui` is passed.
- Requires Tkinter, which ships with standard Python installs on Windows/Mac by default (nothing to `pip install`) but needs an OS-level Tk package on some minimal Linux setups (e.g. `sudo apt install python3-tk`).

## How region monitoring behaves

- `RED_PENDING` only becomes `RED_ACTIVE` once red has been continuously read for a region's `confirmation_seconds`; `RED_ACTIVE` only becomes `ALARM_ACTIVE` after `alarm_seconds`.
- **UNKNOWN readings (camera glitch, bad image quality) never reset or pause a running timer once a red condition is already in progress** — they're treated the same as RED. This is a deliberate fail-safe extension of Rule 4 ("UNKNOWN must never silently become NORMAL"): a flaky camera must not be able to delay or hide a real alarm. An UNKNOWN reading from `NORMAL`, though, does *not* start a new timer — only an actual RED reading can originate one.
- **`ALARM_ACTIVE` only ever clears via explicit acknowledgment** (`RegionMonitor.acknowledge(region_id)`), never just because a later reading happens to come back NORMAL or UNKNOWN. This prevents a brief flicker (or a person briefly blocking the camera) from silently cancelling a real alarm. Acknowledge it from the GUI, or with `--interactive-ack` in a terminal.

## How local alarm output behaves

- `AlarmManager` plays a beep the moment a region hits `ALARM_ACTIVE`, then repeats it every `--alarm-repeat-seconds` (default 5s) for as long as that region stays `ALARM_ACTIVE` and unacknowledged.
- Acknowledging a region (or it returning to `NORMAL`, which per the state machine can only happen after acknowledgment anyway) stops the repeats immediately.
- The actual "make noise" step is `alarms/local_audio.py`'s `SystemBeepBackend` - `winsound.Beep` on Windows, an ASCII bell character elsewhere. It's a placeholder for a real alert sound (`SoundFileBackend` is stubbed in the same file, not yet wired up to a playback library) - swap it in via `AlarmManager(audio_backend=...)` once a real sound and library are picked.
- Per Rule 1, `AlarmManager` only decides *when* to make noise; it doesn't know why a region is alarming, and `RegionStateMachine`/`RegionMonitor` don't know alarms make sound at all.

## How stream health monitoring behaves

- `StreamMonitor` (`camera/stream_monitor.py`) tracks the camera stream *over time*, separately from `frame_health.py`'s per-frame checks: frame rate, whether the picture is actually changing (a stuck camera driver can keep returning a perfectly valid, non-stale frame that just never updates), and whether valid frames have stopped arriving at all.
- **Frozen-stream detection** compares a small downsized thumbnail of each frame; if it's pixel-identical to the last one for longer than `--stream-frozen-threshold` (default 5s), the stream is flagged `frozen`. A genuinely live feed has enough sensor noise that this essentially never false-positives.
- **Timeout detection** flags `timed_out` once no valid frame has arrived for `--stream-timeout` (defaults to `--frame-timeout` if not set separately).
- Neither `frozen` nor `timed_out` currently forces a `SystemState` transition on their own — `StreamMonitor` only reports; `main.py` logs transitions and folds the status into `SystemStatus.camera_status` (which becomes `FAULT` when either is true), visible via the heartbeat file. Whether frozen/timeout should also force `SYSTEM_FAULT` the way a hard camera disconnect does is an open decision — ask if you want that wired in too.

## Install

```bash
pip install -e .
# or, minimally:
pip install opencv-python
```

## Run the main application

```bash
python -m screen_monitor --device-index 0
```

Camera + watchdog only, no region detection — same as before this milestone. Reads frames in a loop, validates each one, and writes `data/status/heartbeat.json` every cycle. Press Ctrl+C to stop.

To also run region detection, pass a config file with a `regions` list:

```bash
python -m screen_monitor --device-index 0 --config config/config.json
```

Region state transitions log at INFO, e.g.:

```
Event: REGION_RED_CONFIRMED (region='test-zone1') - Red pixel percentage 42.3% meets or exceeds threshold 30.0%
Event: REGION_ALARM_TRIGGERED (region='test-zone1') - Region 'test-zone1' has been continuously red for at least 300s
```

(Raw per-cycle detections still happen every loop, but log at DEBUG, to keep this readable.)

Two extra flags for terminal-only testing, or fine-tuning alarm behavior:

```bash
python -m screen_monitor --config config/config.json --interactive-ack --alarm-repeat-seconds 5
```

- `--interactive-ack` — type `ack` (or `ack <region_id>` with multiple regions) at the terminal to acknowledge an active alarm. Ignored if `--gui` is also passed.
- `--alarm-repeat-seconds` — how often the alarm beep repeats while a region is `ALARM_ACTIVE` and unacknowledged (default 5s).

## Find your region's pixel coordinates and calibrate detection

Rather than guessing x/y/width/height by hand, drag a box on the live feed. This tool doubles as a **calibration view**: any region you've already saved is shown automatically with a live, updating red-percentage readout right on the box — so you can watch the number change in real time as you hold up something red, move the phone, or change lighting, without needing to run the full app and dig through log lines.

```bash
python -m screen_monitor.interface.setup_wizard --device-index 0 --output config/config.json
```

(The dashboard's "Configure Regions..." button launches this same tool. `python tools/region_selector.py ...` also still works — it's now a two-line wrapper around the same code, kept for backward compatibility.)

- Drag to draw a **new** rectangle (green), press `s` to save it (you'll be prompted for an id, name, and red-percentage threshold).
- Any region **already saved** in `--output` is loaded on startup and drawn in red/yellow/gray depending on its current live reading, e.g. `screen_1: 42.3% RED (conf 0.71)` or `screen_1: TOO_DARK`.
- **`m` toggles a mask debug view**: the whole feed is dimmed to grayscale, and every pixel the detector currently counts as "red" lights up in solid red. This answers the question that matters most when detection isn't working: *is the region misplaced, or is the color itself not being picked up?* If the red thing is clearly visible in the normal view but nothing lights up in mask view, it's a color/threshold problem, not a placement problem — a red screen viewed from further away or at an angle often loses saturation (looks washed-out/pink) well before its hue actually changes.
- **`[` / `]`** decrease/increase `saturation_min`, **`-` / `=`** decrease/increase `value_min`, live — watch the mask view fill in as you loosen them. These are the two thresholds worth tuning; hue rarely needs adjusting. Saving a region (`s`) also saves the current thresholds into the config file, so `python -m screen_monitor --config ...` picks up the same calibrated values automatically.
- `r` clears the rectangle you're drawing, `q` quits.

**Important for real deployment:** calibrate with the phone in its *actual final position*, not while testing at your desk. A region drawn close-up will look completely different (framing, distance, angle, lighting) once the phone is mounted further back to see the whole screen — redraw the region and re-tune the thresholds once the phone is where it'll actually stay.

## Run the watchdog (in a second terminal)

```bash
python -m screen_monitor.watchdog.watchdog --status-path data/status/heartbeat.json
```

The watchdog only reads the heartbeat file — it never imports the main application's code. If you kill the main process (`kill -9`) or freeze it, the watchdog will log a stale-heartbeat error within `--timeout` seconds (default 10s).

## Preview just the camera

Useful for checking your webcam works before running the full app:

```bash
python tools/camera_viewer.py --device-index 0
```

Shows a live window with a green "VALID" / red fault-reason label overlay. Press `q` to quit.

## Run the tests

```bash
pip install pytest
pytest tests/
```

Tests use a `FakeClock`, synthetic images, and temp directories, so no real camera or watchdog process is needed to run them.

## A note on the red detector

`RedDetector` reports `UNKNOWN` (never a guessed `NORMAL`) whenever the cropped region can't be evaluated reliably — too dark, overexposed, or flat/colorless (a lens cap, a blank wall, a frozen gray feed). This is Rule 4 from the plan: unknown must never silently become normal. A genuinely uniform solid-red alert screen is *not* flagged this way, because the flatness check also looks at color saturation, not just grayscale brightness variance.

## What's intentionally NOT here yet

Per the plan's recommended growth order (section 10): remote notifications (step 13) and watchdog failure notifications (step 7) are deliberately skipped, not forgotten — see the note at the top. A dedicated startup self-test module (step 14 — `main.py` does a minimal inline version today) is deferred until after step 17, also by choice. What's left: logging/audit trail (step 16), automatic recovery (step 17), failure-injection testing (step 18), and packaging/deployment (step 19).

## Project layout

```
screen-monitor/
├── pyproject.toml
├── config/config.example.json
├── src/screen_monitor/
│   ├── __main__.py, main.py        # main monitoring loop
│   ├── camera/                     # source.py interface, usb_camera.py, frame.py, frame_health.py, stream_monitor.py
│   ├── detection/                  # region.py, detector.py, red_detector.py, region_detector.py, confidence.py, image_quality.py
│   ├── monitoring/                 # region_state.py, timer.py, state_machine.py, events.py, monitor.py
│   ├── alarms/                     # alarm_manager.py, local_audio.py
│   ├── interface/                  # gui.py, view_models.py, status_display.py, setup_wizard.py
│   ├── configuration/loader.py     # minimal JSON config + region loading
│   ├── watchdog/                   # heartbeat.py (shared contract), watchdog.py (independent process)
│   ├── diagnostics/system_status.py
│   └── common/                     # enums.py, clock.py (testable time abstraction)
├── tools/                          # camera_viewer.py, region_selector.py (thin wrapper), simulate_monitor.py
└── tests/                          # test_camera.py, test_watchdog.py, test_region.py, test_red_detector.py,
                                     # test_timer.py, test_state_machine.py, test_monitor.py, test_alarm_manager.py,
                                     # test_stream_monitor.py, test_view_models.py
```
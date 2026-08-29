# Screen Red-Alert Monitoring System

Milestones so far, per the project's development plan: camera capture →
frame-health → heartbeat → independent watchdog (section 9); region
configuration and HSV-based red detection (section 10, steps 8-9); and
now the region state machine + confirmation/alarm timers (section 10,
steps 10-11).

```
USB webcam
     ↓
Python (OpenCV VideoCapture)
     ↓
Frame reception
     ↓
Frame health validation (missing / undersized / empty / stale)
     ↓
Region detection (if --config given): crop each region, check image
quality, run HSV red-percentage detection → RED / NORMAL / UNKNOWN
     ↓
Region monitoring: state machine turns per-cycle detections into
NORMAL → RED_PENDING → RED_ACTIVE → ALARM_ACTIVE → ALARM_ACKNOWLEDGED,
using each region's confirmation_seconds / alarm_seconds
     ↓
Heartbeat file (atomic write)
     ↓
Independent watchdog process (separate from the main process)
```

Region state transitions are logged at INFO (raw per-cycle detections
are now DEBUG, to keep the log readable). Real alarm output (local
sound, escalation) and notifications are still the next milestones — an
alarm-triggered event currently just logs a loud warning via a
placeholder hook (`monitoring/monitor.py`'s `default_alarm_hook`).

## How region monitoring behaves

- `RED_PENDING` only becomes `RED_ACTIVE` once red has been continuously
  read for a region's `confirmation_seconds`; `RED_ACTIVE` only becomes
  `ALARM_ACTIVE` after `alarm_seconds`.
- **UNKNOWN readings (camera glitch, bad image quality) never reset or
  pause a running timer once a red condition is already in progress** —
  they're treated the same as RED. This is a deliberate fail-safe
  extension of Rule 4 ("UNKNOWN must never silently become NORMAL"): a
  flaky camera must not be able to delay or hide a real alarm. An
  UNKNOWN reading from `NORMAL`, though, does *not* start a new timer —
  only an actual RED reading can originate one.
- **`ALARM_ACTIVE` only ever clears via explicit acknowledgment**
  (`RegionMonitor.acknowledge(region_id)`), never just because a later
  reading happens to come back NORMAL or UNKNOWN. This prevents a brief
  flicker (or a person briefly blocking the camera) from silently
  cancelling a real alarm. There's no CLI/GUI hookup for acknowledgment
  yet — that arrives with the GUI milestone.

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

Camera + watchdog only, no region detection — same as before this
milestone. Reads frames in a loop, validates each one, and writes
`data/status/heartbeat.json` every cycle. Press Ctrl+C to stop.

To also run region detection, pass a config file with a `regions` list:

```bash
python -m screen_monitor --device-index 0 --config config/config.json
```

Each cycle logs one line per region, e.g.:

```
Region 'screen_1': RED (red=42.3%, confidence=0.71) - Red pixel percentage 42.3% meets or exceeds threshold 30.0%
```

## Find your region's pixel coordinates and calibrate detection

Rather than guessing x/y/width/height by hand, drag a box on the live
feed. This tool doubles as a **calibration view**: any region you've
already saved is shown automatically with a live, updating red-percentage
readout right on the box — so you can watch the number change in real
time as you hold up something red, move the phone, or change lighting,
without needing to run the full app and dig through log lines.

```bash
python tools/region_selector.py --device-index 0 --output config/config.json
```

- Drag to draw a **new** rectangle (green), press `s` to save it (you'll
  be prompted for an id, name, and red-percentage threshold).
- Any region **already saved** in `--output` is loaded on startup and
  drawn in red/yellow/gray depending on its current live reading, e.g.
  `screen_1: 42.3% RED (conf 0.71)` or `screen_1: TOO_DARK`.
- **`m` toggles a mask debug view**: the whole feed is dimmed to
  grayscale, and every pixel the detector currently counts as "red" lights
  up in solid red. This answers the question that matters most when
  detection isn't working: *is the region misplaced, or is the color
  itself not being picked up?* If the red thing is clearly visible in the
  normal view but nothing lights up in mask view, it's a color/threshold
  problem, not a placement problem — a red screen viewed from further
  away or at an angle often loses saturation (looks washed-out/pink) well
  before its hue actually changes.
- **`[` / `]`** decrease/increase `saturation_min`, **`-` / `=`**
  decrease/increase `value_min`, live — watch the mask view fill in as you
  loosen them. These are the two thresholds worth tuning; hue rarely needs
  adjusting. Saving a region (`s`) also saves the current thresholds into
  the config file, so `python -m screen_monitor --config ...` picks up the
  same calibrated values automatically.
- `r` clears the rectangle you're drawing, `q` quits.

**Important for real deployment:** calibrate with the phone in its
*actual final position*, not while testing at your desk. A region drawn
close-up will look completely different (framing, distance, angle,
lighting) once the phone is mounted further back to see the whole
screen — redraw the region and re-tune the thresholds once the phone is
where it'll actually stay.


## Run the watchdog (in a second terminal)

```bash
python -m screen_monitor.watchdog.watchdog --status-path data/status/heartbeat.json
```

The watchdog only reads the heartbeat file — it never imports the main
application's code. If you kill the main process (`kill -9`) or freeze it,
the watchdog will log a stale-heartbeat error within `--timeout` seconds
(default 10s).

## Preview just the camera

Useful for checking your webcam works before running the full app:

```bash
python tools/camera_viewer.py --device-index 0
```

Shows a live window with a green "VALID" / red fault-reason label overlay.
Press `q` to quit.

## Run the tests

```bash
pip install pytest
pytest tests/
```

Tests use a `FakeClock`, synthetic images, and temp directories, so no
real camera or watchdog process is needed to run them.

## A note on the red detector

`RedDetector` reports `UNKNOWN` (never a guessed `NORMAL`) whenever the
cropped region can't be evaluated reliably — too dark, overexposed, or
flat/colorless (a lens cap, a blank wall, a frozen gray feed). This is
Rule 4 from the plan: unknown must never silently become normal. A
genuinely uniform solid-red alert screen is *not* flagged this way,
because the flatness check also looks at color saturation, not just
grayscale brightness variance.

## What's intentionally NOT here yet

Per the plan's recommended growth order (section 10), next up: real
local alarm output (`alarms/local_audio.py`, escalation timing) and
remote notifications. `RegionMonitor`'s `alarm_hook` seam is already in
place for `alarm_manager.py` to plug into. The GUI comes later still,
and should stay a pure presentation layer — it must never own state or
read the camera directly, and will be where `acknowledge()` finally gets
a real caller.

## Project layout

```
screen-monitor/
├── pyproject.toml
├── config/config.example.json
├── src/screen_monitor/
│   ├── __main__.py, main.py        # main monitoring loop
│   ├── camera/                     # source.py interface, usb_camera.py, frame.py, frame_health.py
│   ├── detection/                  # region.py, detector.py, red_detector.py, region_detector.py, confidence.py, image_quality.py
│   ├── monitoring/                 # region_state.py, timer.py, state_machine.py, events.py, monitor.py
│   ├── configuration/loader.py     # minimal JSON config + region loading
│   ├── watchdog/                   # heartbeat.py (shared contract), watchdog.py (independent process)
│   ├── diagnostics/system_status.py
│   └── common/                     # enums.py, clock.py (testable time abstraction)
├── tools/                          # camera_viewer.py, region_selector.py
└── tests/                          # test_camera.py, test_watchdog.py, test_region.py, test_red_detector.py,
                                     # test_timer.py, test_state_machine.py, test_monitor.py
```

"""Local audio playback for alarms.

Per Rule 1 in the plan: this module only knows how to make one noise -
it has no idea what an alarm means, when to repeat, or when to stop.
`alarm_manager.py` owns that policy and calls `play()` on a timer.

`AudioBackend` is a small interface so the actual playback mechanism can
be swapped later (a real alert sound file) without `alarm_manager.py`
changing at all.
"""

from __future__ import annotations

import logging
import math
import struct
import sys
import tempfile
import wave
from pathlib import Path
from typing import Protocol

logger = logging.getLogger(__name__)


class AudioBackend(Protocol):
    def play(self) -> None:
        """Play one alarm cue. Should return quickly rather than block for
        the sound's full duration, so the caller's loop isn't held up.
        """
        ...


class SystemBeepBackend:
    """Cross-platform best-effort beep, no extra dependencies required.

    Uses `winsound` on Windows for a real OS-level beep. Elsewhere, writes
    the ASCII bell character - most terminals will sound it, though it's
    not guaranteed on every system/output device. This is the "beep now"
    half of the plan; swap `AlarmManager`'s `audio_backend` for a
    `SoundFileBackend` (or similar) once a real alert sound and a
    playback library are picked.
    """

    def play(self) -> None:
        try:
            if sys.platform.startswith("win"):
                import winsound

                winsound.Beep(1000, 300)  # 1 kHz for 300ms
            else:
                sys.stdout.write("\a")
                sys.stdout.flush()
        except Exception:
            # A failed beep must never take down the monitoring loop -
            # it's already the alarm-of-last-resort; log it and carry on.
            logger.exception("Failed to play alarm beep")


def _build_siren_wav(path: Path) -> None:
    """Write a ~1.6s two-tone siren (16-bit mono WAV) to `path`."""
    rate = 22050
    tone_seconds = 0.2
    frames = bytearray()
    for tone_index in range(8):
        freq = 950.0 if tone_index % 2 == 0 else 700.0
        count = int(rate * tone_seconds)
        for i in range(count):
            # Short fade in/out per tone so tone changes don't click.
            edge = min(i, count - 1 - i) / (rate * 0.01)
            amplitude = 0.9 * min(1.0, edge)
            sample = int(32767 * amplitude * math.sin(2 * math.pi * freq * i / rate))
            frames += struct.pack("<h", sample)
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(rate)
        wav.writeframes(bytes(frames))


class SirenBackend:
    """Windows alarm sound: a generated two-tone siren played through the
    normal audio device with `winsound.PlaySound`, asynchronously.

    Preferred over `winsound.Beep` because Beep blocks the monitoring/GUI
    loop for its full duration (and once per alarming region), and is
    often quiet or silent on modern hardware. If anything goes wrong it
    falls back to the plain system beep, so an alarm is never silent just
    because the nicer path failed.
    """

    def __init__(self) -> None:
        self._fallback = SystemBeepBackend()
        self._path = Path(tempfile.gettempdir()) / "screen_monitor_alarm.wav"
        self._ready = False
        try:
            _build_siren_wav(self._path)
            self._ready = True
        except Exception:
            logger.exception("Could not create the alarm sound file; using system beep instead")

    def play(self) -> None:
        if not self._ready:
            self._fallback.play()
            return
        try:
            import winsound

            winsound.PlaySound(
                str(self._path),
                winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT,
            )
        except Exception:
            logger.exception("Siren playback failed; falling back to system beep")
            self._fallback.play()


def default_audio_backend() -> AudioBackend:
    """Best available backend for this platform."""
    if sys.platform.startswith("win"):
        return SirenBackend()
    return SystemBeepBackend()


class SoundFileBackend:
    """Placeholder for a future real alert sound.

    Not wired up to actually play anything yet - pick a playback library
    (e.g. `simpleaudio`, `playsound`) and implement `play()` when a real
    alert sound is wanted, then pass an instance of this into
    `AlarmManager(audio_backend=...)` in place of `SystemBeepBackend`.
    """

    def __init__(self, path: str) -> None:
        self.path = path

    def play(self) -> None:
        raise NotImplementedError(
            "SoundFileBackend is a placeholder - wire in a real playback "
            "library before using this as an AlarmManager audio_backend."
        )


if __name__ == "__main__":
    # Sound check: `python -m screen_monitor.alarms.local_audio`
    # Plays the alarm sound 3 times, 3 seconds apart, so you can check
    # your audio output without waiting for a real alarm.
    import time

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    backend = default_audio_backend()
    print(f"Using audio backend: {type(backend).__name__}")
    for n in range(1, 4):
        print(f"Playing alarm sound {n}/3 ...")
        backend.play()
        time.sleep(3)

"""Reusable status indicators and fault banners for gui.py (plan
section 4.10). Purely presentational widgets - they render whatever
label/color they're told to; they never decide what that label should
be (that's view_models.py's job).
"""

from __future__ import annotations

import tkinter as tk
from typing import Optional

OK_COLOR = "#2e7d32"
DEGRADED_COLOR = "#f9a825"
FAULT_COLOR = "#c62828"
UNKNOWN_COLOR = "#616161"

_COLOR_BY_LABEL = {
    "OK": OK_COLOR,
    "DEGRADED": DEGRADED_COLOR,
    "FAULT": FAULT_COLOR,
}


class StatusBadge(tk.Label):
    """A small colored badge for a single status value, e.g. 'OK' / 'DEGRADED' / 'FAULT'."""

    def __init__(self, master: tk.Misc, text: str = "UNKNOWN", **kwargs) -> None:
        super().__init__(
            master,
            text=text,
            fg="white",
            bg=_COLOR_BY_LABEL.get(text.upper(), UNKNOWN_COLOR),
            padx=8,
            pady=2,
            font=("TkDefaultFont", 10, "bold"),
            **kwargs,
        )

    def set_status(self, label: str) -> None:
        self.config(text=label, bg=_COLOR_BY_LABEL.get(label.upper(), UNKNOWN_COLOR))


class FaultBanner(tk.Label):
    """A full-width banner, always visible, that reads a calm "all
    normal" message in green by default and switches to a fault message
    in red when one is set. Kept permanently packed (never toggled with
    pack/pack_forget) so its position in the layout never shifts.
    """

    _NORMAL_TEXT = "All systems normal"
    _NORMAL_COLOR = OK_COLOR

    def __init__(self, master: tk.Misc, **kwargs) -> None:
        super().__init__(
            master,
            text=self._NORMAL_TEXT,
            fg="white",
            bg=self._NORMAL_COLOR,
            font=("TkDefaultFont", 11, "bold"),
            pady=6,
            **kwargs,
        )

    def set_message(self, message: Optional[str]) -> None:
        if message:
            self.config(text=message, bg=FAULT_COLOR)
        else:
            self.config(text=self._NORMAL_TEXT, bg=self._NORMAL_COLOR)


# -- widgets for the redesigned dashboard ---------------------------------

WINDOW_BG = "#c9d1db"  # light blue-gray window background
PANEL_BG = "#000000"  # camera list + stream panels
PANEL_TEXT = "#ffffff"
GOOD_TEXT = "#2b8a3e"  # darker than a pure "green" so it stays readable on WINDOW_BG
BAD_TEXT = "#e03131"
NEUTRAL_TEXT = "#495057"
LABEL_TEXT = "#1b2430"
BUTTON_BG = "#000000"
BUTTON_FG = "#ffffff"
BUTTON_DISABLED_FG = "#6c757d"

_FONT = ("Segoe UI", 10)
_FONT_BOLD = ("Segoe UI", 10, "bold")


def status_color(ok: Optional[bool]) -> str:
    if ok is None:
        return NEUTRAL_TEXT
    return GOOD_TEXT if ok else BAD_TEXT


class StatusItem(tk.Frame):
    """One entry of the top status bar: 'Label: value  [Acknowledge]'.

    Presentational only - it shows whatever text/state it is handed and
    calls `on_acknowledge` when clicked.
    """

    def __init__(self, master: tk.Misc, label: str, on_acknowledge=None, **kwargs) -> None:
        super().__init__(master, bg=WINDOW_BG, **kwargs)
        self._label = tk.Label(self, text=label, bg=WINDOW_BG, fg=LABEL_TEXT, font=_FONT)
        self._label.pack(side="left")
        self._value = tk.Label(self, text="-", bg=WINDOW_BG, fg=NEUTRAL_TEXT, font=_FONT_BOLD)
        self._value.pack(side="left", padx=(4, 8))
        self._button = tk.Button(
            self,
            text="Acknowledge",
            command=on_acknowledge,
            bg=BUTTON_BG,
            fg=BUTTON_FG,
            activebackground="#333333",
            activeforeground=BUTTON_FG,
            disabledforeground=BUTTON_DISABLED_FG,
            relief="flat",
            font=("Segoe UI", 8),
            padx=6,
            pady=1,
            state="disabled",
        )
        self._button.pack(side="left")

    def set(self, text: str, ok: Optional[bool], can_acknowledge: bool) -> None:
        self._value.config(text=text, fg=status_color(ok))
        self._button.config(state=("normal" if can_acknowledge else "disabled"))


class ToggleSwitch(tk.Canvas):
    """A small on/off pill switch."""

    _W, _H = 38, 20

    def __init__(self, master: tk.Misc, value: bool = True, on_toggle=None, bg=PANEL_BG) -> None:
        super().__init__(
            master, width=self._W, height=self._H, bg=bg, highlightthickness=0, cursor="hand2"
        )
        self._value = value
        self._on_toggle = on_toggle
        self.bind("<Button-1>", self._clicked)
        self._draw()

    @property
    def value(self) -> bool:
        return self._value

    def _clicked(self, _event) -> None:
        self._value = not self._value
        self._draw()
        if self._on_toggle is not None:
            self._on_toggle(self._value)

    def _draw(self) -> None:
        self.delete("all")
        r = self._H // 2
        fill = "#4caf50" if self._value else "#5c5c5c"
        self.create_oval(0, 0, self._H, self._H, fill=fill, outline=fill)
        self.create_oval(self._W - self._H, 0, self._W, self._H, fill=fill, outline=fill)
        self.create_rectangle(r, 0, self._W - r, self._H, fill=fill, outline=fill)
        knob_x = self._W - self._H + 2 if self._value else 2
        self.create_oval(knob_x, 2, knob_x + self._H - 4, self._H - 2, fill="white", outline="white")


class AlertStrip(tk.Label):
    """Fixed-height strip across the top of the window. Blank (same color
    as the window) when all is well, red with a message when an alarm or
    fault needs attention. Always packed, so the layout never shifts.
    """

    def __init__(self, master: tk.Misc, **kwargs) -> None:
        super().__init__(
            master,
            text="",
            bg=WINDOW_BG,
            fg="white",
            font=("Segoe UI", 12, "bold"),
            height=1,
            pady=4,
            **kwargs,
        )

    def set_message(self, message: Optional[str]) -> None:
        if message:
            self.config(text=message, bg=FAULT_COLOR)
        else:
            self.config(text="", bg=WINDOW_BG)

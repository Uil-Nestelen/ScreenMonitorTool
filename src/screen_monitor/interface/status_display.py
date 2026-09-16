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

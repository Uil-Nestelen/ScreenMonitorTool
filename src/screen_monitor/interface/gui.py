"""Live status dashboard (plan section 4.10).

Layout (from the "Normal monitor mode" / "Editor mode" mockups):

    +--------------------------------------------------------------+
    | [alert strip - blank unless an alarm/fault needs attention]  |
    | Watchdog Status: .. [Ack]  Stream Status: .. [Ack]  Stream: ..|  X
    +----------------+---------------------------------------------+
    | Camera N       |                                             |
    | ------------   |                                             |
    | Region 1 [o] [Ack] [pencil]      live camera stream          |
    |   (details)    |      with every region drawn on top         |
    | Region 2 ...   |                                             |
    +----------------+---------------------------------------------+

A pure presentation layer: it displays state computed elsewhere
(Application / RegionMonitor / AlarmManager / StreamMonitor) via
view_models.py, and only calls the small public API Application exposes
for it (acknowledge, acknowledge_fault, replace_region, add_region). This
module never reads the camera, runs detection, or owns timers - the
frame it draws is the one Application already read this cycle.

Runs in the same process as the monitoring engine, driven by Tkinter's
own `.after()` scheduler instead of the blocking while-loop main.py uses
headlessly. This is a process-layout choice, not a responsibility
violation - see README.

Region drawing happens directly on the embedded stream (no separate
OpenCV window). Regions are always stored in *frame* pixels; the canvas
is only a scaled view of the frame (see stream_geometry.py).

The toggle next to each region only shows/hides that region's box on the
stream. It does NOT pause monitoring for the region.
"""

from __future__ import annotations

import dataclasses
import logging
import tkinter as tk
from pathlib import Path
from tkinter import messagebox
from typing import Dict, List, Optional, Tuple

import cv2

try:
    from PIL import Image, ImageTk
except ImportError as exc:  # pragma: no cover - environment dependent
    raise RuntimeError(
        "The dashboard needs Pillow to show the camera stream. Install it with: pip install Pillow"
    ) from exc

from screen_monitor.detection.region import Region
from screen_monitor.interface.region_form import FORM_KEYS, form_texts, parse_region_form
from screen_monitor.interface.status_display import (
    BUTTON_BG,
    BUTTON_DISABLED_FG,
    BUTTON_FG,
    PANEL_BG,
    PANEL_TEXT,
    WINDOW_BG,
    AlertStrip,
    StatusItem,
    ToggleSwitch,
)
from screen_monitor.interface.stream_geometry import (
    Viewport,
    compute_viewport,
    frame_to_canvas,
    rect_from_drag,
)
from screen_monitor.interface.view_models import (
    DashboardViewModel,
    RegionViewModel,
    build_dashboard_view_model,
)
from screen_monitor.main import MAX_REGIONS

logger = logging.getLogger("screen_monitor.interface.gui")

_NEW_REGION = "__new__"  # draw-target sentinel: "draw a brand new region"

_LEFT_PANEL_WIDTH = 300
_FONT = "Segoe UI"

# Stream-overlay / row colors by RegionViewModel.severity
_SEVERITY_COLOR = {
    "ok": "#3ddc84",
    "unknown": "#9e9e9e",
    "pending": "#ffa726",
    "alarm": "#ff3b30",
    "acknowledged": "#5aa9ff",
}
_SEVERITY_LINE_WIDTH = {"ok": 2, "unknown": 2, "pending": 3, "alarm": 4, "acknowledged": 2}
_DRAW_COLOR = "#00ff66"


def _flat_button(master: tk.Misc, text: str, command, **kwargs) -> tk.Button:
    return tk.Button(
        master,
        text=text,
        command=command,
        bg=kwargs.pop("bg", "#c9d1db"),
        fg=kwargs.pop("fg", "#1b2430"),
        activebackground="#e1e6ec",
        disabledforeground="#7b8794",
        relief="flat",
        font=(_FONT, 8),
        padx=6,
        pady=1,
        **kwargs,
    )


class RegionRow:
    """One row of the left-hand region list, with an expandable editor
    (the mockup's "editor mode")."""

    _SUBTITLE_COLOR = {
        "ok": "#9aa5b1",
        "unknown": "#9e9e9e",
        "pending": _SEVERITY_COLOR["pending"],
        "alarm": _SEVERITY_COLOR["alarm"],
        "acknowledged": _SEVERITY_COLOR["acknowledged"],
    }

    def __init__(
        self,
        parent: tk.Misc,
        vm: RegionViewModel,
        overlay_visible: bool,
        on_acknowledge,
        on_toggle,
        on_edit,
        on_redraw,
        on_apply,
    ) -> None:
        self.region_id = vm.region_id
        self._expanded = False
        self._on_apply = on_apply
        # The values last received from the engine, as form text. Entries
        # are only overwritten when these change, so a live refresh never
        # clobbers what the user is in the middle of typing.
        self._engine_texts: Optional[Tuple[str, ...]] = None

        self.frame = tk.Frame(parent, bg=PANEL_BG, highlightbackground="white", highlightthickness=1)
        self.frame.pack(fill="x", pady=3)

        top = tk.Frame(self.frame, bg=PANEL_BG)
        top.pack(fill="x", padx=6, pady=(4, 0))

        self._name = tk.Label(
            top, text=vm.display_name, bg=PANEL_BG, fg=PANEL_TEXT, font=(_FONT, 11), anchor="w"
        )
        self._name.pack(side="left", fill="x", expand=True)

        self._pencil = tk.Button(
            top,
            text="\u270e",
            command=on_edit,
            bg=PANEL_BG,
            fg=PANEL_TEXT,
            activebackground="#333333",
            activeforeground=PANEL_TEXT,
            relief="flat",
            borderwidth=0,
            font=(_FONT, 11),
            cursor="hand2",
        )
        self._pencil.pack(side="right")

        self._ack = _flat_button(top, "Acknowledge", lambda: on_acknowledge(self.region_id))
        self._ack.pack(side="right", padx=6)

        self._toggle = ToggleSwitch(top, value=overlay_visible, on_toggle=on_toggle)
        self._toggle.pack(side="right")

        self._subtitle = tk.Label(
            self.frame, text="", bg=PANEL_BG, fg="#9aa5b1", font=(_FONT, 8), anchor="w"
        )
        self._subtitle.pack(fill="x", padx=8, pady=(0, 4))

        # -- editor section - only packed while expanded ----------------
        self._details = tk.Frame(self.frame, bg=PANEL_BG)
        grid = tk.Frame(self._details, bg=PANEL_BG)
        grid.pack(fill="x", padx=10, pady=(0, 4))
        grid.columnconfigure(1, weight=1)

        self._vars = {key: tk.StringVar() for key in FORM_KEYS}
        fields = [
            ("Name:", "name", "#ffffff", 16),
            ("Threshold (0-1):", "threshold", "#ffb347", 8),
            ("Confirm after (s):", "confirmation", "#ffb347", 8),
            ("Alarm after (s):", "alarm", "#ffb347", 8),
        ]
        for index, (label, key, color, width) in enumerate(fields):
            tk.Label(grid, text=label, bg=PANEL_BG, fg=PANEL_TEXT, font=(_FONT, 8, "bold")).grid(
                row=index, column=0, sticky="w"
            )
            entry = tk.Entry(
                grid,
                textvariable=self._vars[key],
                width=width,
                justify="right",
                bg="#1c1c1c",
                fg=color,
                insertbackground="white",
                relief="flat",
                highlightthickness=1,
                highlightbackground="#555555",
                highlightcolor="#7fdbff",
                font=(_FONT, 9),
            )
            entry.grid(row=index, column=1, sticky="e", pady=1)
            entry.bind("<Return>", lambda _event: self._apply())

        tk.Label(grid, text="Detecting color:", bg=PANEL_BG, fg=PANEL_TEXT, font=(_FONT, 8, "bold")).grid(
            row=len(fields), column=0, sticky="w"
        )
        self._color_value = tk.Label(grid, text="RED", bg=PANEL_BG, fg="#7fdbff", font=(_FONT, 8, "bold"))
        self._color_value.grid(row=len(fields), column=1, sticky="e")

        buttons = tk.Frame(self._details, bg=PANEL_BG)
        buttons.pack(fill="x", padx=10, pady=(2, 0))
        self._apply_button = _flat_button(buttons, "Apply", self._apply, bg="#2b8a3e", fg="white")
        self._apply_button.pack(side="left", padx=(0, 6))
        self._revert_button = _flat_button(buttons, "Revert", self._revert)
        self._revert_button.pack(side="left")

        self._message = tk.Label(
            self._details, text="", bg=PANEL_BG, fg="#ff6b6b", font=(_FONT, 8), anchor="w",
            wraplength=250, justify="left",
        )
        self._message.pack(fill="x", padx=10)

        self._redraw = tk.Button(
            self._details,
            text="Redraw Region",
            command=lambda: on_redraw(self.region_id),
            bg=PANEL_BG,
            fg="#ffb347",
            activebackground="#333333",
            activeforeground="#ffb347",
            disabledforeground=BUTTON_DISABLED_FG,
            highlightbackground="white",
            highlightthickness=1,
            relief="flat",
            font=(_FONT, 8),
        )
        self._redraw.pack(fill="x", padx=24, pady=(4, 2))

        self._collapse = tk.Button(
            self._details,
            text="\u25b2",
            command=on_edit,
            bg=PANEL_BG,
            fg=PANEL_TEXT,
            activebackground="#333333",
            activeforeground=PANEL_TEXT,
            relief="flat",
            borderwidth=0,
            font=(_FONT, 8),
            cursor="hand2",
        )
        self._collapse.pack(anchor="e", padx=10, pady=(0, 4))

        # Only now that every widget exists is it safe for edits to trigger
        # the dirty check.
        for var in self._vars.values():
            var.trace_add("write", self._on_field_changed)

        self.update(vm)

    # -- editor plumbing -------------------------------------------------

    def _current_texts(self) -> Tuple[str, ...]:
        return tuple(self._vars[key].get() for key in FORM_KEYS)

    def _on_field_changed(self, *_args) -> None:
        self._message.config(text="")
        self._refresh_dirty()

    def _refresh_dirty(self) -> None:
        if self._engine_texts is None:
            return
        state = "normal" if self._current_texts() != self._engine_texts else "disabled"
        self._apply_button.config(state=state)
        self._revert_button.config(state=state)

    def _apply(self) -> None:
        if self._current_texts() != self._engine_texts:
            self._on_apply(self.region_id, self._current_texts())

    def _revert(self) -> None:
        if self._engine_texts is not None:
            for key, text in zip(FORM_KEYS, self._engine_texts):
                self._vars[key].set(text)
        self._message.config(text="")

    def set_message(self, text: str, ok: bool) -> None:
        self._message.config(text=text, fg="#7CFC9A" if ok else "#ff6b6b")

    # -- public ----------------------------------------------------------

    @property
    def expanded(self) -> bool:
        return self._expanded

    def set_expanded(self, expanded: bool) -> None:
        if expanded == self._expanded:
            return
        self._expanded = expanded
        if expanded:
            self._details.pack(fill="x")
        else:
            self._details.pack_forget()

    def update(self, vm: RegionViewModel) -> None:
        alarming = vm.severity == "alarm"
        self._name.config(
            text=vm.display_name, fg=_SEVERITY_COLOR["alarm"] if alarming else PANEL_TEXT
        )
        self.frame.config(highlightbackground=_SEVERITY_COLOR["alarm"] if alarming else "white")
        self._subtitle.config(text=vm.subtitle, fg=self._SUBTITLE_COLOR.get(vm.severity, "#9aa5b1"))
        self._ack.config(state=("normal" if vm.can_acknowledge else "disabled"))
        self._color_value.config(text=vm.detecting_color)
        self._redraw.config(state=("normal" if vm.can_edit else "disabled"))

        texts = form_texts(vm.display_name, vm.red_threshold, vm.confirmation_seconds, vm.alarm_seconds)
        if texts != self._engine_texts:
            self._engine_texts = texts
            for key, text in zip(FORM_KEYS, texts):
                self._vars[key].set(text)
        self._refresh_dirty()


class Dashboard(tk.Tk):
    def __init__(
        self,
        app,
        config_path: Optional[Path] = None,
        device_index: int = 0,
    ) -> None:
        super().__init__()
        self._app = app
        self._config_path = config_path
        self._device_index = device_index

        self._rows: Dict[str, RegionRow] = {}
        # None (not []) so the first render always builds the list, including
        # the "+ Add region" button when no regions are configured yet.
        self._row_ids: Optional[List[str]] = None
        self._expanded_id: Optional[str] = None
        self._overlay_visible: Dict[str, bool] = {}

        # Stream canvas state
        self._photo = None  # keeps the PhotoImage alive
        self._viewport: Optional[Viewport] = None
        self._draw_target: Optional[str] = None
        self._drag_start: Optional[Tuple[float, float]] = None
        self._drag_end: Optional[Tuple[float, float]] = None
        self._last_vm: Optional[DashboardViewModel] = None
        self._fullscreen = False

        self.title("Screen Red-Alert Monitor")
        self.configure(bg=WINDOW_BG)
        self.geometry("1100x700")
        self.minsize(900, 560)
        try:
            self.state("zoomed")  # Windows / some Linux WMs
        except tk.TclError:
            pass

        self._build_widgets()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.bind("<F11>", self._toggle_fullscreen)
        self.bind("<Escape>", self._on_escape)

    # -- layout --------------------------------------------------------

    def _build_widgets(self) -> None:
        self._alert_strip = AlertStrip(self)
        self._alert_strip.pack(fill="x", side="top")

        # Top status bar ------------------------------------------------
        bar = tk.Frame(self, bg=WINDOW_BG)
        bar.pack(fill="x", side="top", padx=14, pady=(6, 4))

        self._watchdog_item = StatusItem(
            bar, "Watchdog Status:", on_acknowledge=lambda: self._app.acknowledge_fault("watchdog")
        )
        self._watchdog_item.pack(side="left", padx=(0, 26))

        self._stream_item = StatusItem(
            bar, "Stream Status:", on_acknowledge=lambda: self._app.acknowledge_fault("stream")
        )
        self._stream_item.pack(side="left", padx=(0, 26))

        self._fps_item = StatusItem(
            bar, "Stream:", on_acknowledge=lambda: self._app.acknowledge_fault("fps")
        )
        self._fps_item.pack(side="left")

        close = tk.Frame(bar, bg=WINDOW_BG)
        close.pack(side="right")
        tk.Button(
            close,
            text="\u2716",
            command=self._on_close,
            bg=WINDOW_BG,
            fg="#e03131",
            activebackground=WINDOW_BG,
            activeforeground="#a51111",
            relief="flat",
            borderwidth=0,
            font=(_FONT, 20, "bold"),
            cursor="hand2",
        ).pack()
        tk.Label(close, text="Close Application", bg=WINDOW_BG, fg="#1b2430", font=(_FONT, 7)).pack()

        # Body ------------------------------------------------------------
        body = tk.Frame(self, bg=WINDOW_BG)
        body.pack(fill="both", expand=True, padx=14, pady=(0, 14))

        left = tk.Frame(body, bg=PANEL_BG, width=_LEFT_PANEL_WIDTH)
        left.pack(side="left", fill="y", padx=(0, 12))
        left.pack_propagate(False)

        tk.Label(
            left,
            text=f"Camera {self._device_index}",
            bg=PANEL_BG,
            fg=PANEL_TEXT,
            font=(_FONT, 12),
        ).pack(pady=(12, 4))
        tk.Frame(left, bg="white", height=2).pack(fill="x", padx=2)

        self._rows_frame = tk.Frame(left, bg=PANEL_BG)
        self._rows_frame.pack(fill="both", expand=True, padx=10, pady=10)

        stream_holder = tk.Frame(body, bg=PANEL_BG)
        stream_holder.pack(side="left", fill="both", expand=True)
        self._canvas = tk.Canvas(stream_holder, bg="black", highlightthickness=0)
        self._canvas.pack(fill="both", expand=True, padx=6, pady=6)
        self._canvas.bind("<ButtonPress-1>", self._on_canvas_press)
        self._canvas.bind("<B1-Motion>", self._on_canvas_drag)
        self._canvas.bind("<ButtonRelease-1>", self._on_canvas_release)

    # -- lifecycle -------------------------------------------------------

    def start(self) -> None:
        """Runs Application's self-test/camera-connect, then enters the
        Tkinter event loop. Blocks until the window is closed.
        """
        if not self._app.start():
            messagebox.showerror(
                "Screen Monitor", "Camera self-test failed - check the logs for details."
            )
            self.destroy()
            return

        self.after(0, self._tick)
        self.mainloop()

    def _on_close(self) -> None:
        if not messagebox.askyesno(
            "Close Application",
            "Close the application?\n\nMonitoring will stop and no alarms will be raised.",
        ):
            return
        self._app.request_shutdown()
        self._app.shutdown()
        self.destroy()

    def _toggle_fullscreen(self, _event=None) -> None:
        self._fullscreen = not self._fullscreen
        self.attributes("-fullscreen", self._fullscreen)

    def _on_escape(self, _event=None) -> None:
        if self._draw_target is not None:
            self._cancel_draw()

    def _tick(self) -> None:
        if self._app.shutdown_requested:
            self.destroy()
            return

        try:
            self._app._loop_once()
        except Exception:
            logger.exception("Unhandled exception in monitoring tick")
            messagebox.showerror(
                "Screen Monitor", "An unexpected error occurred - see the logs. Shutting down."
            )
            self._app.shutdown()
            self.destroy()
            return

        self._refresh()
        self.after(max(1, int(self._app.loop_interval * 1000)), self._tick)

    # -- rendering -------------------------------------------------------

    def _refresh(self) -> None:
        view_model = build_dashboard_view_model(
            self._app.last_system_status(),
            self._app.region_states(),
            self._app.is_alarming,
            regions=self._app.regions,
            detections=self._app.latest_detections(),
            acknowledged_faults=self._app.acknowledged_faults(),
            now=self._app.now(),
        )
        self._render(view_model)

    def _render(self, vm: DashboardViewModel) -> None:
        self._last_vm = vm

        if vm.has_active_alarm:
            self._alert_strip.set_message("ALARM \u2014 " + ", ".join(vm.alarming_region_names))
        else:
            self._alert_strip.set_message(vm.fault_message)

        self._watchdog_item.set(vm.watchdog_label, vm.watchdog_ok, vm.watchdog_can_acknowledge)
        self._stream_item.set(vm.stream_label, vm.stream_ok, vm.stream_can_acknowledge)
        self._fps_item.set(vm.fps_text, vm.fps_ok, vm.fps_can_acknowledge)

        self._sync_rows(vm)
        self._draw_stream(vm)

    # -- region list -----------------------------------------------------

    def _sync_rows(self, vm: DashboardViewModel) -> None:
        ids = [r.region_id for r in vm.regions]
        if ids != self._row_ids:
            self._rebuild_rows(vm)
        for region_vm in vm.regions:
            self._rows[region_vm.region_id].update(region_vm)

    def _rebuild_rows(self, vm: DashboardViewModel) -> None:
        for child in self._rows_frame.winfo_children():
            child.destroy()
        self._rows = {}
        self._row_ids = [r.region_id for r in vm.regions]
        if self._expanded_id not in self._row_ids:
            self._expanded_id = None

        for region_vm in vm.regions:
            rid = region_vm.region_id
            row = RegionRow(
                self._rows_frame,
                region_vm,
                overlay_visible=self._overlay_visible.get(rid, True),
                on_acknowledge=self._on_acknowledge,
                on_toggle=lambda value, rid=rid: self._overlay_visible.__setitem__(rid, value),
                on_edit=lambda rid=rid: self._toggle_expanded(rid),
                on_redraw=self._start_redraw,
                on_apply=self._apply_edit,
            )
            row.set_expanded(rid == self._expanded_id)
            self._rows[rid] = row

        if len(vm.regions) < MAX_REGIONS:
            _flat_button(
                self._rows_frame, "+ Add region", self._start_add, bg=BUTTON_BG, fg=BUTTON_FG
            ).pack(fill="x", pady=(8, 0))

    def _toggle_expanded(self, region_id: str) -> None:
        self._expanded_id = None if self._expanded_id == region_id else region_id
        for rid, row in self._rows.items():
            row.set_expanded(rid == self._expanded_id)

    # -- stream ----------------------------------------------------------

    def _draw_stream(self, vm: DashboardViewModel) -> None:
        canvas = self._canvas
        canvas.delete("all")
        cw, ch = canvas.winfo_width(), canvas.winfo_height()

        frame = self._app.latest_frame()
        if frame is None or frame.image is None:
            self._viewport = None
            canvas.create_text(
                cw / 2, ch / 2, text="No signal", fill="white", font=(_FONT, 22)
            )
            self._draw_hint(cw)
            return

        viewport = compute_viewport(frame.width, frame.height, cw, ch)
        self._viewport = viewport
        if viewport is None:
            return

        rgb = cv2.cvtColor(frame.image, cv2.COLOR_BGR2RGB)
        interpolation = cv2.INTER_AREA if viewport.scale < 1.0 else cv2.INTER_LINEAR
        resized = cv2.resize(rgb, (viewport.draw_width, viewport.draw_height), interpolation=interpolation)
        self._photo = ImageTk.PhotoImage(Image.fromarray(resized))
        canvas.create_image(viewport.offset_x, viewport.offset_y, image=self._photo, anchor="nw")

        vm_by_id = {r.region_id: r for r in vm.regions}
        for region in self._app.regions:
            if not self._overlay_visible.get(region.id, True):
                continue
            self._draw_region_box(viewport, region, vm_by_id.get(region.id))

        if self._drag_start is not None and self._drag_end is not None:
            x1, y1 = self._drag_start
            x2, y2 = self._drag_end
            canvas.create_rectangle(x1, y1, x2, y2, outline=_DRAW_COLOR, width=2, dash=(6, 3))

        self._draw_hint(cw)

    def _draw_region_box(
        self, viewport: Viewport, region: Region, region_vm: Optional[RegionViewModel]
    ) -> None:
        severity = region_vm.severity if region_vm is not None else "unknown"
        color = _SEVERITY_COLOR[severity]
        x1, y1 = frame_to_canvas(viewport, region.x, region.y)
        x2, y2 = frame_to_canvas(viewport, region.x + region.width, region.y + region.height)
        self._canvas.create_rectangle(
            x1, y1, x2, y2, outline=color, width=_SEVERITY_LINE_WIDTH[severity]
        )

        label = region_vm.display_name if region_vm is not None else region.id
        if region_vm is not None:
            if region_vm.reading_unknown:
                label += "  ?"
            elif region_vm.red_percentage is not None:
                label += f"  {region_vm.red_percentage:.0%}"
            if region_vm.countdown_text:
                label += f"  \u00b7 {region_vm.countdown_text}"

        # Keep the label on-screen even for a box touching the top edge.
        text_y = y1 - 3 if y1 > 18 else y2 + 14
        text = self._canvas.create_text(
            x1 + 2, text_y, text=label, fill=color, anchor="sw", font=(_FONT, 9, "bold")
        )
        bbox = self._canvas.bbox(text)
        if bbox:
            backing = self._canvas.create_rectangle(*bbox, fill="black", outline="")
            self._canvas.tag_lower(backing, text)

    def _draw_hint(self, canvas_width: int) -> None:
        if self._draw_target is None:
            return
        if self._draw_target == _NEW_REGION:
            text = "Drag on the stream to draw the new region  \u2014  Esc to cancel"
        else:
            text = f"Drag on the stream to redraw '{self._draw_target}'  \u2014  Esc to cancel"
        item = self._canvas.create_text(
            canvas_width / 2, 16, text=text, fill="white", font=(_FONT, 11, "bold")
        )
        bbox = self._canvas.bbox(item)
        if bbox:
            backing = self._canvas.create_rectangle(
                bbox[0] - 8, bbox[1] - 4, bbox[2] + 8, bbox[3] + 4, fill="#1b6b3a", outline=""
            )
            self._canvas.tag_lower(backing, item)

    # -- drawing regions on the stream ------------------------------------

    def _start_redraw(self, region_id: str) -> None:
        self._begin_draw(region_id)

    def _start_add(self) -> None:
        self._begin_draw(_NEW_REGION)

    def _begin_draw(self, target: str) -> None:
        self._draw_target = target
        self._drag_start = None
        self._drag_end = None
        self._canvas.config(cursor="crosshair")

    def _cancel_draw(self) -> None:
        self._draw_target = None
        self._drag_start = None
        self._drag_end = None
        self._canvas.config(cursor="")

    def _on_canvas_press(self, event) -> None:
        if self._draw_target is None or self._viewport is None:
            return
        self._drag_start = (event.x, event.y)
        self._drag_end = (event.x, event.y)

    def _on_canvas_drag(self, event) -> None:
        if self._draw_target is None or self._drag_start is None:
            return
        self._drag_end = (event.x, event.y)

    def _on_canvas_release(self, event) -> None:
        if self._draw_target is None or self._drag_start is None or self._viewport is None:
            return
        target = self._draw_target
        rect = rect_from_drag(self._viewport, self._drag_start, (event.x, event.y))
        if rect is None:
            # An accidental click - stay in draw mode so they can retry.
            self._drag_start = None
            self._drag_end = None
            return
        self._cancel_draw()
        self._confirm_and_apply(target, rect)

    def _confirm_and_apply(self, target: str, rect: Tuple[int, int, int, int]) -> None:
        x, y, width, height = rect

        if target == _NEW_REGION:
            n = 1
            existing = {r.id for r in self._app.regions}
            while f"region_{n}" in existing:
                n += 1
            region_id = f"region_{n}"
            if not messagebox.askyesno(
                "Add region", f"Add '{region_id}' at x={x}, y={y}, {width}\u00d7{height}?"
            ):
                return
            try:
                region = Region(id=region_id, name=f"Region {n}", x=x, y=y, width=width, height=height)
            except ValueError as exc:
                messagebox.showerror("Add region", str(exc))
                return
            self._report(self._app.add_region(region), "Add region")
            return

        old = next((r for r in self._app.regions if r.id == target), None)
        if old is None:
            messagebox.showerror("Redraw region", f"Region '{target}' no longer exists.")
            return
        if not messagebox.askyesno(
            "Redraw region",
            f"Move '{old.name or old.id}' to x={x}, y={y}, {width}\u00d7{height}?\n\n"
            "Its current alarm timer will restart.",
        ):
            return
        try:
            new = dataclasses.replace(old, x=x, y=y, width=width, height=height)
        except ValueError as exc:
            messagebox.showerror("Redraw region", str(exc))
            return
        self._report(self._app.replace_region(new), "Redraw region")

    def _report(self, result, title: str) -> None:
        if not result.ok:
            messagebox.showerror(title, result.message)
        elif not result.persisted:
            messagebox.showwarning(title, result.message)

    # -- editing a region's settings ---------------------------------

    def _apply_edit(self, region_id: str, texts) -> None:
        row = self._rows.get(region_id)
        old = next((r for r in self._app.regions if r.id == region_id), None)
        if row is None or old is None:
            return
        try:
            new = parse_region_form(old, *texts)
        except ValueError as exc:
            row.set_message(str(exc), ok=False)
            return
        if new == old:
            row.set_message("No changes.", ok=True)
            return

        result = self._app.replace_region(new)
        if not result.ok:
            row.set_message(result.message, ok=False)
        elif not result.persisted:
            row.set_message(result.message, ok=False)
        else:
            renamed_only = dataclasses.replace(old, name=new.name) == new
            row.set_message("Saved." if renamed_only else "Saved. This region's timers restarted.", ok=True)

    # -- actions -----------------------------------------------------

    def _on_acknowledge(self, region_id: str) -> None:
        event = self._app.acknowledge(region_id)
        if event is None:
            logger.info("Acknowledge no-op for region '%s' (not currently alarming)", region_id)

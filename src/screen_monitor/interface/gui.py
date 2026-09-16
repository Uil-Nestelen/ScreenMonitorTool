"""Live status dashboard (plan section 4.10).

A pure presentation layer: it displays state computed elsewhere
(Application / RegionMonitor / AlarmManager / StreamMonitor) via
view_models.py, and for acknowledgment calls the one method that
already exists for it (Application.acknowledge) - this module never
reads the camera, runs detection, or owns timers itself.

Runs in the same process as the monitoring engine, driven by Tkinter's
own `.after()` scheduler instead of the blocking while-loop main.py uses
headlessly. This is a process-layout choice, not a responsibility
violation: the GUI *code* here still does none of the actual monitoring
work, it just happens to share a process with the engine so the
Acknowledge button can call straight into RegionMonitor.acknowledge()
with no IPC needed. See README for the full reasoning.
"""

from __future__ import annotations

import logging
import subprocess
import sys
import tkinter as tk
from pathlib import Path
from tkinter import messagebox
from typing import Dict, Optional

from screen_monitor.interface.status_display import FaultBanner, StatusBadge
from screen_monitor.interface.view_models import DashboardViewModel, build_dashboard_view_model

logger = logging.getLogger("screen_monitor.interface.gui")


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
        self._region_rows: Dict[str, dict] = {}

        self.title("Screen Red-Alert Monitor")
        self.geometry("520x420")
        self.minsize(420, 320)

        self._build_widgets()
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # -- layout --------------------------------------------------------

    def _build_widgets(self) -> None:
        self._fault_banner = FaultBanner(self)
        self._fault_banner.pack(fill="x", side="top")

        top = tk.Frame(self, padx=12, pady=10)
        top.pack(fill="x")
        top.columnconfigure(1, weight=1)

        tk.Label(top, text="System:", font=("TkDefaultFont", 10, "bold")).grid(
            row=0, column=0, sticky="w"
        )
        self._state_label = tk.Label(top, text=self._app._state.value)
        self._state_label.grid(row=0, column=1, sticky="w")

        tk.Label(top, text="Camera:", font=("TkDefaultFont", 10, "bold")).grid(
            row=1, column=0, sticky="w", pady=(4, 0)
        )
        self._camera_badge = StatusBadge(top, text="UNKNOWN")
        self._camera_badge.grid(row=1, column=1, sticky="w", pady=(4, 0))

        tk.Label(top, text="Stream:", font=("TkDefaultFont", 10, "bold")).grid(
            row=2, column=0, sticky="w", pady=(4, 0)
        )
        self._stream_label = tk.Label(top, text="-")
        self._stream_label.grid(row=2, column=1, sticky="w", pady=(4, 0))

        tk.Label(top, text="Watchdog:", font=("TkDefaultFont", 10, "bold")).grid(
            row=3, column=0, sticky="w", pady=(4, 0)
        )
        self._watchdog_label = tk.Label(top, text="-")
        self._watchdog_label.grid(row=3, column=1, sticky="w", pady=(4, 0))

        tk.Button(top, text="Configure Regions...", command=self._launch_setup_wizard).grid(
            row=4, column=0, columnspan=2, sticky="we", pady=(10, 0)
        )

        tk.Label(self, text="Regions", font=("TkDefaultFont", 10, "bold")).pack(
            anchor="w", padx=12, pady=(12, 0)
        )
        self._regions_frame = tk.Frame(self, padx=12)
        self._regions_frame.pack(fill="both", expand=True)

        if not self._app._regions:
            tk.Label(
                self._regions_frame,
                text="No regions configured - pass --config to enable detection.",
                fg="#616161",
            ).pack(anchor="w", pady=8)

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
        self._app.request_shutdown()
        self._app.shutdown()
        self.destroy()

    def _tick(self) -> None:
        if self._app._shutdown_requested:
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
        self.after(max(1, int(self._app._loop_interval * 1000)), self._tick)

    # -- rendering -------------------------------------------------------

    def _refresh(self) -> None:
        region_states = (
            self._app._region_monitor.all_states() if self._app._region_monitor is not None else {}
        )
        is_alarming_fn = (
            self._app._alarm_manager.is_alarming if self._app._alarm_manager is not None else None
        )
        view_model = build_dashboard_view_model(
            self._app._last_system_status, region_states, is_alarming_fn
        )
        self._render(view_model)

    def _render(self, view_model: DashboardViewModel) -> None:
        self._state_label.config(text=view_model.overall_state)
        self._camera_badge.set_status(view_model.camera_status)

        if view_model.stream_frozen:
            stream_text = "FROZEN"
        elif view_model.stream_timed_out:
            stream_text = "TIMED OUT"
        elif view_model.camera_connected:
            stream_text = f"{view_model.stream_frame_rate:.1f} fps"
        else:
            stream_text = "-"
        self._stream_label.config(text=stream_text)

        self._watchdog_label.config(
            text="heartbeat published" if view_model.watchdog_visible else "not published"
        )

        self._fault_banner.set_message(view_model.fault_message)

        for region_vm in view_model.regions:
            self._render_region_row(region_vm)

    def _render_region_row(self, region_vm) -> None:
        row = self._region_rows.get(region_vm.region_id)
        if row is None:
            frame = tk.Frame(self._regions_frame, pady=4)
            frame.pack(fill="x")
            id_label = tk.Label(frame, text=region_vm.region_id, width=16, anchor="w")
            id_label.pack(side="left")
            status_label = tk.Label(frame, text="", width=20, anchor="w")
            status_label.pack(side="left")
            ack_button = tk.Button(
                frame,
                text="Acknowledge",
                command=lambda rid=region_vm.region_id: self._on_acknowledge(rid),
            )
            ack_button.pack(side="left")
            row = {"status_label": status_label, "ack_button": ack_button}
            self._region_rows[region_vm.region_id] = row

        color = "#c62828" if region_vm.is_alarming else "black"
        row["status_label"].config(text=region_vm.status_label, fg=color)
        row["ack_button"].config(state=("normal" if region_vm.can_acknowledge else "disabled"))

    # -- actions -----------------------------------------------------

    def _on_acknowledge(self, region_id: str) -> None:
        event = self._app.acknowledge(region_id)
        if event is None:
            logger.info("Acknowledge no-op for region '%s' (not currently alarming)", region_id)

    def _launch_setup_wizard(self) -> None:
        # The setup wizard needs exclusive camera access, same as the
        # monitoring engine - they can't both hold the device at once.
        # This is a known, documented limitation rather than something
        # solved here: stop monitoring first if the camera is in active
        # use, calibrate, then relaunch.
        messagebox.showinfo(
            "Configure Regions",
            "This opens the region calibration tool in a separate window.\n\n"
            "It needs its own access to the camera - if this dashboard is "
            "actively monitoring the same device, close it first, calibrate, "
            "then relaunch.",
        )
        cmd = [
            sys.executable,
            "-m",
            "screen_monitor.interface.setup_wizard",
            "--device-index",
            str(self._device_index),
        ]
        if self._config_path is not None:
            cmd += ["--output", str(self._config_path)]
        try:
            subprocess.Popen(cmd)
        except OSError as exc:
            messagebox.showerror("Configure Regions", f"Failed to launch setup wizard: {exc}")

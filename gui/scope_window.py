"""
gui/scope_window.py

Owns the layout/widgets for the main window. This class has no idea a VISA
worker exists - it only exposes callbacks (on_plot_clicked, on_send_command,
on_channels_changed, on_save_plot, etc.) that main.py wires to the worker
and export module, and public methods (update_multi,
append_log) that main.py calls when data comes back.
"""

import dearpygui.dearpygui as dpg
import numpy as np
from gui.theme import (
    ACCENT_AMBER, TEXT_MUTED, TEXT_BRIGHT,
    TRACE_GREEN, TRACE_CYAN, TRACE_YELL, TRACE_VIL,
    CURSOR_A, CURSOR_B,
    build_series_theme, build_button_theme,
)
from analysis.fft import compute_spectrum
from analysis.jitter import compute_jitter

RUN_COLOR = (0, 160, 90)    # green - acquisition running
STOP_COLOR = (170, 50, 50)  # red - acquisition stopped
CHANNELS = ["CH1", "CH2", "CH3", "CH4"]
CHANNEL_COLORS = {"CH1": TRACE_YELL, "CH2": TRACE_CYAN, "CH3": TRACE_VIL, "CH4": TRACE_GREEN}


class ScopeWindow:
    SIMULATED_LABEL = "Simulated (no hardware)"
    DOMAIN_LABELS = {"Waveform": "time", "FFT": "freq", "Jitter": "jitter"}

    def __init__(
        self,
        on_plot_clicked=None,
        on_single_seq_clicked=None,
        on_continuous_toggled=None,
        on_channels_changed=None,
        on_send_command=None,
        on_query_command=None,
        on_refresh_resources=None,
        on_connect=None,
        on_save_plot=None,
        on_run_stop_clicked=None,
    ):
        self.on_plot_clicked = on_plot_clicked
        self.on_single_seq_clicked = on_single_seq_clicked
        self.on_continuous_toggled = on_continuous_toggled
        self.on_channels_changed = on_channels_changed
        self.on_send_command = on_send_command
        self.on_query_command = on_query_command
        self.on_refresh_resources = on_refresh_resources
        self.on_connect = on_connect
        self.on_save_plot = on_save_plot
        self.on_run_stop_clicked = on_run_stop_clicked

        self._last_time_range = None    # (t_min_us, t_max_us) from last capture
        self._last_capture = None       # {"CH1": {"time_us":..., "voltage":...}, ...}
        self._domain = "time"           # "time" or "freq" - which view is currently plotted
        self._need_x_fit = True         # see _render_current_domain() for why this exists
        self._cursors_enabled = False
        self._channel_themes = {}       # built in _build(), maps "CH1".. -> theme id
        self._series_tags = {}          # maps "CH1".. -> line series tag
        self._build()

    def _build(self):
        self._channel_themes = {ch: build_series_theme(CHANNEL_COLORS[ch]) for ch in CHANNELS}
        self._run_theme = build_button_theme(RUN_COLOR)
        self._stop_theme = build_button_theme(STOP_COLOR)

        with dpg.window(tag="main_window"):
            with dpg.group(horizontal=True):
                dpg.add_text("Tektronix TDS", color=TEXT_BRIGHT)
                dpg.add_text("—  Waveform", color=TEXT_MUTED, tag="title_channel_text")

            with dpg.group(horizontal=True):
                dpg.add_combo(
                    items=[self.SIMULATED_LABEL],
                    default_value=self.SIMULATED_LABEL,
                    tag="resource_combo",
                    width=300,
                    callback=None,
                )
                dpg.add_button(label="Refresh", callback=self._refresh_clicked)
                dpg.add_button(label="Connect", callback=self._connect_clicked)

            with dpg.group(horizontal=True):
                dpg.add_text("Channels:", color=TEXT_MUTED)
                for ch in CHANNELS:
                    dpg.add_checkbox(
                        label=ch,
                        tag=f"chk_{ch}",
                        default_value=(ch == "CH1"),
                        callback=self._channels_changed,
                    )
                dpg.add_spacer(width=10)
                dpg.add_button(label="Plot", callback=self._plot_clicked)
                with dpg.tooltip(dpg.last_item()):
                    dpg.add_text("Read out whatever's currently in acquisition memory.")
                dpg.add_button(label="Single Seq", callback=self._single_seq_clicked)
                with dpg.tooltip(dpg.last_item()):
                    dpg.add_text("Force exactly one new trigger, wait for it, then plot it.")
                dpg.add_checkbox(label="Continuous", callback=self._continuous_toggled)
                with dpg.tooltip(dpg.last_item()):
                                    dpg.add_text("Continuosly pulls points")
                dpg.add_button(label="Run/Stop", tag="run_stop_button", callback=self._run_stop_clicked)

            dpg.add_text("", tag="status_text", color=TEXT_MUTED)

            with dpg.group(horizontal=True):
                dpg.add_text("", tag="vpp_text", color=ACCENT_AMBER)
                dpg.add_spacer(width=20)
                dpg.add_text("", tag="info_text", color=TEXT_MUTED)

            with dpg.plot(label="", height=400, width=-1):
                dpg.add_plot_legend()
                self.x_axis = dpg.add_plot_axis(dpg.mvXAxis, label="Time (us)")
                self.y_axis = dpg.add_plot_axis(dpg.mvYAxis, label="Voltage (V)")
                for ch in CHANNELS:
                    tag = f"series_{ch}"
                    self._series_tags[ch] = tag
                    dpg.add_line_series([], [], label=ch, parent=self.y_axis, tag=tag, show=(ch == "CH1"))
                    dpg.bind_item_theme(tag, self._channel_themes[ch])

                # Cursors: 2 vertical (X1/X2) + 2 horizontal (Y1/Y2), hidden
                # until toggled on. Dragging any of them updates the readout
                # via _cursor_moved (see _cursors_toggled/_reset_cursor_positions
                # for how they get positioned when first turned on).
                self.cursor_x1 = dpg.add_drag_line(
                    label="X1", default_value=0.0, color=(*CURSOR_A, 255),
                    vertical=True, show=False, callback=self._cursor_moved,
                )
                self.cursor_x2 = dpg.add_drag_line(
                    label="X2", default_value=0.0, color=(*CURSOR_B, 255),
                    vertical=True, show=False, callback=self._cursor_moved,
                )
                self.cursor_y1 = dpg.add_drag_line(
                    label="Y1", default_value=0.0, color=(*CURSOR_A, 255),
                    vertical=False, show=False, callback=self._cursor_moved,
                )
                self.cursor_y2 = dpg.add_drag_line(
                    label="Y2", default_value=0.0, color=(*CURSOR_B, 255),
                    vertical=False, show=False, callback=self._cursor_moved,
                )

            with dpg.group(horizontal=True):
                dpg.add_button(label="Fit X", callback=self._fit_x_clicked)
                dpg.add_text("View:", color=TEXT_MUTED)
                dpg.add_radio_button(
                    items=["Waveform", "FFT", "Jitter"],
                    default_value="Waveform",
                    tag="domain_radio",
                    horizontal=True,
                    callback=self._domain_changed,
                )
                dpg.add_checkbox(label="Cursors", tag="cursors_checkbox", callback=self._cursors_toggled)
                dpg.add_button(label="Save Plot...", callback=self._save_plot_clicked)

            dpg.add_text("", tag="cursor_readout", color=ACCENT_AMBER)

            dpg.add_spacer(height=8)
            dpg.add_text("Manual Command", color=TEXT_BRIGHT)
            with dpg.group(horizontal=True):
                dpg.add_input_text(
                    tag="cmd_input",
                    hint="e.g. CH1:SCALE 0.5   or   *IDN?",
                    width=400,
                    on_enter=True,
                    callback=self._query_clicked,  # Enter key = Query (most common case)
                )
                dpg.add_button(label="Send", callback=self._send_clicked)
                dpg.add_button(label="Query", callback=self._query_clicked)

            dpg.add_input_text(
                tag="cmd_log",
                multiline=True,
                readonly=True,
                height=140,
                width=-1,
            )

        # Save-file dialog, created hidden; shown on "Save Plot..." click.
        with dpg.file_dialog(
            directory_selector=False,
            show=False,
            callback=self._save_dialog_confirmed,
            tag="save_file_dialog",
            default_filename="capture.csv",
            width=600,
            height=400,
        ):
            dpg.add_file_extension(".csv", color=(0, 255, 0, 255))
            dpg.add_file_extension(".*")

        # Matches VisaWorker's default self._simulated_running = True
        self.set_run_state(running=True)

    # -- widget callbacks -> external handlers -----------------------------

    def _refresh_clicked(self, sender, app_data):
        if self.on_refresh_resources:
            self.on_refresh_resources()

    def _connect_clicked(self, sender, app_data):
        selected = dpg.get_value("resource_combo")
        resource_name = None if selected == self.SIMULATED_LABEL else selected
        if self.on_connect:
            self.on_connect(resource_name)

    def _channels_changed(self, sender, app_data):
        enabled = [ch for ch in CHANNELS if dpg.get_value(f"chk_{ch}")]
        if self.on_channels_changed:
            self.on_channels_changed(enabled)

    def _plot_clicked(self, sender, app_data):
        if self.on_plot_clicked:
            self.on_plot_clicked()

    def _single_seq_clicked(self, sender, app_data):
        if self.on_single_seq_clicked:
            self.on_single_seq_clicked()

    def _continuous_toggled(self, sender, app_data):
        if self.on_continuous_toggled:
            self.on_continuous_toggled(app_data)  # app_data = checkbox bool state

    def _fit_x_clicked(self, sender, app_data):
        """Manual 'zoom out to see everything' button. This is separate from
        the automatic fit in _render_current_domain() - that one only fires
        on the first capture or a domain switch, so it won't fight you if
        you've manually zoomed in and Continuous is still running."""
        dpg.fit_axis_data(self.x_axis)

    def _domain_changed(self, sender, app_data):
        self._domain = self.DOMAIN_LABELS[app_data]
        self._need_x_fit = True  # each view has an unrelated x-axis scale - always re-fit on switch
        self._render_current_domain()
        self._update_cursor_readout()  # units (us/kHz, V/dBV/ns) depend on domain

    def _cursors_toggled(self, sender, app_data):
        self._cursors_enabled = app_data
        for tag in (self.cursor_x1, self.cursor_x2, self.cursor_y1, self.cursor_y2):
            dpg.configure_item(tag, show=self._cursors_enabled)
        if self._cursors_enabled:
            self._reset_cursor_positions()
        self._update_cursor_readout()

    def _cursor_moved(self, sender, app_data):
        self._update_cursor_readout()

    def _save_plot_clicked(self, sender, app_data):
        if self._last_capture is None:
            self.append_log("(no capture yet - nothing to save)")
            return
        dpg.show_item("save_file_dialog")

    def _save_dialog_confirmed(self, sender, app_data):
        filepath = app_data.get("file_path_name")
        if filepath and self.on_save_plot:
            self.on_save_plot(filepath)

    def _send_clicked(self, sender, app_data):
        cmd = dpg.get_value("cmd_input").strip()
        if cmd and self.on_send_command:
            self.on_send_command(cmd)

    def _query_clicked(self, sender, app_data):
        cmd = dpg.get_value("cmd_input").strip()
        if cmd and self.on_query_command:
            self.on_query_command(cmd)

    def _run_stop_clicked(self, sender, app_data):
        if self.on_run_stop_clicked:
            self.on_run_stop_clicked()

    # -- public API, called from main.py's poll loop ------------------------


    def update_multi(self, channels_data: dict):
        """channels_data: {"CH1": {"time_us":..., "voltage":..., "x_incr":..., "n_points":...}, ...}
        Stores the raw capture and renders whichever domain (time/freq) is
        currently selected. If Continuous is running while the FFT view is
        active, the spectrum updates live too - _render_current_domain()
        always reflects self._domain, not just the domain active when the
        capture arrived."""
        self._last_capture = channels_data
        self._render_current_domain()

    def _render_current_domain(self):
        data = self._last_capture
        if not data:
            return

        info_parts = []
        first_time_us = None
        for ch in CHANNELS:
            tag = self._series_tags[ch]
            if ch not in data:
                dpg.configure_item(tag, show=False)
                continue

            d = data[ch]
            time_us, voltage = d["time_us"], d["voltage"]
            if first_time_us is None:
                first_time_us = time_us  # only meaningful in time domain, tracked below

            if self._domain == "time":
                x, y = time_us, voltage
                vpp = float(voltage.max() - voltage.min())
                info_parts.append(f"{ch} Vpp={vpp:.3f}V")
                if d.get("x_incr") is not None and d.get("n_points") is not None:
                    dpg.set_value("info_text", f"XINCR = {d['x_incr']:.3e} s   |   {d['n_points']} pts")

            elif self._domain == "freq":
                freq_hz, mag_db = compute_spectrum(voltage, d["x_incr"], db=True)
                x, y = freq_hz / 1000.0, mag_db  # kHz for readability
                if len(y) > 1:
                    peak_idx = int(y[1:].argmax()) + 1  # skip the DC bin
                    info_parts.append(f"{ch}: {x[peak_idx]:.2f} kHz @ {y[peak_idx]:.1f} dBV")
                bin_width_hz = 1.0 / (d["x_incr"] * d["n_points"])
                dpg.set_value("info_text", f"FFT bin width = {bin_width_hz:.2f} Hz")

            else:  # jitter
                jitter = compute_jitter(time_us, voltage)
                if jitter is None:
                    x, y = np.array([]), np.array([])
                    info_parts.append(f"{ch}: not enough edges detected")
                else:
                    # TIE plotted against each edge's actual time position
                    x = jitter["edge_times_s"] * 1e6  # us, same units as the Time view's x-axis
                    y = jitter["tie_s"] * 1e9          # ns
                    freq_khz = (1.0 / jitter["fitted_period_s"]) / 1000.0
                    info_parts.append(
                        f"{ch}: {freq_khz:.3f} kHz  "
                        f"RMS={jitter['rms_jitter_s']*1e9:.2f}ns  "
                        f"pk-pk={jitter['pp_jitter_s']*1e9:.2f}ns  "
                        f"({jitter['n_edges']} edges)"
                    )

            dpg.set_value(tag, [list(x), list(y)])
            dpg.configure_item(tag, show=True)

        domain_labels = {"time": "Waveform", "freq": "Spectrum", "jitter": "Jitter (TIE)"}
        axis_labels = {
            "time": ("Time (us)", "Voltage (V)"),
            "freq": ("Frequency (kHz)", "Magnitude (dBV)"),
            "jitter": ("Time (us)", "TIE (ns)"),
        }
        dpg.set_value("title_channel_text", f"—  {', '.join(data.keys())} {domain_labels[self._domain]}")
        dpg.set_value("vpp_text", "   ".join(info_parts))
        x_label, y_label = axis_labels[self._domain]
        dpg.configure_item(self.x_axis, label=x_label)
        dpg.configure_item(self.y_axis, label=y_label)

        # X axis: only auto-fit on the first-ever render or right after a
        # Time/FFT domain switch (self._need_x_fit, set in those two spots).
        # NOT on every routine data update - otherwise a manually zoomed-in
        # view would get reset back out to "fit everything" on each new
        # capture, which is especially disruptive with Continuous running.
        # Manual zoom (scroll wheel, click-drag box zoom, right-click-drag
        # to pan) is a built-in DearPyGui/ImPlot plot feature - no code
        # needed for that part, it just needs to stop being fought.
        if self._need_x_fit:
            dpg.fit_axis_data(self.x_axis)
            self._need_x_fit = False

        # Y axis: keep auto-fitting every update, like a scope's vertical
        # autoscale reacting to each new acquisition.
        dpg.fit_axis_data(self.y_axis)

        if self._domain == "time" and first_time_us is not None:
            self._last_time_range = (float(first_time_us[0]), float(first_time_us[-1]))

        self._update_cursor_readout()

    def get_last_capture(self):
        return self._last_capture

    def _reset_cursor_positions(self):
        """Snap cursors to a sensible starting spot (25%/75% across the
        current view) instead of wherever they defaulted to at startup,
        which could be off-screen depending on the current axis range."""
        try:
            x_lo, x_hi = dpg.get_axis_limits(self.x_axis)
            y_lo, y_hi = dpg.get_axis_limits(self.y_axis)
        except Exception:
            return
        x_span, y_span = x_hi - x_lo, y_hi - y_lo
        dpg.set_value(self.cursor_x1, x_lo + 0.25 * x_span)
        dpg.set_value(self.cursor_x2, x_lo + 0.75 * x_span)
        dpg.set_value(self.cursor_y1, y_lo + 0.25 * y_span)
        dpg.set_value(self.cursor_y2, y_lo + 0.75 * y_span)

    def _update_cursor_readout(self):
        if not self._cursors_enabled:
            dpg.set_value("cursor_readout", "")
            return

        x1, x2 = dpg.get_value(self.cursor_x1), dpg.get_value(self.cursor_x2)
        y1, y2 = dpg.get_value(self.cursor_y1), dpg.get_value(self.cursor_y2)
        dx, dy = x2 - x1, y2 - y1

        x_unit, y_unit = {
            "time": ("us", "V"),
            "freq": ("kHz", "dBV"),
            "jitter": ("us", "ns"),
        }[self._domain]

        extra = ""
        if self._domain == "time" and dx != 0:
            # classic scope-cursor move: measure a period with cursors, read off the frequency
            extra = f"   1/dX = {1e6/dx:,.2f} Hz"

        dpg.set_value(
            "cursor_readout",
            f"A: X={x1:.4f}{x_unit} Y={y1:.4f}{y_unit}   "
            f"B: X={x2:.4f}{x_unit} Y={y2:.4f}{y_unit}   "
            f"dX={dx:.4f}{x_unit} dY={dy:.4f}{y_unit}{extra}",
        )

    def set_run_state(self, running: bool):
        """Called from main.py's poll loop when the worker reports what the
        instrument's run/stop state actually is (never set optimistically
        by the button click itself - see _run_stop_clicked)."""
        dpg.configure_item("run_stop_button", label="Stop" if running else "Run")
        dpg.bind_item_theme("run_stop_button", self._run_theme if running else self._stop_theme)

    def set_resource_options(self, resources: list[str]):
        """Populate the resource combo. Always includes the simulated option
        first; real VISA resources (if any) follow."""
        items = [self.SIMULATED_LABEL] + resources
        dpg.configure_item("resource_combo", items=items)
        current = dpg.get_value("resource_combo")
        if current not in items:
            dpg.set_value("resource_combo", items[0])

    def set_status(self, text: str):
        dpg.set_value("status_text", text)

    def append_log(self, line: str):
        current = dpg.get_value("cmd_log")
        new_text = (current + "\n" + line) if current else line
        dpg.set_value("cmd_log", new_text)

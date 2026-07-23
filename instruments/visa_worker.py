"""
instruments/visa_worker.py

Runs all VISA/instrument communication on a dedicated background thread.
has a built in simulation as well to fallback on if not connected

Things that can wake the worker up, all safe to call from the GUI thread:
- connect(resource_name)         -> (re)connect to a resource, or None to go
                                     to simulated mode
- request_capture()              -> passive read: grab whatever's currently
                                     in acquisition memory, no new trigger
- request_single_sequence()      -> force exactly one new trigger, wait for
                                     it, then read all enabled channels
- set_continuous(True/False)     -> free-running acquisition at poll_interval
                                     (each cycle forces a new trigger, same
                                     as request_single_sequence would)
- set_enabled_channels(list)     -> which channels the NEXT acquisition(s) read
- send_command(cmd)              -> write-only, no response expected
- query_command(cmd)             -> write + read, response reported back

set_enabled_channels() assigns a plain list attribute (a fresh list, never
mutated in place). That's safe without a lock because CPython attribute
assignment is atomic under the GIL - the worker thread will always see
either the old list or the new one, never a half-written one.

Multi-channel capture: reading N channels means N sequential CURVE?
transfers (a scope can only stream one channel's data at a time), but they
all come from the SAME acquisition record, so they stay time-aligned - as
long as the scope isn't allowed to re-trigger between channel reads. When a
fresh trigger IS wanted (Single Seq button, or each cycle of Continuous),
_capture_channels(arm=True) puts the instrument in single-sequence mode
(ACQuire:STOPAFTER SEQUENCE + ACQuire:STATE ON, then waits on *OPC?) before
reading any channel. This is standard across Tek TDS/MSO/DPO scopes, but
wrapped in try/except: if a particular model/firmware doesn't like it,
capture still proceeds (just without that extra guarantee) instead of
crashing the worker. When arm=False (a plain Plot click), none of that
happens - it just reads out whatever's already in acquisition memory,
which is exactly what you want for e.g. an event you triggered manually
from the scope's own front panel and don't want to disturb.
"""

from __future__ import annotations

import threading
import queue
import numpy as np

try:
    import pyvisa
except ImportError:
    pyvisa = None


def list_visa_resources() -> list[str]:
#find available instruments
    if pyvisa is None:
        return []
    try:
        rm = pyvisa.ResourceManager()
        resources = list(rm.list_resources())
        rm.close()
        return resources
    except Exception:
        return []


class VisaWorker:
    def __init__(self, resource_name: str | None = None, poll_interval: float = 0.3):
        self.resource_name = resource_name
        self.poll_interval = poll_interval

        # bounded queue: if the GUI falls behind, we drop old waveform frames
        # instead of piling up unbounded memory. Command responses always
        # get through (see _push).
        self.data_queue: "queue.Queue[dict]" = queue.Queue(maxsize=10)
        self._command_queue: "queue.Queue[dict]" = queue.Queue()

        self._stop_event = threading.Event()
        self._capture_requested = threading.Event()
        self._single_seq_requested = threading.Event()
        self._continuous = threading.Event()
        self._connect_requested = threading.Event()
        self._wake_event = threading.Event()

        self._pending_resource_name = resource_name
        self._simulate = True  # updated for real once the thread starts

        self._enabled_channels: list[str] = ["CH1"]
        self._run_stop_requested = threading.Event()
        self._simulated_running = True  # only meaningful while self._simulate is True

        self._thread: threading.Thread | None = None
        self._rm = None
        self._inst = None

    # -- public API, called from the GUI thread ---------------------------

    def start(self):
        if self._thread and self._thread.is_alive():
            return  # already running
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop_event.set()
        self._wake_event.set()
        if self._thread:
            self._thread.join(timeout=5)

    def connect(self, resource_name: str | None):
        """(Re)connect to a VISA resource. Pass None to switch to simulated
        data. Actual connecting happens on the worker thread."""
        self._pending_resource_name = resource_name
        self._connect_requested.set()
        self._wake_event.set()

    def request_capture(self):
        #Passive read: grab whatever's currently in acquisition memory,
        self._capture_requested.set()
        self._wake_event.set()

    def request_single_sequence(self):
        """Force exactly one new trigger, wait for it to complete, then read
        all enabled channels. This is what the Plot button used to do before
        it became a passive read - use this when you specifically want a
        fresh acquisition."""
        self._single_seq_requested.set()
        self._wake_event.set()

    def set_continuous(self, enabled: bool):
        """Enable/disable free-running acquisition."""
        if enabled:
            self._continuous.set()
        else:
            self._continuous.clear()
        self._wake_event.set()

    def set_enabled_channels(self, channels: list[str]):
        #Select which channels the next acquisition(s) will read, e.g. ['CH1','CH2', so on].
        self._enabled_channels = list(channels)

    def send_command(self, command: str):
        """Write a command, no response expected (e.g. 'CH1:SCALE 0.5')."""
        self._command_queue.put({"kind": "send", "command": command})
        self._wake_event.set()

    def query_command(self, command: str):
        #Write a command and read back a response (e.g. '*IDN?')
        self._command_queue.put({"kind": "query", "command": command})
        self._wake_event.set()

    def toggle_run_stop(self):
        self._run_stop_requested.set()
        self._wake_event.set()


    def _handle_run_stop_toggle(self):
        if self._simulate:
            self._simulated_running = not self._simulated_running
            self._push({"run_state": "RUN" if self._simulated_running else "STOP"})
            return
        try:
            self._inst.write("ACQ:STOPA RUNST")
            is_running = self._inst.query("ACQUIRE:STATE?").strip() in ("1", "ON")
            self._inst.write("ACQUIRE:STATE OFF" if is_running else "ACQUIRE:STATE ON")
            self._push({"run_state": "STOP" if is_running else "RUN"})
        except Exception as e:
            self._push({"error": str(e)})    

    # -- internals, run on the worker thread -------------------------------

    def _run(self):
        self._apply_connect(self._pending_resource_name)

        while not self._stop_event.is_set():
            self._wake_event.wait(timeout=self.poll_interval)
            self._wake_event.clear()

            if self._stop_event.is_set():
                break

            if self._connect_requested.is_set():
                self._connect_requested.clear()
                self._apply_connect(self._pending_resource_name)

            # This has to come BEFORE the capture-related "continue" below.
            # A wake-up caused only by the Run/Stop button (no capture
            # requested, Continuous off) would otherwise hit that `continue`
            # and jump straight back to the top of the loop, skipping this
            # check entirely - the event would be set but never handled.
            if self._run_stop_requested.is_set():
                self._run_stop_requested.clear()
                self._handle_run_stop_toggle()

            # Always drain manual commands first - they're explicit user actions
            self._drain_commands()

            triggered_passive = self._capture_requested.is_set()
            triggered_single_seq = self._single_seq_requested.is_set()
            if triggered_passive:
                self._capture_requested.clear()
            if triggered_single_seq:
                self._single_seq_requested.clear()

            if not (triggered_passive or triggered_single_seq or self._continuous.is_set()):
                continue  # nothing to capture, go back to waiting

            channels = list(self._enabled_channels)
            if not channels:
                self._push({"error": "No channels selected to capture"})
                continue

            # Continuous and an explicit Single Seq both force a fresh
            # trigger. A plain passive capture (Plot) just reads out
            # whatever's already in acquisition memory.
            arm_new_trigger = triggered_single_seq or self._continuous.is_set()

            try:
                result = self._capture_channels(channels, arm=arm_new_trigger)
                self._push(result)
            except Exception as e:
                self._push({"error": str(e)})

        self._disconnect()

    def _apply_connect(self, resource_name: str | None):
        """Runs on the worker thread. Closes any existing connection, then
        either opens the new resource or drops into simulated mode."""
        self._disconnect()
        self.resource_name = resource_name

        if resource_name is None or pyvisa is None:
            self._simulate = True
            self._push({"status": "Simulated data"})
            return

        try:
            self._rm = pyvisa.ResourceManager()
            self._inst = self._rm.open_resource(resource_name)
            self._inst.timeout = 10_000
            idn = self._inst.query("*IDN?").strip()
            self._simulate = False
            self._push({"status": f"Connected to: {idn}"})
        except Exception as e:
            self._simulate = True
            self._inst = None
            self._rm = None
            self._push({"error": f"Connect failed ({resource_name}): {e}"})

    def _drain_commands(self):
        while True:
            try:
                req = self._command_queue.get_nowait()
            except queue.Empty:
                break

            cmd = req["command"]
            try:
                if req["kind"] == "send":
                    if not self._simulate:
                        self._inst.write(cmd)
                    self._push({"cmd_type": "send", "command": cmd, "status": "sent"})
                else:  # query
                    if self._simulate:
                        response = "SIM_RESPONSE"
                    else:
                        response = self._inst.query(cmd).strip()
                    self._push({"cmd_type": "query", "command": cmd, "response": response})
            except Exception as e:
                self._push({"cmd_type": req["kind"], "command": cmd, "cmd_error": str(e)})

    def _push(self, item: dict):
        # command responses and errors should never be dropped; only drop
        # stale waveform frames if the GUI is falling behind
        is_droppable = "channels" in item
        if is_droppable and self.data_queue.full():
            try:
                self.data_queue.get_nowait()
            except queue.Empty:
                pass
        self.data_queue.put(item)

    def _disconnect(self):
        try:
            if self._inst:
                self._inst.close()
            if self._rm:
                self._rm.close()
        except Exception:
            pass
        self._inst = None
        self._rm = None

    def _arm_single_acquisition(self):
        """Freeze the scope on exactly one new acquisition so every channel
        we then read comes from the same trigger event. Best-effort: if the
        instrument doesn't like these commands, we swallow the error and
        proceed with whatever's currently in acquisition memory."""
        try:
            self._inst.write("ACQUIRE:STOPAFTER SEQUENCE")
            self._inst.write("ACQUIRE:STATE ON")
            self._inst.query("*OPC?")  # blocks until the acquisition completes
        except Exception:
            pass

    def _capture_channels(self, channels: list[str], arm: bool) -> dict:
        """Read multiple channels from the SAME acquisition (synchronized),
        so the result is suitable for multi-trace export / protocol decode.

        arm=True forces a fresh single-sequence trigger first (Single Seq
        button, or each cycle of Continuous). arm=False just reads out
        whatever's currently in acquisition memory - e.g. an event you
        triggered manually from the scope's own front panel."""
        if arm and not self._simulate:
            self._arm_single_acquisition()

        per_channel = {}
        for ch in channels:
            data = self._simulate_waveform(ch) if self._simulate else self._acquire_waveform(ch)
            per_channel[ch] = data

        return {"channels": per_channel}

    def _get_scaling_params(self, channel: str) -> dict:
        """Get the 6 numbers needed to scale raw ADC counts into time/voltage.

        Queries each parameter BY NAME (WFMPRE:XINCR? etc.) rather than
        parsing the combined WFMPRE? string by field position. Different
        Tek models don't necessarily put the same fields in the same
        position - e.g. a TDS2014C and a TDS3012B were observed to disagree
        on where YZERO falls in the combined string. Named sub-queries are
        documented and consistent across the TDS2000C/TDS3000B/MSO command
        set, so this is the robust way to do it.

        Falls back to positional parsing of WFMPRE? only if the instrument
        doesn't support the individual sub-queries (older/other firmware).
        """
        inst = self._inst
        try:
            return {
                "x_incr": float(inst.query("WFMPRE:XINCR?").strip()),
                "x_off": float(inst.query("WFMPRE:PT_OFF?").strip()),
                "x_zero": float(inst.query("WFMPRE:XZERO?").strip()),
                "y_mult": float(inst.query("WFMPRE:YMULT?").strip()),
                "y_off": float(inst.query("WFMPRE:YOFF?").strip()),
                "y_zero": float(inst.query("WFMPRE:YZERO?").strip()),
            }
        except Exception:
            # Fallback: standard field order for the combined WFMPRE? string
            #   0 BYT_NR, 1 BIT_NR, 2 ENCDG, 3 BN_FMT, 4 BYT_OR, 5 NR_PT,
            #   6 WFID, 7 PT_FMT, 8 XINCR, 9 PT_OFF, 10 XZERO, 11 XUNIT,
            #   12 YMULT, 13 YOFF, 14 YZERO, 15 YUNIT
            preamble = inst.query("WFMPRE?")
            fields = [f.strip().strip('"') for f in preamble.strip().split(";")]
            return {
                "x_incr": float(fields[8]),
                "x_off": float(fields[9]),
                "x_zero": float(fields[10]),
                "y_mult": float(fields[12]),
                "y_off": float(fields[13]),
                "y_zero": float(fields[14]),
            }

    def _get_record_length(self) -> int:
        """How many points are actually in the current acquisition
        """
        try:
            return int(float(self._inst.query("HORIZONTAL:RECORDLENGTH?").strip()))
        except Exception:
            return 10000

    def _acquire_waveform(self, channel: str) -> dict:
        """Voltage conversion per the Tek programmer manual:
            voltage = YZERO + YMULT * (raw - YOFF)
        """
        inst = self._inst

        record_length = self._get_record_length()

        inst.write(f"DATA:SOURCE {channel}")
        inst.write("DATA:ENC RIBinary")
        inst.write("DATA:WIDTH 2")
        inst.write("DATA:START 1")
        inst.write(f"DATA:STOP {record_length}")

        p = self._get_scaling_params(channel)

        raw = inst.query_binary_values(
            "CURVE?",
            datatype="h",           # signed 16-bit int (matches DATA:WIDTH 2)
            is_big_endian=True,
            container=np.array,
        )

        voltage = p["y_zero"] + p["y_mult"] * (raw - p["y_off"])
        time_s = (np.arange(len(raw)) - p["x_off"]) * p["x_incr"] + p["x_zero"]
        time_us = time_s * 1e6

        return {
            "time_us": time_us,
            "voltage": voltage,
            "x_incr": p["x_incr"],
            "n_points": len(raw),
        }

    def _simulate_waveform(self, channel: str, n=2000, span_us=200) -> dict:
        # give each channel a visibly different signal so multi-channel
        # capture/export is obvious even with no hardware attached
        offsets = {"CH1": (1.6, (10_000, 100_000)), "CH2": (0.8, (5_000, 20_000)),
                   "CH3": (0.4, (1_000, 5_000)), "CH4": (0.2, (500, 2_000))}
        amplitude, freq_range = offsets.get(channel, (1.0, (1_000, 10_000)))
        freq_hz = np.random.randint(*freq_range)

        time_us = np.linspace(0, span_us, n)
        time_s = time_us * 1e-6
        voltage = amplitude * np.sin(2 * np.pi * freq_hz * time_s) + 0.03 * np.random.randn(n)
        return {
            "time_us": time_us,
            "voltage": voltage,
            "x_incr": (time_us[1] - time_us[0]) * 1e-6,
            "n_points": n,
        }

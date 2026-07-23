# Oscilloscope Viewer
oscilloscope desktop app (Dear PyGui + PyVISA) for pulling
waveforms off a Tektronix scope, viewing them as a waveform, FFT, or clock
jitter (TIE) plot, and exporting multi-channel captures for use in
sigrok/PulseView. Runs fine with no hardware attached (simulated data),
so the UI is fully testable standalone.
 
Tested against a Tektronix **TDS2014C**, **TDS3012B**, and **TDS5054B** —

## Features
 
- Multi-channel (CH1-CH4) **synchronized** capture — reads all enabled
  channels from the same trigger event, suitable for protocol decode
- VISA resource discovery in-app (Refresh/Connect); falls back to
  **simulated data** when nothing is connected
- Three ways to capture:
  - **Plot** - passive read of whatever's currently in acquisition memory
    (e.g. an event you triggered manually from the scope's front panel)
  - **Single Seq** - forces one fresh trigger, waits for it, then reads
  - **Continuous** - free-running, forces a fresh trigger every cycle
- **Run/Stop** button - queries the instrument's actual acquisition state
  and sends the opposite, so the button always reflects reality
- Three plot views, toggled live on the same chart:
  - **Waveform** - voltage vs. time
  - **FFT** - dBV magnitude spectrum (Hann window, DC removed, RMS-referenced)
  - **Jitter (TIE)** - Time Interval Error per edge vs. a least-squares
    ideal clock, plus RMS/pk-pk period jitter and frequency
- **Draggable cursors** (on/off toggle) with a live delta + frequency
  readout, units adapting to whichever view is active
- **Manual SCPI console** - Send (write-only) and Query (write + read)
  buttons, for debugging or one-off measurements outside the main UI
- **Save Plot** - exports the current multi-channel capture as a CSV
  compatible with sigrok/PulseView's CSV importer
## Requirements
 
- Python 3.10+
- A VISA backend if you want to talk to real hardware (NI-VISA, Keysight
  IO libraries, or the pure-Python `pyvisa-py`). Without one, the app
  still runs fully in simulated mode.
```bash
pip install -r requirements.txt
```
 
## Running
 
```bash
python main.py
```
 
The app starts in simulated mode. Click **Refresh** to list VISA
resources, pick one, and click **Connect** to switch to real hardware.
 
## Project structure
 
```
scope_project/
├── main.py                  Entry point; wires VisaWorker <-> ScopeWindow via a queue.Queue
├── requirements.txt
├── gui/
│   ├── theme.py              Colors + DearPyGui theme; per-channel/button color helpers
│   └── scope_window.py       All widgets/layout; owns the plot, cursors, command console
├── instruments/
│   └── visa_worker.py        All VISA I/O, isolated on its own background thread
├── analysis/
│   ├── fft.py                Spectrum (dBV) from a captured waveform
│   └── jitter.py             Edge detection (sub-sample interpolated) + period jitter / TIE
└── export/
    └── sigrok_csv.py         Multi-channel CSV writer, sigrok/PulseView-compatible
```
 
## Architecture notes
 
- **All VISA/instrument I/O runs on one background thread** (`VisaWorker`).
  The GUI thread never touches `pyvisa` directly. Communication is
  one-way via a `queue.Queue`: the GUI submits requests (capture, connect,
  send/query command, ...) by calling `worker.<method>()`, which just sets
  a `threading.Event` and wakes the worker thread; the worker does the
  actual VISA calls and pushes results back onto the queue for the GUI's
  render loop to drain once per frame. This keeps slow/blocking
  instrument I/O from ever freezing the UI.
- **No hardware required for development.** With nothing connected,
  `VisaWorker` generates synthetic per-channel waveforms, so the whole
  app - including FFT, jitter analysis, and CSV export - is testable
  without a scope attached.
- **Preamble parsing is robust across models.** Different Tek scopes lay
  out `WFMPRE?`'s fields in different orders (confirmed directly across
  the TDS2014C/TDS3012B/TDS5054B during testing - not just theoretical).
  `visa_worker.py` queries each scaling parameter individually by name
  (`WFMPRE:XINCR?`, `WFMPRE:YZERO?`, etc.) rather than parsing fixed field
  positions, falling back to positional parsing only if an instrument
  doesn't support the named sub-queries.
- **Record length is queried, not assumed.** `DATA:STOP` is set from
  `HORIZONTAL:RECORDLENGTH?` each capture rather than a hardcoded value,
  so multi-channel reads pull the instrument's actual full record instead
  of an arbitrary slice of it.
## Known limitations/things to verify per instrument
 
- `ACQUIRE:STOPAFTER SEQUENCE` (used to synchronize multi-channel reads,
  and by Single Seq) is standard across the Tek TDS/DPO/MSO line but
  wrapped in a try/except - if a particular instrument's firmware doesn't
  support it, capture still proceeds, just without that synchronization
  guarantee.
- Jitter measurements need enough edges in the capture window to mean
  anything statistically - as a rough guide, 50+ edges before trusting
  the RMS/pk-pk numbers. A handful of edges will produce a number that
  *looks* plausible but isn't.
- Sub-sample edge interpolation is only as good as how many real samples
  land on the actual transition. A transition that completes within one
  sample period will mostly show interpolation noise, not real jitter.
  When in doubt, cross-check against the instrument's own
  `MEASUREMENT:MEASx:STDDEV?` (with `MEASUREMENT:STATISTICS:MODE ALL`
  enabled and the scope left running for a few seconds first).
- CSV export produces **analog** channels for PulseView. Running a
  protocol decoder (I2C, SPI, UART, ...) on the imported data requires
  applying a logic-threshold Conversion in PulseView first - the decoder
  can't be pointed at analog data directly.

"""
export/sigrok_csv.py

Writes a multi-channel scope capture as a CSV file that sigrok/PulseView's
CSV input module can read as "mixed signal" (timestamped, multi-column)
data. Format:

    Time (s),CH1,CH2
    0.000000000,1.234500,-0.021300
    0.000000640,1.198700,-0.018800
    ...

PulseView's CSV import dialog lets you mark the first column as a timestamp
(it derives the samplerate from the first two timestamp deltas) and each
remaining column as Analog. If auto-detection of the samplerate looks off,
you can also enter it directly - it's 1 / XINCR from the capture.

IMPORTANT: sigrok's protocol decoders (I2C, SPI, UART, ...) operate on
LOGIC (digital) channels, not analog voltage traces. After importing this
CSV, right-click each analog channel in PulseView and use "Conversion" to
apply a logic threshold before attaching a decoder. This export can't do
that step for you - a digital 0/1 threshold is a judgment call about your
specific signal that only makes sense to set in PulseView where you can
see the trace.
"""

from __future__ import annotations

import csv


def write_multi_channel_csv(path: str, channels_data: dict[str, dict]) -> int:
    """channels_data: {"CH1": {"time_us": array, "voltage": array}, "CH2": {...}, ...}
    as produced by VisaWorker's multi-channel capture result.

    All channels are assumed to share the same time base (true for a
    synchronized multi-channel scope capture) - the first channel's time
    array is used as the canonical "Time (s)" column.

    Returns the number of rows written.
    """
    channels = list(channels_data.keys())
    if not channels:
        raise ValueError("No channel data to export")

    time_us = channels_data[channels[0]]["time_us"]
    n = len(time_us)

    with open(path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["Time (s)"] + channels)
        for i in range(n):
            row = [f"{time_us[i] * 1e-6:.9f}"]
            for ch in channels:
                voltage = channels_data[ch]["voltage"]
                row.append(f"{voltage[i]:.6f}" if i < len(voltage) else "")
            writer.writerow(row)

    return n

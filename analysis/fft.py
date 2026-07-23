"""
analysis/fft.py

Turns a captured voltage array into a frequency-domain spectrum. Pure numpy
math - no dependency on DearPyGui or VISA, so it's usable/testable on its
own (and could be reused from a script or a test without pulling in the
whole GUI).
"""

from __future__ import annotations

import numpy as np


def compute_spectrum(voltage: np.ndarray, x_incr: float, db: bool = True):
    """Compute the single-sided amplitude spectrum of a real-valued signal.

    voltage: the captured samples (volts)
    x_incr:  time between samples, in seconds (this is XINCR from the scope
             preamble). The sample rate is 1/x_incr.
    db:      if True, return magnitude in dBV (20*log10(V_rms)); if False,
             return raw V_rms.

    Returns (freq_hz, magnitude) as numpy arrays, same length, covering
    0 Hz up to the Nyquist frequency (sample_rate / 2).

    Implementation notes:
    - The DC offset (mean) is removed first so a nonzero average voltage
      doesn't dominate the spectrum as a giant 0 Hz spike.
    - A Hann window is applied before the FFT. A captured waveform is a
      finite, non-periodic slice of a continuous signal; without windowing,
      that abrupt start/end acts like a discontinuity and smears energy
      across many frequency bins ("spectral leakage"). Hann is a reasonable
      general-purpose default - it's what most benchtop spectrum analyzers
      use out of the box.
    - Magnitude is normalized by the window's coherent gain so amplitude
      readings are meaningful in volts, not just relative units.
    - Each FFT bin's raw amplitude represents the PEAK amplitude of a
      sinusoidal component at that frequency. Spectrum analyzers report
      dBV relative to RMS, not peak - for a sine wave, RMS = peak / sqrt(2).
      Skipping this conversion overstates every reading by 20*log10(sqrt(2))
      = ~3.01 dB (e.g. a 4 Vpp / 2 V-peak tone reads 6.02 dBV peak-based vs.
      the correct ~3.01 dBV RMS-based). This function reports RMS-based dBV
      to match what a real spectrum analyzer or scope FFT display would show.
    """
    n = len(voltage)
    if n < 2:
        return np.array([]), np.array([])

    window = np.hanning(n)
    windowed = (voltage - np.mean(voltage)) * window

    spectrum = np.fft.rfft(windowed)
    freq_hz = np.fft.rfftfreq(n, d=x_incr)

    # single-sided PEAK amplitude, corrected for the window's coherent gain
    magnitude_peak = np.abs(spectrum) / (np.sum(window) / 2)
    # convert to RMS - see docstring
    magnitude = magnitude_peak / np.sqrt(2)

    if db:
        eps = 1e-12  # avoid log10(0) for bins with ~zero energy
        magnitude = 20 * np.log10(np.maximum(magnitude, eps))

    return freq_hz, magnitude

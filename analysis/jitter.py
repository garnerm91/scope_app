"""
analysis/jitter.py

Extracts clock/logic edge timestamps from a captured waveform (with
sub-sample interpolation) and computes basic timing-jitter metrics from
them. Pure numpy math - no dependency on DearPyGui or VISA, same pattern
as analysis/fft.py.
"""

from __future__ import annotations

import numpy as np


def find_rising_edges(time_s: np.ndarray, voltage: np.ndarray, threshold: float | None = None) -> np.ndarray:
    """Find rising-edge crossing times, interpolated to sub-sample precision.

    threshold: voltage level that counts as a crossing. Defaults to the
    midpoint between the signal's min and max - a reasonable default for a
    clean clock/logic signal. Override it if your signal has an asymmetric
    duty cycle or you want a specific logic threshold (e.g. 1.4V for TTL).

    Returns interpolated crossing times, one per rising edge. Interpolation
    is linear between the sample just before and just after the threshold
    crossing. This matters a lot: without it, edge timing is quantized to
    the sample period, which for most captures is far coarser than the
    signal's actual jitter and would dominate/corrupt the measurement -
    you'd mostly be measuring the scope's sample clock, not the signal's.
    """
    if threshold is None:
        threshold = (voltage.max() + voltage.min()) / 2.0

    above = voltage >= threshold
    crossing_idx = np.where(~above[:-1] & above[1:])[0]  # low sample followed by high sample
    if len(crossing_idx) == 0:
        return np.array([])

    t0, t1 = time_s[crossing_idx], time_s[crossing_idx + 1]
    v0, v1 = voltage[crossing_idx], voltage[crossing_idx + 1]

    frac = (threshold - v0) / (v1 - v0)  # where between t0 and t1 does voltage cross threshold?
    return t0 + frac * (t1 - t0)


def compute_jitter(time_us: np.ndarray, voltage: np.ndarray, threshold: float | None = None) -> dict | None:
    """Compute period jitter and TIE (Time Interval Error) from a captured
    clock/logic waveform.

    - period_jitter: how much each individual cycle's period deviates from
      the mean period. Short-term, cycle-to-cycle noise.
    - TIE: how far each edge has drifted from where a perfectly regular
      clock would have placed it, using a least-squares fit of frequency
      across the whole capture as the "ideal" reference. Long-term drift/
      wander, not just per-cycle noise - a steady upward or downward trend
      in TIE usually means the actual frequency differs slightly from what
      you assumed, rather than random jitter.

    Returns None if fewer than 3 edges were found (not enough to compute a
    meaningful period or TIE).
    """
    time_s = time_us * 1e-6
    edge_times = find_rising_edges(time_s, voltage, threshold)

    if len(edge_times) < 3:
        return None

    periods = np.diff(edge_times)
    mean_period = periods.mean()
    period_jitter = periods - mean_period

    # Least-squares fit of edge_time vs. edge_index gives the best estimate
    # of the "ideal" clock frequency across the whole capture - a better
    # reference for TIE than just anchoring off the first edge, since that
    # would bias any period-estimate error into a false linear TIE ramp.
    idx = np.arange(len(edge_times))
    slope, intercept = np.polyfit(idx, edge_times, 1)
    ideal_edges = slope * idx + intercept
    tie = edge_times - ideal_edges

    return {
        "edge_times_s": edge_times,
        "periods_s": periods,
        "period_jitter_s": period_jitter,
        "tie_s": tie,
        "rms_jitter_s": float(period_jitter.std()),
        "pp_jitter_s": float(period_jitter.max() - period_jitter.min()),
        "mean_period_s": float(mean_period),
        "fitted_period_s": float(slope),
        "n_edges": len(edge_times),
    }

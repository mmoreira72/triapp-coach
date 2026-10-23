"""Is it the same session? The one place the matching rules live.

The same workout reaches the app by several routes (WKO bundle, TP file export, Intervals.icu), with clocks that can
disagree: a WKO entry without its file carries the planned time of day, and a workout recorded abroad keeps the local
clock in the WKO but UTC in the FIT. Two tests, used alone or together:

  near_start         starts within a few minutes of each other
  same_day_duration  same calendar day (± day_tol) and about the same duration

Callers narrow the candidates to the same athlete (and sport, when both sides know it) first.
"""

import pandas as pd

DUR_FRAC, DUR_ABS_S = 0.03, 120  # durations match within 3% or 2 min, whichever is larger


def near_start(starts, t, minutes):
    """Boolean Series: candidates starting within `minutes` of t."""
    return (starts - t).abs() <= pd.Timedelta(minutes=minutes)


def same_day_duration(starts, durations_s, t, duration_s, day_tol=1, frac=DUR_FRAC, abs_s=DUR_ABS_S):
    """Boolean Series: candidates on the same day (± day_tol days) whose duration is within max(frac, abs_s) of duration_s."""
    if duration_s is None or duration_s != duration_s:
        return pd.Series(False, index=starts.index)
    same_day = (starts.dt.normalize() - pd.Timestamp(t).normalize()).abs() <= pd.Timedelta(days=day_tol)
    return same_day & ((durations_s - duration_s).abs() <= max(frac * duration_s, abs_s))


DOUBLE_RECORDING_SHARE = 0.75  # two recordings of one session overlap for most of the longer one


def overlap_share(start_a, end_a, start_b, end_b):
    """Time both windows share, as a share of the longer one (0 when they do not overlap)."""
    shared = (min(end_a, end_b) - max(start_a, start_b)).total_seconds()
    longer = max((end_a - start_a).total_seconds(), (end_b - start_b).total_seconds())
    return max(shared, 0) / longer if longer > 0 else 0.0

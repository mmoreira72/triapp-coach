"""Parse every TrainingPeaks FIT export into activity-level and lap-level tables.

Usage: python parse_fit.py  (writes analysis/data/activities.csv, laps.csv, parse_errors.csv)
"""

import gzip, io, os, sys, glob, json, math, traceback
from datetime import timedelta
from concurrent.futures import ProcessPoolExecutor
import numpy as np, pandas as pd
from garmin_fit_sdk import Decoder, Stream

# TrainingPeaks Virtual writes developer fields of size 0; the SDK then indexes an empty tuple and drops every record of the
# file (IndexError). Read a zero-size field as "no value" instead.
_sdk_read_raw_value = Decoder._Decoder__read_raw_value
Decoder._Decoder__read_raw_value = lambda self, size, fmt: None if not size else _sdk_read_raw_value(self, size, fmt)

ROOT = os.environ.get("TRIAPP_ROOT") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(os.environ.get("TRIAPP_OUT", os.path.join(ROOT, "analysis")), "data")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from athletes import fit_dirs

ATHLETES = fit_dirs()  # every <First>_<Last>_Fit_Files folder under TRIAPP_ROOT
# best average power over these durations (s): dense enough for the power-duration model (pd_model.py), from 1 s to 3 h
POWER_WINDOWS = [
    1,
    2,
    3,
    5,
    10,
    15,
    20,
    30,
    45,
    60,
    90,
    120,
    180,
    240,
    300,
    360,
    480,
    600,
    720,
    900,
    1200,
    1500,
    1800,
    2400,
    3000,
    3600,
    4500,
    5400,
    7200,
    9000,
    10800,
]
SPEED_WINDOWS = [60, 300, 600, 1200, 3600, 5400, 7200]


def best_window(x, w):
    if x is None or len(x) < w:
        return None
    c = np.concatenate([[0.0], np.cumsum(x)])
    s = (c[w:] - c[:-w]) / w
    return float(s.max()) if len(s) else None


PAUSE_S = 5.0


def hist_json(bins, weights):
    """{bin: seconds} as compact JSON (bins with under 1 s dropped)."""
    if not len(bins):
        return None
    s = pd.Series(weights).groupby(bins.astype(int)).sum()
    return json.dumps({int(k): int(round(v)) for k, v in s.items() if v >= 1}, separators=(",", ":"))


def to_1hz(ts, vals):
    """ts: seconds (float) since start; vals: float with NaN. Returns a 1 Hz array of moving time: gaps longer than PAUSE_S
    (auto-pause, stops with the recording paused) are removed, shorter gaps (1 s smart recording, dropouts) are zero-filled,
    as TrainingPeaks does for mean-max curves."""
    if len(ts) < 2:
        return None
    dt = np.diff(ts, prepend=ts[0])
    dt = np.where(dt > PAUSE_S, 1.0, dt)  # collapse pauses to one second
    t = np.cumsum(dt)
    t = t - t[0]
    n = int(t[-1]) + 1
    if n <= 0 or n > 48 * 3600:
        return None
    out = np.zeros(n)
    ok = ~np.isnan(vals)
    out[t[ok].astype(int)] = vals[ok]
    return out


def g(d, *keys):
    for k in keys:
        if d is not None and k in d and d[k] is not None:
            v = d[k]
            if isinstance(v, float) and math.isnan(v):
                continue
            return v
    return None


def parse_one(args):
    athlete, path = args
    with open(path, "rb") as f:
        data = f.read()  # .fit.gz (TP export) or plain .fit (Intervals.icu); parse_fit_bytes ungzips
    return parse_fit_bytes(data, athlete, os.path.basename(path))


def parse_fit_bytes(data, athlete, name):
    """Decode one FIT file (bytes) into one activity row per session (multisport files give one row per leg) plus lap rows."""
    base = {"athlete": athlete, "file": name}
    rows, laps_out = [], []
    try:
        if data[:2] == b"\x1f\x8b":
            data = gzip.decompress(data)
        dec = Decoder(Stream.from_byte_array(bytearray(data)))
        msgs, errors = dec.read(
            convert_datetimes_to_dates=True,
            convert_types_to_strings=True,
            enable_crc_check=False,
            expand_sub_fields=True,
            expand_components=True,
            merge_heart_rates=True,
        )
        common = {"decode_errors": len(errors)}
        fid = (msgs.get("file_id_mesgs") or [{}])[0]
        common["manufacturer"] = g(fid, "manufacturer")
        pn = g(fid, "product_name")
        if isinstance(pn, list):
            pn = None
        common["device"] = g(fid, "garmin_product") or pn or (str(g(fid, "product")) if g(fid, "product") is not None else None)
        common["file_time_created"] = g(fid, "time_created")
        sportm = (msgs.get("sport_mesgs") or [{}])[0]
        wk = (msgs.get("workout_mesgs") or [{}])[0]
        zt = (msgs.get("zones_target_mesgs") or [{}])[0]
        up = (msgs.get("user_profile_mesgs") or [{}])[0]
        common["workout_name"] = g(wk, "wkt_name")
        common["workout_steps"] = len(msgs.get("workout_step_mesgs") or [])
        common["ftp_setting"] = g(zt, "functional_threshold_power")
        common["lthr_setting"] = g(zt, "threshold_heart_rate")
        common["maxhr_setting"] = g(zt, "max_heart_rate")
        common["weight_kg"] = g(up, "weight")
        common["resting_hr_setting"] = g(up, "resting_heart_rate")
        dev_fields = [g(d, "field_name") for d in (msgs.get("field_description_mesgs") or [])]
        common["developer_fields"] = "|".join([x for x in dev_fields if x])[:300]
        common["has_hrv"] = "hrv_mesgs" in msgs
        # records in time order, and only those with a timestamp: pool swims of older watches write one record per length
        # without one (the session and lap messages still describe the swim), and FORM goggles write some out of order
        recs = sorted((r for r in msgs.get("record_mesgs") or [] if r.get("timestamp") is not None), key=lambda r: r["timestamp"])
        lapsm = msgs.get("lap_mesgs") or []
        sessions = msgs.get("session_mesgs") or [None]
        multi = len(sessions) > 1
        for si, sess in enumerate(sessions):
            row = dict(base)
            row.update(common)
            row["session_index"] = si
            row["n_sessions"] = len(sessions)
            row["has_session"] = sess is not None
            s = sess or {}
            row["sport"] = g(s, "sport") or g(sportm, "sport")
            row["sub_sport"] = g(s, "sub_sport") or g(sportm, "sub_sport")
            row["sport_profile"] = g(s, "sport_profile_name") or g(sportm, "name")
            st = g(s, "start_time") or (recs[0]["timestamp"] if recs else None)
            et = None
            if st is not None and g(s, "total_elapsed_time") is not None:
                et = st + timedelta(seconds=float(g(s, "total_elapsed_time")) + 1)
            if et is None:
                et = g(s, "timestamp")
            # if the elapsed-time window looks wrong, close the window at the next session's start
            if multi and st is not None and si + 1 < len(sessions):
                nst = g(sessions[si + 1] or {}, "start_time")
                if nst is not None and (et is None or et > nst):
                    et = nst
            row["start_time"] = st
            row["elapsed_s"] = g(s, "total_elapsed_time")
            row["timer_s"] = g(s, "total_timer_time")
            row["distance_m"] = g(s, "total_distance")
            row["calories"] = g(s, "total_calories")
            row["avg_hr"] = g(s, "avg_heart_rate")
            row["max_hr"] = g(s, "max_heart_rate")
            row["avg_power"] = g(s, "avg_power")
            row["max_power"] = g(s, "max_power")
            row["np"] = g(s, "normalized_power")
            row["tss"] = g(s, "training_stress_score")
            row["if"] = g(s, "intensity_factor")
            row["threshold_power_session"] = g(s, "threshold_power")
            row["avg_cadence"] = g(s, "avg_running_cadence", "avg_cadence")
            row["max_cadence"] = g(s, "max_running_cadence", "max_cadence")
            row["ascent_m"] = g(s, "total_ascent")
            row["descent_m"] = g(s, "total_descent")
            row["avg_speed"] = g(s, "enhanced_avg_speed", "avg_speed")
            row["max_speed"] = g(s, "enhanced_max_speed", "max_speed")
            row["total_work_j"] = g(s, "total_work")
            row["aerobic_te"] = g(s, "total_training_effect")
            row["anaerobic_te"] = g(s, "total_anaerobic_training_effect")
            row["training_load_peak"] = g(s, "training_load_peak")
            row["pool_length_m"] = g(s, "pool_length")
            row["num_active_lengths"] = g(s, "num_active_lengths")
            row["total_strokes"] = g(s, "total_strokes")
            row["avg_stroke_distance"] = g(s, "avg_stroke_distance")
            row["avg_temperature"] = g(s, "avg_temperature")
            row["avg_vertical_oscillation"] = g(s, "avg_vertical_oscillation")
            row["avg_stance_time"] = g(s, "avg_stance_time")
            row["avg_step_length"] = g(s, "avg_step_length")
            row["left_right_balance"] = g(s, "left_right_balance")
            row["num_lengths_msgs"] = len(msgs.get("length_mesgs") or []) if not multi else None
            # records inside this session's window
            if multi and st is not None:
                srecs = [
                    r
                    for r in recs
                    if r.get("timestamp") is not None and r["timestamp"] >= st and (et is None or r["timestamp"] <= et)
                ]
            else:
                srecs = recs
            row["n_records"] = len(srecs)
            if srecs:
                t0 = srecs[0]["timestamp"]
                ts = np.array([(r["timestamp"] - t0).total_seconds() for r in srecs], dtype=float)

                def col(*names):
                    return np.array([float(v) if (v := g(r, *names)) is not None else np.nan for r in srecs])

                hr = col("heart_rate")
                pw = col("power")
                sp = col("enhanced_speed", "speed")
                cad = col("cadence")
                lat = col("position_lat")
                alt = col("enhanced_altitude", "altitude")
                row["has_hr"] = bool(np.isfinite(hr).mean() > 0.5)
                row["has_power"] = bool(np.isfinite(pw).mean() > 0.5)
                row["has_speed"] = bool(np.isfinite(sp).mean() > 0.5)
                row["has_cadence"] = bool(np.isfinite(cad).mean() > 0.5)
                row["has_gps"] = bool(np.isfinite(lat).mean() > 0.5)
                row["has_altitude"] = bool(np.isfinite(alt).mean() > 0.5)
                row["rec_avg_hr"] = float(np.nanmean(hr)) if row["has_hr"] else None
                row["rec_avg_power"] = float(np.nanmean(pw)) if row["has_power"] else None
                # share of moving time (speed > 2.5 m/s) with power > 0: a dead power meter keeps writing 0 W
                mv = np.isfinite(sp) & (sp > 2.5)
                row["power_cov"] = float((np.nan_to_num(pw[mv]) > 0).mean()) if row["has_power"] and mv.sum() >= 300 else None
                row["rec_elapsed_s"] = float(ts[-1])
                if row["elapsed_s"] is None:
                    row["elapsed_s"] = float(ts[-1])
                if row["timer_s"] is None:
                    row["timer_s"] = float(np.isfinite(ts).sum())
                if row["distance_m"] is None:
                    dist = col("distance")
                    row["distance_m"] = float(np.nanmax(dist) - np.nanmin(dist)) if np.isfinite(dist).any() else None
                if row["avg_hr"] is None and row["has_hr"]:
                    row["avg_hr"] = row["rec_avg_hr"]
                    row["max_hr"] = float(np.nanmax(hr))
                if row["avg_power"] is None and row["has_power"]:
                    row["avg_power"] = row["rec_avg_power"]
                    row["max_power"] = float(np.nanmax(pw))
                if row["has_power"]:
                    p1 = to_1hz(ts, pw)
                    if p1 is not None:
                        for w in POWER_WINDOWS:
                            row[f"p_best_{w}s"] = best_window(p1, w)
                        if row["np"] is None and len(p1) >= 30:
                            k = np.convolve(p1, np.ones(30) / 30, mode="valid")
                            row["np"] = float(np.mean(k**4) ** 0.25)
                # time-in-zone raw material: seconds at each heart rate (2 bpm bins) and power (10 W bins), moving time only
                dt = np.clip(np.diff(ts, append=ts[-1]), 0, PAUSE_S)
                if row["has_hr"]:
                    ok = np.isfinite(hr) & (hr > 40) & (hr < 230)
                    row["hr_hist"] = hist_json(hr[ok] // 2 * 2, dt[ok])
                if row["has_power"]:
                    ok = np.isfinite(pw) & (pw >= 0) & (pw < 2000)
                    row["pw_hist"] = hist_json(pw[ok] // 10 * 10, dt[ok])
                if row["has_speed"]:
                    s1 = to_1hz(ts, sp)
                    if s1 is not None:
                        for w in SPEED_WINDOWS:
                            row[f"v_best_{w}s"] = best_window(s1, w)
            # laps inside this session's window
            slaps = (
                lapsm
                if not multi
                else [
                    lp
                    for lp in lapsm
                    if g(lp, "start_time") is not None
                    and st is not None
                    and st <= g(lp, "start_time")
                    and (et is None or g(lp, "start_time") <= et)
                ]
            )
            row["num_laps"] = g(s, "num_laps") or len(slaps)
            for i, lp in enumerate(slaps):
                laps_out.append(
                    {
                        "athlete": athlete,
                        "file": name,
                        "session_index": si,
                        "lap": i,
                        "start_time": g(lp, "start_time"),
                        "timer_s": g(lp, "total_timer_time"),
                        "elapsed_s": g(lp, "total_elapsed_time"),
                        "distance_m": g(lp, "total_distance"),
                        "avg_hr": g(lp, "avg_heart_rate"),
                        "max_hr": g(lp, "max_heart_rate"),
                        "avg_power": g(lp, "avg_power"),
                        "max_power": g(lp, "max_power"),
                        "np": g(lp, "normalized_power"),
                        "avg_speed": g(lp, "enhanced_avg_speed", "avg_speed"),
                        "avg_cadence": g(lp, "avg_running_cadence", "avg_cadence"),
                        "intensity": g(lp, "intensity"),
                        "lap_trigger": g(lp, "lap_trigger"),
                        "wkt_step_index": g(lp, "wkt_step_index"),
                        "num_lengths": g(lp, "num_active_lengths", "num_lengths"),
                        "total_strokes": g(lp, "total_strokes"),
                        "ascent_m": g(lp, "total_ascent"),
                        "sport": g(lp, "sport"),
                        "sub_sport": g(lp, "sub_sport"),
                    }
                )
            rows.append(row)
    except Exception as e:
        rows = [dict(base, error=repr(e)[:300])]
    return rows, laps_out


def main():
    jobs = []
    for a, d in ATHLETES.items():
        for p in sorted(f for f in glob.glob(os.path.join(ROOT, d, "*")) if f.lower().endswith((".fit", ".fit.gz", ".gz"))):
            jobs.append((a, p))
    print("files:", len(jobs), flush=True)
    rows, laps = [], []
    with ProcessPoolExecutor(max_workers=8) as ex:
        for i, (r, l) in enumerate(ex.map(parse_one, jobs, chunksize=8)):
            rows.extend(r)
            laps.extend(l)
            if i % 200 == 0:
                print("..", i, flush=True)
    df = pd.DataFrame(rows)
    df["start_time"] = pd.to_datetime(df["start_time"], utc=True, errors="coerce")
    df = df.sort_values(["athlete", "start_time"]).reset_index(drop=True)
    df.to_csv(os.path.join(OUT, "activities.csv"), index=False)
    pd.DataFrame(laps).to_csv(os.path.join(OUT, "laps.csv"), index=False)
    print("written", df.shape, len(laps))
    print(df[["athlete", "error"]].dropna().to_string()[:3000] if "error" in df else "no errors")


if __name__ == "__main__":
    main()

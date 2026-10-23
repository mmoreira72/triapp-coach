"""Index every WKO4 workout file and parse the device file embedded in it.

Writes analysis/data/wko_index.csv (one row per WKO4 file: coach plan, thresholds at the time,
TP summary, plus the FIT-derived metrics when an embedded FIT was parsed) and wko_laps.csv.
"""

import os, sys, glob, gzip
from concurrent.futures import ProcessPoolExecutor
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from wko_decode import load, top_level
from parse_fit import parse_fit_bytes

ROOT = os.environ.get("TRIAPP_ROOT") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(os.environ.get("TRIAPP_OUT", os.path.join(ROOT, "analysis")), "data")
from athletes import wko_dirs

WKO_DIRS = wko_dirs()  # every <First>_<Last>_wko folder under TRIAPP_ROOT
FIELDS = {
    4001: "f4001",
    4020: "title",
    4003: "description",
    4005: "sport_tp",
    4044: "workout_time_local",
    4067: "activity_type_device",
    4017: "device_brand",
    4018: "device_product",
    4050: "embedded_format",
    4035: "planned_tss",
    4036: "planned_duration_s",
    4037: "planned_distance_km",
    4054: "planned_work_j",
    4038: "tp_4038",
    4039: "tp_duration_s",
    4040: "tp_distance_km_round",
    4053: "tp_work_j",
    4057: "tp_4057",
    4056: "tp_duration_s2",
    4055: "tp_distance_km",
    4058: "tp_work_j2",
    4009: "weight_kg_tp",
    4061: "height_m",
    4010: "ftp_tp",
    4012: "thr_hr_tp",
    4011: "max_hr_tp",
    4060: "f4060",
    4063: "threshold_pace_tp",
    4033: "code_4033",
    4046: "code_4046",
    4069: "f4069",
    4070: "f4070",
    4071: "f4071",
    4072: "f4072",
    4059: "f4059",
    4024: "f4024",
    4062: "f4062",
    4068: "device_file_id",
    4030: "title2",
    4031: "description2",
    4027: "planned2_tss",
    4028: "planned2_duration_s",
    4029: "planned2_distance_km",
    4048: "planned2_work_j",
    4045: "f4045_str",
    4034: "f4034_str",
}

WANT = {
    "Swim": {"swimming"},
    "Road Bike": {"cycling"},
    "Indoor Bike": {"cycling"},
    "Mountain Bike": {"cycling"},
    "Run": {"running"},
}


def pick_session(frows, row):
    """A multisport FIT (race) is embedded whole in each leg's TP entry: keep the session that matches the entry's sport
    and duration."""
    if len(frows) == 1:
        return frows[0]
    want = WANT.get(row.get("sport_tp"))
    cands = [f for f in frows if f.get("sport") in want] if want else frows
    cands = cands or frows
    dur = row.get("tp_duration_s")
    if dur:
        cands = sorted(cands, key=lambda f: abs((f.get("timer_s") or f.get("elapsed_s") or 0) - dur))
    return cands[0]


def index_one(args):
    athlete, path = args
    row = {
        "athlete": athlete,
        "wko_file": os.path.basename(path),
        "wko_year": os.path.basename(os.path.dirname(path)),
        "wko_size": os.path.getsize(path),
    }
    laps = []
    try:
        b, start = load(path)
        blob = None
        for fid, wt, v in top_level(b, start):
            if fid == 4049 and wt == 3:
                blob = v if isinstance(v, (bytes, bytearray)) else v.encode("utf-8", "surrogateescape")
                continue
            name = FIELDS.get(fid)
            if name and name not in row:
                row[name] = v
        row["embedded_bytes"] = len(blob) if blob else 0
        fmt = (row.get("embedded_format") or "").lower()
        if blob:
            if fmt == "fit" or blob[8:12] == b".FIT" or blob[:2] == b"\x1f\x8b":
                frows, laps = parse_fit_bytes(bytes(blob), athlete, row["wko_file"])
                frow = pick_session(frows, row)
                laps = [l for l in laps if l.get("session_index") == frow.get("session_index")]
                frow.pop("athlete", None)
                frow.pop("file", None)
                row.update({f"fit_{k}" if k in ("error",) else k: v for k, v in frow.items()})
                row["parsed"] = "fit"
            else:
                row["parsed"] = f"skipped:{fmt or blob[:6]!r}"
        else:
            row["parsed"] = "no-embedded-file"
    except Exception as e:
        row["index_error"] = repr(e)[:300]
    return row, laps


def main():
    jobs = [(a, p) for a, d in WKO_DIRS.items() for p in sorted(glob.glob(os.path.join(ROOT, d, "20*", "*.wko4")))]
    print("wko4 files:", len(jobs), flush=True)
    rows, laps = [], []
    with ProcessPoolExecutor(max_workers=8) as ex:
        for i, (r, l) in enumerate(ex.map(index_one, jobs, chunksize=16)):
            rows.append(r)
            laps.extend(l)
            if i % 1000 == 0:
                print("..", i, flush=True)
    df = pd.DataFrame(rows)
    if "start_time" in df:
        df["start_time"] = pd.to_datetime(df["start_time"], utc=True, errors="coerce")
    df.to_csv(os.path.join(OUT, "wko_index.csv"), index=False)
    pd.DataFrame(laps).to_csv(os.path.join(OUT, "wko_laps.csv"), index=False)
    print("written", df.shape, "laps", len(laps))
    print(df.parsed.value_counts(dropna=False).to_string())
    print(df.embedded_format.value_counts(dropna=False).to_string())
    if "index_error" in df:
        print("index errors:", df.index_error.notna().sum(), df.index_error.dropna().head(5).tolist())
    if "fit_error" in df:
        print("fit errors:", df.fit_error.notna().sum(), df.fit_error.dropna().head(5).tolist())


if __name__ == "__main__":
    main()

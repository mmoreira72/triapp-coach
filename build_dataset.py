"""Unify the WKO history and the FIT files of every athlete into activities_all.csv: one row per calendar entry,
with a TSS for every completed session (TrainingPeaks' own when present, otherwise from power, heart rate or duration).

Steps, in order (see main):
  1. load the WKO index and the FIT sessions
  2. drop FIT sessions that are the same workout as a WKO entry or another FIT; drop WKO entries without their file
     when a FIT has the session; write the fate of every source item to ingest_ledger.csv (read by reconcile.py)
  3. one table; races recorded as separate files are marked as one multisport event
  4. thresholds in use on each day: bike FTP, run LTHR, bike LTHR; Intervals.icu settings win from the day they appear
  5. TSS estimate and corrections; flags for dead power meters and implausible power
"""

import os, ast, re, json
import numpy as np, pandas as pd

import matching
from athletes import discover, overrides, override

ROOT = os.environ.get("TRIAPP_ROOT") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
D = os.path.join(os.environ.get("TRIAPP_OUT", os.path.join(ROOT, "analysis")), "data")
TZ = "Europe/Lisbon"  # FIT timestamps are UTC; WKO gives local time. The squad trains on WET/WEST.
SPORT_TP = {
    "Swim": "swim",
    "Road Bike": "bike",
    "Indoor Bike": "bike",
    "Mountain Bike": "bike",
    "Run": "run",
    "Strength": "strength",
    "Day Off": "dayoff",
    "Walk": "other",
    "Other": "other",
    "Row": "other",
}
SPORT_FIT = {
    "swimming": "swim",
    "cycling": "bike",
    "running": "run",
    "training": "strength",
    "fitness_equipment": "strength",
    "hiking": "other",
    "walking": "other",
    "meditation": "other",
    "rowing": "other",
    "generic": "other",
    "stand_up_paddleboarding": "other",
}
DEFAULT_TSS_PER_H = {"swim": 50, "bike": 50, "run": 60, "strength": 25, "other": 25}
DEFAULT_LTHR = 165
DEFAULT_RESTING_HR = 60
SAME_START_MIN = 3  # FIT vs WKO entry with its file: both carry the device clock
PLANNED_START_MIN = 10  # FIT vs WKO entry without its file: TP's time of day is less exact
CHAIN_GAP_S = 20 * 60  # legs of a race recorded as separate files start within 20 min of the previous one's end
DEAD_POWER_COVERAGE = 0.4  # power > 0 in under 40% of the moving time = the power meter died
MAX_PLAUSIBLE_IF = 1.15  # a TP TSS implying more than this for 20+ min is a threshold-setup error in TP
KEEP = [
    "athlete",
    "source",
    "file",
    "t_local",
    "sport_n",
    "sport",
    "sub_sport",
    "sport_tp",
    "completed",
    "title_n",
    "plan_desc",
    "planned_tss",
    "planned_duration_s",
    "planned_distance_km",
    "duration_h",
    "distance_km",
    "tss_tp",
    "tss",
    "if",
    "avg_hr",
    "max_hr",
    "avg_power",
    "max_power",
    "np",
    "threshold_power_session",
    "ftp_setting",
    "lthr_setting",
    "weight_kg",
    "weight_kg_tp",
    "avg_cadence",
    "ascent_m",
    "avg_speed",
    "total_work_j",
    "pool_length_m",
    "num_active_lengths",
    "has_hr",
    "has_power",
    "has_gps",
    "n_records",
    "device_brand",
    "device",
    "workout_steps",
    "aerobic_te",
    "calories",
    "session_index",
    "n_sessions",
    "power_cov",
    "hr_hist",
    "pw_hist",
]


def clean_title(s):
    """Workout names arrive as a string or as the repr of a list of strings."""
    if not isinstance(s, str):
        return None
    if s.startswith("["):
        try:
            v = ast.literal_eval(s)
            if isinstance(v, list) and v:
                return str(v[0])
        except Exception:
            m = re.match(r"^\[\"?'?(.+?)'?\"?,", s)
            return m.group(1) if m else s
    return s


def fit_local_time(a):
    return a.start_time.dt.tz_convert(TZ).dt.tz_localize(None)


# ---------------------------------------------------------------- 1. load
def wko_done(w):
    """Done = any trace of execution in TP. Field 4046 == 2 only says "a recorded file is attached", it is not TP's completed
    flag: sessions completed by hand (strength, swims, rides marked done) and virtual rides synced without their file carry
    an actual duration (4039) or TP TSS (4038) and 4046 == 0."""
    traces = (
        (w.code_4046 == 2)
        | (w.tp_duration_s.fillna(0) > 0)
        | (w.tp_4038.fillna(0) > 0)
        | (w.has_file & (w.timer_s.fillna(0) > 0))
    )
    return traces & ~w.sport_tp.astype(str).eq("Day Off")


def load_wko():
    w = pd.read_csv(os.path.join(D, "wko_index.csv"), low_memory=False)
    w["source"] = "wko"
    w["t_local"] = pd.to_datetime(w.workout_time_local, errors="coerce")
    w["sport_n"] = w.sport_tp.map(SPORT_TP).fillna(w.sport.map(SPORT_FIT)).fillna("other")
    w["has_file"] = w.parsed.astype(str).eq("fit")  # the embedded FIT was read: HR, power and laps are available
    w["completed"] = wko_done(w)
    w["duration_h"] = w.tp_duration_s.fillna(w.timer_s).fillna(w.elapsed_s) / 3600
    w["distance_km"] = w.tp_distance_km.fillna(w.distance_m / 1000)
    w["tss_tp"] = w.tp_4038.where(w.tp_4038 > 0)
    w["title_n"] = w.title.fillna(w.workout_name.map(clean_title))
    w["plan_desc"] = w.description
    w["file"] = w.wko_file
    return w


def load_fit():
    return pd.read_csv(os.path.join(D, "activities.csv"), parse_dates=["start_time"], low_memory=False)


# ---------------------------------------------------------------- 2. one copy of each session
def drop_duplicate_fits(a_all, w):
    """FIT sessions to add to the WKO history, and the fate of every FIT row that is not added ({row index: reason})."""
    fate = {}
    a = a_all[(a_all.n_records > 0) | (a_all.has_session.fillna(False).astype(bool) & (a_all.timer_s > 0))].copy()
    fate.update({i: "sem dados no ficheiro" for i in a_all.index.difference(a.index)})
    t_fit = fit_local_time(a)
    with_file = w[w.completed & w.has_file & w.t_local.notna()]  # only WKO entries with data beat a FIT

    # same start, to the minute
    wko_starts = with_file[["athlete", "t_local"]].sort_values("t_local").rename(columns={"t_local": "t_wko"})
    fit_starts = (
        pd.DataFrame({"i": a.index, "athlete": a.athlete.values, "t_local": t_fit.values})
        .dropna(subset=["t_local"])
        .sort_values("t_local")
    )
    m = pd.merge_asof(
        fit_starts,
        wko_starts,
        left_on="t_local",
        right_on="t_wko",
        by="athlete",
        direction="nearest",
        tolerance=pd.Timedelta(minutes=SAME_START_MIN),
    )
    dup = set(m.loc[m.t_wko.notna(), "i"])
    fate.update({i: "duplicado do WKO (mesma hora)" for i in dup})

    # recorded in another time zone (WKO keeps the local clock): same sport, day ± 1 and duration
    cands = with_file.assign(
        dur=with_file.timer_s.fillna(with_file.tp_duration_s),
        sp=with_file.sport_tp.map(SPORT_TP).fillna(with_file.sport.map(SPORT_FIT)),
    )
    by_athlete_sport = {k: g for k, g in cands.groupby(["athlete", "sp"])}
    for i, r in a.loc[~a.index.isin(dup)].iterrows():
        cand = by_athlete_sport.get((r.athlete, SPORT_FIT.get(r.sport, "other")))
        if cand is None or pd.isna(r.timer_s) or pd.isna(t_fit.loc[i]):
            continue
        if matching.same_day_duration(cand.t_local, cand.dur, t_fit.loc[i], float(r.timer_s)).any():
            dup.add(i)
            fate[i] = "duplicado do WKO (mesmo dia, desporto e duração)"

    # the same recording from two FIT sources (TP export and Intervals.icu); the Intervals copy stays, it has the activity name
    rest = a.loc[~a.index.isin(dup)]
    rest = rest.assign(_intervals=rest.file.astype(str).str.startswith("intervals-")).sort_values("_intervals", ascending=False)
    twice = set(rest.index[rest.duplicated(["athlete", "start_time", "sport", "session_index"], keep="first")])
    fate.update({i: "duplicado de outro FIT (TP e Intervals)" for i in twice})
    dup |= twice

    print(
        f"FIT export: {len(a)} sessions, {len(dup)} already in WKO, {len(a) - len(dup)} added:",
        a.loc[~a.index.isin(dup)].groupby("athlete").size().to_dict(),
    )
    return a.loc[~a.index.isin(dup)].copy(), fate


def fileless_wko_with_fit(w, a):
    """Index of the WKO entries done without their file for which a FIT of the same session exists (same athlete and sport,
    start within 10 min, or same day ± 1 with the same duration). The FIT wins: it carries heart rate, power and laps."""
    fileless = w[w.completed & ~w.has_file & w.t_local.notna()]
    replaced = set()
    if len(fileless):
        fit = a.assign(_t=fit_local_time(a), _sp=a.sport.map(SPORT_FIT).fillna("other"))
        sport = fileless.sport_tp.map(SPORT_TP).fillna("other")
        for i, r in fileless.iterrows():
            c = fit[(fit.athlete == r.athlete) & (fit._sp == sport.loc[i])]
            if not len(c):
                continue
            same = matching.near_start(c._t, r.t_local, PLANNED_START_MIN) | matching.same_day_duration(
                c._t, c.timer_s, r.t_local, r.tp_duration_s
            )
            if same.any():
                replaced.add(i)
    print(
        f"WKO sessions done without their file: {len(fileless)}, {len(replaced)} replaced by the matching FIT, "
        f"{len(fileless) - len(replaced)} kept with TP duration and TSS"
    )
    return list(replaced)


def write_ledger(w, a_all, fit_fate, wko_replaced, doubles=frozenset()):
    """ingest_ledger.csv: one row per source item with exactly one fate. doubles: (file, session_index) of the second
    recordings dropped from the unified table (session_index None for WKO entries)."""
    DOUBLE = "duplicado: outra gravação da mesma sessão"
    for i in a_all.index:
        if (a_all.file[i], a_all.session_index[i]) in doubles:
            fit_fate[i] = DOUBLE
    fit = pd.DataFrame(
        {
            "athlete": a_all.athlete,
            "origin": np.where(a_all.file.astype(str).str.startswith("intervals-"), "intervals", "fit"),
            "file": a_all.file,
            "session_index": a_all.session_index,
            "t_local": fit_local_time(a_all),
            "sport": a_all.sport.map(SPORT_FIT).fillna("other"),
            "fate": pd.Series(a_all.index.map(fit_fate), index=a_all.index).fillna("na app"),
        }
    )
    wko_fate = np.select(
        [
            w.index.isin(wko_replaced),
            w.wko_file.map(lambda f: (f, None) in doubles),
            w.sport_tp.astype(str).eq("Day Off"),
            w.completed & w.t_local.isna(),
            w.completed & w.has_file,
            w.completed,
        ],
        ["substituído pelo FIT da mesma sessão", DOUBLE, "folga", "na app (sem data)", "na app", "na app (feito, sem ficheiro)"],
        default="planeado, não feito",
    )
    wko = pd.DataFrame(
        {
            "athlete": w.athlete,
            "origin": "wko",
            "file": w.wko_file,
            "session_index": w.session_index if "session_index" in w else np.nan,
            "t_local": w.t_local,
            "sport": w.sport_n,
            "fate": wko_fate,
        }
    )
    pd.concat([wko, fit], ignore_index=True).to_csv(os.path.join(D, "ingest_ledger.csv"), index=False)


def recording_quality(r):
    """Sort key for the copy to keep among recordings of one session: power and heart rate together, then power, heart rate,
    per-second data, then length."""
    power, hr = bool(r["np"] > 0 or r.avg_power > 0), bool(r.avg_hr > 0)
    return (
        power and hr,
        power,
        hr,
        bool(r.n_records > 0),
        float(r.n_records) if r.n_records == r.n_records else 0.0,
        float(r.duration_h or 0),
    )


PLAN_COLS = ["title_n", "plan_desc", "planned_tss", "planned_duration_s", "planned_distance_km"]
HR_COLS = ["avg_hr", "max_hr", "has_hr", "hr_hist"]


def carry_plan(df, dropped, kept):
    """What the dropped copy knew and the kept one does not. TP pairs the coach's plan with one of the recordings, not always
    the one that stays; and a race leg recorded by the watch's multisport file and by the bike computer is the same leg: the
    kept copy must stay part of the race."""
    has_plan = lambda i: df.planned_tss[i] > 0 or df.planned_duration_s[i] > 0
    if has_plan(dropped) and not has_plan(kept):
        df.loc[kept, PLAN_COLS] = df.loc[dropped, PLAN_COLS].values
    if df.multisport[dropped] and not df.multisport[kept]:
        df.loc[kept, ["multisport", "n_sessions"]] = [True, df.n_sessions[dropped]]
    # Zwift records the power and the watch the heart rate: the kept copy (power) takes the heart rate of the dropped one
    if not (df.avg_hr[kept] > 0) and df.avg_hr[dropped] > 0:
        cols = [c for c in HR_COLS if c in df]
        df.loc[kept, cols] = df.loc[dropped, cols].values


def double_recordings(df):
    """Index of the rows that are a second recording of the same session: two devices on at once (bike computer and watch,
    Zwift and watch), both uploaded to TP or Intervals. Same athlete and sport, windows sharing at least 75% of the longer
    one. The most complete copy stays (recording_quality)."""
    cand = df[df.completed & df.sport_n.isin(["bike", "run", "swim"]) & df.t_local.notna() & (df.duration_h > 1 / 6)]
    cand = cand.assign(_end=cand.t_local + pd.to_timedelta(cand.duration_h, unit="h"))
    drop = set()
    for _, day in cand.groupby(["athlete", "sport_n", cand.t_local.dt.date]):
        if len(day) < 2:
            continue
        kept = []
        for i in sorted(day.index, key=lambda i: recording_quality(day.loc[i]), reverse=True):
            r = day.loc[i]
            same = [
                k
                for k in kept
                if matching.overlap_share(r.t_local, r._end, day.t_local[k], day._end[k]) >= matching.DOUBLE_RECORDING_SHARE
            ]
            if same:
                drop.add(i)
                carry_plan(df, i, same[0])
            else:
                kept.append(i)
    print(f"double recordings: {len(drop)} second copies dropped", df.loc[list(drop)].groupby("athlete").size().to_dict())
    return drop


# ---------------------------------------------------------------- 3. one table
def shape_fit(a):
    """FIT sessions in the columns of the unified table."""
    a["source"] = "fit"
    a["t_local"] = fit_local_time(a)
    a["sport_n"] = a.sport.map(SPORT_FIT).fillna("other")
    a["completed"] = True
    a["duration_h"] = a.timer_s.fillna(a.elapsed_s) / 3600
    a["distance_km"] = a.distance_m / 1000
    a["tss_tp"] = np.nan
    a["title_n"] = a.workout_name.map(clean_title)
    try:  # Intervals.icu activity names (intervals_sync.py): FIT files carry no title
        with open(os.path.join(D, "intervals_activities.json")) as f:
            a["title_n"] = a.file.map(json.load(f)).fillna(a.title_n)
    except Exception:
        pass
    for c in ["plan_desc", "planned_tss", "planned_duration_s", "planned_distance_km", "weight_kg_tp", "sport_tp"]:
        a[c] = np.nan
    a["device_brand"] = a.manufacturer
    return a


def unify(w, a):
    keep = KEEP + [c for c in w.columns if c.startswith("p_best_") or c.startswith("v_best_")]
    for c in keep:
        if c not in w:
            w[c] = np.nan
        if c not in a:
            a[c] = np.nan
    df = pd.concat([w[keep], a[keep]], ignore_index=True).rename(columns={"tss": "tss_device", "if": "if_device"})
    df["date"] = df.t_local.dt.normalize()
    df["multisport"] = df.n_sessions.fillna(1) >= 2
    return df


def mark_chained_races(df):
    """Races recorded as separate files (bike computer + watch): legs of different sports on the same day, each starting within
    20 min of the previous one's end, count as one multisport event when they include bike + run (bike >= 15 km) or swim + run."""
    df["chained"] = False
    legs = df[df.sport_n.isin(["swim", "bike", "run"]) & ~df.multisport & df.t_local.notna() & (df.duration_h > 0.1)].sort_values(
        ["athlete", "t_local"]
    )
    for _, day in legs.groupby(["athlete", "date"]):
        if day.sport_n.nunique() < 2:
            continue
        groups, cur, prev_end = [], [], None
        for i, r in day.iterrows():
            gap = (r.t_local - prev_end).total_seconds() if prev_end is not None else None
            if cur and not (-600 <= gap <= CHAIN_GAP_S):
                groups.append(cur)
                cur = []
            cur.append(i)
            prev_end = r.t_local + pd.Timedelta(hours=r.duration_h)
        groups.append(cur)
        for grp in groups:
            legs_ = df.loc[grp]
            sports = set(legs_.sport_n)
            bike_km = legs_.loc[legs_.sport_n == "bike", "distance_km"].sum()
            if len(sports) == len(legs_) and "run" in sports and (("bike" in sports and bike_km >= 15) or "swim" in sports):
                df.loc[grp, ["multisport", "chained"]] = True


# ---------------------------------------------------------------- 4. thresholds
def load_intervals_thresholds():
    """{athlete: [{date, settings}]}: dated history written by intervals_sync.py."""
    try:
        with open(os.path.join(D, "intervals_thresholds.json")) as f:
            return json.load(f)
    except Exception:
        return {}


def implied_ftp_by_quarter(df):
    """Bike FTP backed out of TP's TSS (FTP = NP * sqrt(h * 100 / TSS)): quarterly median, filled forward and back."""
    bike = df[(df.sport_n == "bike") & (df.tss_tp > 5) & (df["np"] > 0) & (df.duration_h > 0.25)].copy()
    bike["ftp_implied"] = bike["np"] * np.sqrt(bike.duration_h * 100 / bike.tss_tp)
    by_quarter = bike.groupby(["athlete", "quarter"]).ftp_implied.median().round(0)
    quarters = sorted(df.quarter.dropna().unique())
    table = {
        ath: by_quarter.loc[ath].reindex(quarters).ffill().bfill().to_dict()
        for ath in by_quarter.index.get_level_values(0).unique()
    }
    print("== implied bike FTP (TP) by quarter, last 8 ==")
    print(pd.DataFrame(table).tail(8).to_string())
    return table


def ftp_on(row, table):
    """FTP in use on the day of the session: TP's implied value, else the athlete's fallback in athlete_overrides.json."""
    v = table.get(row.athlete, {}).get(row.quarter, np.nan)
    fallback = override(row.athlete, "ftp_fallback")
    if v == v or not fallback:
        return v
    ftp = fallback[0]["ftp"]
    for step in fallback:
        if row.t_local >= pd.Timestamp(step["from"]):
            ftp = step["ftp"]
    return float(ftp)


def drop_dead_power(df):
    """Power meter that died mid-ride: drop the power of the session (NP, averages, mean-max curve) so TSS falls back to heart
    rate and the curves and efficiency trends are not dragged down by zeros."""
    df["power_dropout"] = (df.sport_n == "bike") & (df.power_cov < DEAD_POWER_COVERAGE)
    power_cols = ["np", "avg_power", "max_power", "pw_hist"] + [c for c in df.columns if c.startswith("p_best_")]
    df.loc[df.power_dropout, power_cols] = np.nan
    df.loc[df.power_dropout, "has_power"] = False
    print(
        "power dropouts (bike, power > 0 in < 40% of moving time):",
        int(df.power_dropout.sum()),
        df.loc[df.power_dropout, ["athlete", "t_local", "power_cov"]].tail(6).to_string(),
    )


def run_lthr(df):
    """{athlete: run LTHR}. athlete_overrides.json first; else the median threshold-HR setting of the athlete's devices; else
    backed out of TP's run TSS on runs without power (LTHR = avg HR * sqrt(h * 100 / TSS), last 2 years); else 165 bpm."""
    lthr = {a: v["lthr_run"] for a, v in overrides().get("athletes", {}).items() if v.get("lthr_run")}
    for ath in df.athlete.unique():
        if ath in lthr:
            continue
        g = df[df.athlete == ath]
        settings = g.lthr_setting[(g.lthr_setting > 120) & (g.lthr_setting < 210)]
        if len(settings) >= 5:
            lthr[ath], src = int(settings.median()), "device setting"
        else:
            r = g[
                (g.sport_n == "run")
                & (g.tss_tp > 10)
                & (g.avg_hr > 90)
                & (g["np"].isna())
                & (g.duration_h > 0.4)
                & (g.t_local >= g.t_local.max() - pd.Timedelta(days=730))
            ]
            implied = (r.avg_hr * np.sqrt(r.duration_h * 100 / r.tss_tp)).clip(140, 200)
            lthr[ath], src = (
                (int(implied.median()), f"backed out of {len(implied)} TP run TSS values")
                if len(implied) >= 10
                else (DEFAULT_LTHR, "default")
            )
        print(f"LTHR {ath}: {lthr[ath]} bpm ({src})")
    return lthr


def bike_lthr(df, lthr_run):
    """{athlete: bike LTHR}. Heart rate on the bike runs 5-15 bpm below running at the same effort, so the run LTHR puts every
    bike effort a zone too low. Estimated from steady rides with power and heart rate (VI <= 1.08, last 2 years, 40+ min): fit
    of average HR on IF, read at IF 1.0, kept between run LTHR - 25 and run LTHR. Too few rides near threshold: run LTHR - 10."""
    out = {}
    for ath in df.athlete.unique():
        run_l = lthr_run.get(ath)
        r = df[
            (df.athlete == ath)
            & (df.sport_n == "bike")
            & (df["np"] > 0)
            & (df.ftp_used > 0)
            & (df.avg_hr > 90)
            & (df.duration_h >= 0.66)
            & (df["np"] / df.avg_power <= 1.08)
            & (df.t_local >= df.t_local.max() - pd.Timedelta(days=730))
        ]
        if run_l and len(r) >= 60 and (r["np"] / r.ftp_used).max() >= 0.8:
            slope, intercept = np.polyfit((r["np"] / r.ftp_used).values, r.avg_hr.values, 1)
            out[ath] = int(round(min(max(intercept + slope, run_l - 25), run_l)))
            print(
                f"bike LTHR {ath}: {out[ath]} bpm (HR = {intercept:.0f} + {slope:.0f} x IF over {len(r)} rides; run LTHR {run_l})"
            )
        elif run_l:
            out[ath] = run_l - 10
            print(f"bike LTHR {ath}: {out[ath]} bpm (run LTHR - 10: too few steady rides near threshold to estimate)")
    return out


def set_thresholds(df, intervals):
    """ftp_used and lthr_used for every row. Thresholds set in Intervals.icu win from the date they were first seen; a bike
    LTHR there counts only when it differs from the run one (equal means "not set")."""
    table = implied_ftp_by_quarter(df)
    df["ftp_used"] = df.apply(ftp_on, axis=1, table=table)
    drop_dead_power(df)
    lthr_run = run_lthr(df)
    df["lthr_used"] = df.athlete.map(lthr_run)
    for ath, history in intervals.items():
        for entry in history:
            s = entry.get("settings", {})
            since = (df.athlete == ath) & (df.t_local >= pd.Timestamp(entry["date"]))
            ftp = (s.get("bike") or {}).get("ftp")
            lthr = (s.get("run") or {}).get("lthr") or (s.get("bike") or {}).get("lthr")
            if ftp:
                df.loc[since, "ftp_used"] = float(ftp)
            if lthr:
                df.loc[since, "lthr_used"] = float(lthr)
            print(f"Intervals thresholds {ath} from {entry['date']}: FTP {ftp} W, LTHR {lthr} bpm")
    is_bike = df.sport_n == "bike"
    df.loc[is_bike, "lthr_used"] = df.loc[is_bike, "athlete"].map(bike_lthr(df, lthr_run)).astype(float)
    for ath, history in intervals.items():
        for entry in history:
            s = entry.get("settings", {})
            bike, run = (s.get("bike") or {}).get("lthr"), (s.get("run") or {}).get("lthr")
            if bike and bike != run:
                df.loc[is_bike & (df.athlete == ath) & (df.t_local >= pd.Timestamp(entry["date"])), "lthr_used"] = float(bike)
                print(f"Intervals bike LTHR {ath} from {entry['date']}: {bike} bpm")
    # set in the app (a projection applied or a test): wins over every other source from its date on
    try:
        import userdata

        entries = userdata.thresholds()
    except Exception as e:
        entries = []
        print(f"app thresholds: {type(e).__name__}")
    for e in entries:
        since = (df.athlete == e["athlete"]) & (df.t_local >= pd.Timestamp(e["from_date"]))
        if e.get("ftp"):
            df.loc[since, "ftp_used"] = float(e["ftp"])
        if e.get("lthr_run"):
            df.loc[since & ~is_bike, "lthr_used"] = float(e["lthr_run"])
        if e.get("lthr_bike"):
            df.loc[since & is_bike, "lthr_used"] = float(e["lthr_bike"])
        print(
            f"app thresholds {e['athlete']} from {e['from_date']}: FTP {e.get('ftp')}, LTHR run {e.get('lthr_run')}, bike {e.get('lthr_bike')} ({e.get('source')})"
        )


# ---------------------------------------------------------------- 5. TSS
def hr_limits(df, intervals):
    """({athlete: resting HR}, {athlete: max HR}) for the heart-rate TSS. Resting HR is a fixed value (athlete_overrides.json,
    else 60): the daily reading of a watch would rescore the whole history every day. Max HR: Intervals.icu setting, else the
    median of the 5 highest session maxima of the last 2 years (190 when there are fewer than 5)."""
    rest, top = {}, {}
    for ath in df.athlete.unique():
        s = (intervals.get(ath) or [{}])[-1].get("settings", {})
        rest[ath] = float(override(ath, "resting_hr", DEFAULT_RESTING_HR))
        mx = (s.get("run") or {}).get("max_hr") or (s.get("bike") or {}).get("max_hr")
        if not mx:
            m = df[
                (df.athlete == ath)
                & (df.max_hr > 120)
                & (df.max_hr < 215)
                & (df.t_local >= df.t_local.max() - pd.Timedelta(days=730))
            ].max_hr
            mx = float(m.nlargest(5).median()) if len(m) >= 5 else 190.0
        top[ath] = float(mx)
    return rest, top


def trimp_rate(hr, rest, hr_max, k):
    """Banister TRIMP per minute at heart rate `hr` (k = 1.92 men, 1.67 women)."""
    x = np.clip((hr - rest) / (hr_max - rest), 0, 1.1)
    return x * 0.64 * np.exp(k * x)


def hr_tss(hr_hist, lthr, rest, hr_max, female=False):
    """TSS from the time spent at each heart rate: TRIMP normalised so one hour at LTHR = 100, the method TrainingPeaks and
    Intervals.icu use. hr_hist is the JSON histogram {bpm bin (2 wide): seconds} written by parse_fit. NaN when not computable."""
    if not isinstance(hr_hist, str) or not (lthr > 0):
        return np.nan
    try:
        hist = json.loads(hr_hist)
    except Exception:
        return np.nan
    if not hist:
        return np.nan
    hr_max = max(hr_max, lthr + 5)
    k = 1.67 if female else 1.92
    bpm = np.array([int(b) + 1 for b in hist])
    minutes = np.array(list(hist.values()), float) / 60
    return float((minutes * trimp_rate(bpm, rest, hr_max, k)).sum() / (60 * trimp_rate(lthr, rest, hr_max, k)) * 100)


def estimate_tss(row, rest, hr_max, females):
    """(TSS, method): bike with power -> NP and FTP; heart-rate histogram -> TRIMP; average HR; else a default per hour."""
    h = row.duration_h
    if not (h > 0):
        return (np.nan, None)
    if row.sport_n == "bike" and row["np"] > 0 and row.ftp_used > 0:
        return (h * (row["np"] / row.ftp_used) ** 2 * 100, "power")
    if row.sport_n in ("bike", "run", "other") and row.lthr_used > 0:
        v = hr_tss(row.get("hr_hist"), row.lthr_used, rest[row.athlete], hr_max[row.athlete], row.athlete in females)
        if v == v and v > 0:
            return (v, "hr")
    if row.avg_hr > 60 and row.lthr_used > 0:
        return (h * min(row.avg_hr / row.lthr_used, 1.15) ** 2 * 100, "hr")
    return (h * DEFAULT_TSS_PER_H.get(row.sport_n, 25), "duration")


def set_tss(df, intervals):
    """tss_est, tss_final, tss_method, power_suspect and if_final."""
    rest, hr_max = hr_limits(df, intervals)
    females = {a for a, p in discover().items() if str(p.get("sex") or "").lower().startswith("f")}
    est = df.apply(estimate_tss, axis=1, result_type="expand", rest=rest, hr_max=hr_max, females=females)
    df["tss_est"], df["tss_est_method"] = est[0], est[1]
    df.loc[~df.completed | (df.sport_n == "dayoff"), ["tss_est", "tss_est_method"]] = [np.nan, None]
    df["tss_final"] = df.tss_tp.fillna(df.tss_est)
    df["tss_method"] = np.where(df.tss_tp.notna(), "tp", df.tss_est_method)

    # TP TSS that implies an intensity factor above 1.15 on a session longer than 20 min is a threshold-setup error in TP
    # (run power scored against the bike FTP, for example): use the estimate; the TP value stays in tss_tp.
    implied_if = np.sqrt(df.tss_tp / (df.duration_h * 100))
    wrong = df.tss_tp.notna() & df.tss_est.notna() & (df.duration_h > 0.33) & (implied_if > MAX_PLAUSIBLE_IF)
    # TP scored a dead-power-meter ride from the zeros as well
    dead = df.power_dropout & df.tss_tp.notna() & df.tss_est.notna()
    for mask in (wrong, dead):
        df.loc[mask, "tss_final"] = df.loc[mask, "tss_est"]
        df.loc[mask, "tss_method"] = "hr-corrected"
    print("\nTP TSS corrected (implied IF > 1.15):", df[wrong].groupby(["athlete", "sport_n"]).size().to_dict())

    # power that is physiologically implausible against the FTP in use: kept out of curves and profiles
    df["power_suspect"] = (df.sport_n == "bike") & (
        (df.p_best_3600s > 1.3 * df.ftp_used)
        | (df.p_best_1200s > 1.45 * df.ftp_used)
        | (df.p_best_300s > 1.8 * df.ftp_used)
        | (df.p_best_5s > 1800)
        | (df.max_power > 2500)
    )
    print("power-suspect bike files:", int(df.power_suspect.sum()))
    df["if_final"] = np.where(df.duration_h > 0, np.sqrt(df.tss_final / (df.duration_h * 100)), np.nan)


def report(df):
    """How the estimate compares with TP's TSS where both exist, and what was written."""
    v = df[df.tss_tp.notna() & df.tss_est.notna() & (df.year >= 2020)].copy()
    v["err"], v["ratio"] = v.tss_est - v.tss_tp, v.tss_est / v.tss_tp
    print("\n== estimator vs TP TSS (2020+): by athlete, sport, method: n, median ratio, MAE ==")
    print(
        v.groupby(["athlete", "sport_n", "tss_est_method"])
        .agg(n=("err", "size"), med_ratio=("ratio", "median"), mae=("err", lambda s: s.abs().mean()))
        .round(2)
        .to_string()
    )
    print("\nwritten activities_all.csv", df.shape)
    print(
        df.groupby(["athlete", "source"])
        .agg(rows=("file", "size"), completed=("completed", "sum"), first=("t_local", "min"), last=("t_local", "max"))
        .to_string()
    )
    print("\nTSS method mix (completed sessions):")
    print(pd.crosstab(df[df.completed].athlete, df[df.completed].tss_method))


def main():
    w, a_all = load_wko(), load_fit()
    a, fit_fate = drop_duplicate_fits(a_all, w)
    replaced = fileless_wko_with_fit(w, a)

    df = unify(w.drop(index=replaced), shape_fit(a))
    doubles = double_recordings(df)
    write_ledger(
        w,
        a_all,
        fit_fate,
        replaced,
        {(r.file, None if r.source == "wko" else r.session_index) for _, r in df.loc[list(doubles)].iterrows()},
    )
    df = df.drop(index=list(doubles)).reset_index(drop=True)
    mark_chained_races(df)
    df["quarter"] = df.t_local.dt.to_period("Q").astype(str)
    df["year"] = df.t_local.dt.year

    intervals = load_intervals_thresholds()
    set_thresholds(df, intervals)
    set_tss(df, intervals)

    df = df.sort_values(["athlete", "t_local"]).reset_index(drop=True)
    df.to_csv(os.path.join(D, "activities_all.csv"), index=False)
    report(df)


if __name__ == "__main__":
    main()

"""Aggregate activities_all.csv into analysis/data/dashboard_data.json for the dashboard page."""

import os, json, math
import numpy as np, pandas as pd

ROOT = os.environ.get("TRIAPP_ROOT") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
D = os.path.join(os.environ.get("TRIAPP_OUT", os.path.join(ROOT, "analysis")), "data")
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from athletes import discover, overrides

TODAY = pd.Timestamp(os.environ.get("TRIAPP_TODAY", datetime.now().strftime("%Y-%m-%d")))
SPORTS = ["swim", "bike", "run", "strength", "other"]
PW = [5, 60, 300, 1200, 3600, 5400, 7200, 10800]
VW = [60, 300, 600, 1200, 3600, 5400, 7200]

df = pd.read_csv(os.path.join(D, "activities_all.csv"), parse_dates=["t_local", "date"], low_memory=False)
done = df[df.completed & (df.sport_n != "dayoff") & (df.duration_h > 0)].copy()
done["week"] = done.date - pd.to_timedelta(done.date.dt.weekday, unit="D")
done["hour"] = done.t_local.dt.hour
# athletes in the data; the ones listed in athlete_overrides.json keep that order (and their colours), newcomers follow alphabetically
_known = overrides().get("order", [])
_present = [a for a in done.athlete.dropna().unique()]
ATHLETES = [a for a in _known if a in _present] + sorted(a for a in _present if a not in _known)
PROFILES = discover()
try:  # the TrainingPeaks plan kept by tp_calendar.py
    PLAN_HISTORY = json.load(open(os.path.join(D, "plan_history.json")))
except Exception:
    PLAN_HISTORY = {}
import execution, session_types, pd_model

_laps = [
    pd.read_csv(os.path.join(D, f), low_memory=False) for f in ("laps.csv", "wko_laps.csv") if os.path.exists(os.path.join(D, f))
]
LAPS = execution.lap_index(pd.concat(_laps, ignore_index=True)) if _laps else {}


def nz(x, nd=1):
    if x is None:
        return None
    try:
        if isinstance(x, (float, np.floating)) and (math.isnan(x) or math.isinf(x)):
            return None
    except Exception:
        pass
    if isinstance(x, (np.integer,)):
        return int(x)
    if isinstance(x, (float, np.floating)):
        return round(float(x), nd)
    if isinstance(x, (pd.Timestamp,)):
        return x.strftime("%Y-%m-%d")
    return x


def weekly(g, weeks_back=None):
    wk = (
        g.groupby(["week", "sport_n"])
        .agg(h=("duration_h", "sum"), tss=("tss_final", "sum"), n=("file", "size"), km=("distance_km", "sum"))
        .reset_index()
    )
    if weeks_back:
        start = (TODAY - pd.to_timedelta(TODAY.weekday(), unit="D")) - pd.Timedelta(weeks=weeks_back - 1)
        idx = pd.date_range(start, periods=weeks_back, freq="7D")
    else:
        idx = pd.date_range(wk.week.min(), (TODAY - pd.to_timedelta(TODAY.weekday(), unit="D")), freq="7D")
    out = []
    for wkst in idx:
        row = {"w": wkst.strftime("%Y-%m-%d")}
        sub = wk[wk.week == wkst]
        for s in SPORTS:
            r = sub[sub.sport_n == s]
            row[f"h_{s}"] = nz(r.h.sum(), 2)
            row[f"tss_{s}"] = nz(r.tss.sum(), 0)
            row[f"n_{s}"] = int(r.n.sum())
            row[f"km_{s}"] = nz(r.km.sum(), 1)
        row["h"] = nz(sub.h.sum(), 2)
        row["tss"] = nz(sub.tss.sum(), 0)
        row["n"] = int(sub.n.sum())
        out.append(row)
    return out


def pmc(g):
    daily = g.groupby("date").tss_final.sum()
    idx = pd.date_range(daily.index.min(), TODAY, freq="D")
    daily = daily.reindex(idx, fill_value=0.0)
    ctl = np.zeros(len(idx))
    atl = np.zeros(len(idx))
    tsb = np.zeros(len(idx))
    c = a = 0.0
    for i, t in enumerate(daily.values):
        tsb[i] = c - a
        c += (t - c) / 42.0
        a += (t - a) / 7.0
        ctl[i] = c
        atl[i] = a
    s = pd.DataFrame({"ctl": ctl, "atl": atl, "tsb": tsb, "tss": daily.values}, index=idx)
    last = s[s.index >= TODAY - pd.Timedelta(days=365)]
    daily_out = [
        {"d": d.strftime("%Y-%m-%d"), "ctl": nz(r.ctl), "atl": nz(r.atl), "tsb": nz(r.tsb), "tss": nz(r.tss, 0)}
        for d, r in last.iterrows()
    ]
    weekly_hist = [{"d": d.strftime("%Y-%m-%d"), "ctl": nz(r.ctl)} for d, r in s[s.index.weekday == 0].iterrows()]
    peak = s.ctl.idxmax()
    return {
        "daily": daily_out,
        "history": weekly_hist,
        "now": {"ctl": nz(s.ctl.iloc[-1]), "atl": nz(s.atl.iloc[-1]), "tsb": nz(s.tsb.iloc[-1])},
        "peak": {"ctl": nz(s.ctl.max()), "date": peak.strftime("%Y-%m-%d")},
        "peak_52w": {"ctl": nz(last.ctl.max()), "date": last.ctl.idxmax().strftime("%Y-%m-%d")},
    }


def bests(g, weight):
    out = {"bike": [], "run": [], "swim": []}
    b = g[(g.sport_n == "bike") & (~g.power_suspect.fillna(False).astype(bool))]
    for y, gy in b.groupby("year"):
        row = {"year": int(y)}
        for wv in PW:
            col = f"p_best_{wv}s"
            val = gy[col].max() if col in gy else np.nan
            if wv >= 60 and val > 1500:
                val = np.nan  # sensor spike guard
            row[f"p{wv}"] = nz(val, 0)
            row[f"wkg{wv}"] = nz(val / weight, 2) if (weight and val == val) else None
        if any(row[f"p{wv}"] for wv in PW):
            out["bike"].append(row)
    # runs: whole file dropped when its 60-s best is beyond 6 m/s (2:47/km) - GPS or footpod spikes
    r = g[(g.sport_n == "run") & (g.has_gps | (g.sub_sport == "treadmill")) & (g.v_best_60s < 6.0) & (g.v_best_300s < 5.6)]
    for y, gy in r.groupby("year"):
        row = {"year": int(y)}
        for wv in VW:
            col = f"v_best_{wv}s"
            v = gy[col].max() if col in gy else np.nan
            row[f"pace{wv}"] = nz(1000 / v / 60, 2) if v and v == v and v > 0 else None  # min/km
        if any(row[f"pace{wv}"] for wv in VW):
            out["run"].append(row)
    # swims: pool sessions only, >= 1 km, pace slower than 1:03/100 m; "best" is the 2nd fastest session of the year
    s = g[
        (g.sport_n == "swim")
        & (g.sub_sport == "lap_swimming")
        & (g.distance_km >= 1.0)
        & (g.avg_speed > 0.5)
        & (g.pool_length_m >= 15)
        & (g.pool_length_m <= 50)
    ].copy()
    s["pace100"] = 100 / s.avg_speed / 60
    s = s[s.pace100 >= 1.05]
    for y, gy in s.groupby("year"):
        p = gy.pace100.sort_values()
        best = p.iloc[1] if len(p) >= 3 else p.iloc[0]
        out["swim"].append({"year": int(y), "best_pace100": nz(best, 2), "median_pace100": nz(p.median(), 2), "n": int(len(gy))})
    return out


def with_tp_plan(g, tp_rows):
    """Sessions after the last day the WKO export has a plan for, completed with the TrainingPeaks plan (plan_history.json):
    each planned workout becomes a planned row; a done one takes the watch session of the same day and sport (which then stops
    counting as extra)."""
    has_plan = g.planned_tss.fillna(0) + g.planned_duration_s.fillna(0) > 0
    cutoff = g.loc[has_plan, "date"].max() if has_plan.any() else pd.Timestamp.min
    add = []
    for r in tp_rows or []:
        day = pd.Timestamp(r["date"])
        if not (cutoff < day < TODAY):
            continue
        row = {
            "date": day,
            "sport_n": r.get("sport") or "other",
            "completed": bool(r.get("completed")),
            "planned_tss": r.get("load"),
            "planned_duration_s": r.get("duration_s") or 0,
            "tss_final": None,
            "duration_h": None,
        }
        if row["completed"]:
            same = g[(g.date == day) & (g.sport_n == row["sport_n"]) & g.completed & ~has_plan.reindex(g.index, fill_value=False)]
            if len(same):
                i = same.index[0]
                row["tss_final"], row["duration_h"] = g.at[i, "tss_final"], g.at[i, "duration_h"]
                g, has_plan = g.drop(i), has_plan.drop(i)
        if not row["planned_tss"] and not row["planned_duration_s"]:
            row["planned_duration_s"] = 1  # a plan with a description only still counts as planned
        add.append(row)
    if not add:
        return g
    g = pd.concat([g, pd.DataFrame(add)], ignore_index=True)
    g["completed"] = g.completed.fillna(False).astype(bool)
    return g


def compliance(gall, weeks_back=52, tp_rows=None):
    start = (TODAY - pd.to_timedelta(TODAY.weekday(), unit="D")) - pd.Timedelta(weeks=weeks_back - 1)
    g = gall[(gall.date >= start) & (gall.date < TODAY) & (gall.sport_n != "dayoff")].copy()
    g = with_tp_plan(g, tp_rows)
    g["week"] = g.date - pd.to_timedelta(g.date.dt.weekday, unit="D")
    g["planned"] = g.planned_tss.fillna(0) + g.planned_duration_s.fillna(0) > 0
    # a planned session left "uncompleted" in TP while an unplanned session of the same sport was done the same day
    # is almost always the same workout that TP failed to pair -> count it as "matched"
    out = []
    for wkst, sub in g.groupby("week"):
        matched = 0
        for (_, _), ds in sub.groupby(["date", "sport_n"]):
            matched += min(int((ds.planned & ~ds.completed).sum()), int((~ds.planned & ds.completed).sum()))
        tss_known = not sub.planned.any() or sub.loc[sub.planned, "planned_tss"].fillna(0).gt(0).any()
        out.append(
            {
                "w": wkst.strftime("%Y-%m-%d"),
                "planned": int(sub.planned.sum()),
                "planned_done": int((sub.planned & sub.completed).sum()),
                "matched": matched,
                "unplanned_done": int((~sub.planned & sub.completed).sum()) - matched,
                "missed": int((sub.planned & ~sub.completed).sum()) - matched,
                # the TrainingPeaks feed has no planned TSS: a week planned there only leaves a gap in the load chart, not a 0
                "planned_tss": nz(sub.loc[sub.planned, "planned_tss"].sum(), 0) if tss_known else None,
                "done_tss": nz(sub.loc[sub.planned & sub.completed, "tss_final"].sum(), 0) if tss_known else None,
                "planned_h": nz(sub.loc[sub.planned, "planned_duration_s"].sum() / 3600, 1),
                "done_h": nz(sub.loc[sub.planned & sub.completed, "duration_h"].sum(), 1),
            }
        )
    tot_pl = sum(o["planned"] for o in out)
    tot_done = sum(o["planned_done"] for o in out)
    tot_m = sum(o["matched"] for o in out)
    return {
        "weeks": out,
        "planned": tot_pl,
        "planned_done": tot_done,
        "matched": tot_m,
        "missed": sum(o["missed"] for o in out),
        "rate": nz((tot_done + tot_m) / tot_pl, 3) if tot_pl else None,
        "rate_strict": nz(tot_done / tot_pl, 3) if tot_pl else None,
        "unplanned": sum(o["unplanned_done"] for o in out),
    }


PD_WINDOWS = [
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
PD_DAYS = 90  # the WKO5 default window


def power_duration(g, ftp_now):
    """MMP of the last 90 days (and of the last 30 and 60, for comparison), the OmPD fit (Pmax, mFTP, FRC, TTE) and the monthly history of mFTP and TTE (rolling 90 days)."""
    bike = g[(g.sport_n == "bike") & ~g.power_suspect.fillna(False).astype(bool)]

    def window(end, days=PD_DAYS):
        s = bike[(bike.date > end - pd.Timedelta(days=days)) & (bike.date <= end)]
        pts = pd_model.mmp(s, PD_WINDOWS)
        return s, pts, pd_model.fit([w for w, _ in pts], [v for _, v in pts]) if pts else None

    s, pts, model = window(TODAY)
    if not pts:
        return None
    out = {
        "days": PD_DAYS,
        "sessions": int(len(s.dropna(subset=["p_best_60s"]))),
        "ftp_in_use": nz(ftp_now, 0),
        "mmp": [{"t": w, "w": round(v)} for w, v in pts],
        "model": model,
    }
    if model:  # the modelled curve at the same durations, for the chart and its table
        par = (model["pmax"], model["mftp"], model["frc_kj"] * 1000, model["a"])
        for m in out["mmp"]:
            m["pd"] = round(float(pd_model.ompd(m["t"], *par)))
    for days in (30, 60):  # shorter windows, to compare recent form with the 90-day curve
        _, p, _ = window(TODAY, days)
        best = dict(p)
        for m in out["mmp"]:
            m[f"w{days}"] = round(best[m["t"]]) if m["t"] in best else None
    hist = []
    for k in range(12, -1, -1):
        end = (TODAY - pd.DateOffset(months=k)).normalize()
        _, _, m = window(end)
        if m:
            hist.append(
                {
                    "d": end.strftime("%Y-%m-%d"),
                    "mftp": m["mftp"],
                    "tte_min": m["tte_min"],
                    "frc_kj": m["frc_kj"],
                    "pmax": m["pmax"],
                }
            )
    out["history"] = hist
    return out


def races(g):
    """Multisport files (swim + bike + run legs in one recording) are race days."""
    m = g[g.multisport.fillna(False).astype(bool) & g.sport_n.isin(["swim", "bike", "run", "other"])]
    out = []
    for d, sub in m.groupby("date"):
        legs = [
            {
                "sport": r.sport_n if r.sport != "transition" else "transition",
                "km": nz(r.distance_km, 1),
                "h": nz(r.duration_h, 2),
                "hr": nz(r.avg_hr, 0),
                "np": nz(r["np"], 0),
                "tss": nz(r.tss_final, 0),
                "pace": nz(1000 / r.avg_speed / 60, 2) if (r.sport_n == "run" and r.avg_speed and r.avg_speed > 0) else None,
                "kmh": nz(r.avg_speed * 3.6, 1) if (r.sport_n == "bike" and r.avg_speed and r.avg_speed > 0) else None,
            }
            for _, r in sub.sort_values("t_local").iterrows()
        ]
        titles = [
            t
            for t in sub.title_n.dropna().unique()
            if isinstance(t, str)
            and t.lower()
            not in (
                "cycling",
                "running",
                "swimming",
                "run",
                "swim",
                "bike",
                "open water swim",
                "lap swimming",
                "open water swimming",
                "transition",
                "multisport",
                "morning multisport",
            )
        ]
        by = {}
        for s in ("swim", "bike", "run"):
            ss = sub[sub.sport_n == s]
            if len(ss):
                km = float(ss.distance_km.sum())
                h = float(ss.duration_h.sum())
                by[s] = {
                    "km": nz(km, 1),
                    "h": nz(h, 2),
                    "legs": int(len(ss)),
                    "hr": nz(ss.avg_hr.mean(), 0),
                    "np": nz(ss["np"].max(), 0),
                    "pace": nz(h * 60 / km, 2) if (s == "run" and km > 0.3) else None,
                    "kmh": nz(km / h, 1) if (s == "bike" and h > 0) else None,
                }
        import re as _re

        coachy = any(
            _re.search(r"\d+'|\bA[12]\b|\bZ\d|ritmo|\dx\d|I=|workout|treino|threshold|brick", str(t), _re.I) for t in titles
        )
        chained = bool(sub["chained"].fillna(False).astype(bool).any()) if "chained" in sub else False
        if (
            chained
        ):  # separate files chained on the same day: only swim + bike + run (outdoor bike) is a race, the rest is training
            virtual = sub[sub.sport_n == "bike"].sport_tp.fillna("").str.contains("indoor|virtual", case=False).any() or any(
                "virtual" in str(t).lower() for t in sub.title_n
            )
            kind = (
                "race"
                if ("swim" in by and "bike" in by and "run" in by and by["bike"]["km"] >= 15 and not virtual and not coachy)
                else "brick"
            )
        elif "swim" in by and "bike" in by and "run" in by and by["bike"]["km"] >= 15:
            kind = "race"
        elif "swim" in by and "run" in by and "bike" not in by and len(sub) >= 5:
            kind = "swimrun"
        elif "bike" in by and "run" in by and not coachy and by["bike"]["km"] >= 15:
            kind = "duathlon"
        else:
            kind = "brick"
        out.append(
            {
                "date": d.strftime("%Y-%m-%d"),
                "title": (
                    titles[0]
                    if titles
                    else {"race": "Triathlon", "swimrun": "Swimrun", "brick": "Brick session", "duathlon": "Duathlon"}[kind]
                ),
                "kind": kind,
                "legs": legs,
                "by": by,
                "total_h": nz(sub.duration_h.sum(), 2),
                "total_km": nz(sub.distance_km.sum(), 1),
                "tss": nz(sub.tss_final.sum(), 0),
            }
        )
    return out


data = {"generated": TODAY.strftime("%Y-%m-%d"), "athletes": {}, "team": {}}
weights = {}
for ath in ATHLETES:
    g = done[done.athlete == ath]
    gall = df[df.athlete == ath]
    w_garmin = g.dropna(subset=["weight_kg"]).sort_values("t_local").weight_kg
    w_tp = g.dropna(subset=["weight_kg_tp"]).sort_values("t_local").weight_kg_tp
    weight = float(w_garmin.iloc[-1]) if len(w_garmin) else (float(w_tp.iloc[-1]) if len(w_tp) else None)
    weights[ath] = weight
    last52 = g[g.date >= TODAY - pd.Timedelta(weeks=52)]
    prev52 = g[(g.date >= TODAY - pd.Timedelta(weeks=104)) & (g.date < TODAY - pd.Timedelta(weeks=52))]
    by_time = gall.dropna(subset=["t_local"]).sort_values("t_local")  # latest value in use: rows come in source order
    ftp_now = g.sort_values("t_local")[lambda x: x.sport_n == "bike"].ftp_used.dropna()
    lthr = by_time[by_time.sport_n != "bike"].lthr_used.dropna()  # the run one; the bike one is shown apart
    lthr_bike = by_time[by_time.sport_n == "bike"].lthr_used.dropna()
    recent = g.sort_values("t_local", ascending=False).head(30)
    yearly = g.groupby("year").agg(h=("duration_h", "sum"), n=("file", "size"), tss=("tss_final", "sum")).reset_index()
    yearly_sport = g.pivot_table(index="year", columns="sport_n", values="duration_h", aggfunc="sum").fillna(0)
    hours_by_sport_52 = last52.groupby("sport_n").duration_h.sum()
    tod = last52.groupby(["hour", "sport_n"]).size().unstack(fill_value=0).reindex(range(24), fill_value=0)
    ftp_hist = g[g.sport_n == "bike"].dropna(subset=["ftp_used"]).groupby("quarter").ftp_used.median()
    lthr_hist = g.dropna(subset=["lthr_setting"]).groupby("quarter").lthr_setting.median()
    wt_hist = g.dropna(subset=["weight_kg"]).groupby(g.t_local.dt.to_period("M").astype(str)).weight_kg.median()
    data["athletes"][ath] = {
        "name": ath,
        "full_name": PROFILES.get(ath, {}).get("full_name", ath),
        "birth_year": PROFILES.get(ath, {}).get("birth_year"),
        "weight_kg": nz(weight),
        "ftp": nz(ftp_now.iloc[-1], 0) if len(ftp_now) else None,
        "lthr": nz(lthr.iloc[-1], 0) if len(lthr) else None,
        "lthr_bike": nz(lthr_bike.iloc[-1], 0) if len(lthr_bike) else None,
        "first_date": g.date.min().strftime("%Y-%m-%d"),
        "last_date": g.date.max().strftime("%Y-%m-%d"),
        "sources": sorted(g.source.unique().tolist()),
        "devices": g.device_brand.fillna(g.device).value_counts().head(5).to_dict(),
        "kpi52": {
            "sessions": int(len(last52)),
            "hours": nz(last52.duration_h.sum(), 0),
            "tss": nz(last52.tss_final.sum(), 0),
            "km_bike": nz(last52[last52.sport_n == "bike"].distance_km.sum(), 0),
            "km_run": nz(last52[last52.sport_n == "run"].distance_km.sum(), 0),
            "km_swim": nz(last52[last52.sport_n == "swim"].distance_km.sum(), 1),
            "hours_prev": nz(prev52.duration_h.sum(), 0),
            "sessions_prev": int(len(prev52)),
            "hours_by_sport": {s: nz(hours_by_sport_52.get(s, 0.0), 1) for s in SPORTS},
            "weeks_active": int(last52.week.nunique()),
            "avg_sessions_week": nz(len(last52) / 52, 1),
            "avg_hours_week": nz(last52.duration_h.sum() / 52, 1),
            "tss_method": last52.tss_method.value_counts().to_dict(),
        },
        "weekly52": weekly(g, 52),
        "weekly_all": weekly(g),
        "pmc": pmc(g),
        "bests": bests(g, weight),
        "compliance": (
            compliance(gall, tp_rows=list(PLAN_HISTORY.get(ath, {}).values()))
            if (gall.planned_tss.fillna(0) > 0).any() or PLAN_HISTORY.get(ath)
            else None
        ),
        "races": races(g),
        "power_duration": power_duration(g, ftp_now.iloc[-1] if len(ftp_now) else None),
        "executions": execution.executions(gall, LAPS, today=TODAY),
        "structured_share": nz((last52.workout_steps.fillna(0) > 0).mean(), 2),
        "recent": [
            {
                "date": r.t_local.strftime("%Y-%m-%d %H:%M"),
                "sport": r.sport_n,
                "title": (r.title_n if isinstance(r.title_n, str) else "")[:60],
                "h": nz(r.duration_h, 2),
                "km": nz(r.distance_km, 1),
                "tss": nz(r.tss_final, 0),
                "method": r.tss_method,
                "hr": nz(r.avg_hr, 0),
                "np": nz(r["np"], 0),
                "if": nz(r.if_final, 2),
                "planned_tss": nz(r.planned_tss, 0),
                "key": f"{r.file}|{int(r.session_index) if pd.notna(r.session_index) else 0}",
                "kind": session_types.classify(r),
                "power_dropout": bool(r.get("power_dropout")) if pd.notna(r.get("power_dropout")) else False,
            }
            for _, r in recent.iterrows()
        ],
        "yearly": [
            {
                "year": int(r.year),
                "h": nz(r.h, 0),
                "n": int(r.n),
                "tss": nz(r.tss, 0),
                **{f"h_{s}": nz(yearly_sport.loc[r.year, s], 0) if s in yearly_sport else 0 for s in SPORTS},
            }
            for _, r in yearly.iterrows()
        ],
        "time_of_day": {s: [int(tod.loc[h, s]) if s in tod else 0 for h in range(24)] for s in SPORTS},
        "thresholds": {
            "ftp": [{"q": q, "ftp": nz(v, 0)} for q, v in ftp_hist.items()],
            "lthr": [{"q": q, "lthr": nz(v, 0)} for q, v in lthr_hist.items()],
            "weight": [{"m": m, "kg": nz(v)} for m, v in wt_hist.items()],
        },
    }

# ---------- team ----------
t = {}
l52 = done[done.date >= TODAY - pd.Timedelta(weeks=52)]
t["weekly_hours"] = {ath: [w["h"] for w in data["athletes"][ath]["weekly52"]] for ath in ATHLETES}
t["weekly_tss"] = {ath: [w["tss"] for w in data["athletes"][ath]["weekly52"]] for ath in ATHLETES}
t["weeks"] = [w["w"] for w in data["athletes"][ATHLETES[0]]["weekly52"]]
t["sport_mix"] = {ath: data["athletes"][ath]["kpi52"]["hours_by_sport"] for ath in ATHLETES}
t["ctl"] = {ath: data["athletes"][ath]["pmc"]["now"] for ath in ATHLETES}
t["ctl_daily"] = {ath: [d["ctl"] for d in data["athletes"][ath]["pmc"]["daily"]] for ath in ATHLETES}
t["ctl_days"] = [d["d"] for d in data["athletes"][ATHLETES[0]]["pmc"]["daily"]]
# align every athlete's daily CTL series to the same 365-day window (athletes with shorter history are left-padded)
for ath in ATHLETES:
    dd = {d["d"]: d["ctl"] for d in data["athletes"][ath]["pmc"]["daily"]}
    t["ctl_daily"][ath] = [dd.get(day) for day in t["ctl_days"]]


def best_last52(ath, sport, key):
    rows = [r for r in data["athletes"][ath]["bests"][sport] if r["year"] >= 2025]
    vals = [r[key] for r in rows if r.get(key)]
    if not vals:
        return None
    return min(vals) if key.startswith(("pace", "best_pace")) else max(vals)


t["bike_curve_wkg"] = {ath: [best_last52(ath, "bike", f"wkg{w}") for w in PW] for ath in ATHLETES}
t["bike_curve_w"] = {ath: [best_last52(ath, "bike", f"p{w}") for w in PW] for ath in ATHLETES}
t["run_curve_pace"] = {ath: [best_last52(ath, "run", f"pace{w}") for w in VW] for ath in ATHLETES}
t["swim_best"] = {ath: best_last52(ath, "swim", "best_pace100") for ath in ATHLETES}
t["consistency"] = {
    ath: {
        "weeks_5plus": int(sum(1 for w in data["athletes"][ath]["weekly52"] if w["n"] >= 5)),
        "weeks_zero": int(sum(1 for w in data["athletes"][ath]["weekly52"] if w["n"] == 0)),
        "longest_streak_weeks": 0,
    }
    for ath in ATHLETES
}
for ath in ATHLETES:
    best = cur = 0
    for w in data["athletes"][ath]["weekly52"]:
        cur = cur + 1 if w["n"] >= 3 else 0
        best = max(best, cur)
    t["consistency"][ath]["longest_streak_weeks"] = best
t["time_of_day"] = {ath: [sum(data["athletes"][ath]["time_of_day"][s][h] for s in SPORTS) for h in range(24)] for ath in ATHLETES}
t["yearly_hours"] = {ath: {str(r["year"]): r["h"] for r in data["athletes"][ath]["yearly"]} for ath in ATHLETES}
t["weights"] = {a: nz(v) for a, v in weights.items()}
data["team"] = t

# ---------- notes & insights (generic for any number of athletes) ----------
A = data["athletes"]
K = {a: A[a]["kpi52"] for a in ATHLETES}
C = {a: A[a]["pmc"] for a in ATHLETES}


def pace(m):
    return f"{int(m)}:{int(round((m - int(m)) * 60)):02d}"


def share(a, s):
    return round(100 * (K[a]["hours_by_sport"][s] or 0) / K[a]["hours"]) if K[a]["hours"] else 0


def join(items):
    return ", ".join(items[:-1]) + (" and " + items[-1] if len(items) > 1 else (items[0] if items else ""))


active = [a for a in ATHLETES if (K[a]["hours"] or 0) >= 20]
inactive = [a for a in ATHLETES if a not in active]
w20 = {a: t["bike_curve_wkg"][a][3] for a in ATHLETES}
p20 = {a: t["run_curve_pace"][a][3] for a in ATHLETES}
fixes = {
    a: int(
        done[(done.athlete == a) & (done.tss_method == "hr-corrected") & (done.date >= TODAY - pd.Timedelta(weeks=52))].shape[0]
    )
    for a in ATHLETES
}
EXTRA_NOTES = {a: v["notes"] for a, v in overrides().get("athletes", {}).items() if v.get("notes")}
data["notes"] = {}
for a in ATHLETES:
    g = done[done.athlete == a]
    k = K[a]
    n = []
    yrs = f"{A[a]['first_date'][:4]}-{A[a]['last_date'][:4]}"
    src = "WKO export (TrainingPeaks history)" if "wko" in A[a]["sources"] else "FIT export (last 12 months only, no TP metrics)"
    n.append(
        f"{yrs} from the {src}. TP's own TSS on {k['tss_method'].get('tp', 0)} of the last {k['sessions']} sessions; the rest is estimated from power, heart rate or duration."
    )
    if fixes[a]:
        n.append(
            f"{fixes[a]} sessions in the last 52 weeks had impossible TP TSS values (implied IF above 1.15) and use a heart-rate estimate instead."
        )
    lthr = A[a]["lthr"]
    wt = A[a]["weight_kg"]
    n.append(
        f"Thresholds used: FTP {A[a]['ftp']:.0f} W (backed out of TP's TSS), LTHR {lthr:.0f} bpm."
        if A[a]["ftp"] and lthr
        else f"LTHR {lthr:.0f} bpm; no bike FTP could be derived." if lthr else "No thresholds could be derived."
    )
    n.append(f"Weight {wt} kg from the latest device or TP profile." if wt else "No weight in the files, so W/kg is not shown.")
    if (k["hours"] or 0) < 20:
        n.append(
            f"Only {k['hours'] or 0:.0f} h logged in the last 52 weeks (last session {A[a]['last_date']}); current-form numbers are not meaningful."
        )
    n += EXTRA_NOTES.get(a, [])
    data["notes"][a] = n
ins = []
if active:
    by_h = sorted(active, key=lambda a: -K[a]["hours"])
    ins.append(
        {
            "t": "Volume",
            "x": "Training hours in the last 52 weeks: "
            + join([f"{a} {K[a]['hours']:.0f} h ({K[a]['avg_hours_week']} h/week)" for a in by_h])
            + "."
            + " "
            + " ".join(
                f"{a} is {abs(round(100*(K[a]['hours']/K[a]['hours_prev']-1)))}% {'above' if K[a]['hours'] >= K[a]['hours_prev'] else 'below'} the previous 52 weeks."
                for a in by_h
                if (K[a]["hours_prev"] or 0) >= 50
            ),
        }
    )
    ins.append(
        {
            "t": "Sport mix",
            "x": "Share of hours on the bike: "
            + join([f"{a} {share(a, 'bike')}%" for a in active])
            + ". Swimming share: "
            + join([f"{a} {share(a, 'swim')}%" for a in active])
            + ".",
        }
    )
    ins.append(
        {
            "t": "Fitness",
            "x": "Fitness (CTL) today: "
            + join([f"{a} {C[a]['now']['ctl']:.0f}" for a in sorted(active, key=lambda a: -C[a]["now"]["ctl"])])
            + ". Peak of the last 52 weeks: "
            + join([f"{a} {C[a]['peak_52w']['ctl']:.0f} on {C[a]['peak_52w']['date']}" for a in active])
            + ".",
        }
    )
    wk = [a for a in active if w20[a]]
    if wk:
        ins.append(
            {
                "t": "Bike",
                "x": "Best 20-minute power since 2025, per kilo: "
                + join([f"{a} {w20[a]} W/kg" for a in sorted(wk, key=lambda a: -w20[a])])
                + ". FTP in use: "
                + join([f"{a} {A[a]['ftp']:.0f} W" for a in active if A[a]["ftp"]])
                + ".",
            }
        )
    rk = [a for a in active if p20[a]]
    if rk:
        ins.append(
            {
                "t": "Run",
                "x": "Best 20-minute pace since 2025: "
                + join([f"{a} {pace(p20[a])}/km" for a in sorted(rk, key=lambda a: p20[a])])
                + ". Run volume: "
                + join([f"{a} {K[a]['km_run']:.0f} km" for a in active])
                + ".",
            }
        )
    comp = [a for a in active if A[a]["compliance"]]
    if comp:
        ins.append(
            {
                "t": "Plan compliance",
                "x": "Coach-planned sessions completed in the last 52 weeks: "
                + join(
                    [
                        f"{a} {round(100*A[a]['compliance']['rate'])}% of {A[a]['compliance']['planned']} ({A[a]['compliance']['missed']} missed, {A[a]['compliance']['unplanned']} extra)"
                        for a in comp
                    ]
                )
                + ".",
            }
        )
    fx = [a for a in active if fixes[a] >= 10]
    if fx:
        ins.append(
            {
                "t": "TP setup issue",
                "x": "Sessions with impossible TP TSS (implied IF above 1.15), almost always runs scored against a run power threshold set too low: "
                + join([f"{a} {fixes[a]}" for a in fx])
                + ". The fix in TrainingPeaks is a device-specific run threshold power in each athlete's zones.",
            }
        )

    def slot(a):
        tod = t["time_of_day"][a]
        tot = sum(tod) or 1
        early = sum(tod[4:9]) / tot
        lunch = sum(tod[11:14]) / tot
        eve = sum(tod[17:22]) / tot
        return max([("early morning", early), ("lunchtime", lunch), ("evening", eve)], key=lambda x: x[1])

    ins.append(
        {
            "t": "Rhythm",
            "x": "Most common start window: "
            + join([f"{a} {slot(a)[0]} ({slot(a)[1]*100:.0f}%)" for a in active])
            + ". Longest run of weeks with 3+ sessions: "
            + join([f"{a} {t['consistency'][a]['longest_streak_weeks']}" for a in active])
            + ".",
        }
    )
yh = t["yearly_hours"]
hist = [a for a in ATHLETES if len([y for y, h in yh[a].items() if (h or 0) > 50]) >= 3]
if hist:
    ins.append(
        {
            "t": "History",
            "x": "Peak season: "
            + join([f"{a} {max(yh[a].values()):.0f} h in {max(yh[a], key=lambda y: yh[a][y] or 0)}" for a in hist])
            + f". {TODAY.year} so far: "
            + join([f"{a} {yh[a].get(str(TODAY.year)) or 0:.0f} h" for a in hist])
            + ".",
        }
    )
if inactive:
    ins.append(
        {
            "t": "Not training recently",
            "x": join(inactive) + " logged under 20 h in the last 52 weeks; they are left out of the comparisons above.",
        }
    )
data["insights"] = ins

with open(os.path.join(D, "dashboard_data.json"), "w") as f:
    json.dump(data, f, separators=(",", ":"), allow_nan=False)
print("json bytes:", os.path.getsize(os.path.join(D, "dashboard_data.json")))
for ath in ATHLETES:
    A = data["athletes"][ath]
    print(f"\n### {ath} weight={A['weight_kg']} ftp={A['ftp']} lthr={A['lthr']} range {A['first_date']}..{A['last_date']}")
    print("kpi52:", {k: v for k, v in A["kpi52"].items() if k not in ("hours_by_sport", "tss_method")})
    print("hours_by_sport:", A["kpi52"]["hours_by_sport"], "| tss methods:", A["kpi52"]["tss_method"])
    print("pmc now:", A["pmc"]["now"], "peak:", A["pmc"]["peak"], "peak52:", A["pmc"]["peak_52w"])
    print("bike bests:", A["bests"]["bike"][-2:])
    print("run bests:", A["bests"]["run"][-2:])
    print("swim:", A["bests"]["swim"][-2:])
    if A["compliance"]:
        print("compliance:", {k: v for k, v in A["compliance"].items() if k != "weeks"})
    print("structured share:", A["structured_share"])
    print("yearly:", [(y["year"], y["h"], y["n"]) for y in A["yearly"]])
    print(
        "races:",
        [
            (r["date"], r["kind"], r["title"], r["total_h"], {k: (v["km"], v["h"]) for k, v in r["by"].items()})
            for r in A["races"]
        ],
    )
    print("thresholds ftp:", A["thresholds"]["ftp"][-6:])
    print("lthr:", A["thresholds"]["lthr"][-4:])
    print("weight:", A["thresholds"]["weight"][-3:])
print("\nteam:", {k: t[k] for k in ("ctl", "bike_curve_wkg", "run_curve_pace", "swim_best", "consistency", "weights")})
print("yearly hours:", t["yearly_hours"])

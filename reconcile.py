"""Reconciliation: is everything in the sources in the app?

Compares, per athlete and per source, what exists at the origin with what the app holds, and gives every item one fate
(in the app / duplicate of another source / planned, not done / day off / unreadable), so nothing is silently lost:

  1. Drive -> box      files in the coach's Drive folder that are not on the box (rclone listing)
  2. box -> index      .wko4 and FIT files on disk that the parsers did not read, or read without any session
  3. index -> app      fate of every WKO session and FIT session (ingest_ledger.csv, written by build_dataset.py)
  4. Intervals -> app  every activity Intervals.icu lists in the last year must match a session in the app
  5. freshness         last session per athlete and source; weeks without sessions between active weeks

Writes <OUT>/data/reconciliation.json (shown in /admin) and prints a summary. Never fails the pipeline.

Usage: python reconcile.py
"""

import os, sys, json, glob, subprocess
from datetime import date, timedelta
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from athletes import discover, ROOT
import intervals_sync, matching

OUT = os.path.join(os.environ.get("TRIAPP_OUT", os.path.join(ROOT, "analysis")), "data")
REMOTE = os.environ.get("TRIAPP_RCLONE_REMOTE", "")
TODAY = pd.Timestamp(os.environ.get("TRIAPP_TODAY", date.today().isoformat()))
IV_TYPES = {
    "Ride": "bike",
    "VirtualRide": "bike",
    "MountainBikeRide": "bike",
    "GravelRide": "bike",
    "Run": "run",
    "VirtualRun": "run",
    "TrailRun": "run",
    "Swim": "swim",
    "OpenWaterSwim": "swim",
    "WeightTraining": "strength",
}
IS_DATA = lambda f: f.lower().endswith((".wko4", ".fit", ".fit.gz", ".gz"))


def disk_files(folder):
    base = os.path.join(ROOT, folder)
    return {
        os.path.relpath(p, base)
        for p in glob.glob(os.path.join(base, "**", "*"), recursive=True)
        if os.path.isfile(p) and IS_DATA(p)
    }


def drive_listing():
    """{top folder: set(relative paths)} of data files on the Drive, or None when rclone / the remote is not available."""
    if not REMOTE:
        return None
    try:
        r = subprocess.run(
            ["rclone", "lsf", "-R", "--files-only", "--fast-list", REMOTE], capture_output=True, text=True, timeout=600
        )
        if r.returncode != 0:
            return None
    except Exception:
        return None
    out = {}
    for line in r.stdout.splitlines():
        if "/" not in line or not IS_DATA(line):
            continue
        top, rel = line.split("/", 1)
        out.setdefault(top, set()).add(rel)
    return out


def intervals_check(app):
    """Activities listed by Intervals.icu in the last year without a matching session in the app."""
    res = {}
    for athlete, (key, aid) in intervals_sync.configured().items():
        try:
            acts = intervals_sync.get(
                key,
                f"/athlete/{aid}/activities",
                {"oldest": (TODAY - pd.Timedelta(days=365)).strftime("%Y-%m-%d"), "newest": TODAY.strftime("%Y-%m-%d")},
            )
        except Exception as e:
            res[athlete] = {"error": f"{type(e).__name__}"}
            continue
        g = app[(app.athlete == athlete) & app.completed & app.t_local.notna()]
        missing = []
        for a in acts:
            t = pd.Timestamp(a.get("start_date_local")) if a.get("start_date_local") else None
            if t is None:
                continue
            if matching.near_start(g.t_local, t, minutes=10).any():
                continue
            dur = a.get("moving_time") or a.get("elapsed_time") or 0
            same_sport = g[g.sport_n == IV_TYPES.get(a.get("type"), "other")]
            # Intervals reports moving time, the app timer time: a little more slack than between two files of the same recording
            if matching.same_day_duration(
                same_sport.t_local, same_sport.duration_h * 3600, t, dur, day_tol=0, frac=0.05, abs_s=180
            ).any():
                continue
            missing.append({"date": str(t)[:16], "type": a.get("type"), "name": a.get("name"), "min": round(dur / 60)})
        res[athlete] = {"listed": len(acts), "in_app": len(acts) - len(missing), "missing": missing}
    return res


def main():
    prof = discover()
    led = pd.read_csv(os.path.join(OUT, "ingest_ledger.csv"), parse_dates=["t_local"], low_memory=False)
    app = pd.read_csv(os.path.join(OUT, "activities_all.csv"), parse_dates=["t_local"], low_memory=False)
    wko = pd.read_csv(
        os.path.join(OUT, "wko_index.csv"),
        usecols=lambda c: c in ("athlete", "wko_file", "parsed", "fit_error", "index_error"),
        low_memory=False,
    )
    fit = pd.read_csv(os.path.join(OUT, "activities.csv"), usecols=lambda c: c in ("athlete", "file", "error"), low_memory=False)
    drive = drive_listing()
    alerts, athletes = [], {}
    for name, a in prof.items():
        A = {"sources": []}
        for kind, folder in (("wko", a.get("wko")), ("fit", a.get("fit"))):
            if not folder:
                continue
            on_disk = disk_files(folder)
            names = {os.path.basename(p) for p in on_disk}
            if kind == "wko":
                idx = set(wko[wko.athlete == name].wko_file.astype(str))
                unread = sorted(names - idx)
                mine = wko[wko.athlete == name]
                # the WKO entry could not be read, or its embedded device file failed to parse (the session is then in the
                # app with TP's duration and TSS only: no heart rate, power or laps)
                errors = int(sum(mine[c].notna().sum() for c in ("fit_error", "index_error") if c in mine))
                other_format = int(mine.parsed.astype(str).str.startswith("skipped").sum())
            else:
                rows = fit[fit.athlete == name]
                idx = set(rows.file.astype(str))
                unread = sorted(names - idx)
                errors = int(rows["error"].notna().sum()) if "error" in rows else 0
            l = led[(led.athlete == name) & (led.origin.isin(["wko"] if kind == "wko" else ["fit", "intervals"]))]
            fates = l.fate.value_counts().to_dict()
            in_app = int(l.fate.astype(str).str.startswith("na app").sum())
            nodata = l[l.fate == "sem dados no ficheiro"]
            S = {
                "kind": kind,
                "folder": folder,
                "files_on_disk": len(on_disk),
                "files_indexed": len(idx),
                "files_unread": unread[:20],
                "n_unread": len(unread),
                "parse_errors": errors,
                "other_format": other_format if kind == "wko" else 0,
                "sessions": int(len(l)),
                "in_app": in_app,
                "fates": {k: int(v) for k, v in fates.items()},
                "duplicates": int(l.fate.astype(str).str.contains("duplicado|substituído").sum()),
                "no_data_files": sorted(nodata.file.astype(str).unique())[:20],
                "n_no_data": int(nodata.file.nunique()),
                "last_session": l.t_local.max().strftime("%Y-%m-%d") if l.t_local.notna().any() else None,
            }
            if drive is not None:
                on_drive = drive.get(folder, set())
                miss = sorted(on_drive - on_disk)
                S["drive_files"] = len(on_drive)
                S["drive_missing_on_box"] = len(miss)
                S["drive_missing_examples"] = miss[:10]
                S["box_not_on_drive"] = len(on_disk - on_drive) if kind == "wko" else None  # copy never deletes: informational
                if miss:
                    alerts.append(
                        {
                            "athlete": name,
                            "level": "alert",
                            "text": f"{len(miss)} ficheiros do Drive ({folder}) não estão na box.",
                        }
                    )
            if unread:
                alerts.append(
                    {
                        "athlete": name,
                        "level": "alert",
                        "text": f"{len(unread)} ficheiros em {folder} não foram lidos pelo pipeline (ex.: {unread[0]}).",
                    }
                )
            if errors:
                alerts.append(
                    {
                        "athlete": name,
                        "level": "alert",
                        "text": f"{errors} ficheiros em {folder} deram erro de leitura: essas sessões estão na app só com duração e TSS, sem FC, potência nem voltas.",
                    }
                )
            if len(nodata):
                alerts.append(
                    {
                        "athlete": name,
                        "level": "watch",
                        "text": f"{S['n_no_data']} ficheiros FIT em {folder} foram lidos mas não têm sessão nem registos (ex.: {S['no_data_files'][0]}).",
                    }
                )
            A["sources"].append(S)
        g = app[(app.athlete == name) & app.completed & app.t_local.notna()]
        A["app_sessions"] = int(len(g))
        A["app_by_source"] = {k: int(v) for k, v in g.source.value_counts().items()}
        A["app_no_file"] = int((g.has_hr.isna() & g.n_records.isna()).sum()) if "n_records" in g else None
        A["last_in_app"] = g.t_local.max().strftime("%Y-%m-%d") if len(g) else None
        if len(g):
            stale = (TODAY - g.t_local.max().normalize()).days
            A["days_since_last"] = int(stale)
            if 7 < stale <= 120:
                alerts.append(
                    {
                        "athlete": name,
                        "level": "watch",
                        "text": f"Última sessão na app há {stale} dias ({A['last_in_app']}): falta exportação nova ou ligação ao Intervals.",
                    }
                )
            wk = g[g.t_local >= TODAY - pd.Timedelta(weeks=26)]
            weeks = pd.date_range((TODAY - pd.Timedelta(weeks=26)).normalize(), g.t_local.max(), freq="W-MON")
            cnt = wk.groupby(wk.t_local.dt.to_period("W-SUN")).size()
            empty = [str(w.date()) for w in weeks if cnt.get(w.to_period("W-SUN"), 0) == 0]
            A["empty_weeks_26"] = empty
        athletes[name] = A
    iv = intervals_check(app)
    for name, r in iv.items():
        if r.get("error"):
            alerts.append({"athlete": name, "level": "watch", "text": f"Intervals.icu não respondeu ({r['error']})."})
        elif r["missing"]:
            alerts.append(
                {
                    "athlete": name,
                    "level": "alert",
                    "text": f"{len(r['missing'])} atividades do Intervals.icu não estão na app (ex.: {r['missing'][0]['date']} {r['missing'][0]['name']}).",
                }
            )
    no_iv = [n for n, A in athletes.items() if n not in iv and A.get("days_since_last", 999) <= 120]
    total = {
        "source_sessions": int(len(led)),
        "in_app": int(led.fate.astype(str).str.startswith("na app").sum()),
        "fates": {k: int(v) for k, v in led.fate.value_counts().items()},
        "unexplained": int(led.fate.isna().sum()),
    }
    out = {
        "generated": TODAY.strftime("%Y-%m-%d"),
        "drive_checked": drive is not None,
        "athletes": athletes,
        "intervals": iv,
        "without_intervals": no_iv,
        "alerts": alerts,
        "total": total,
    }
    with open(os.path.join(OUT, "reconciliation.json"), "w") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"reconciliation: {total['source_sessions']} source sessions, {total['in_app']} in the app, fates {total['fates']}")
    print(f"drive checked: {drive is not None}; alerts: {len(alerts)}")
    for a in alerts:
        print(f"  [{a['level']}] {a['athlete']}: {a['text']}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:  # reporting must never block the pipeline
        print(f"reconciliation failed: {type(e).__name__}: {e}")
        sys.exit(0)

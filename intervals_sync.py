"""Pull new activities and threshold settings from Intervals.icu for every athlete with an API key.

Configuration (box .env, passed to the container): for an athlete key K (spaces -> _), e.g. Marcelo or Joao_Faria:
  TRIAPP_INTERVALS_KEY_K = the athlete's (or coach's) API key   (Intervals.icu > Settings > Developer settings)
  TRIAPP_INTERVALS_ID_K  = the Intervals athlete id, e.g. i123456 (default "0" = the key's own athlete)

For each one:
  - original files of activities since the last sync (first run: TRIAPP_INTERVALS_DAYS, default 365) are saved as
    <ROOT>/<athlete fit folder>/intervals-<id>.fit[.gz]; parse_fit picks them up, build_dataset drops the ones already
    present from the WKO bundle or the TP export
  - sport settings (FTP, LTHR, threshold pace, run FTP) and the latest weight are appended to
    <OUT>/data/intervals_thresholds.json when they change, with the date seen: a dated threshold history

Usage: python intervals_sync.py [Athlete ...]
"""

import os, sys, json, base64, gzip, urllib.request, urllib.parse, urllib.error
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from athletes import discover, ROOT

OUT = os.path.join(os.environ.get("TRIAPP_OUT", os.path.join(ROOT, "analysis")), "data")
API = "https://intervals.icu/api/v1"
FIRST_DAYS = int(os.environ.get("TRIAPP_INTERVALS_DAYS", "365"))
STATE = os.path.join(OUT, "intervals_state.json")
THRESH = os.path.join(OUT, "intervals_thresholds.json")
NAMES = os.path.join(OUT, "intervals_activities.json")  # file name -> activity name (FIT files carry no title)
PLANNED = os.path.join(OUT, "intervals_planned.json")  # athlete -> planned workouts, yesterday to a week ahead
SPORT = {"Ride": "bike", "VirtualRide": "bike", "Run": "run", "VirtualRun": "run", "Swim": "swim", "WeightTraining": "strength"}


def env_key(athlete):
    return athlete.replace(" ", "_")


def configured():
    """{athlete: (key, id)}: TRIAPP_INTERVALS_KEY_/ID_<Athlete> from .env, then the keys athletes saved in the app (Ligações)."""
    import userdata

    out = {}
    for a in discover():
        k = os.environ.get(f"TRIAPP_INTERVALS_KEY_{env_key(a)}")
        if k:
            out[a] = (k, os.environ.get(f"TRIAPP_INTERVALS_ID_{env_key(a)}", "0"))
    for a, i in userdata.integrations().items():
        if i.get("intervals_key") and i.get("intervals_id"):
            out[a] = (i["intervals_key"], i["intervals_id"])
    return out


RETRY_WAITS = [20, 60, 180]  # seconds between attempts after a 5xx or a network error


def get(key, path, params=None, raw=False):
    url = API + path + ("?" + urllib.parse.urlencode(params) if params else "")
    auth = base64.b64encode(f"API_KEY:{key}".encode()).decode()
    req = urllib.request.Request(
        url, headers={"Authorization": f"Basic {auth}", "Accept": "*/*", "User-Agent": "triapp/1.0 (+https://tri.laloop.ai)"}
    )
    import time

    for wait in RETRY_WAITS + [None]:  # Intervals.icu answers 503 now and then: a few spaced retries before giving up
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                data = r.read()
            return data if raw else json.loads(data)
        except urllib.error.HTTPError as e:
            if e.code < 500 or wait is None:
                raise
        except (urllib.error.URLError, TimeoutError):
            if wait is None:
                raise
        time.sleep(wait)


def load(path, default):
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return default


def fit_folder(athlete):
    a = discover()[athlete]
    d = a.get("fit") or (a["full_name"].replace(" ", "_") + "_Fit_Files")
    path = os.path.join(ROOT, d)
    os.makedirs(path, exist_ok=True)
    return path


def sync_activities(athlete, key, aid, state, names):
    oldest = state.get(athlete, {}).get("newest") or (date.today() - timedelta(days=FIRST_DAYS)).isoformat()
    oldest = (date.fromisoformat(oldest) - timedelta(days=2)).isoformat()  # small overlap: late uploads
    acts = get(key, f"/athlete/{aid}/activities", {"oldest": oldest, "newest": date.today().isoformat()})
    folder = fit_folder(athlete)
    have = set(os.listdir(folder))
    new = skipped = 0
    for act in acts:
        iid = str(act.get("id", ""))
        if iid and act.get("name"):
            names[f"intervals-{iid}.fit"] = names[f"intervals-{iid}.fit.gz"] = act["name"]
        if not iid or any(f.startswith(f"intervals-{iid}.") for f in have):
            continue
        try:
            data = get(key, f"/activity/{iid}/file", raw=True)
        except urllib.error.HTTPError as e:
            if e.code in (404, 422):
                skipped += 1
                continue
            raise
        if not data:
            skipped += 1
            continue
        name = f"intervals-{iid}.fit.gz" if data[:2] == b"\x1f\x8b" else f"intervals-{iid}.fit"
        if not (data[:2] == b"\x1f\x8b" or data[8:12] == b".FIT"):
            skipped += 1
            continue  # not a FIT file (e.g. GPX/TCX upload)
        with open(os.path.join(folder, name), "wb") as f:
            f.write(data)
        new += 1
    days = [a.get("start_date_local", "")[:10] for a in acts if a.get("start_date_local")]
    state[athlete] = {
        "newest": max(days) if days else state.get(athlete, {}).get("newest", oldest),
        "last_run": date.today().isoformat(),
    }
    return new, skipped, len(acts)


def compact(rows):
    """Threshold history without consecutive entries that carry the same settings."""
    out = []
    for r in rows:
        if not out or out[-1]["settings"] != r["settings"]:
            out.append(r)
    return out


def sync_thresholds(athlete, key, aid, hist):
    prof = get(key, f"/athlete/{aid}")
    cur = {}
    for s in prof.get("sportSettings", []) or []:
        types = s.get("types") or []
        sport = (
            "bike"
            if any("Ride" in t for t in types)
            else "run" if any("Run" in t for t in types) else "swim" if any("Swim" in t for t in types) else None
        )
        if not sport or sport in cur:
            continue
        cur[sport] = {k: s.get(k) for k in ("ftp", "indoor_ftp", "lthr", "max_hr", "threshold_pace") if s.get(k)}
    if prof.get("icu_weight"):
        cur["weight_kg"] = prof["icu_weight"]
    rows = hist.setdefault(athlete, [])
    # the daily resting-HR reading is not a threshold: it used to be stored here and made a new entry every day
    rows[:] = compact(
        [{"date": r["date"], "settings": {k: v for k, v in r["settings"].items() if k != "resting_hr"}} for r in rows]
    )
    last = rows[-1]["settings"] if rows else None
    if cur and cur != last:
        rows.append({"date": date.today().isoformat(), "settings": cur})
    return cur


def planned_rows(events):
    """Intervals.icu calendar events -> planned workouts (the coach's TrainingPeaks plan when that connection is on)."""
    out = []
    for e in events or []:
        if e.get("category") != "WORKOUT":
            continue
        out.append(
            {
                "date": (e.get("start_date_local") or "")[:10],
                "sport": SPORT.get(e.get("type"), (e.get("type") or "other").lower()),
                "title": e.get("name") or "Treino",
                "description": (e.get("description") or "").strip()[:2000],
                "duration_s": e.get("moving_time") or e.get("planned_duration") or None,
                "load": e.get("icu_training_load") or e.get("load_target") or None,
            }
        )
    return sorted(out, key=lambda r: r["date"])


def sync_planned(athlete, key, aid, planned):
    end = date.today()
    ev = get(
        key,
        f"/athlete/{aid}/events",
        {"oldest": (end - timedelta(days=1)).isoformat(), "newest": (end + timedelta(days=7)).isoformat()},
    )
    rows = [dict(r, source="intervals") for r in planned_rows(ev)]
    cur = planned.get(athlete) or []
    # only a real Intervals.icu plan replaces what is there; an empty calendar never wipes the TrainingPeaks or WKO plan
    if rows and all(r.get("source") in (None, "intervals") for r in cur if r.get("source") != "wko"):
        planned[athlete] = rows
    elif not rows and cur and all(r.get("source") == "intervals" for r in cur):
        planned.pop(athlete)  # the coach removed the Intervals.icu plan
    return len(rows)


WELLNESS_DAYS = 90


def wellness_rows(rows):
    """Intervals.icu wellness -> health.py rows (sleep hours, resting HR, overnight rMSSD, sleep score; SDNN when given)."""
    out = []
    for r in rows or []:
        out.append(
            {
                "date": r.get("id"),
                "sleep_h": r["sleepSecs"] / 3600 if r.get("sleepSecs") else None,
                "rhr": r.get("restingHR"),
                "hrv_rmssd": r.get("hrv"),
                "hrv": r.get("hrvSDNN"),
                "sleep_score": r.get("sleepScore"),
            }
        )
    return out


def sync_wellness(athlete, key, aid):
    """Last WELLNESS_DAYS of sleep, resting HR and HRV into the app database (Garmin and other watches synced to Intervals)."""
    import health

    end = date.today()
    rows = get(
        key, f"/athlete/{aid}/wellness", {"oldest": (end - timedelta(days=WELLNESS_DAYS)).isoformat(), "newest": end.isoformat()}
    )
    return health.save(athlete, wellness_rows(rows), source="intervals")


def main(only=None):
    conf = configured()
    if only:
        conf = {a: v for a, v in conf.items() if a in only}
    if not conf:
        print("intervals: no athlete configured (TRIAPP_INTERVALS_KEY_<Athlete>)")
        return 0
    os.makedirs(OUT, exist_ok=True)
    state, hist, names, planned = load(STATE, {}), load(THRESH, {}), load(NAMES, {}), load(PLANNED, {})
    failed = 0
    for athlete, (key, aid) in conf.items():
        try:
            new, skipped, n = sync_activities(athlete, key, aid, state, names)
            th = sync_thresholds(athlete, key, aid, hist)
            well = sync_wellness(athlete, key, aid)
            plan = sync_planned(athlete, key, aid, planned)
            print(
                f"intervals {athlete}: {n} activities listed, {new} new files, {skipped} without FIT; thresholds {th}; "
                f"{well} wellness days; {plan} planned workouts"
            )
        except urllib.error.HTTPError as e:
            failed += 1
            print(f"intervals {athlete}: HTTP {e.code} ({'chave ou id inválido' if e.code in (401, 403) else e.reason})")
        except Exception as e:
            failed += 1
            print(f"intervals {athlete}: {type(e).__name__}: {e}")
    with open(STATE, "w") as f:
        json.dump(state, f, indent=1)
    with open(THRESH, "w") as f:
        json.dump(hist, f, indent=1, ensure_ascii=False)
    with open(NAMES, "w") as f:
        json.dump(names, f, ensure_ascii=False)
    with open(PLANNED, "w") as f:
        json.dump(planned, f, ensure_ascii=False, indent=1)
    return 0  # never block the pipeline: a failed athlete keeps the data already on disk


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:] or None))

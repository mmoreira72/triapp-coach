"""Pipeline step: the coach's plan from each athlete's TrainingPeaks calendar feed (iCal, TrainingPeaks Premium).

TrainingPeaks has no open API, but every Premium athlete has a private calendar link (Settings > Account > Calendar,
webcal://www.trainingpeaks.com/ical/<id>.ics): 5 days back and 14 ahead, each workout with its title, sport, the coach's
description, planned time and distance. It can lag behind TrainingPeaks by up to 24 hours.

Per athlete: TRIAPP_TP_ICAL_<Athlete>=<that link> in .env. The plan goes into <OUT>/data/intervals_planned.json (the same
file the Intervals.icu calendar fills), replacing it for that athlete: yesterday to two weeks ahead.

Usage: python tp_calendar.py [Athlete ...]
"""

import os, sys, json, re, urllib.request
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from athletes import ROOT

OUT = os.path.join(os.environ.get("TRIAPP_OUT", os.path.join(ROOT, "analysis")), "data")
PLANNED = os.path.join(OUT, "intervals_planned.json")
SPORT = {"swim": "swim", "bike": "bike", "run": "run", "strength": "strength", "mtb": "bike", "brick": "other"}
SKIP = {"day off", "note", "event", "goals", "metrics"}


def configured():
    """{athlete: feed URL}: the links athletes saved in the app (Ligações), then TRIAPP_TP_ICAL_<Athlete> from .env."""
    import userdata

    saved = {a: i["tp_ical"].replace("webcal://", "https://", 1) for a, i in userdata.integrations().items() if i.get("tp_ical")}
    return {**env_configured(), **saved}


def env_configured():
    p = "TRIAPP_TP_ICAL_"
    return {
        k[len(p) :].replace("_", " "): v.replace("webcal://", "https://", 1)
        for k, v in os.environ.items()
        if k.startswith(p) and v
    }


def unescape(v):
    return v.replace("\\n", "\n").replace("\\N", "\n").replace("\\,", ",").replace("\\;", ";").replace("\\\\", "\\")


def events(ics):
    """VEVENT blocks of an iCal text as {property: value} (folded lines joined, values unescaped)."""
    text = re.sub(r"\r?\n[ \t]", "", ics)
    out = []
    for block in text.split("BEGIN:VEVENT")[1:]:
        e = {}
        for line in block.split("END:VEVENT")[0].splitlines():
            if ":" in line:
                k, v = line.split(":", 1)
                e[k.split(";")[0].upper()] = unescape(v)
        out.append(e)
    return out


def hms(v):
    """'1:15' or '0:35:00' -> seconds."""
    parts = [int(x) for x in v.strip().split(":") if x.strip().isdigit()]
    if not parts:
        return None
    if len(parts) == 2:
        return parts[0] * 3600 + parts[1] * 60
    return parts[0] * 3600 + parts[1] * 60 + parts[2]


def planned_rows(ics, today=None):
    """TrainingPeaks feed -> planned workouts of the last 7 days and ahead (date, sport, title, the coach's description, duration,
    and whether TrainingPeaks shows it as done: a completed workout carries its "Actual Time")."""
    since = ((today or date.today()) - timedelta(days=7)).isoformat()
    out = []
    for e in events(ics):
        d = e.get("DTSTART", "")[:8]
        if len(d) != 8 or not d.isdigit():
            continue
        day = f"{d[:4]}-{d[4:6]}-{d[6:]}"
        summary = e.get("SUMMARY", "")
        kind, _, title = summary.partition(": ") if ": " in summary else ("", "", summary)
        desc, wtype, planned_s, km, done = [], kind, None, None, False
        for line in e.get("DESCRIPTION", "").split("\n"):
            m = re.match(
                r"\s*(Workout type|Planned Time|Distance Planned|Actual \w+|Speed|Pace|Power|Heart Rate|TSS|IF)\s*:\s*(.*)", line
            )
            if not m:
                desc.append(line)
            elif m.group(1).startswith("Actual"):
                done = True
            elif m.group(1) == "Workout type":
                wtype = m.group(2).strip()
            elif m.group(1) == "Planned Time":
                planned_s = hms(m.group(2))
            elif m.group(1) == "Distance Planned":
                n = re.match(r"([\d.]+)\s*(km|m|mi)?", m.group(2).strip())
                if n:
                    km = float(n.group(1)) / (1000 if n.group(2) == "m" else 1) * (1.609 if n.group(2) == "mi" else 1)
        if day < since or wtype.lower() in SKIP:
            continue
        if not planned_s and not "\n".join(desc).strip():
            continue  # an unplanned workout TrainingPeaks got from the watch ("Cycling"): done, not part of the coach's plan
        out.append(
            {
                "date": day,
                "sport": SPORT.get(wtype.lower(), "other"),
                "title": title.strip() or summary or "Treino",
                "description": "\n".join(desc).strip()[:2000],
                "duration_s": planned_s,
                "distance_km": round(km, 2) if km else None,
                "load": None,
                "completed": done,
                "source": "tp",
            }
        )
    return sorted(out, key=lambda r: r["date"])


def main(only=None):
    conf = {a: u for a, u in configured().items() if not only or a in only}
    if not conf:
        print("tp calendar: no athlete configured (TRIAPP_TP_ICAL_<Athlete>)")
        return 0
    try:
        planned = json.load(open(PLANNED))
    except Exception:
        planned = {}
    for athlete, url in conf.items():
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "triapp/1.0 (+https://tri.laloop.ai)"})
            ics = urllib.request.urlopen(req, timeout=60).read().decode("utf-8", "replace")
            planned[athlete] = planned_rows(ics)
            print(f"tp calendar {athlete}: {len(planned[athlete])} workouts (last 7 days and ahead)")
        except Exception as e:  # keep what is already there
            print(f"tp calendar {athlete}: {type(e).__name__}")
    os.makedirs(OUT, exist_ok=True)
    with open(PLANNED, "w") as f:
        json.dump(planned, f, ensure_ascii=False, indent=1)
    keep_history({a: rows for a, rows in planned.items() if a in conf})
    return 0


HISTORY = os.path.join(OUT, "plan_history.json")


def keep_history(planned, path=None):
    """The feed only carries about two weeks: every workout it shows is kept in plan_history.json ({athlete: {"date|title": row}},
    the latest reading wins), so planned-vs-done keeps the TrainingPeaks plan after the coach's WKO export stops."""
    path = path or HISTORY
    try:
        hist = json.load(open(path))
    except Exception:
        hist = {}
    for athlete, rows in planned.items():
        h = hist.setdefault(athlete, {})
        for r in rows:
            if r.get("source") == "tp":
                h[f"{r['date']}|{r['title']}"] = r
    with open(path, "w") as f:
        json.dump(hist, f, ensure_ascii=False, indent=0)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:] or None))

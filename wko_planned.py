"""Pipeline step: the coach's plan from the WKO5 libraries, for athletes without a TrainingPeaks calendar link.

The coach's WKO5 libraries (copied nightly to the Drive by coach-agent/) carry the TrainingPeaks plan about two weeks ahead,
with the coach's description, planned duration and TSS, and whether each workout was done. This needs no TrainingPeaks
Premium on the athlete's side. For each athlete whose plan file entry is empty (no TrainingPeaks calendar link, no Intervals.icu
workouts), the last 7 days and everything ahead go into <OUT>/data/intervals_planned.json, in the same shape tp_calendar.py writes.

Usage: python wko_planned.py
"""

import os, sys, json
from datetime import date, timedelta
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from athletes import ROOT

OUT = os.path.join(os.environ.get("TRIAPP_OUT", os.path.join(ROOT, "analysis")), "data")
PLANNED = os.path.join(OUT, "intervals_planned.json")


def rows_from(acts, athlete, today):
    """Planned workouts of one athlete from activities_all.csv (WKO rows with a plan), last 7 days and ahead."""
    since = (date.fromisoformat(today) - timedelta(days=7)).isoformat()
    g = acts[(acts.athlete == athlete) & acts.source.astype(str).str.lower().str.contains("wko")]
    # the library knows what was done only up to its last sync: a planned workout after that is "unknown", never "missed"
    done = g[g.completed.fillna(False).astype(bool)]
    known_until = str(done.date.max())[:10] if len(done) else ""
    has_plan = g.plan_desc.notna() | g.planned_duration_s.fillna(0).gt(0) | g.planned_tss.fillna(0).gt(0)
    g = g[has_plan & (g.date.astype(str).str[:10] >= since)].sort_values("date")
    out = []
    for _, r in g.iterrows():
        dur = r.get("planned_duration_s")
        out.append(
            {
                "date": str(r.date)[:10],
                "sport": r.sport_n if isinstance(r.sport_n, str) else "other",
                "title": (r.title_n if isinstance(r.title_n, str) and r.title_n.strip() else "Treino").strip(),
                "description": (r.plan_desc if isinstance(r.plan_desc, str) else "").strip()[:2000],
                "duration_s": int(dur) if pd.notna(dur) and dur > 0 else None,
                "load": round(float(r.planned_tss)) if pd.notna(r.get("planned_tss")) and r.planned_tss > 0 else None,
                "completed": (bool(r.completed) if pd.notna(r.completed) else False) if str(r.date)[:10] <= known_until else None,
                "source": "wko",
            }
        )
    return out


def main(today=None):
    today = today or date.today().isoformat()
    acts = pd.read_csv(os.path.join(OUT, "activities_all.csv"), low_memory=False)
    try:
        planned = json.load(open(PLANNED))
    except Exception:
        planned = {}
    filled = []
    for athlete in sorted(acts.athlete.dropna().unique()):
        if planned.get(athlete) and not all(p.get("source") == "wko" for p in planned[athlete]):
            continue  # a TrainingPeaks calendar link or Intervals.icu workouts already give this athlete's plan
        rows = rows_from(acts, athlete, today)
        if rows:
            planned[athlete] = rows
            filled.append(f"{athlete} {len(rows)}")
        elif planned.get(athlete):
            planned.pop(athlete)  # an old WKO plan that no longer covers the last week
    with open(PLANNED, "w") as f:
        json.dump(planned, f, ensure_ascii=False, indent=1)
    print(f"wko planned: {', '.join(filled) or 'none'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

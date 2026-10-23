"""Daily recovery data: sleep hours, resting heart rate and HRV, one row per athlete and day (table health_daily).

Two sources fill it:
  apple_health  the native app's HealthKit plugin (Apple Watch), last 30 days on each open; HRV is SDNN (column hrv)
  intervals     the daily pipeline, from Intervals.icu wellness (Garmin and other watches); HRV is the overnight rMSSD
                (column hrv_rmssd) and there is a sleep score
SDNN and rMSSD are different measures and are never mixed. A resend fills in the day without erasing other values.
"""

import os, sqlite3
from datetime import date, timedelta
from pathlib import Path

# plausible ranges; values outside are dropped
FIELDS = {"sleep_h": (0, 16), "rhr": (25, 120), "hrv": (5, 300), "hrv_rmssd": (5, 300), "sleep_score": (0, 101)}


def _db():
    out = Path(os.environ.get("TRIAPP_OUT", Path(__file__).resolve().parent / "out"))
    con = sqlite3.connect(os.environ.get("TRIAPP_DB", str(out / "app.sqlite")))
    con.row_factory = sqlite3.Row
    con.execute("""CREATE TABLE IF NOT EXISTS health_daily (athlete TEXT NOT NULL, date TEXT NOT NULL, sleep_h REAL, rhr REAL,
                   hrv REAL, source TEXT, updated_at TEXT DEFAULT CURRENT_TIMESTAMP, PRIMARY KEY (athlete, date))""")
    have = {r[1] for r in con.execute("PRAGMA table_info(health_daily)")}
    for col in ("hrv_rmssd", "sleep_score"):  # added with the Intervals.icu source
        if col not in have:
            con.execute(f"ALTER TABLE health_daily ADD COLUMN {col} REAL")
    return con


def clean(row):
    """{date, sleep_h, rhr, hrv} with implausible values removed, or None for a row without a valid date or any value."""
    try:
        d = date.fromisoformat(str(row.get("date", ""))[:10])
    except ValueError:
        return None
    if d > date.today() + timedelta(days=1):
        return None
    out = {"date": d.isoformat()}
    for k, (lo, hi) in FIELDS.items():
        v = row.get(k)
        out[k] = float(v) if isinstance(v, (int, float)) and lo < v < hi else None
    return out if any(out[k] is not None for k in FIELDS) else None


def save(athlete, rows, source="apple_health"):
    good = [r for r in (clean(x) for x in (rows or [])[:400]) if r]
    cols = list(FIELDS)
    con = _db()
    con.executemany(
        f"""INSERT INTO health_daily(athlete,date,{",".join(cols)},source) VALUES(?,?,{",".join("?" * len(cols))},?)
           ON CONFLICT(athlete,date) DO UPDATE SET {", ".join(f"{c}=COALESCE(excluded.{c}, {c})" for c in cols)},
           source=excluded.source, updated_at=CURRENT_TIMESTAMP""",
        [(athlete, r["date"], *[r[c] for c in cols], source) for r in good],
    )
    con.commit()
    con.close()
    return len(good)


def recent(athlete, days=90):
    since = (date.today() - timedelta(days=days)).isoformat()
    try:
        con = _db()
        rows = [
            dict(r)
            for r in con.execute(
                "SELECT date, sleep_h, rhr, hrv, hrv_rmssd, sleep_score, source FROM health_daily WHERE athlete=? AND date>=? ORDER BY date",
                (athlete, since),
            )
        ]
        con.close()
        return rows
    except sqlite3.Error:
        return []

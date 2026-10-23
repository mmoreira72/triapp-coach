"""What athletes and coaches enter in the app, shared by the app and the pipeline (app database, TRIAPP_DB).

integrations    each athlete's own links: TrainingPeaks calendar (the plan) and Intervals.icu key (what was done)
race_marks      sessions marked as races (besides the multisport files found automatically)
race_reports    the athlete's and the coach's report of each race
missed_reasons  why a planned session was skipped (asked when two are missed)
session_comments comments on a session by the athlete or the coach, and the assistant's reply to the athlete's (reply_to)
thresholds      FTP and LTHR (run, bike) set in the app from a date on (a projection applied, a test); they win over every other source
race_nutrition  the coach's numbers and advice for an athlete's fuelling plan in a target race (nutrition.py fills the rest)
race_plan       the coach's choices for an athlete in a target race: entered or not, and the bike IF he recommends
"""

import os, sqlite3, urllib.request, json
from datetime import date, timedelta
from pathlib import Path

REASONS = {
    "doenca": "Doente",
    "lesao": "Lesão ou dor",
    "trabalho": "Trabalho",
    "familia": "Família",
    "cansaco": "Cansaço",
    "viagem": "Viagem",
    "tempo": "Meteorologia",
    "outro": "Outro",
}


def _db():
    out = Path(os.environ.get("TRIAPP_OUT", Path(__file__).resolve().parent / "out"))
    con = sqlite3.connect(os.environ.get("TRIAPP_DB", str(out / "app.sqlite")))
    con.row_factory = sqlite3.Row
    con.executescript(
        """CREATE TABLE IF NOT EXISTS integrations (athlete TEXT PRIMARY KEY, tp_ical TEXT, intervals_key TEXT, intervals_id TEXT,
                         updated_by TEXT, updated_at TEXT DEFAULT CURRENT_TIMESTAMP);
           CREATE TABLE IF NOT EXISTS race_marks (athlete TEXT NOT NULL, session_key TEXT NOT NULL, date TEXT, title TEXT,
                         created_by TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP, PRIMARY KEY (athlete, session_key));
           CREATE TABLE IF NOT EXISTS race_reports (athlete TEXT NOT NULL, race_date TEXT NOT NULL, role TEXT NOT NULL, text TEXT,
                         author TEXT, updated_at TEXT DEFAULT CURRENT_TIMESTAMP, PRIMARY KEY (athlete, race_date, role));
           CREATE TABLE IF NOT EXISTS session_comments (id INTEGER PRIMARY KEY, athlete TEXT NOT NULL, session_key TEXT NOT NULL,
                         day TEXT, author TEXT, role TEXT, text TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP);
           CREATE TABLE IF NOT EXISTS missed_reasons (athlete TEXT NOT NULL, date TEXT NOT NULL, title TEXT NOT NULL, reason TEXT,
                         note TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP, PRIMARY KEY (athlete, date, title));
           CREATE TABLE IF NOT EXISTS thresholds (id INTEGER PRIMARY KEY, athlete TEXT NOT NULL, from_date TEXT NOT NULL, ftp REAL,
                         lthr_run REAL, lthr_bike REAL, source TEXT, set_by TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP);
           CREATE TABLE IF NOT EXISTS race_plan (athlete TEXT NOT NULL, race TEXT NOT NULL, entered INTEGER, bike_if REAL,
                         updated_by TEXT, updated_at TEXT DEFAULT CURRENT_TIMESTAMP, PRIMARY KEY (athlete, race));
           CREATE TABLE IF NOT EXISTS race_nutrition (athlete TEXT NOT NULL, race TEXT NOT NULL, data TEXT, updated_by TEXT,
                         updated_at TEXT DEFAULT CURRENT_TIMESTAMP, PRIMARY KEY (athlete, race));"""
    )
    if "reply_to" not in {r[1] for r in con.execute("PRAGMA table_info(session_comments)")}:
        con.execute("ALTER TABLE session_comments ADD COLUMN reply_to INTEGER")
    return con


def _rows(sql, args=()):
    try:
        con = _db()
        out = [dict(r) for r in con.execute(sql, args)]
        con.close()
        return out
    except sqlite3.Error:
        return []


def _write(sql, args):
    con = _db()
    cur = con.execute(sql, args)
    con.commit()
    con.close()
    return cur


# ------------------------------------------------------------------ integrations
def integrations():
    """{athlete: {tp_ical, intervals_key, intervals_id}} as saved by the athletes (the .env keeps working alongside)."""
    return {r["athlete"]: r for r in _rows("SELECT * FROM integrations")}


def save_integrations(athlete, who, tp_ical=None, intervals_key=None, intervals_id=None):
    """Save what was sent; an empty string clears a field, None keeps it."""
    cur = integrations().get(athlete, {})
    val = lambda new, old: (old if new is None else (new.strip() or None))
    tp = val(tp_ical, cur.get("tp_ical"))
    if tp and (not tp.startswith(("webcal://", "https://")) or "trainingpeaks.com" not in tp):
        raise ValueError("o link do calendário tem de ser o webcal:// do TrainingPeaks")
    iid = val(intervals_id, cur.get("intervals_id"))
    if iid and not iid.lstrip("i").isdigit():
        raise ValueError("o ID do Intervals.icu é o i seguido de números (por exemplo i123456)")
    _write(
        """INSERT INTO integrations(athlete,tp_ical,intervals_key,intervals_id,updated_by) VALUES(?,?,?,?,?)
           ON CONFLICT(athlete) DO UPDATE SET tp_ical=excluded.tp_ical, intervals_key=excluded.intervals_key,
           intervals_id=excluded.intervals_id, updated_by=excluded.updated_by, updated_at=CURRENT_TIMESTAMP""",
        (athlete, tp, val(intervals_key, cur.get("intervals_key")), iid, who),
    )


def integration_status(athlete):
    """What the athlete page shows: which links are set (never the secrets themselves)."""
    i = integrations().get(athlete, {})
    env = lambda p: bool(os.environ.get(p + athlete.replace(" ", "_")))
    return {
        "tp": bool(i.get("tp_ical")) or env("TRIAPP_TP_ICAL_"),
        "intervals": bool(i.get("intervals_key") and i.get("intervals_id")) or env("TRIAPP_INTERVALS_KEY_"),
        "intervals_id": i.get("intervals_id"),
        "updated_at": i.get("updated_at"),
    }


def test_tp(url):
    """Number of workouts in a TrainingPeaks calendar link (raises when it cannot be read)."""
    req = urllib.request.Request(
        url.replace("webcal://", "https://", 1), headers={"User-Agent": "triapp/1.0 (+https://tri.laloop.ai)"}
    )
    text = urllib.request.urlopen(req, timeout=30).read().decode("utf-8", "replace")
    if "BEGIN:VCALENDAR" not in text:
        raise ValueError("o link não devolveu um calendário")
    return text.count("BEGIN:VEVENT")


def delete_athlete_entries(athlete, email):
    """Everything the athlete linked or wrote in the app (account deletion). The coach's training exports stay with the coach."""
    con = _db()
    for sql, args in (
        ("DELETE FROM integrations WHERE athlete=?", (athlete,)),
        ("DELETE FROM missed_reasons WHERE athlete=?", (athlete,)),
        ("DELETE FROM session_comments WHERE athlete=? AND role IN ('athlete','assistant')", (athlete,)),
        ("DELETE FROM race_reports WHERE athlete=? AND role='athlete'", (athlete,)),
        ("DELETE FROM race_marks WHERE athlete=?", (athlete,)),
        ("DELETE FROM push_devices WHERE email=?", (email,)),
        ("DELETE FROM health_daily WHERE athlete=?", (athlete,)),
        ("DELETE FROM athlete_voice WHERE athlete=?", (athlete,)),
        ("DELETE FROM athlete_context WHERE athlete=?", (athlete,)),
        ("DELETE FROM finding_ack WHERE athlete=?", (athlete,)),
        ("DELETE FROM qa_log WHERE athlete=?", (athlete,)),
    ):
        try:
            con.execute(sql, args)
        except sqlite3.OperationalError:
            pass  # a table that was never created holds nothing
    con.commit()
    con.close()


# ------------------------------------------------------------------ thresholds set in the app
def set_thresholds(athlete, from_date, who, source, ftp=None, lthr_run=None, lthr_bike=None):
    """New FTP / LTHR from `from_date` on; a value left out keeps the one in use (each key is read on its own)."""
    date.fromisoformat(from_date)
    for v, lo, hi, what in ((ftp, 60, 600, "FTP"), (lthr_run, 100, 210, "LTHR corrida"), (lthr_bike, 100, 210, "LTHR bike")):
        if v is not None and not lo <= float(v) <= hi:
            raise ValueError(f"{what} fora do intervalo ({lo} a {hi})")
    if ftp is None and lthr_run is None and lthr_bike is None:
        raise ValueError("nenhum valor")
    _write(
        "INSERT INTO thresholds(athlete,from_date,ftp,lthr_run,lthr_bike,source,set_by) VALUES(?,?,?,?,?,?,?)",
        (athlete, from_date, ftp, lthr_run, lthr_bike, (source or "")[:200], who),
    )


def thresholds(athlete=None):
    """Threshold entries, oldest first (by date, then by when they were set)."""
    q = "SELECT * FROM thresholds" + (" WHERE athlete=?" if athlete else "") + " ORDER BY from_date, id"
    return _rows(q, (athlete,) if athlete else ())


# ------------------------------------------------------------------ race plan (entered, bike IF chosen by the coach)
def race_plans(athlete=None):
    """{athlete: {race: {entered, bike_if, updated_by, updated_at}}}"""
    out = {}
    q = "SELECT * FROM race_plan" + (" WHERE athlete=?" if athlete else "")
    for r in _rows(q, (athlete,) if athlete else ()):
        out.setdefault(r["athlete"], {})[r["race"]] = {k: r[k] for k in ("entered", "bike_if", "updated_by", "updated_at")}
    return out


def save_race_plan(athlete, race, who, entered=None, bike_if=None, clear_if=False):
    """Set what was sent (None keeps it); clear_if goes back to the model's IF."""
    cur = race_plans(athlete).get(athlete, {}).get(race, {})
    if bike_if is not None and not 0.5 <= float(bike_if) <= 1.05:
        raise ValueError("IF fora do intervalo (0,50 a 1,05)")
    ent = cur.get("entered") if entered is None else int(bool(entered))
    bif = None if clear_if else (cur.get("bike_if") if bike_if is None else round(float(bike_if), 2))
    _write(
        """INSERT INTO race_plan(athlete,race,entered,bike_if,updated_by) VALUES(?,?,?,?,?)
           ON CONFLICT(athlete,race) DO UPDATE SET entered=excluded.entered, bike_if=excluded.bike_if,
           updated_by=excluded.updated_by, updated_at=CURRENT_TIMESTAMP""",
        (athlete, race, ent, bif, who),
    )


# ------------------------------------------------------------------ race fuelling (coach's numbers and advice)
NUTRITION_KEYS = {
    "carbs_bike": (0, 150),
    "carbs_run": (0, 120),
    "fluid_ml": (0, 1500),
    "sodium_mg": (0, 2500),
    "feed_min": (10, 60),
    "gel_g": (10, 60),
    "drink_g_h": (0, 120),
    "start_min": (0, 180),
    "bottle_ml": (300, 1200),
    "cages": (1, 4),
}


def race_nutrition(athlete):
    """{race slug: {carbs_bike, ..., caffeine, note, updated_by, updated_at}}"""
    import json

    out = {}
    for r in _rows("SELECT * FROM race_nutrition WHERE athlete=?", (athlete,)):
        try:
            d = json.loads(r["data"] or "{}")
        except ValueError:
            d = {}
        out[r["race"]] = {**d, "updated_by": r["updated_by"], "updated_at": r["updated_at"]}
    return out


def save_race_nutrition(athlete, race, data, who):
    """The coach's numbers (empty = the plan's default) and note for one race."""
    import json

    clean = {}
    for k, (lo, hi) in NUTRITION_KEYS.items():
        v = data.get(k)
        if v in (None, ""):
            continue
        v = float(v)
        if not lo <= v <= hi:
            raise ValueError(f"{k} fora do intervalo ({lo} a {hi})")
        clean[k] = v
    for k in ("caffeine", "t1"):
        if k in data:
            clean[k] = bool(data[k])
    clean["note"] = (data.get("note") or "").strip()[:3000]
    if isinstance(data.get("from_note"), dict):  # numbers the box read in the note (nutrition.from_note), already checked
        clean["from_note"] = data["from_note"]
        clean["unread"] = list(data.get("unread") or [])[:5]
    _write(
        """INSERT INTO race_nutrition(athlete,race,data,updated_by) VALUES(?,?,?,?)
           ON CONFLICT(athlete,race) DO UPDATE SET data=excluded.data, updated_by=excluded.updated_by, updated_at=CURRENT_TIMESTAMP""",
        (athlete, race, json.dumps(clean, ensure_ascii=False), who),
    )


# ------------------------------------------------------------------ races
def race_marks(athlete=None):
    if athlete:
        return _rows("SELECT * FROM race_marks WHERE athlete=? ORDER BY date", (athlete,))
    return _rows("SELECT * FROM race_marks ORDER BY date")


def mark_race(athlete, session_key, day, title, who, on=True):
    if on:
        _write(
            "INSERT OR REPLACE INTO race_marks(athlete,session_key,date,title,created_by) VALUES(?,?,?,?,?)",
            (athlete, session_key, day, (title or "Prova")[:200], who),
        )
    else:
        _write("DELETE FROM race_marks WHERE athlete=? AND session_key=?", (athlete, session_key))


def race_reports(athlete):
    """{race_date: {"athlete": {text, author, updated_at}, "coach": {...}}}"""
    out = {}
    for r in _rows("SELECT * FROM race_reports WHERE athlete=?", (athlete,)):
        out.setdefault(r["race_date"], {})[r["role"]] = {"text": r["text"], "author": r["author"], "updated_at": r["updated_at"]}
    return out


def save_race_report(athlete, race_date, role, text, author):
    if role not in ("athlete", "coach"):
        raise ValueError("papel inválido")
    date.fromisoformat(race_date[:10])
    _write(
        """INSERT INTO race_reports(athlete,race_date,role,text,author) VALUES(?,?,?,?,?)
           ON CONFLICT(athlete,race_date,role) DO UPDATE SET text=excluded.text, author=excluded.author, updated_at=CURRENT_TIMESTAMP""",
        (athlete, race_date[:10], role, (text or "").strip()[:5000], author),
    )


# ------------------------------------------------------------------ comments on sessions
def add_comment(athlete, session_key, day, text, author, role):
    text = (text or "").strip()
    if not text or not session_key:
        raise ValueError("comentário vazio")
    return _write(
        "INSERT INTO session_comments(athlete,session_key,day,author,role,text) VALUES(?,?,?,?,?,?)",
        (athlete, session_key, (day or "")[:10], author, role, text[:1000]),
    ).lastrowid


ASSISTANT = "Tri Squad"


def add_reply(athlete, cid, text):
    """The assistant's reply to comment `cid`, on the same session. None when the comment is gone (deleted meanwhile)."""
    c = comment(athlete, cid)
    if not c or not (text or "").strip():
        return None
    return _write(
        "INSERT INTO session_comments(athlete,session_key,day,author,role,text,reply_to) VALUES(?,?,?,?,?,?,?)",
        (athlete, c["session_key"], c["day"], ASSISTANT, "assistant", text.strip()[:1500], cid),
    ).lastrowid


def comment(athlete, cid):
    r = _rows("SELECT * FROM session_comments WHERE id=? AND athlete=?", (cid, athlete))
    return r[0] if r else None


def delete_comment(athlete, cid, author):
    """Only the author removes a comment; the assistant's reply to it goes with it."""
    if _write("DELETE FROM session_comments WHERE id=? AND athlete=? AND author=?", (cid, athlete, author)).rowcount:
        _write("DELETE FROM session_comments WHERE reply_to=? AND athlete=?", (cid, athlete))


def comments(athlete, since=None, replies=True):
    """{session_key: [{id, author, role, text, created_at, day, reply_to}]} oldest first. replies=False leaves out the
    assistant's replies (what people wrote only, for the model's own input)."""
    q, args = "SELECT * FROM session_comments WHERE athlete=?" + ("" if replies else " AND role!='assistant'"), [athlete]
    if since:
        q, args = q + " AND day>=?", args + [since]
    out = {}
    for r in _rows(q + " ORDER BY id", tuple(args)):
        out.setdefault(r["session_key"], []).append(r)
    return out


# ------------------------------------------------------------------ missed sessions
def missed_sessions(planned, today=None, days=7):
    """Planned workouts of the last `days` days (before today) that TrainingPeaks does not show as done."""
    today = today or date.today().isoformat()
    since = (date.fromisoformat(today) - timedelta(days=days)).isoformat()
    return [p for p in planned if since <= p["date"] < today and p.get("completed") is False]


def missed_reasons(athlete):
    return {(r["date"], r["title"]): r for r in _rows("SELECT * FROM missed_reasons WHERE athlete=?", (athlete,))}


def save_missed_reason(athlete, day, title, reason, note=""):
    if reason not in REASONS:
        raise ValueError("motivo inválido")
    date.fromisoformat(day)
    _write(
        """INSERT INTO missed_reasons(athlete,date,title,reason,note) VALUES(?,?,?,?,?)
           ON CONFLICT(athlete,date,title) DO UPDATE SET reason=excluded.reason, note=excluded.note""",
        (athlete, day, title[:200], reason, (note or "").strip()[:500]),
    )


def missed_with_reasons(athlete, planned, today=None):
    """The missed sessions of the last week, each with the reason the athlete gave (or None)."""
    given = missed_reasons(athlete)
    return [{**p, "reason": given.get((p["date"], p["title"]))} for p in missed_sessions(planned, today)]

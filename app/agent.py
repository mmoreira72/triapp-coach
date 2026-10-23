"""The coach's Mac agent (coach-agent/) talking to the box: the coach asks for a WKO5 sync on the admin page, the agent picks
the request up on its next poll (every few minutes), runs WKO5, copies the libraries to the Drive and reports back; the box
then pulls the Drive and runs the pipeline.

The agent authenticates with a token the box puts in the zip the coach downloads (one token; a new download replaces it).
"""

import json, secrets, sqlite3, threading, time
from contextlib import contextmanager
from datetime import datetime, timezone
from . import config

ONLINE_MIN = 12  # the agent polls every 5 min: not heard of for longer means the Mac is off or asleep
DRIVE_WAIT_S = 240  # Google Drive needs a few minutes to upload what the agent copied before the box pulls it


@contextmanager
def db():
    con = sqlite3.connect(config.DB)
    con.row_factory = sqlite3.Row
    con.execute(
        """CREATE TABLE IF NOT EXISTS coach_agent (id INTEGER PRIMARY KEY CHECK (id = 1), token TEXT, seen_at TEXT, version TEXT,
           requested_at TEXT, requested_by TEXT, picked_at TEXT, finished_at TEXT, result TEXT)"""
    )
    con.execute("INSERT OR IGNORE INTO coach_agent(id) VALUES(1)")
    try:
        yield con
        con.commit()
    finally:
        con.close()


now = lambda: datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def new_token():
    """A fresh token for a new download of the agent (the old one stops working)."""
    t = secrets.token_urlsafe(24)
    with db() as con:
        con.execute("UPDATE coach_agent SET token=? WHERE id=1", (t,))
    return t


def check(token):
    with db() as con:
        row = con.execute("SELECT token FROM coach_agent WHERE id=1").fetchone()
    return bool(token) and bool(row["token"]) and secrets.compare_digest(token, row["token"])


def request(who):
    with db() as con:
        con.execute(
            "UPDATE coach_agent SET requested_at=?, requested_by=?, picked_at=NULL, finished_at=NULL WHERE id=1", (now(), who)
        )


def poll(version=""):
    """The agent asks whether to sync now; a pending request is handed over once."""
    with db() as con:
        r = con.execute("SELECT * FROM coach_agent WHERE id=1").fetchone()
        run = bool(r["requested_at"]) and not r["picked_at"]
        con.execute(
            "UPDATE coach_agent SET seen_at=?, version=?" + (", picked_at=?" if run else "") + " WHERE id=1",
            (now(), version[:40], now()) if run else (now(), version[:40]),
        )
    return run


def report(result, start_pipeline):
    """The agent's report after a sync (requested or the nightly one); the box pulls the Drive a few minutes later."""
    with db() as con:
        con.execute(
            "UPDATE coach_agent SET finished_at=?, seen_at=?, result=? WHERE id=1",
            (now(), now(), json.dumps(result, ensure_ascii=False)[:20000]),
        )
    if result.get("copied"):

        def later():
            time.sleep(DRIVE_WAIT_S)
            start_pipeline()

        threading.Thread(target=later, daemon=True).start()


def status():
    """For the admin page: online or not, the last request and the last report."""
    with db() as con:
        r = dict(con.execute("SELECT * FROM coach_agent WHERE id=1").fetchone())
    r.pop("token", None)
    try:
        r["result"] = json.loads(r["result"]) if r.get("result") else None
    except ValueError:
        r["result"] = None
    seen = r.get("seen_at")
    age = (
        (datetime.now(timezone.utc) - datetime.strptime(seen, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)).total_seconds()
        if seen
        else None
    )
    r["online"] = age is not None and age < ONLINE_MIN * 60
    r["seen_min"] = round(age / 60) if age is not None else None
    r["pending"] = bool(r.get("requested_at")) and not r.get("finished_at")
    return r

"""Drive sync (rclone) + analysis pipeline, run in a background thread with a lock and a status file."""

import json, os, subprocess, threading, time
from datetime import datetime
from . import config

_lock = threading.Lock()
STEPS = [
    "intervals_sync.py",
    "tp_calendar.py",
    "parse_fit.py",
    "parse_wko.py",
    "build_dataset.py",
    "wko_planned.py",
    "reconcile.py",
    "build_dashboard_data.py",
    "threshold_projection.py",
    "race_impact.py",
    "skills_engine.py",
    "session_notes.py",
    "morning_brief.py",
    "notify.py",
]
# a few times a day: new sessions from Intervals.icu and the TrainingPeaks calendar, load, notes and the brief, without the Drive
# copy, the race model or the skills (those run at night)
LIGHT_STEPS = [
    "intervals_sync.py",
    "tp_calendar.py",
    "parse_fit.py",
    "build_dataset.py",
    "wko_planned.py",
    "build_dashboard_data.py",
    "session_notes.py",
    "morning_brief.py",
    "notify.py",
]


def status():
    try:
        return json.loads(config.STATUS_JSON.read_text())
    except Exception:
        return {"state": "never-run"}


def _write(**kw):
    s = status()
    s.update(kw)
    config.STATUS_JSON.write_text(json.dumps(s, indent=1))


def log_tail(n=40):
    try:
        return config.LOG_FILE.read_text().splitlines()[-n:]
    except Exception:
        return []


def _run(cmd, log, cwd=None):
    log.write(f"\n$ {' '.join(cmd)}\n")
    log.flush()
    p = subprocess.run(cmd, cwd=cwd, stdout=log, stderr=subprocess.STDOUT, text=True)
    if p.returncode != 0:
        raise RuntimeError(f"{cmd[0]} {cmd[1] if len(cmd) > 1 else ''} failed with exit {p.returncode}")


def _light_job(who):
    env_log = config.OUT / "light_refresh.log"
    with open(env_log, "w") as log:
        log.write(f"light refresh by {who} at {datetime.now().isoformat(timespec='seconds')}\n")
        for step in LIGHT_STEPS:
            try:
                _run([config.PY, step], log, cwd=str(config.REPO_DIR))
            except Exception as e:  # one failing step (Intervals.icu down) must not stop the rest
                log.write(f"\nstep {step} failed: {e}\n")
    _write(light_last=datetime.now().isoformat(timespec="seconds"))


def start_light(who="schedule"):
    """The light refresh in the background (skipped while the full pipeline runs: they share the lock)."""
    if not _lock.acquire(blocking=False):
        return False

    def wrapper():
        try:
            os.environ.setdefault("TRIAPP_ROOT", str(config.ROOT))
            os.environ.setdefault("TRIAPP_OUT", str(config.OUT))
            _light_job(who)
        finally:
            _lock.release()

    threading.Thread(target=wrapper, daemon=True).start()
    return True


def start_light_schedule():
    """Light refreshes at TRIAPP_LIGHT_AT (comma-separated HH:MM, box local time; empty = off), each once a day. The 07:00 one
    runs before the morning push, so a night-time Intervals.icu outage does not leave yesterday's sessions out of the brief."""
    times = [t.strip() for t in os.environ.get("TRIAPP_LIGHT_AT", "07:00,13:00,20:00").split(",") if t.strip()]
    if not times:
        return
    marker = config.OUT / "light_last.txt"

    def loop():
        while True:
            now = datetime.now()
            try:
                done = set(marker.read_text().split())
            except Exception:
                done = set()
            for t in times:
                key = f"{now.date().isoformat()}T{t}"
                if now.strftime("%H:%M") >= t and key not in done and status().get("state") != "running":
                    if start_light(who=f"schedule {t}"):
                        done = {d for d in done if d.startswith(now.date().isoformat())} | {key}
                        marker.write_text(" ".join(sorted(done)))
                    break
            time.sleep(60)

    threading.Thread(target=loop, daemon=True).start()


def _job(skip_drive, who):
    config.OUT.mkdir(parents=True, exist_ok=True)
    (config.OUT / "data").mkdir(exist_ok=True)
    env = dict(os.environ, TRIAPP_ROOT=str(config.ROOT), TRIAPP_OUT=str(config.OUT))
    with open(config.LOG_FILE, "w") as log:
        try:
            _write(state="running", started=datetime.now().isoformat(timespec="seconds"), by=who, step="drive sync", error=None)
            if config.RCLONE_REMOTE and not skip_drive:
                config.ROOT.mkdir(parents=True, exist_ok=True)
                # copy, not sync: files removed from the Drive stay on the box (a partial re-upload must not wipe history);
                # an athlete is removed by deleting their folder on the box
                _run(
                    [
                        "rclone",
                        "copy",
                        config.RCLONE_REMOTE,
                        str(config.ROOT),
                        "--exclude",
                        ".DS_Store",
                        "--exclude",
                        "analysis/**",
                        "--fast-list",
                        "--transfers",
                        "8",
                        "--stats-one-line",
                        "-v",
                    ],
                    log,
                )
            for step in STEPS:
                _write(step=step)
                _run([config.PY, step], log, cwd=str(config.REPO_DIR))
                if step == STEPS[-1]:
                    pass
            _write(state="ok", finished=datetime.now().isoformat(timespec="seconds"), step=None)
        except Exception as e:
            _write(state="error", finished=datetime.now().isoformat(timespec="seconds"), error=str(e))
            log.write(f"\nERROR: {e}\n")


def start(skip_drive=False, who=""):
    if not _lock.acquire(blocking=False):
        return False

    def wrapper():
        try:
            # subprocess env must see the same TRIAPP_* the app uses
            os.environ.setdefault("TRIAPP_ROOT", str(config.ROOT))
            os.environ.setdefault("TRIAPP_OUT", str(config.OUT))
            _job(skip_drive, who)
        finally:
            _lock.release()

    threading.Thread(target=wrapper, daemon=True).start()
    return True


def start_daily():
    """Run the full refresh (Drive + Intervals.icu + analysis) every day at TRIAPP_DAILY_AT (HH:MM, box local time; empty = off)."""
    at = os.environ.get("TRIAPP_DAILY_AT", "05:00").strip()
    if not at:
        return
    hh, mm = (int(x) for x in at.split(":"))
    marker = config.OUT / "daily_last.txt"  # date of the last daily run, survives container restarts

    def loop():
        while True:
            now = datetime.now()
            today = now.date().isoformat()
            try:
                last = marker.read_text().strip()
            except Exception:
                last = ""
            if (now.hour, now.minute) >= (hh, mm) and last != today and start(who="daily"):
                marker.write_text(today)
            time.sleep(60)

    threading.Thread(target=loop, daemon=True).start()
    start_briefs()
    start_light_schedule()


def start_briefs():
    """Push each athlete's daily brief at TRIAPP_BRIEF_AT (HH:MM, box local time; empty = off), once a day."""
    at = os.environ.get("TRIAPP_BRIEF_AT", "07:30").strip()
    if not at:
        return
    hh, mm = (int(x) for x in at.split(":"))

    def loop():
        import sys

        sys.path.insert(0, str(config.REPO_DIR))
        import notify

        while True:
            now = datetime.now()
            if (now.hour, now.minute) >= (hh, mm) and not notify.quiet(now):
                try:
                    notify.send_briefs(now.date().isoformat())  # each athlete once a day (push_sent)
                except Exception:
                    pass
            time.sleep(300)

    threading.Thread(target=loop, daemon=True).start()


def build_race(slug, who):
    """A race created on the admin page: course files (course_builder.py), then the predictions of every athlete (race_impact.py)."""

    def job():
        env = dict(os.environ, TRIAPP_ROOT=str(config.ROOT), TRIAPP_OUT=str(config.OUT))
        folder = config.OUT / "courses" / slug
        with open(folder / "build.log", "w") as log:
            try:
                log.write(f"built by {who}\n")
                p = subprocess.run(
                    [config.PY, "course_builder.py", slug],
                    cwd=str(config.REPO_DIR),
                    env=env,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    text=True,
                )
                if p.returncode == 0:
                    _set_build(folder, step="previsões dos atletas")
                    subprocess.run(
                        [config.PY, "race_impact.py"],
                        cwd=str(config.REPO_DIR),
                        env=env,
                        stdout=log,
                        stderr=subprocess.STDOUT,
                        text=True,
                    )
                    _set_build(folder, step=None, predicted=True)
            except Exception as e:
                log.write(f"\nERROR: {e}\n")

    threading.Thread(target=job, daemon=True).start()


def refresh_races():
    """race_impact.py again (a race was deleted)."""
    env = dict(os.environ, TRIAPP_ROOT=str(config.ROOT), TRIAPP_OUT=str(config.OUT))
    threading.Thread(
        target=lambda: subprocess.run([config.PY, "race_impact.py"], cwd=str(config.REPO_DIR), env=env, capture_output=True),
        daemon=True,
    ).start()


def _set_build(folder, **kw):
    path = folder / "build.json"
    try:
        cur = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        cur = {}
    cur.update(kw)
    path.write_text(json.dumps(cur, ensure_ascii=False), encoding="utf-8")

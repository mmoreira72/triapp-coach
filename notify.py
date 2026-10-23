"""Push notifications: at most one per person per morning, nothing at night (push.py; nothing happens until APNs is set up).

What is new (each item once, table push_sent):
  - the note of a session from today or yesterday          -> the athlete
  - a skill alert that was not there before                -> the athlete, and the coaches in one team digest
  - two planned sessions of the last week missed, no reason -> the athlete (asked why) and the coaches
  - the morning brief (morning_brief.py)                   -> the athlete, at TRIAPP_BRIEF_AT

The pipeline step (python notify.py) sends only outside the quiet hours (TRIAPP_QUIET, default 22:00-07:30); what it holds
back goes out with the morning brief (send_briefs), folded into a single notification per athlete and one digest for the
coaches. Never fails the pipeline.

Usage: python notify.py
"""

import os, sys, json
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import push, userdata

OUT = Path(os.environ.get("TRIAPP_OUT", Path(__file__).resolve().parent / "out")) / "data"
SPORT = {"run": "Corrida", "bike": "Bike", "swim": "Natação", "strength": "Força"}


def first_sentences(text, limit=200):
    out = ""
    for part in text.replace("\n", " ").split(". "):
        nxt = (out + ". " if out else "") + part
        if len(nxt) > limit and out:
            break
        out = nxt
    return out.rstrip(".") + "."


def note_events(dash, notes, today):
    since = (date.fromisoformat(today) - timedelta(days=1)).isoformat()
    for name, A in dash["athletes"].items():
        for s in A.get("recent", []):
            n = notes.get(f"{name}|{s.get('key')}")
            if not n or s["date"][:10] < since:
                continue
            title = f"{SPORT.get(s['sport'], s['sport'].title())} · {s.get('title') or 'treino'}"
            yield f"note|{name}|{s['key']}", name, False, title, first_sentences(n.get("text_voice") or n["text"])


def alert_events(analysis, today):
    """Each alert once, until it goes away (keyed by its title, not by the week)."""
    for name, an in analysis.items():
        for f in (an or {}).get("findings", []):
            if f.get("severity") == "alert":
                key = f"alert|{name}|{f['skill']}|{f['title']}"
                yield key, name, True, f"Atenção: {f['title']}", first_sentences(f.get("detail") or "", 220)


DOW = ["seg", "ter", "qua", "qui", "sex", "sáb", "dom"]


def missed_events(planned, today):
    """Two or more planned sessions of the last week not done and not explained: the coach is told, the athlete asked why."""
    for name, plan in planned.items():
        miss = [m for m in userdata.missed_with_reasons(name, plan, today) if not m["reason"]]
        if len(miss) < 2:
            continue
        key = "missed|" + name + "|" + ",".join(sorted(f"{m['date']}:{m['title']}" for m in miss))
        what = "; ".join(f"{DOW[date.fromisoformat(m['date']).weekday()]} {m['title']}" for m in miss)
        yield key, name, True, f"Falhou {len(miss)} treinos", f"{what}. Conta ao treinador o que aconteceu: abre a app, separador Treinos."


def quiet(now=None):
    """True inside TRIAPP_QUIET (HH:MM-HH:MM, box local time; empty = never quiet)."""
    from datetime import datetime

    span = os.environ.get("TRIAPP_QUIET", "22:00-07:30").strip()
    if not span:
        return False
    a, b = span.split("-")
    t = (now or datetime.now()).strftime("%H:%M")
    return (t >= a or t < b) if a > b else (a <= t < b)


def pending(today):
    """Events not sent yet: (key, athlete, for_coaches, title, body)."""
    dash = json.loads((OUT / "dashboard_data.json").read_text(encoding="utf-8"))
    load = lambda f: json.loads((OUT / f).read_text(encoding="utf-8")) if (OUT / f).exists() else {}
    ev = (
        list(note_events(dash, load("session_notes.json"), today))
        + list(alert_events(load("analysis.json"), today))
        + list(missed_events(load("intervals_planned.json"), today))
    )
    return [e for e in ev if not push.was_sent(e[0])]


def deliver(today=None, morning=False):
    """One notification per athlete (the brief, when it is morning, with any new alert added) and one digest for the coaches."""
    if not push.configured():
        return {"sent": 0, "skipped": "apns not configured"}
    if not morning and quiet():
        return {"sent": 0, "skipped": "quiet hours: held for the morning"}
    today = today or date.today().isoformat()
    events = pending(today)
    path = OUT / "daily_brief.json"
    briefs = json.loads(path.read_text(encoding="utf-8")) if (morning and path.exists()) else {}
    total = {"sent": 0, "failed": 0}

    def send(tokens, title, body, thread):
        r = push.send(tokens, title, body, url="/", thread=thread)
        total["sent"] += r["sent"]
        total["failed"] += r["failed"]

    names = {e[1] for e in events} | set(briefs)
    for name in sorted(names):
        tokens = [d["token"] for d in push.devices(athlete=name)]
        mine = [e for e in events if e[1] == name]
        b = briefs.get(name)
        bkey = f"brief|{name}|{today}"
        b = b if (b and b.get("date") == today and not push.was_sent(bkey)) else None
        if not tokens or not (mine or b):
            continue
        alerts = sorted((e for e in mine if e[0].startswith(("missed|", "alert|"))), key=lambda e: not e[0].startswith("missed|"))
        if b:  # the brief already covers yesterday's sessions; a new alert is added at the end
            plan = b.get("today") or []
            sug = b.get("suggestion")
            title = f"Hoje: {plan[0]['title']}" if plan else (f"Hoje (sugestão): {sug['title']}" if sug else "Treino de ontem")
            body = first_sentences(b.get("text_voice") or b["text"], 200)
            if alerts:
                body += f" {alerts[0][3]}" + (f" (+{len(alerts) - 1})" if len(alerts) > 1 else "")
            send(tokens, title, body, name)
            push.mark_sent([bkey] + [e[0] for e in mine])
        elif len(mine) == 1:
            send(tokens, mine[0][3], mine[0][4], name)
            push.mark_sent([mine[0][0]])
        else:
            send(tokens, f"Tri Squad: {len(mine)} novidades", "; ".join(e[3] for e in mine)[:230], name)
            push.mark_sent([e[0] for e in mine])
    # coaches: the new alerts of the whole team in one notification
    coach_tokens = [d["token"] for d in push.devices(coaches=True)]
    team = [e for e in events if e[2] and not push.was_sent("coach|" + e[0])]
    if coach_tokens and team:
        by = {}
        for e in team:
            by.setdefault(e[1], []).append(e[3].replace("Atenção: ", ""))
        body = "; ".join(f"{n}: {', '.join(t)}" for n, t in by.items())[:230]
        send(
            coach_tokens,
            f"Equipa: {len(team)} alerta{'s' if len(team) > 1 else ''} novo{'s' if len(team) > 1 else ''}",
            body,
            "equipa",
        )
        push.mark_sent(["coach|" + e[0] for e in team])
    # coaches: a target race with entered athletes is 15 days away (once per race)
    for key, title, body in race_soon_events(today):
        if coach_tokens and not push.was_sent(key):
            r = push.send(coach_tokens, title, body, url="/board", thread="provas")
            total["sent"] += r["sent"]
            total["failed"] += r["failed"]
            push.mark_sent([key])
    return total


RACE_SOON_DAYS = 15


def race_soon_events(today):
    """(key, title, body) for each target race 0-15 days away with athletes the coach marked as entered."""
    try:
        races = json.loads((OUT / "race_impact.json").read_text(encoding="utf-8"))
    except Exception:
        return []
    plans = userdata.race_plans()
    out = []
    for slug, R in races.items():
        days = (date.fromisoformat(R["race"]["date"]) - date.fromisoformat(today)).days
        if not 0 <= days <= RACE_SOON_DAYS:
            continue
        who = [a for a in sorted(R.get("athletes", {})) if (plans.get(a, {}).get(slug) or {}).get("entered")]
        if not who:
            continue
        times = ", ".join(f"{a} ({(R['athletes'][a].get('total') or {}).get('time', '–')})" for a in who)
        out.append(
            (
                f"race15|{slug}|{R['race']['date']}",
                f"Faltam {days} dias: {R['race']['name']}",
                f"Inscritos: {times}. Revê o IF do bike e a nutrição na vista das provas (Equipa).",
            )
        )
    return out


def send_briefs(today=None):
    """The morning round (the app calls it at TRIAPP_BRIEF_AT): briefs plus everything held back during the night."""
    return deliver(today, morning=True)


def main():
    print(f"notify: {deliver()}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:  # notifications must never block the pipeline
        print(f"notify failed: {type(e).__name__}: {e}")
        sys.exit(0)

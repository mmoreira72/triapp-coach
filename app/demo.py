"""Demo athlete for App Store review and TestFlight (the login Apple's reviewers use).

"Demo" is an anonymised copy of a real athlete's numbers (TRIAPP_DEMO_FROM, default Marcelo, with his consent): the name
becomes "Atleta Demo" and everything that could identify the person is left out (session notes, the LLM reading, context
periods such as injuries). It exists only when asked for, so the coach's team view never shows it.
"""

import copy, os

DEMO = "Demo"
NAME = "Atleta Demo"
SOURCE = os.environ.get("TRIAPP_DEMO_FROM", "Marcelo")


def source(athlete):
    """The athlete whose data backs `athlete` (the demo's source, or the athlete itself)."""
    return SOURCE if athlete == DEMO else athlete


def athlete_data(A):
    a = copy.deepcopy(A)
    a["name"], a["full_name"] = DEMO, NAME
    a.pop("brief", None)  # the morning brief quotes notes and context
    a.pop("planned", None)
    for s in a.get("recent", []) + a.get("executions", []):
        s.pop("note", None)
        s.pop("note_voice", None)
    return a


def analysis(an):
    if not an:
        return an
    an = copy.deepcopy(an)
    an["findings"] = [f for f in an.get("findings", []) if f.get("skill") != "context"]
    for f in an["findings"]:  # an alert the real athlete chose to ignore carries their name: show it as it was
        ig = f.pop("ignored", None)
        if ig:
            f["severity"] = ig["was"]
            f["detail"] = f["detail"].split(" Ignorado por ")[0]
    an["narrative"] = an["narrative_voice"] = None
    an["counts"] = {s: sum(1 for f in an["findings"] if f["severity"] == s) for s in ("alert", "watch", "ok", "info")}
    return an


def slice_for(d):
    """The data slice for the demo login, built from the full data `d`."""
    if SOURCE not in d.get("athletes", {}):
        return {"generated": d["generated"], "athletes": {}, "notes": {}, "analysis": {}, "races_target": {}}
    return {
        "generated": d["generated"],
        "athletes": {DEMO: athlete_data(d["athletes"][SOURCE])},
        "notes": {DEMO: []},
        "analysis": {DEMO: analysis(d.get("analysis", {}).get(SOURCE))} if d.get("analysis", {}).get(SOURCE) else {},
        "races_target": {
            slug: {
                **{k: v for k, v in r.items() if k != "athletes"},
                "athletes": {DEMO: {**r["athletes"][SOURCE], "name": NAME}} if SOURCE in r.get("athletes", {}) else {},
            }
            for slug, r in d.get("races_target", {}).items()
        },
    }

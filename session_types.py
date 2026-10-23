"""Session type from time in zones (deterministic; the LLM only writes the benefit from this result).

Zones are TrainingPeaks defaults (zones.py), from the per-session histograms written by parse_fit (hr_hist: seconds per
2 bpm, pw_hist: seconds per 10 W) and the threshold in use on that day (lthr_used, ftp_used).
Bike uses power when the session has it (no lag, no drift), heart rate otherwise; run uses heart rate. Swimming uses heart
rate against a swim threshold SWIM_LTHR_DROP bpm below the run one (heart rate runs lower in the water; an athlete's measured
swim threshold, lthr_swim in athlete_overrides.json, wins). Heart rate lags
2-3 min, so short intervals are under-counted: the type is then a lower bound, and the text says so.
"""

import json

import zones

HR_BIN, POWER_BIN = 2, 10  # histogram bin widths written by parse_fit
SWIM_LTHR_DROP = 10  # bpm: swim threshold heart rate vs the run one


def swim_lthr(row):
    try:
        from athletes import override

        own = override(row.get("athlete"), "lthr_swim")
    except Exception:
        own = None
    base = row.get("lthr_used")
    return own or (base - SWIM_LTHR_DROP if base == base and base else None)


def _hist(s):
    if not isinstance(s, str) or not s:
        return {}
    try:
        return {int(k): v for k, v in json.loads(s).items()}
    except Exception:
        return {}


def zones_for(row):
    """(basis, {zone: seconds}) for a session row of activities_all."""
    if row.sport_n == "bike":
        z = zones.seconds_by_zone(_hist(row.get("pw_hist")), row.get("ftp_used"), zones.POWER_EDGES, POWER_BIN)
        if sum(z.values()) >= 600:
            return "potência", z
    if row.sport_n in ("bike", "run"):
        z = zones.seconds_by_zone(_hist(row.get("hr_hist")), row.get("lthr_used"), zones.HR_EDGES[row.sport_n], HR_BIN)
        if sum(z.values()) >= 600:
            return "FC", z
    if row.sport_n == "swim":
        z = zones.seconds_by_zone(_hist(row.get("hr_hist")), swim_lthr(row), zones.HR_EDGES["run"], HR_BIN)
        if sum(z.values()) >= 600:
            return "FC", z
    return None, {}


def classify(row):
    """{'type', 'basis', 'zones': {Zn: share}, 'long': bool} or None."""
    sport = row.sport_n
    dur = float(row.duration_h or 0) * 3600
    if sport == "swim" and not zones_for(row)[1]:
        return {"type": "Natação", "basis": None, "zones": {}}
    if sport == "strength":
        return {"type": "Força", "basis": None, "zones": {}}
    if sport not in ("bike", "run", "swim") or dur < 600:
        return None
    basis, z = zones_for(row)
    long_ = dur >= {"bike": 2.5 * 3600, "run": 1.5 * 3600, "swim": 3600}[sport]
    if not z:
        return {
            "type": "Resistência aeróbia longa" if long_ else "Sem dados de intensidade",
            "basis": None,
            "zones": {},
            "long": long_,
        }
    tot = sum(z.values())
    sec = lambda *zs: sum(z.get(k, 0) for k in zs)
    if basis == "potência":
        anaer, vo2, thr, tempo, low, z1 = sec(6, 7), sec(5), sec(4), sec(3), sec(1, 2), sec(1)
    else:  # heart rate: 100-102% LTHR (5a) is still threshold work; VO2max needs time at 103%+ (5b, 5c)
        anaer, vo2, thr, tempo, low, z1 = 0, sec(6, 7), sec(4, 5), sec(3), sec(1, 2), sec(1)
    if anaer >= 120 and anaer / tot >= 0.03:
        t = "Anaeróbio / sprints"
    elif vo2 >= 300 and (basis != "potência" or vo2 >= 1.25 * thr):
        # power second by second swings above the threshold reps (Zwift without ERG, a group ride): Z5 has to clearly
        # outweigh Z4 before a power session counts as VO2max
        t = "VO2max"
    elif thr >= 600 or thr / tot >= 0.15:
        t = "Limiar"
    elif tempo >= 1800 or tempo / tot >= 0.30:
        t = "Tempo"  # Z3 must be the main block, not a share of a mixed ride
    elif z1 / tot >= 0.70 and dur <= 75 * 60:
        t = "Recuperação"
    else:
        t = "Resistência aeróbia longa" if long_ else "Resistência aeróbia"
    return {
        "type": t,
        "basis": basis,
        "long": long_,
        "zones": {zones.label(k, basis): round(v / tot, 2) for k, v in sorted(z.items()) if v / tot >= 0.01},
    }

"""Planned vs executed for structured sessions.

Executed structure comes from the laps of the file: devices mark each lap of a structured workout with its intensity
(warmup / active / interval / rest / recovery / cooldown) and workout step. Consecutive laps of the same step are one block.
The target comes from the session title written by the coach ("2x10' Z3", "4x20' Half Pace", "7x2000 Z4 I=2'"): number
of repeats, length of each repeat (time or distance) and zone. Zones are TrainingPeaks defaults (zones.py).
Planned TSS / duration from TrainingPeaks (WKO bundle) are added when present.
"""

import re
import numpy as np, pandas as pd

import zones

WORK = {"active", "interval"}
EASY = {"rest", "recovery"}
STRUCT = {"warmup", "rest", "recovery", "cooldown", "interval"}

REP = re.compile(r"(\d+)\s*x\s*(\d+(?:[.,]\d+)?)\s*(h\b|min\b|['’`]|km\b|m\b)?", re.I)
ZONE = re.compile(r"\bZ(?:ona)?\s?([1-7])\b", re.I)


def parse_title(title, sport):
    """{'reps', 'rep_s' | 'rep_m', 'zone', 'text'} or None. Only simple patterns: one repeat group (NxD) and one zone after it."""
    t = str(title or "")
    reps = list(REP.finditer(t))
    if len(reps) != 1 or re.search(r"\dx\s*\d+\s*x|\(", t):
        return None
    m = reps[0]
    n = int(m.group(1))
    v = float(m.group(2).replace(",", "."))
    u = (m.group(3) or "").lower()
    if n < 1 or n > 40:
        return None
    out = {"reps": n, "text": t}
    if u in ("'", "’", "`", "min"):
        out["rep_s"] = v * 60
    elif u == "h":
        out["rep_s"] = v * 3600
    elif u == "km":
        out["rep_m"] = v * 1000
    elif u == "m" or (not u and v >= 100 and sport in ("run", "swim")):
        out["rep_m"] = v
    else:
        return None
    z = ZONE.search(t[m.end() : m.end() + 25]) or (ZONE.search(t[: m.start()]) if len(ZONE.findall(t)) == 1 else None)
    if z:
        out["zone"] = int(z.group(1))
    return out


def blocks(laps):
    """Merge consecutive laps of the same workout step and intensity into blocks."""
    laps = laps.sort_values("lap")
    out, cur, key = [], None, None
    for _, l in laps.iterrows():
        k = (str(l.intensity), l.wkt_step_index if pd.notna(l.wkt_step_index) else None)
        if cur is None or k != key:
            if cur:
                out.append(cur)
            cur = {"kind": str(l.intensity), "step": k[1], "s": 0.0, "m": 0.0, "hr_w": 0.0, "hr_s": 0.0, "p_w": 0.0, "p_s": 0.0}
            key = k
        s = float(l.timer_s or 0)
        cur["s"] += s
        cur["m"] += float(l.distance_m or 0) if pd.notna(l.distance_m) else 0
        if pd.notna(l.avg_hr) and l.avg_hr > 40:
            cur["hr_w"] += l.avg_hr * s
            cur["hr_s"] += s
        if pd.notna(l.avg_power) and l.avg_power > 0:
            cur["p_w"] += l.avg_power * s
            cur["p_s"] += s
    if cur:
        out.append(cur)
    res = []
    for b in out:
        if b["s"] < 20:
            continue
        res.append(
            {
                "kind": b["kind"],
                "s": round(b["s"]),
                "m": round(b["m"]),
                "hr": round(b["hr_w"] / b["hr_s"]) if b["hr_s"] else None,
                "w": round(b["p_w"] / b["p_s"]) if b["p_s"] else None,
                "pace": round(b["s"] / 60 / (b["m"] / 1000), 2) if b["m"] > 50 else None,
            }
        )
    return res


def assess(row, bl, plan):
    sport = row.sport_n
    lthr = row.lthr_used
    ftp = row.ftp_used
    work = [b for b in bl if b["kind"] in WORK]
    for b in work:
        # heart rate lags 2-3 min and is unreliable in the water: no HR zone for swims or blocks under 5 min
        b["z_hr"] = zones.hr_zone(b["hr"], lthr, sport, split_z5=False) if sport != "swim" and b["s"] >= 300 else None
        b["z_w"] = zones.power_zone(b["w"], ftp) if sport == "bike" else None
    notes, verdict = [], "sem alvo no título"
    if plan:
        verdict = "cumprido"
        tol = 2 if sport == "swim" else 0  # watches miss or add pool lengths
        if abs(len(work) - plan["reps"]) > tol:
            notes.append(f"{len(work)} blocos de trabalho para {plan['reps']} planeados")
            verdict = "estrutura diferente"
        if plan.get("rep_s") and work:
            off = [b for b in work if abs(b["s"] - plan["rep_s"]) > max(0.1 * plan["rep_s"], 30)]
            if off:
                notes.append(f"{len(off)} bloco(s) com duração fora de ±10% de {fmt_s(plan['rep_s'])}")
                verdict = "estrutura diferente"
        if plan.get("rep_m") and work:
            off = [b for b in work if abs(b["m"] - plan["rep_m"]) > 0.1 * plan["rep_m"]]
            if off:
                notes.append(f"{len(off)} bloco(s) com distância fora de ±10% de {plan['rep_m']:.0f} m")
                verdict = "estrutura diferente"
        if plan.get("zone") and work:
            zs = [b["z_w"] if b.get("z_w") else b.get("z_hr") for b in work]
            zs = [z for z in zs if z]
            if not zs:
                notes.append(
                    "zona não avaliada: "
                    + (
                        "FC na água não é fiável"
                        if sport == "swim"
                        else "blocos curtos, a FC não chega a estabilizar (sem potência)"
                    )
                )
            if zs:
                basis = "potência" if sport == "bike" and any(b.get("z_w") for b in work) else "FC"
                above = sum(z > plan["zone"] for z in zs)
                below = sum(z < plan["zone"] for z in zs)
                if above > len(zs) / 2:
                    notes.append(f"intensidade acima da Z{plan['zone']} ({basis}) em {above} de {len(zs)} blocos")
                    verdict = "acima do alvo" if verdict == "cumprido" else verdict
                elif below > len(zs) / 2:
                    notes.append(f"intensidade abaixo da Z{plan['zone']} ({basis}) em {below} de {len(zs)} blocos")
                    verdict = "abaixo do alvo" if verdict == "cumprido" else verdict
                else:
                    notes.append(f"blocos na Z{plan['zone']} por {basis}")
        zone_checked = bool(plan.get("zone")) and any(n.startswith(("blocos na", "intensidade")) for n in notes)
        if verdict == "cumprido" and not zone_checked:
            verdict = "estrutura cumprida"
    hrs = [b["hr"] for b in work if b["hr"] and b["s"] >= 300] if sport != "swim" else []
    if len(hrs) >= 2 and hrs[-1] - hrs[0] >= 5:
        notes.append(f"FC subiu {hrs[-1] - hrs[0]} bpm do primeiro ao último bloco")
    return verdict, notes


def fmt_s(s):
    s = int(round(s))
    return f"{s // 60}:{s % 60:02d}"


def executions(g, laps, days=60, limit=12, today=None):
    """Structured sessions of one athlete in the last `days`, newest first."""
    today = today or pd.Timestamp.today()
    r = g[(g.date >= today - pd.Timedelta(days=days)) & g.completed & g.sport_n.isin(["run", "bike", "swim"])].sort_values(
        "t_local", ascending=False
    )
    out = []
    for _, row in r.iterrows():
        lp = laps.get((row.athlete, row.file, row.session_index if pd.notna(row.session_index) else 0))
        if lp is None or len(lp) < 3:
            continue
        plan = parse_title(row.title_n, row.sport_n)
        kinds = set(lp.intensity.dropna().astype(str))
        if not (kinds & STRUCT) and lp.wkt_step_index.nunique() < 2:
            # manual laps: when the title gives the repeat length, laps of that length (±10%) are the work blocks
            if not plan:
                continue
            if plan.get("rep_s"):
                hit = (lp.timer_s - plan["rep_s"]).abs() <= max(0.1 * plan["rep_s"], 15)
            else:
                hit = (lp.distance_m - plan["rep_m"]).abs() <= 0.1 * plan["rep_m"]
            if hit.sum() < max(2, 0.6 * plan["reps"]):
                continue
            lp = lp.assign(intensity=np.where(hit, "active", "rest"), wkt_step_index=np.nan)
            lp.loc[lp.index[: hit.values.argmax()], "intensity"] = "warmup"
            last = len(hit) - 1 - hit.values[::-1].argmax()
            lp.loc[lp.index[last + 1 :], "intensity"] = "cooldown"
            # each work / recovery lap is its own block; warm-up and cool-down laps merge into one block each
            lp = lp.assign(wkt_step_index=np.where(lp.intensity.isin(["active", "rest"]), lp.lap, -1))
        bl = blocks(lp)
        if not any(b["kind"] in WORK for b in bl) or not any(b["kind"] not in WORK for b in bl):
            continue
        verdict, notes = assess(row, bl, plan)
        planned = {}
        if pd.notna(row.get("planned_duration_s")) and row.planned_duration_s > 0:
            planned["h"] = round(row.planned_duration_s / 3600, 2)
        if pd.notna(row.get("planned_tss")) and row.planned_tss > 0:
            planned["tss"] = round(float(row.planned_tss))
        out.append(
            {
                "date": row.t_local.strftime("%Y-%m-%d %H:%M"),
                "sport": row.sport_n,
                "key": f"{row.file}|{int(row.session_index) if pd.notna(row.session_index) else 0}",
                "title": row.title_n if isinstance(row.title_n, str) else "",
                "plan": {k: v for k, v in (plan or {}).items() if k != "text"} or None,
                "planned": planned or None,
                "done": {
                    "h": round(float(row.duration_h), 2),
                    "tss": round(float(row.tss_final)) if pd.notna(row.tss_final) else None,
                },
                "lthr": float(row.lthr_used) if pd.notna(row.lthr_used) else None,
                "ftp": float(row.ftp_used) if row.sport_n == "bike" and pd.notna(row.ftp_used) else None,
                "blocks": bl,
                "verdict": verdict,
                "notes": notes,
            }
        )
        if len(out) >= limit:
            break
    return out


def lap_index(laps):
    laps = laps.copy()
    laps["session_index"] = laps.session_index.fillna(0)
    return {k: v for k, v in laps.groupby(["athlete", "file", "session_index"])}

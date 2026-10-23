"""Pipeline step: the daily brief of each athlete, written by the local LLM and sent as a push in the morning.

  yesterday  what yesterday's sessions did (from their session notes), or a rest day
  today      the session the coach planned (Intervals.icu calendar, fed by TrainingPeaks) and the effect it is planned for

Writes <OUT>/data/daily_brief.json: {athlete: {date, yesterday, today, text, text_voice, voice}}. The neutral text is the
coach's; the athlete reads and receives their own voice (voice.py), except while an injury or illness is ongoing. The app
pushes the brief at TRIAPP_BRIEF_AT (notify.send_briefs). Athletes without a calendar for the week (TrainingPeaks not
connected to Intervals.icu), or with no plan today and nothing yesterday, get none.

Usage: python morning_brief.py [Athlete ...]
"""

import os, sys, json
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import llm, voice, health, userdata
from skills_engine import load_context
from session_notes import context_line, target_race, SPORT

OUT = Path(os.environ.get("TRIAPP_OUT", Path(__file__).resolve().parent / "out")) / "data"
BRIEF = OUT / "daily_brief.json"

SYSTEM = (
    "És o assistente de um treinador de triatlo. Escreves em português europeu, para um atleta amador, tratando-o por tu. "
    "Escreves o resumo da manhã em 3 ou 4 frases curtas (máximo 70 palavras): primeiro, numa frase, o que o treino de ontem "
    "fez (ou que foi dia de descanso); depois o treino de hoje e para que serve na preparação, explicando em linguagem simples "
    "o que a descrição do treinador pede (zonas, blocos, material). Regras: usa só os dados fornecidos; não inventes números, "
    "zonas nem treinos; se não houver treino planeado hoje mas houver SUGESTÃO DA APP, apresenta-a como sugestão (o plano do treinador prevalece); se houver TREINO DA SEMANA A QUE MAIS TE DEVES DEDICAR, menciona-o numa frase. Lesões: só refere uma lesão recente do "
    "CONTEXTO se o treino for da disciplina afetada. Sem listas, sem LaTeX."
)


def today_input(name, A, notes, planned, today):
    yday = (date.fromisoformat(today) - timedelta(days=1)).isoformat()
    done = [s for s in A.get("recent", []) if s["date"][:10] == yday]
    cmts = userdata.comments(name, since=yday, replies=False)
    yesterday = [
        {
            "sport": s["sport"],
            "title": s.get("title") or "treino",
            "type": (s.get("kind") or {}).get("type"),
            "note": (notes.get(f"{name}|{s.get('key')}") or {}).get("text"),
            "comments": [
                f"{c['author']}{' (treinador)' if c['role'] == 'coach' else ''}: {c['text']}" for c in cmts.get(s.get("key"), [])
            ],
        }
        for s in done
    ]
    if not yesterday:  # nothing synced yet (Intervals.icu down at night?) but TrainingPeaks shows yesterday's plan as done
        yesterday = [
            {
                "sport": p["sport"],
                "title": p["title"],
                "type": None,
                "note": "feito segundo o TrainingPeaks; os dados do relógio ainda não chegaram",
            }
            for p in planned.get(name, [])
            if p["date"] == yday and p.get("completed")
        ]
    plan = [p for p in planned.get(name, []) if p["date"] == today]
    return yesterday, plan


def describe(yesterday, plan):
    y = (
        "\n".join(
            f"- {SPORT.get(s['sport'], s['sport'])}: {s['title']} (tipo {s['type'] or '?'}). Nota: {s['note'] or 'sem nota'}"
            + (
                f" COMENTÁRIOS (o que o atleta ou o treinador disseram; respeita-os): {' | '.join(s['comments'])}"
                if s.get("comments")
                else ""
            )
            for s in yesterday
        )
        or "- nenhum treino registado (descanso)"
    )
    t = (
        "\n".join(
            f"- {SPORT.get(p['sport'], p['sport'])}: {p['title']}"
            + (f", {round(p['duration_s'] / 60)} min" if p.get("duration_s") else "")
            + (f", carga planeada {round(p['load'])}" if p.get("load") else "")
            + (f"\n  Descrição do treinador: {p['description']}" if p.get("description") else "")
            for p in plan
        )
        or "- nada no plano"
    )
    return f"ONTEM:\n{y}\n\nHOJE (planeado):\n{t}"


REPORT_SYSTEM = (
    "És o assistente de um treinador de triatlo. Escreves em português europeu, para o atleta, tratando-o por tu, o relatório "
    "do dia de ontem, completo mas direto (máximo 220 palavras), com estes títulos em **negrito**, cada um com 1 a 3 frases: "
    "**Treinos** (o que foi feito, comparado com o planeado pelo treinador: duração, intensidade, blocos e o que correu fora do "
    "plano); **Carga** (como mudaram a forma (CTL), a fadiga (ATL) e a frescura (TSB) e o que isso significa); **Atenção** "
    "(alertas das skills; omite se não houver); "
    "**Hoje e amanhã** (o que vem no plano e como encaixa). Regras: usa só os dados fornecidos, não inventes números; sem LaTeX."
)


def recovery_section(name):
    """The report's Recuperação section, written here and not by the LLM: small models swap "above" and "below" average
    even when told which one it is (a resting HR of 57 against an average of 59 came back as "above")."""
    hk = health.recent(name, 15)
    if not hk:
        return None
    last = hk[-1]
    hrv_k = "hrv" if last.get("hrv") is not None else "hrv_rmssd"
    fmt = lambda x, nd: f"{x:.{nd}f}".replace(".", ",") if nd else f"{round(x)}"

    def part(k, label, unit, nd, good_when_up):
        v, prev = last.get(k), [r[k] for r in hk[:-1] if r.get(k) is not None]
        if v is None:
            return None
        if len(prev) < 3:
            return f"{label} {fmt(v, nd)} {unit}"
        m = sum(prev) / len(prev)
        if abs(v - m) <= 0.03 * m:
            return f"{label} {fmt(v, nd)} {unit}, na tua média"
        up = v > m
        txt = f"{label} {fmt(v, nd)} {unit}, {'acima' if up else 'abaixo'} da tua média ({fmt(m, nd)})"
        if good_when_up is None:
            return txt
        return txt + (": bom sinal" if up == good_when_up else (": a vigiar" if k == "rhr" else ": pede mais recuperação"))

    bits = [
        part("sleep_h", "Dormiste", "h", 1, None),
        part("rhr", "FC de repouso", "bpm", 0, False),
        part(hrv_k, "HRV", "ms", 0, True),
    ]
    bits = [b for b in bits if b]
    if last.get("sleep_score"):
        bits.append(f"pontuação do sono {round(last['sleep_score'])}")
    return "**Recuperação**\n" + "; ".join(bits) + "." if bits else None


def assemble(report, recovery):
    """Insert the code-written recovery section before Atenção (or Hoje e amanhã, or at the end)."""
    if not recovery:
        return report
    for title in ("**Atenção**", "**Hoje e amanhã**"):
        i = report.find(title)
        if i >= 0:
            return report[:i].rstrip() + "\n" + recovery + "\n" + report[i:]
    return report.rstrip() + "\n" + recovery


def report_input(name, A, notes, planned, analysis, today):
    """Everything about yesterday for the detailed report: sessions with plan vs execution, load, recovery, alerts, next days."""
    yday = (date.fromisoformat(today) - timedelta(days=1)).isoformat()
    tmrw = (date.fromisoformat(today) + timedelta(days=1)).isoformat()
    lines = ["TREINOS DE ONTEM:"]
    plan_y = [p for p in planned.get(name, []) if p["date"] == yday]
    for p in plan_y:
        lines.append(
            f"- Planeado: {SPORT.get(p['sport'], p['sport'])} {p['title']}"
            + (f" ({round(p['duration_s'] / 60)} min)" if p.get("duration_s") else "")
            + (f". Descrição: {p['description']}" if p.get("description") else "")
        )
    done = [s for s in A.get("recent", []) if s["date"][:10] == yday]
    for s in done:
        k = s.get("kind") or {}
        zones = ", ".join(f"{z} {round(v * 100)}%" for z, v in (k.get("zones") or {}).items())
        lines.append(
            f"- Feito: {SPORT.get(s['sport'], s['sport'])} {s.get('title') or ''}: {round((s.get('h') or 0) * 60)} min"
            + (f", {s['km']} km" if s.get("km") else "")
            + (f", TSS {round(s['tss'])}" if s.get("tss") else "")
            + (f", IF {s['if']}" if s.get("if") else "")
            + (f", FC média {round(s['hr'])}" if s.get("hr") else "")
            + (f", tipo {k['type']}" if k.get("type") else "")
            + (f", tempo em zona: {zones}" if zones else "")
            + (". Medidor de potência falhou." if s.get("power_dropout") else "")
        )
        n = (notes.get(f"{name}|{s.get('key')}") or {}).get("text")
        if n:
            lines.append(f"  Nota: {n}")
        for c in userdata.comments(name, since=yday, replies=False).get(s.get("key"), []):
            lines.append(f"  Comentário de {c['author']}{' (treinador)' if c['role'] == 'coach' else ''}: {c['text']}")
    if not done:
        lines.append("- nenhum treino registado")
    daily = {d["d"]: d for d in (A.get("pmc") or {}).get("daily", [])}
    before = (date.fromisoformat(yday) - timedelta(days=1)).isoformat()
    if yday in daily:
        y, b = daily[yday], daily.get(before, {})
        lines.append(
            f"\nCARGA: CTL {y['ctl']} (antes {b.get('ctl', '?')}), ATL {y['atl']} (antes {b.get('atl', '?')}), TSB {y['tsb']}, TSS do dia {y['tss']}"
        )
    if yday in daily:
        lines.append(
            "CARGA, DIREÇÃO: "
            + ", ".join(
                f"{k.upper()} {'subiu' if daily[yday][k] > daily.get(before, {}).get(k, daily[yday][k]) else 'desceu' if daily[yday][k] < daily.get(before, {}).get(k, daily[yday][k]) else 'igual'}"
                for k in ("ctl", "atl", "tsb")
            )
        )
    alerts = [f for f in (analysis.get(name) or {}).get("findings", []) if f.get("severity") == "alert"]
    if alerts:
        lines.append("\nALERTAS: " + " | ".join(f"{f['title']}: {f['detail'][:200]}" for f in alerts))
    nxt = [p for p in planned.get(name, []) if p["date"] in (today, tmrw)]
    lines.append(
        "\nPLANO: "
        + (
            " | ".join(
                f"{'hoje' if p['date'] == today else 'amanhã'}: {SPORT.get(p['sport'], p['sport'])} {p['title']}"
                + (f" ({round(p['duration_s'] / 60)} min)" if p.get("duration_s") else "")
                for p in nxt
            )
            or "nada planeado hoje e amanhã"
        )
    )
    return "\n".join(lines)


# ------------------------------------------------------------------ suggestion (no plan today) and focus of the week
SPORTS = {"run": "corrida", "bike": "bike", "swim": "natação", "strength": "força"}
CAP_TEXT = {
    "leve": "só leve: Z1 e Z2, sem blocos intensos",
    "moderada": "até moderada: Z1 a Z3, sem Z4 nem Z5",
    "livre": "livre: pode incluir blocos em Z4 ou Z5 se a lacuna o pedir",
}

SUGGEST_SYSTEM = (
    "És o assistente de um treinador de triatlo. Hoje o atleta não tem nada no plano do treinador e pede uma sugestão. "
    "Propõe UM treino concreto que ataque a lacuna mais importante das listadas, respeitando à risca a INTENSIDADE PERMITIDA, "
    "a lista NÃO SUGERIR, os ÚLTIMOS DIAS (alterna modalidades e estímulos: depois de um treino longo ou duro numa modalidade, "
    "outra modalidade ou recuperação), a semana planeada (não repitas no dia anterior o tipo de treino-chave de amanhã) e a "
    "prova-alvo, e nunca passes a DURAÇÃO MÁXIMA da modalidade. Duração entre 30 e 120 "
    "minutos (até 180 se a lacuna for falta de sessões longas, sempre em Z1-Z2); o campo minutes tem de ser a soma das partes da descrição. Responde SÓ com JSON, sem texto à "
    'volta, neste formato: {"sport": "run|bike|swim|strength", "title": "título curto", "minutes": 60, '
    '"description": "aquecimento, parte principal com zonas e duração, retorno à calma", "why": "uma ou duas frases a ligar o '
    'treino à lacuna", "skill": "o valor de skill= da lacuna que o treino ataca"}. Português europeu, tratando o atleta por tu. Não inventes números de limiares nem dados que não estejam aqui.'
)

FOCUS_SYSTEM = (
    "És o assistente de um treinador de triatlo. Da lista numerada de treinos planeados pelo treinador para os próximos dias, "
    "escolhe o UM a que o atleta se deve dedicar mais, por ser o que melhor ataca as lacunas listadas. Responde SÓ com JSON: "
    '{"index": <número da lista>, "why": "uma ou duas frases, em português europeu, tratando o atleta por tu, a dizer porquê '
    'e como o fazer bem", "skill": "o valor de skill= da lacuna que esse treino ataca"}. Usa só os dados fornecidos.'
)


def gap_findings(analysis, name):
    """The athlete's current weak points: alert and watch findings of the skills (context and ignored ones left out)."""
    return [
        f
        for f in (analysis.get(name) or {}).get("findings", [])
        if f.get("severity") in ("alert", "watch") and f.get("skill") != "context" and not f.get("ignored")
    ]


def gaps(analysis, name):
    return [
        f"[{'agir' if f['severity'] == 'alert' else 'atenção'}] skill={f['skill']} · {f['title']}: {f['detail'][:240]}"
        for f in gap_findings(analysis, name)
    ]


def which_gap(analysis, name, skill):
    """The finding behind the skill the model named (None when it named one that is not in the list)."""
    return next((f for f in gap_findings(analysis, name) if f["skill"] == skill), None)


def intensity_cap(name, A, today):
    """How hard a suggested session may be, decided here and not by the model: (cap, reasons)."""
    why = []
    if voice.health_ongoing(load_context(name), today):
        return "leve", ["lesão ou doença em curso"]
    daily = (A.get("pmc") or {}).get("daily") or []
    tsb = daily[-1]["tsb"] if daily else None
    hk = health.recent(name, 15)
    hrv_low = False
    if len(hk) >= 4:
        k = "hrv" if hk[-1].get("hrv") is not None else "hrv_rmssd"
        prev = [r[k] for r in hk[:-1] if r.get(k) is not None]
        if hk[-1].get(k) is not None and len(prev) >= 3 and hk[-1][k] < 0.95 * sum(prev) / len(prev):
            hrv_low = True
            why.append("HRV da noite abaixo da média")
    if tsb is not None and tsb < -20:
        why.append(f"frescura (TSB) {round(tsb)}: muito cansado")
    if why:
        return "leve", why
    if tsb is not None and tsb < -10:
        return "moderada", [f"frescura (TSB) {round(tsb)}: cansado"]
    return "livre", [f"frescura (TSB) {round(tsb)}" if tsb is not None else "sem dados de frescura"]


def week_plan(planned, name, today, days=7):
    end = (date.fromisoformat(today) + timedelta(days=days)).isoformat()
    return [p for p in planned.get(name, []) if today <= p["date"] < end]


def plan_lines(rows):
    return "\n".join(
        f"{i + 1}. {p['date']} {SPORTS.get(p['sport'], p['sport'])}: {p['title']}"
        + (f" ({round(p['duration_s'] / 60)} min)" if p.get("duration_s") else "")
        + (f". {p['description'][:200]}" if p.get("description") else "")
        for i, p in enumerate(rows)
    )


def ask_json(system, user):
    import re as _re

    txt = write(system, user)
    m = _re.search(r"\{.*\}", txt, _re.S)
    return json.loads(m.group(0)) if m else None


LEGS = {"bike", "run"}


def max_minutes(A, sport, today, days=42):
    """Longest session of this sport in the last 6 weeks × 1.25: a suggestion never jumps far beyond what was done lately."""
    since = (date.fromisoformat(today) - timedelta(days=days)).isoformat()
    longest = max(
        (round((s.get("h") or 0) * 60) for s in A.get("recent", []) if s["sport"] == sport and s["date"][:10] >= since), default=0
    )
    return max(45, round(longest * 1.25))


def legs_rest(avoid):
    """After a long or hard bike or run, the other leg sport is allowed only short and easy."""
    return bool(avoid & LEGS)


INTENSE_TYPES = ("Limiar", "VO2", "Anaeróbio", "Tempo", "Neuromuscular", "Intervalado", "Prova")


def recent_load(A, planned, day, extra=None):
    """The sessions of the 3 days before `day`: done (from the data), or planned / already suggested when that day has not come
    yet. Returns (lines for the model, sports not to suggest): a long (75 min or more) or hard session the day before rules
    its sport out, so the legs (or the arms) get a break."""
    d = date.fromisoformat(day)
    days = [(d - timedelta(days=k)).isoformat() for k in (3, 2, 1)]
    lines, avoid = [], set()
    for dd in days:
        done = [s for s in A.get("recent", []) if s["date"][:10] == dd]
        rows = [
            (s["sport"], round((s.get("h") or 0) * 60), (s.get("kind") or {}).get("type"), s.get("tss"), "feito") for s in done
        ]
        if not rows:  # not done yet (it is today, or the data is late): what is planned or suggested for that day
            rows = [
                (p["sport"], round((p.get("duration_s") or 0) / 60), None, p.get("load"), "planeado")
                for p in planned
                if p["date"] == dd
            ]
            if not rows and extra and extra.get("date") == dd:
                rows = [(extra["sport"], extra["minutes"], None, None, "sugerido")]
        for sport, mins, typ, tss, how in rows:
            lines.append(
                f"- {dd} ({how}): {SPORTS.get(sport, sport)} {mins} min"
                + (f", {typ}" if typ else "")
                + (f", TSS {round(tss)}" if tss else "")
            )
            if dd == days[-1] and sport in SPORTS and (mins >= 75 or (typ and typ.startswith(INTENSE_TYPES))):
                avoid.add(sport)
    return lines, avoid


def suggestion(name, A, planned, analysis, today, race, extra=None):
    cap, why = intensity_cap(name, A, today)
    lines, avoid = recent_load(A, planned.get(name, []), today, extra)
    limits = {sp: max_minutes(A, sp, today) for sp in SPORTS}
    if legs_rest(avoid):
        for sp in LEGS - avoid:
            limits[sp] = min(limits[sp], 45)
    limit_txt = (
        "DURAÇÃO MÁXIMA POR MODALIDADE (progressão: 1,25 × o treino mais longo das últimas 6 semanas"
        + ("; pernas cansadas: a outra modalidade de pernas só curta e leve, em Z1-Z2" if legs_rest(avoid) else "")
        + "): "
        + ", ".join(f"{SPORTS[sp]} {m} min" for sp, m in limits.items() if sp not in avoid)
        + "\n"
    )
    user = f"Atleta: {name}\nProva-alvo: {race}\nINTENSIDADE PERMITIDA: {CAP_TEXT[cap]} (porque: {'; '.join(why)})\n" + (
        f"NÃO SUGERIR: {', '.join(SPORTS[x] for x in sorted(avoid))} (treino longo ou intenso nessa modalidade no dia anterior: "
        "dá descanso a esses músculos; natação ou força leve servem de recuperação ativa para as pernas)\n"
        if avoid
        else ""
    ) + limit_txt + "\nÚLTIMOS DIAS:\n" + ("\n".join(lines) or "sem treinos") + "\n\n" "LACUNAS (das regras de treino):\n" + (
        "\n".join(gaps(analysis, name)) or "nenhuma assinalada: treino aeróbio de base"
    ) + "\n\nPLANO DOS PRÓXIMOS DIAS:\n" + (
        plan_lines(week_plan(planned, name, today)) or "nada planeado"
    )
    s = ask_json(SUGGEST_SYSTEM, user)
    if not s or s.get("sport") not in SPORTS or not s.get("title"):
        return None
    if s["sport"] in avoid:  # the model ignored the rule: ask once more, plainly
        s = ask_json(SUGGEST_SYSTEM, user + f"\n\nIMPORTANTE: a modalidade {SPORTS[s['sport']]} não é permitida. Escolhe outra.")
        if not s or s.get("sport") not in SPORTS or s["sport"] in avoid or not s.get("title"):
            return None
    import html as _html

    if int(s.get("minutes") or 0) > limits[s["sport"]]:  # too long for now: ask again with the limit, so the text matches
        again = ask_json(
            SUGGEST_SYSTEM,
            user
            + f"\n\nIMPORTANTE: {SPORTS[s['sport']]} no máximo {limits[s['sport']]} min no total; ajusta a descrição a essa duração.",
        )
        if again and again.get("sport") == s["sport"] and again.get("title"):
            s = again
    minutes = max(20, min(int(s.get("minutes") or 60), limits[s["sport"]]))  # never beyond the progression limit
    clean = lambda v, n: _html.unescape(str(v or ""))[:n]
    return {
        "sport": s["sport"],
        "title": clean(s["title"], 80),
        "minutes": minutes,
        "description": clean(s.get("description"), 600),
        "why": clean(s.get("why"), 400),
        "cap": cap,
        "cap_why": why,
        **gap_ref(analysis, name, s.get("skill")),
    }


CAP_RANK = {"leve": 0, "moderada": 1, "livre": 2}


def reuse(prev, name, A, today):
    """The suggestion the athlete already saw (yesterday, for today) stays, unless recovery now allows less than it did."""
    if not prev:
        return None
    cap, why = intensity_cap(name, A, today)
    if CAP_RANK.get(prev.get("cap"), 2) > CAP_RANK[cap]:
        return None  # e.g. suggested at "livre", HRV dropped overnight: a new, easier one
    return {**prev, "cap": prev.get("cap"), "cap_why": why}


def gap_ref(analysis, name, skill):
    g = which_gap(analysis, name, str(skill or ""))
    return {"skill": g["skill"], "gap": g["title"]} if g else {}


def focus(name, planned, analysis, today):
    rows = week_plan(planned, name, today)
    if len(rows) < 2 or not gaps(analysis, name):
        return None
    f = ask_json(FOCUS_SYSTEM, "LACUNAS:\n" + "\n".join(gaps(analysis, name)) + "\n\nTREINOS PLANEADOS:\n" + plan_lines(rows))
    try:
        p = rows[int(f["index"]) - 1]
    except Exception:
        return None
    return {
        "date": p["date"],
        "sport": p["sport"],
        "title": p["title"],
        "why": str(f.get("why") or "")[:400],
        **gap_ref(analysis, name, f.get("skill")),
    }


def write(system, user):
    return llm.chat(
        [{"role": "system", "content": system}, {"role": "user", "content": user}], timeout=420, temperature=0.3, think=True
    )


def main(only=None):
    if not llm.available():
        print("morning brief: no LLM configured")
        return 0
    dash = json.loads((OUT / "dashboard_data.json").read_text(encoding="utf-8"))
    load = lambda f: json.loads((OUT / f).read_text(encoding="utf-8")) if (OUT / f).exists() else {}
    notes, planned, briefs = load("session_notes.json"), load("intervals_planned.json"), load("daily_brief.json")
    analysis = load("analysis.json")
    today = date.today().isoformat()
    done = 0
    for name, A in dash["athletes"].items():
        if only and name not in only:
            continue
        if not planned.get(name):
            briefs.pop(name, None)  # no calendar for the week (TrainingPeaks not connected to Intervals.icu): no brief
            continue
        yesterday, plan = today_input(name, A, notes, planned, today)
        v = voice.NEUTRAL if voice.health_ongoing(load_context(name), today) else voice.get(name)
        old = briefs.get(name) or {}
        same_yesterday = [y["title"] for y in old.get("yesterday", [])] == [y["title"] for y in yesterday]
        if (
            old.get("date") == today
            and old.get("today") == plan
            and old.get("voice") == v
            and old.get("report")
            and same_yesterday
        ):
            continue  # already written today for the same plan and the same sessions yesterday
        race = target_race(name)
        yday = (date.fromisoformat(today) - timedelta(days=1)).isoformat()
        try:
            prev = (
                old.get("suggestion")
                if old.get("date") == today
                else old.get("suggestion_tomorrow") if old.get("date") == yday else None
            )
            trained_today = any(x["date"][:10] == today for x in A.get("recent", []))  # rerun late in the day: already trained
            sug = (
                None
                if plan or trained_today
                else reuse(prev, name, A, today) or suggestion(name, A, planned, analysis, today, race)
            )
            tmrw = (date.fromisoformat(today) + timedelta(days=1)).isoformat()
            # nothing planned tomorrow either: suggest it the day before, so the athlete can organise the day
            sug_t = (
                None
                if any(p["date"] == tmrw for p in planned.get(name, []))
                else suggestion(name, A, planned, analysis, tmrw, race, extra={**sug, "date": today} if sug else None)
            )
            foc = focus(name, planned, analysis, today)
        except Exception as e:
            print(f"morning brief {name} suggestion/focus: {type(e).__name__}")
            sug = sug_t = foc = None
        extra = ""
        if sug:
            extra += f"\n\nSUGESTÃO DA APP PARA HOJE (o plano do treinador está vazio): {SPORTS[sug['sport']]} {sug['title']}, {sug['minutes']} min. {sug['description']} Porquê: {sug['why']}"
        if foc:
            extra += f"\n\nTREINO DA SEMANA A QUE MAIS TE DEVES DEDICAR: {foc['date']} {foc['title']}. {foc['why']}"
        user = f"Atleta: {name}\nProva-alvo: {race}\nCONTEXTO: {context_line(name, today)}\n\n{describe(yesterday, plan)}{extra}"
        try:
            b = {"date": today, "yesterday": yesterday, "today": plan, "voice": v, "text": write(SYSTEM, user)}
            if sug:
                b["suggestion"] = sug
            if sug_t:
                b["suggestion_tomorrow"] = sug_t
            if foc:
                b["focus"] = foc
            ruser = f"Atleta: {name}\nProva-alvo: {target_race(name)}\nCONTEXTO: {context_line(name, today)}\n\n" + report_input(
                name, A, notes, planned, analysis, today
            )
            rec = recovery_section(name)
            b["report"] = assemble(write(REPORT_SYSTEM, ruser), rec)
            if v != voice.NEUTRAL:
                b["text_voice"] = write(SYSTEM + voice.tone(v), user)
                b["report_voice"] = assemble(write(REPORT_SYSTEM + voice.tone(v), ruser), rec)
        except Exception as e:
            print(f"morning brief {name}: {type(e).__name__}")
            continue
        briefs[name] = b
        done += 1
    BRIEF.write_text(json.dumps(briefs, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"morning brief: {done} written, {len(briefs)} for today")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:] or None))

"""Athlete context (injuries, breaks) and the grounded Q&A assistant.

The assistant answers only from: the athlete's skill findings (analysis.json), the rules of each skill (skills/*.md),
a compact set of the athlete's metrics (dashboard_data.json, race_impact.json) and the context periods registered by
the athlete or the coach. The model never sees raw training files.
"""

import json, sqlite3, sys, urllib.request, threading, queue
from contextlib import contextmanager
from datetime import datetime, date, timedelta
from . import config, demo

KINDS = {
    "lesao": "Lesão",
    "doenca": "Doença",
    "pausa": "Pausa planeada / férias",
    "familia": "Parentalidade / família",
    "fora_epoca": "Fora de época",
}
sys.path.insert(0, str(config.REPO_DIR))
import llm, voice, health, userdata  # noqa: E402  (shared with the pipeline scripts at the repo root)

SKILLS_DIR = config.REPO_DIR / "skills"


@contextmanager
def db():
    con = sqlite3.connect(config.DB)
    con.row_factory = sqlite3.Row
    con.execute("""CREATE TABLE IF NOT EXISTS athlete_context (id INTEGER PRIMARY KEY, athlete TEXT NOT NULL, kind TEXT NOT NULL,
                   start TEXT NOT NULL, end TEXT, note TEXT, created_by TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP)""")
    con.execute("""CREATE TABLE IF NOT EXISTS finding_ack (id INTEGER PRIMARY KEY, athlete TEXT NOT NULL, title TEXT NOT NULL,
                   evidence TEXT NOT NULL, created_by TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP)""")
    con.execute(
        """CREATE TABLE IF NOT EXISTS qa_log (id INTEGER PRIMARY KEY, athlete TEXT, asked_by TEXT, question TEXT, answer TEXT,
                   model TEXT, seconds REAL, created_at TEXT DEFAULT CURRENT_TIMESTAMP)"""
    )
    try:
        yield con
        con.commit()
    finally:
        con.close()


# ------------------------------------------------------------------ context periods
def list_context(athlete):
    with db() as con:
        return [
            dict(r)
            for r in con.execute(
                "SELECT id,athlete,kind,start,end,note,created_by,created_at FROM athlete_context WHERE athlete=? ORDER BY start DESC",
                (athlete,),
            )
        ]


def add_context(athlete, kind, start, end, note, who):
    if kind not in KINDS:
        raise ValueError("tipo inválido")
    date.fromisoformat(start)
    if end:
        date.fromisoformat(end)
    if end and end < start:
        raise ValueError("o fim é anterior ao início")
    with db() as con:
        con.execute(
            "INSERT INTO athlete_context(athlete,kind,start,end,note,created_by) VALUES(?,?,?,?,?,?)",
            (athlete, kind, start, end or None, (note or "").strip()[:500], who),
        )


def ack_finding(athlete, title, evidence, who):
    """Ignore a finding for the sessions it lists now (skills_engine.apply_acks); a new session brings it back."""
    ev = sorted({str(e)[:16] for e in (evidence or [])})
    if not title or not ev:
        raise ValueError("alerta sem sessões para ignorar")
    with db() as con:
        con.execute(
            "INSERT INTO finding_ack(athlete,title,evidence,created_by) VALUES(?,?,?,?)",
            (athlete, title[:200], json.dumps(ev), who),
        )


def unack_finding(athlete, title):
    with db() as con:
        con.execute("DELETE FROM finding_ack WHERE athlete=? AND title=?", (athlete, title))


def delete_context(athlete, cid):
    with db() as con:
        con.execute("DELETE FROM athlete_context WHERE id=? AND athlete=?", (cid, athlete))


def context_text(athlete, today=None):
    today = today or date.today().isoformat()
    out = []
    for c in list_context(athlete):
        status = (
            "em curso"
            if (not c["end"] or c["end"] >= today) and c["start"] <= today
            else ("terminado" if c["end"] and c["end"] < today else "futuro")
        )
        weeks = None
        try:
            ref = c["end"] if c["end"] and c["end"] < today else today
            weeks = (date.fromisoformat(ref) - date.fromisoformat(c["start"])).days // 7
        except Exception:
            pass
        since_end = ""
        if c["end"] and c["end"] < today:
            since_end = f", terminou há {(date.fromisoformat(today) - date.fromisoformat(c['end'])).days // 7} semanas"
        out.append(
            f"- {KINDS.get(c['kind'], c['kind'])}: de {c['start']} a {c['end'] or 'sem data de fim'} ({status}, {weeks} semanas{since_end}). Nota: {c['note'] or '—'}"
        )
    return "\n".join(out) or "- Nenhum período registado."


# ------------------------------------------------------------------ facts for the model
def _load(name):
    p = config.DATA_JSON.parent / name
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def facts(athlete):
    src = demo.source(athlete)  # the demo login answers from an anonymised copy (app/demo.py)
    an = _load("analysis.json").get(src) or {}
    if athlete == demo.DEMO:
        an = demo.analysis(an)
    dash = json.loads(config.DATA_JSON.read_text(encoding="utf-8")).get("athletes", {}).get(src, {})
    races = _load("race_impact.json")
    weeks = dash.get("weekly52", [])[-12:]
    pmc = dash.get("pmc", {})
    daily = pmc.get("daily", [])
    ctl_8w = [{"d": d["d"], "ctl": d["ctl"], "tsb": d["tsb"]} for d in daily[-56::7]]
    f = {
        "atleta": demo.NAME if athlete == demo.DEMO else dash.get("full_name", athlete),
        "hoje": date.today().isoformat(),
        "forma_hoje": pmc.get("now"),
        "pico_ctl_52_semanas": pmc.get("peak_52w"),
        "ftp_w": dash.get("ftp"),
        "lthr_corrida_bpm": dash.get("lthr"),
        "lthr_bike_bpm": dash.get("lthr_bike"),
        "peso_kg": dash.get("weight_kg"),
        "ultimas_12_semanas": [{"semana": w["w"], "horas": w["h"], "tss": w["tss"], "sessoes": w["n"]} for w in weeks],
        "ctl_e_tsb_semanal_8_semanas": ctl_8w,
        "modelo_potencia_duracao_90_dias": {  # OmPD, análogo ao WKO5: mftp (teto aeróbio), tte_min (tempo que o aguenta), frc_kj, pmax
            **((dash.get("power_duration") or {}).get("model") or {}),
            "ftp_em_uso": (dash.get("power_duration") or {}).get("ftp_in_use"),
            "evolucao_mensal": ((dash.get("power_duration") or {}).get("history") or [])[-6:],
        },
        "achados_das_skills": [
            {"skill": x["skill"], "gravidade": x["severity"], "titulo": x["title"], "detalhe": x["detail"]}
            for x in an.get("findings", [])
        ],
    }
    since = (date.today() - timedelta(days=14)).isoformat()
    cm = [
        {"data": c["day"], "autor": c["author"], "papel": c["role"], "texto": c["text"]}
        for cs in userdata.comments(src, since=since, replies=False).values()
        for c in cs
    ]
    if cm:  # what the athlete or the coach wrote on sessions: reasons, sensations (they change the reading)
        f["comentarios_nos_treinos_14_dias"] = cm
    hk = health.recent(athlete, 14)
    if hk:  # Apple Health from the native app: sleep (h), resting HR (bpm), HRV SDNN (ms)
        f["recuperacao_apple_health_14_dias"] = hk
    for slug, r in races.items():
        p = r.get("athletes", {}).get(src)
        if p and p.get("total"):
            f.setdefault("previsoes_de_prova", []).append(
                {
                    "prova": r["race"]["name"],
                    "data": r["race"]["date"],
                    "total": p["total"],
                    "bike_w_plano": p.get("bike", {}).get("flat_w"),
                    "lacunas": [{"tema": g["topic"], "gravidade": g["severity"], "texto": g["text"]} for g in p.get("gaps", [])],
                    "notas": p.get("notes", []),
                }
            )
    return f


def skill_rules():
    return "\n\n".join(p.read_text(encoding="utf-8") for p in sorted(SKILLS_DIR.glob("*.md")) if p.name != "README.md")


SYSTEM = """És o assistente de análise de treino do Tri Squad. Respondes em português europeu, de forma direta, a quem pergunta (atleta ou treinador), tratando o atleta por tu.

Regras de fundamentação, obrigatórias:
1. Sobre o atleta, usa APENAS os FACTOS (métricas e achados das skills), as REGRAS DAS SKILLS e o CONTEXTO DO ATLETA abaixo. Para perguntas de treino em geral (equivalências entre modalidades, como substituir um treino, princípios de treino de endurance) podes usar conhecimento geral de treino, dizendo que é uma regra geral, e liga-o aos dados do atleta quando fizer sentido.
2. Se a resposta exigir um dado do atleta que não está aí (por exemplo dor, um treino específico que não aparece), diz claramente que essa informação não está disponível e o que seria preciso. Não inventes números, datas, sessões nem diagnósticos médicos.
3. Quando usares um achado, indica a skill entre parênteses, por exemplo (load_monitor).
4. O contexto registado (lesões, pausas, família) muda a leitura: depois de uma lesão ou pausa, uma subida de carga é retoma e avalia-se pelas regras de carga (rampa de CTL até 5 a 7 pontos por semana, sem semanas mais de 10% acima do máximo recente), não como falha; durante uma pausa, a queda de CTL é esperada.
5. Não dás conselhos médicos: para dor, lesão ou regresso depois de fratura, remete para o médico ou fisioterapeuta e limita-te ao que a carga mostra.
6. Se o CONTEXTO tiver uma lesão ou doença em curso, ou terminada há menos de 8 semanas: começa a resposta por aí; deduz da nota a disciplina afetada (pé, tornozelo, perna ou joelho afeta sobretudo a corrida; ombro ou braço afeta a natação; costas ou anca podem afetar todas); para essa disciplina, as metas de volume das skills (corridas longas, pedais longos) passam a ser de médio prazo, a progressão é prudente (as outras disciplinas podem carregar primeiro) e diz explicitamente que o regresso à carga nessa disciplina deve ter autorização do médico ou fisioterapeuta. Uma pausa planeada ou de família terminada há pouco é retoma, sem tom de alarme.
7. Responde de forma concreta e prática: quando sugeres ou substituis um treino, dá a duração, as zonas e a estrutura.
8. Máximo 180 palavras. Texto simples, podes usar listas curtas e **negrito**. Sem LaTeX."""


def compact_facts(athlete, tone="", limit=2500):
    """Only precomputed values, one labelled line each, for Apple's ~3B on-device model: it answers direct questions ("qual o
    meu FTP?", "como está a minha forma?") by reading one line, and sends everything else to the box (PRECISO_DO_BOX).
    No findings, comments or context: with them the small model mixed numbers up and made answers up."""
    src = demo.source(athlete)
    dash = json.loads(config.DATA_JSON.read_text(encoding="utf-8")).get("athletes", {}).get(src, {})
    r1 = lambda x, n=0: None if x is None else (round(x, n) if n else int(round(x)))
    lines = [f"Hoje: {date.today().isoformat()}"]
    pmc = dash.get("pmc") or {}
    now, peak = pmc.get("now") or {}, pmc.get("peak_52w") or {}
    if now:
        lines.append(
            f"Forma hoje: CTL (fitness) {r1(now.get('ctl'))}, ATL (fadiga) {r1(now.get('atl'))}, TSB (frescura) {r1(now.get('tsb'))}"
        )
    if peak:
        lines.append(f"Pico de CTL nas últimas 52 semanas: {r1(peak.get('ctl'))} em {peak.get('date')}")
    pdm = dash.get("power_duration") or {}
    m = pdm.get("model") or {}
    if dash.get("ftp"):
        w = dash.get("weight_kg")
        lines.append(f"FTP em uso: {r1(dash['ftp'])} W" + (f" ({dash['ftp'] / w:.2f} W/kg)" if w else ""))
    if m.get("mftp"):
        lines.append(f"mFTP do modelo potência-duração (90 dias): {m['mftp']} W, aguentado {m.get('tte_min')} min (TTE)")
    if m.get("pmax"):
        lines.append(f"Potência máxima (Pmax): {m['pmax']} W; FRC {m.get('frc_kj')} kJ")
    if dash.get("lthr"):
        lines.append(
            f"Limiar de frequência cardíaca (LTHR): corrida {r1(dash['lthr'])} bpm"
            + (f", bike {r1(dash['lthr_bike'])} bpm" if dash.get("lthr_bike") else "")
        )
    if dash.get("weight_kg"):
        lines.append(f"Peso: {r1(dash['weight_kg'], 1)} kg")
    weeks = dash.get("weekly52") or []
    for label, w in (("Esta semana", weeks[-1] if weeks else None), ("Semana passada", weeks[-2] if len(weeks) > 1 else None)):
        if w:
            lines.append(f"{label} (desde {w['w']}): {w['h']:.1f} h, {r1(w['tss'])} TSS, {w['n']} sessões")
    if len(weeks) >= 5:
        last4 = weeks[-5:-1]
        lines.append(
            f"Média das 4 semanas anteriores: {sum(x['h'] for x in last4) / 4:.1f} h e {r1(sum(x['tss'] for x in last4) / 4)} TSS por semana"
        )
    rec = dash.get("recent") or []
    if rec:
        x = rec[0]
        k = (x.get("kind") or {}).get("type")
        lines.append(
            f"Último treino: {x['date'][:10]}, {x.get('sport')}, {x.get('title') or 'sem título'}, {round((x.get('h') or 0) * 60)} min"
            + (f", {r1(x['tss'])} TSS" if x.get("tss") else "")
            + (f", tipo {k}" if k else "")
        )
    today = date.today().isoformat()
    try:
        planned = json.loads((config.DATA_JSON.parent / "intervals_planned.json").read_text(encoding="utf-8")).get(src, [])
    except Exception:
        planned = []
    for label, day in (("Plano de hoje", today), ("Plano de amanhã", (date.today() + timedelta(days=1)).isoformat())):
        ps = [p for p in planned if p.get("date") == day]
        if ps:
            lines.append(
                f"{label}: "
                + "; ".join(
                    f"{p.get('title')}" + (f" ({round(p['duration_s'] / 60)} min)" if p.get("duration_s") else "") for p in ps
                )
            )
    hk = health.recent(athlete, 3)
    if hk:
        h = hk[-1]
        parts = [
            f"sono {h['sleep_h']:.1f} h" if h.get("sleep_h") else None,
            f"FC em repouso {r1(h['rhr'])} bpm" if h.get("rhr") else None,
        ]
        hrv = h.get("hrv_rmssd") or h.get("hrv")
        parts.append(f"HRV {r1(hrv)} ms" if hrv else None)
        if any(parts):
            lines.append(f"Última noite ({h['date']}): " + ", ".join(p for p in parts if p))
    races = sorted(
        (r["race"]["date"], r["race"]["name"], p["total"].get("time"))
        for r in _load("race_impact.json").values()
        for p in [r.get("athletes", {}).get(src)]
        if p and p.get("total") and r["race"]["date"] >= today
    )
    for d, name, t in races[:3]:  # the next three: the small model reads the nearest ones, not a calendar
        lines.append(f"Próxima prova: {name} em {d}, tempo previsto {t}")
    system = (
        "És o assistente de treino do Tri Squad no iPhone. Recebes uma lista de VALORES do atleta, já calculados. "
        "Se a pergunta pede um destes valores, responde em português europeu, numa ou duas frases curtas, tratando o atleta "
        "por tu, com o número exatamente como está na lista. Para qualquer outra coisa (porquês, conselhos, avaliações, "
        "planos, comparações, treinos a fazer, números que não estão na lista, ou se tiveres dúvida) responde só a palavra "
        "PRECISO_DO_BOX. Nunca calcules nem inventes números." + tone
    )
    return {"system": system, "facts": ("VALORES:\n" + "\n".join(lines))[:limit], "context": ""}


def build_messages(athlete, question, history, tone=""):
    f = facts(athlete)
    user_block = (
        f"FACTOS (JSON):\n{json.dumps(f, ensure_ascii=False)}\n\nREGRAS DAS SKILLS:\n{skill_rules()}\n\n"
        f"CONTEXTO DO ATLETA (registado pelo atleta ou pelo treinador):\n{context_text(athlete)}"
    )
    msgs = [
        {"role": "system", "content": SYSTEM + tone},
        {"role": "user", "content": user_block},
        {"role": "assistant", "content": "Entendido. Respondo só com base nisto."},
    ]
    for h in (history or [])[-6:]:
        if h.get("role") in ("user", "assistant") and isinstance(h.get("content"), str):
            msgs.append({"role": h["role"], "content": h["content"][:1500]})
    msgs.append({"role": "user", "content": question[:1000]})
    return msgs


HEARTBEAT = (
    "\u200b"  # zero-width space: keeps proxies (Cloudflare closes after 100 s without bytes) from timing out; the page strips it
)


def warm(athlete):
    """Pre-process the athlete's facts in the model (prefix KV cache) so the first question answers faster. Fire and forget."""
    if not llm.available():
        return

    def run():
        try:
            msgs = build_messages(athlete, "ok", [])[:3] + [{"role": "user", "content": "Responde só: pronto."}]
            urllib.request.urlopen(llm.request(msgs, temperature=0, stream=False, max_tokens=1), timeout=300).read()
        except Exception:
            pass

    threading.Thread(target=run, daemon=True).start()


# ------------------------------------------------------------------ answers the box prepares in the background
def _jobs(con):
    con.execute(
        """CREATE TABLE IF NOT EXISTS qa_jobs (id INTEGER PRIMARY KEY, athlete TEXT, email TEXT, question TEXT, answer TEXT,
           status TEXT DEFAULT 'pending', created_at TEXT DEFAULT CURRENT_TIMESTAMP, done_at TEXT)"""
    )


def notify_ready(who, question, text, lid):
    """Push "Resposta pronta"; opening it shows the conversation with that answer (/?resposta=<qa_log id>)."""
    try:
        import push

        first = (text or "").replace("\n", " ").split(". ")[0][:180]
        push.send(push.devices_of(who), "Resposta pronta", f"{question[:60]}… — {first}", url=f"/?resposta={lid}")
    except Exception:
        pass


def chat_history(athlete, who, days=30, limit=12):
    """The asker's saved conversation about this athlete, oldest first: answered questions (qa_log) and the ones the box is
    still preparing in the background (qa_jobs pending). The chat panel rebuilds itself from this on every open."""
    since = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
    with db() as con:
        _jobs(con)
        done = [
            dict(r)
            for r in con.execute(
                "SELECT id, question, answer, created_at FROM qa_log WHERE athlete=? AND asked_by=? AND created_at>=? "
                "AND coalesce(answer,'')!='' ORDER BY id DESC LIMIT ?",
                (athlete, who, since, limit),
            )
        ]
        pending = [
            dict(r)
            for r in con.execute(
                "SELECT id AS job, question, created_at FROM qa_jobs WHERE athlete=? AND email=? AND status='pending' AND created_at>=?",
                (athlete, who, (datetime.now() - timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")),
            )
        ]
    return sorted(done, key=lambda r: r["id"]) + pending


def ask_later(athlete, question, history, who, tone=""):
    """The box answers in the background (the app can be closed) and the asker gets a push when it is ready. Returns the id."""
    with db() as con:
        _jobs(con)
        cur = con.execute("INSERT INTO qa_jobs(athlete,email,question) VALUES(?,?,?)", (athlete, who, question[:1000]))
        jid = cur.lastrowid

    def run():
        t0 = datetime.now()
        try:
            msgs = build_messages(athlete, question, history, tone)  # deferred by the iPhone as hard, and nobody waits: reason
            text = voice.scrub(llm.clean(llm.chat(msgs, timeout=900, temperature=0.2, think=True), strip=True))
            status = "done" if text else "failed"
        except Exception as e:
            text, status = f"O box não conseguiu responder ({type(e).__name__}). Tenta outra vez daqui a pouco.", "failed"
        with db() as con:
            _jobs(con)
            con.execute("UPDATE qa_jobs SET answer=?, status=?, done_at=CURRENT_TIMESTAMP WHERE id=?", (text, status, jid))
            lid = con.execute(
                "INSERT INTO qa_log(athlete,asked_by,question,answer,model,seconds) VALUES(?,?,?,?,?,?)",
                (athlete, who, question[:1000], text, f"{llm.MODEL} +raciocínio", (datetime.now() - t0).total_seconds()),
            ).lastrowid
        notify_ready(who, question, text, lid)

    threading.Thread(target=run, daemon=True).start()
    return jid


def answers(athlete, who, limit=10):
    """The asker's recent background answers (newest first)."""
    with db() as con:
        _jobs(con)
        return [
            dict(r)
            for r in con.execute(
                "SELECT id, question, answer, status, created_at, done_at FROM qa_jobs WHERE athlete=? AND email=? ORDER BY id DESC LIMIT ?",
                (athlete, who, limit),
            )
        ]


# ------------------------------------------------------------------ replies to the athlete's comments on a session
REPLY_SYSTEM = (
    "És o assistente do treinador de triatlo do Tri Squad e respondes ao comentário que o atleta deixou num treino. Escreves em "
    "português europeu, tratando o atleta por tu, em 2 ou 3 frases curtas (máximo 70 palavras). Reconhece o que o atleta disse, "
    "explica o que isso muda (ou não muda) no efeito do treino, com um dado concreto do treino ou do plano, e, se fizer sentido, "
    "dá um ajuste prático para os próximos dias. Regras: usa só os dados do treino, do plano, do contexto e dos comentários; não "
    "inventes números nem sessões; o TIPO foi calculado a partir do tempo em cada zona e é a verdade sobre a intensidade; não "
    "dês conselhos médicos: perante dor, desconforto ou lesão, diz para falar com o médico ou fisioterapeuta se persistir; "
    "o treinador lê os comentários, não digas que vais avisá-lo. Sem listas, sem LaTeX, sem emojis."
)


def reply_input(athlete, cid):
    """What the model reads to reply to comment `cid`: the session, its note, the plan of that day and the thread so far."""
    import session_notes  # repo root

    c = userdata.comment(athlete, cid)
    if not c:
        return None
    from . import render

    A = render.load_data().get("athletes", {}).get(athlete, {})
    s = next((x for x in A.get("recent", []) + A.get("executions", []) if x.get("key") == c["session_key"]), None)
    day = c["day"] or (s or {}).get("date", "")[:10]
    parts = [f"Atleta: {athlete}", f"CONTEXTO: {session_notes.context_line(athlete, date.today().isoformat())}"]
    if s:
        parts.append(f"Treino de {s['date'][:10]}:\n{session_notes.describe(s)}")
        if s.get("note"):
            parts.append(f"Nota da app sobre este treino: {s['note']}")
    plan = [p for p in A.get("planned", []) if p.get("date") == day]
    for p in plan[:3]:
        mins = f", {round(p['duration_s'] / 60)} min" if p.get("duration_s") else ""
        parts.append(f"Plano do treinador nesse dia: {p.get('title')}{mins}. {(p.get('description') or '')[:800]}")
    thread = [x for x in userdata.comments(athlete).get(c["session_key"], []) if x["id"] <= cid]
    parts.append(
        "COMENTÁRIOS (o último é o que respondes):\n"
        + "\n".join(f"- {'Assistente' if x['role'] == 'assistant' else x['author']}: {x['text']}" for x in thread)
    )
    return "\n\n".join(parts)


def reply_to_comment(athlete, cid, who):
    """The box replies to an athlete's comment in the background, in the athlete's voice, and pushes it to them."""
    if not llm.available() or athlete == demo.DEMO:
        return

    def run():
        try:
            import session_notes
            from skills_engine import load_context

            user = reply_input(athlete, cid)
            if not user:
                return
            v = voice.get(athlete)
            if voice.health_ongoing(load_context(athlete), date.today().isoformat()):
                v = voice.NEUTRAL
            msgs = [{"role": "system", "content": REPLY_SYSTEM + voice.tone(v)}, {"role": "user", "content": user}]
            text = voice.scrub(llm.clean(llm.chat(msgs, timeout=300, temperature=0.3), strip=True))
            if not userdata.add_reply(athlete, cid, text):
                return
            import push

            push.send(push.devices_of(who), "Resposta ao teu comentário", text.replace("\n", " ")[:180], url="/")
        except Exception as e:
            print(f"comment reply {athlete} {cid}: {type(e).__name__} {e}")

    threading.Thread(target=run, daemon=True).start()


# ------------------------------------------------------------------ when the chat answer needs the model's reasoning
ROUTER = (
    "Antes de responder, classifica a última pergunta. Responde só com uma palavra:\n"
    "PENSAR se a resposta exige decidir ou planear com várias regras ao mesmo tempo: planear dias ou semanas, sugerir, "
    "substituir ou comparar treinos, equivalências entre modalidades, ajustar a carga a uma lesão, a uma prova ou ao cansaço, "
    "ou explicar porquês que cruzam vários dados;\n"
    "SIMPLES se é ler ou explicar um dado ou um conceito: um número, uma métrica, uma tendência, o que significa um termo, "
    "um treino já feito, um cumprimento ou um agradecimento."
)
THINKING = (
    "\u2060"  # word joiner: tells the page the model is reasoning first (it shows "a pensar…"), stripped like the heartbeat
)


def needs_thinking(athlete, question, history, tone=""):
    """(think, how): the same model, reasoning off, says SIMPLES or PENSAR. The question goes after the same facts prefix the
    answer uses, so the model's prompt cache is shared and the decision costs about a second. Any failure answers quickly."""
    if not question.strip():
        return False, "vazia"
    msgs = build_messages(athlete, question, history, tone)
    msgs[-1] = {"role": "user", "content": f"{ROUTER}\n\nPergunta: {question[:1000]}"}
    try:
        out = llm.chat(msgs, timeout=120, temperature=0, max_tokens=4)
    except Exception as e:
        return False, f"router falhou ({type(e).__name__})"
    return "PENSAR" in out.upper(), out.strip()[:20]


def stream_answer(athlete, question, history, who, tone=""):
    """Yield answer text chunks from the LLM (OpenAI-compatible streaming), with heartbeats while the model reads the prompt;
    log the full answer when done."""
    if not llm.available():
        yield "O assistente não está configurado (TRIAPP_LLM_URL)."
        return
    yield HEARTBEAT
    think, how = needs_thinking(athlete, question, history, tone)
    if think:
        yield THINKING
    req = llm.request(build_messages(athlete, question, history, tone), temperature=0.2, stream=True, think=think)
    q = queue.Queue()
    END = object()

    def reader():
        try:
            with urllib.request.urlopen(req, timeout=300) as resp:
                in_think = False
                for raw in resp:
                    line = raw.decode("utf-8", "ignore").strip()
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        break
                    try:
                        delta = json.loads(data)["choices"][0]["delta"].get("content") or ""
                    except Exception:
                        continue
                    if "<think>" in delta:
                        in_think = True
                    if in_think:
                        if "</think>" in delta:
                            in_think = False
                            delta = delta.split("</think>", 1)[1]
                        else:
                            continue
                    delta = llm.clean(delta, strip=False)
                    if delta:
                        q.put(delta)
        except Exception as e:
            q.put(f"\n\n(O modelo não respondeu: {type(e).__name__}. Tenta outra vez daqui a pouco.)")
        finally:
            q.put(END)

    threading.Thread(target=reader, daemon=True).start()
    t0 = datetime.now()
    full, finished = [], False

    def save():
        with db() as con:
            return con.execute(
                "INSERT INTO qa_log(athlete,asked_by,question,answer,model,seconds) VALUES(?,?,?,?,?,?)",
                (
                    athlete,
                    who,
                    question[:1000],
                    voice.scrub(llm.clean("".join(full), strip=False)),
                    f"{llm.MODEL}{' +raciocínio' if think else ''} [{how}]",
                    (datetime.now() - t0).total_seconds(),
                ),
            ).lastrowid

    def finish_unseen():  # the page went away (app closed, phone locked): the answer is finished, saved and pushed
        while True:
            item = q.get()
            if item is END:
                break
            full.append(item)
        notify_ready(who, question, "".join(full), save())

    yield HEARTBEAT
    try:
        while True:
            try:
                item = q.get(timeout=15)
            except queue.Empty:
                yield HEARTBEAT
                continue
            if item is END:
                finished = True
                break
            full.append(item)
            yield item
    finally:
        if finished:
            save()
        else:
            threading.Thread(target=finish_unseen, daemon=True).start()

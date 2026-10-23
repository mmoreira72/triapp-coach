"""Plain-language benefit of each recent session, written by the local LLM from the deterministic session type.

Reads the recent sessions of dashboard_data.json (type and time in zones from session_types.py), the athlete's context
periods (injury, break) and the target race, and asks the model for 2-3 sentences: what the session trains and how it
fits. Each text is generated once and cached in <OUT>/data/session_notes.json by session key; sessions older than
NOTE_DAYS are not written (only the cache is kept). At most NOTE_MAX new texts per run, newest first.

The neutral text ("text") is what the coach reads. When the athlete picked another voice (voice.py), a second text in that
voice ("text_voice", "voice") is written for the athlete's own view, except while an injury or illness is ongoing.

Usage: python session_notes.py [Athlete ...]
"""

import os, sys, json
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import llm, voice
from skills_engine import load_context, KIND_LABEL

OUT = Path(os.environ.get("TRIAPP_OUT", Path(__file__).resolve().parent / "out")) / "data"
NOTES = OUT / "session_notes.json"
NOTE_DAYS = int(os.environ.get("TRIAPP_NOTE_DAYS", "10"))
NOTE_MAX = int(os.environ.get("TRIAPP_NOTE_MAX", "40"))
SPORT = {"run": "corrida", "bike": "bike", "swim": "natação", "strength": "força"}

SYSTEM = (
    "És o assistente de um treinador de triatlo. Escreves em português europeu, para um atleta amador que pode não conhecer "
    "termos técnicos, tratando-o por tu. Explicas em 2 ou 3 frases curtas (máximo 60 palavras) o benefício do treino descrito: "
    "o que treina no corpo e para que serve na preparação, citando um dado concreto do treino (duração, tempo em zona fácil ou intensa, "
    "ou os blocos). Regras: usa só os dados fornecidos; o TIPO foi calculado a partir do tempo "
    "em cada zona e é a verdade sobre a intensidade, mesmo que o título diga outra coisa; não inventes números, zonas nem sessões; "
    "se a base for FC e houver blocos curtos, a intensidade real pode ter sido maior do que a FC mostra; se o medidor de potência falhou, "
    "não fales de potência; lesões: pé, tornozelo, perna ou joelho afetam a corrida, ombro ou braço afetam a natação, costas ou anca "
    "afetam todas; só refere uma lesão recente do CONTEXTO se este treino for da disciplina afetada (retoma, com naturalidade, sem "
    "conselhos médicos); uma pausa de férias ou família recente pode ser referida em qualquer treino. Sem listas, sem LaTeX, sem emojis."
)


def context_line(name, today):
    out = []
    for c in load_context(name):
        end = c["end"] or today
        if end >= (date.fromisoformat(today) - timedelta(weeks=12)).isoformat():
            out.append(
                f"{KIND_LABEL.get(c['kind'], c['kind'])} de {c['start']} a {c['end'] or 'em curso'} ({c['note'] or 'sem nota'})"
            )
    return "; ".join(out) or "nenhum"


def target_race(name):
    try:
        ri = json.loads((OUT / "race_impact.json").read_text(encoding="utf-8"))
        for r in ri.values():
            if name in r.get("athletes", {}):
                return f"{r['race']['name']} em {r['race']['date']}"
    except Exception:
        pass
    return "nenhuma"


def describe(s):
    k = s.get("kind") or {}
    zones = ", ".join(f"{z} {round(v * 100)}%" for z, v in (k.get("zones") or {}).items())
    parts = [
        f"Desporto: {SPORT.get(s['sport'], s['sport'])}",
        f"Título: {s.get('title') or 'sem título'}",
        f"Duração: {round((s.get('h') or 0) * 60)} min",
    ]
    if s.get("km"):
        parts.append(f"Distância: {s['km']} km")
    parts.append(f"TIPO: {k.get('type', 'desconhecido')}")
    if zones:
        parts.append(f"Tempo em zona ({k.get('basis')}): {zones}")
    if s.get("tss"):
        parts.append(f"TSS: {round(s['tss'])}")
    if s.get("if"):
        parts.append(f"IF: {s['if']}")
    if s.get("power_dropout"):
        parts.append("O medidor de potência falhou durante o treino; a intensidade foi lida pela FC.")
    return "\n".join(parts)


def write(system, user):
    return llm.chat([{"role": "system", "content": system}, {"role": "user", "content": user}], timeout=180, temperature=0.3)


def main(only=None):
    if not llm.available():
        print("session notes: no LLM configured")
        return 0
    dash = json.loads((OUT / "dashboard_data.json").read_text(encoding="utf-8"))
    try:
        notes = json.loads(NOTES.read_text(encoding="utf-8"))
    except Exception:
        notes = {}
    today = dash.get("generated") or date.today().isoformat()
    since = (date.fromisoformat(today) - timedelta(days=NOTE_DAYS)).isoformat()
    voices = {}

    def wanted(name):  # the athlete's voice, or neutral while an injury or illness is ongoing
        if name not in voices:
            v = voice.get(name)
            voices[name] = voice.NEUTRAL if voice.health_ongoing(load_context(name), today) else v
        return voices[name]

    todo = []
    for name, A in dash["athletes"].items():
        if only and name not in only:
            continue
        for s in A.get("recent", []):
            k = s.get("kind") or {}
            if s["date"][:10] < since or not k or k.get("type") == "Sem dados de intensidade":
                continue
            key = f"{name}|{s['key']}"
            n = notes.get(key)
            if n and n.get("type") == k.get("type") and n.get("voice", voice.NEUTRAL) == wanted(name):
                continue
            todo.append((s["date"], name, key, s))
    todo.sort(reverse=True)
    ctx_cache, race_cache, done = {}, {}, 0
    for _, name, key, s in todo[:NOTE_MAX]:
        ctx = ctx_cache.setdefault(name, context_line(name, today))
        race = race_cache.setdefault(name, target_race(name))
        user = f"Atleta: {name}\nProva-alvo: {race}\nCONTEXTO: {ctx}\n\nTreino de {s['date'][:10]}:\n{describe(s)}"
        typ, v, old = (s.get("kind") or {}).get("type"), wanted(name), notes.get(key) or {}
        try:
            text = old["text"] if old.get("type") == typ and old.get("text") else write(SYSTEM, user)
            n = {"text": text, "type": typ, "model": llm.MODEL, "written": today, "voice": v}
            if v != voice.NEUTRAL:
                n["text_voice"] = write(SYSTEM + voice.tone(v), user)
        except Exception as e:
            print(f"session notes {name} {s['date']}: {type(e).__name__}")
            continue
        notes[key] = n
        done += 1
    NOTES.write_text(json.dumps(notes, ensure_ascii=False, indent=0), encoding="utf-8")
    print(f"session notes: {done} written, {len(todo) - done} pending or failed, {len(notes)} cached")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:] or None))

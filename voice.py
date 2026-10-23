"""Voice of the LLM texts an athlete reads: each athlete picks one (stored in the app database, table athlete_voice).

The voice only changes the tone. Facts, numbers and recommendations stay the same, and three things are always neutral:
the coach's view (the coach reads the "treinador" text), alerts (said seriously inside the voiced text), and anything written while an injury or illness is ongoing.
"""

import os, re, sqlite3
from pathlib import Path

NEUTRAL = "treinador"
VOICES = {
    "treinador": {"label": "Treinador", "hint": "neutro e explicativo", "tone": ""},
    "claque": {
        "label": "Claque",
        "hint": "cada treino é uma conquista",
        "tone": (
            "PERSONALIDADE: és uma claque entusiasta, ao estilo das apps que festejam tudo. Celebras cada treino como uma "
            "conquista: começas com uma exclamação de festejo ('Que máquina!', 'Isto sim!'), usas 1 ou 2 emojis (🔥💪🎉) e "
            "tratas o dado do treino como um feito. O entusiasmo nunca muda os factos: "
            "os números, as zonas e as recomendações são os mesmos, e o que está mal continua a ser dito (com otimismo)."
        ),
    },
    "rabugento": {
        "label": "Velho rabugento",
        "hint": "seco, desconfiado, nunca impressionado",
        "tone": (
            "PERSONALIDADE: és um treinador velho e rabugento, seco e desconfiado, que já viu tudo e nunca se impressiona. "
            "Frases curtas, ironia seca, sem interjeições nem resmungos no início (nada de 'Hmpf' ou 'Pois'). Acabas com um elogio "
            "relutante ou uma exigência seca para a próxima, sempre por palavras tuas e diferentes de texto para texto (nunca repitas "
            "a mesma frase de fecho, nem copies exemplos); a exigência é sempre sobre o treino (cumprir, descansar, medir), nunca "
            "sobre a pessoa. Sem emojis. Nunca és ofensivo nem humilhas o atleta; não gozas com cansaço, doença ou vida pessoal; "
            "nunca comentas como o atleta escreve (erros de digitação, abreviaturas, a forma da pergunta) nem lhe dizes como se "
            "deve comportar: se não percebes uma palavra, perguntas o que quis dizer, sem reparo. A rabugice nunca muda os factos: os números, as zonas e as "
            "recomendações são os mesmos."
        ),
    },
}
SERIOUS = (
    " Exceção: o que é alerta ou risco (gravidade 'alert', sobrecarga, sinais de lesão, retoma depois de lesão ou doença, quebra de consistência) dizes a "
    "sério, sem piada nem festejo, numa frase própria."
)
RECENT_HEALTH_KINDS = ("lesao", "doenca")
# a sentence that remarks on how the athlete writes: the model adds it in the grumpy voice despite the rule, so it is cut
_ABOUT_WRITING = re.compile(
    r"[^.!?\n]*\b(escrev\w*\s+(direito|bem|melhor|como deve)|erros?\s+de\s+(digita\w*|ortografia|escrita)|"
    r"gralhas?|ortografia|aprende\s+a\s+escrever)\b[^.!?\n]*[.!?]?",
    re.I,
)


def scrub(text):
    """Drop sentences about the athlete's writing (typos, spelling); everything else stays as written."""
    if not text:
        return text
    out = _ABOUT_WRITING.sub("", text)
    out = re.sub(r"[ \t]+\n", "\n", out)
    return re.sub(r"\n{3,}", "\n\n", out).strip()


def _db():
    out = Path(os.environ.get("TRIAPP_OUT", Path(__file__).resolve().parent / "out"))
    con = sqlite3.connect(os.environ.get("TRIAPP_DB", str(out / "app.sqlite")))
    con.execute("CREATE TABLE IF NOT EXISTS athlete_voice (athlete TEXT PRIMARY KEY, voice TEXT NOT NULL)")
    return con


def get(athlete):
    try:
        con = _db()
        r = con.execute("SELECT voice FROM athlete_voice WHERE athlete=?", (athlete,)).fetchone()
        con.close()
        return r[0] if r and r[0] in VOICES else NEUTRAL
    except Exception:
        return NEUTRAL


def set(athlete, v):
    if v not in VOICES:
        raise ValueError("voz inválida")
    con = _db()
    con.execute(
        "INSERT INTO athlete_voice(athlete,voice) VALUES(?,?) ON CONFLICT(athlete) DO UPDATE SET voice=excluded.voice",
        (athlete, v),
    )
    con.commit()
    con.close()


def tone(v):
    """Text appended to a system prompt ('' for the neutral voice)."""
    t = VOICES.get(v, VOICES[NEUTRAL])["tone"]
    return f"\n\n{t}{SERIOUS}" if t else ""


def health_ongoing(ctx, today):
    """True while an injury or illness is ongoing (no end, or an end not yet reached): the texts then stay neutral. After it
    ends the voice comes back, and the return is said seriously (SERIOUS).
    ctx: rows with kind, start, end (ISO dates), as skills_engine.load_context returns them."""
    return any(c["kind"] in RECENT_HEALTH_KINDS and c["start"] <= today and (not c.get("end") or c["end"] >= today) for c in ctx)

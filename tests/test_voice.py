import voice


def test_default_is_neutral_and_choice_is_stored(tmp_path, monkeypatch):
    monkeypatch.setenv("TRIAPP_DB", str(tmp_path / "app.sqlite"))
    assert voice.get("Marcelo") == voice.NEUTRAL
    voice.set("Marcelo", "rabugento")
    assert voice.get("Marcelo") == "rabugento"
    assert voice.get("Athlete2") == voice.NEUTRAL


def test_unknown_voice_is_refused(tmp_path, monkeypatch):
    monkeypatch.setenv("TRIAPP_DB", str(tmp_path / "app.sqlite"))
    try:
        voice.set("Marcelo", "pirata")
        assert False
    except ValueError:
        pass


def test_neutral_adds_nothing_and_voices_keep_alerts_serious():
    assert voice.tone(voice.NEUTRAL) == ""
    for v in ("claque", "rabugento"):
        assert "PERSONALIDADE" in voice.tone(v) and "a sério" in voice.tone(v)


def test_only_an_ongoing_injury_or_illness_keeps_texts_neutral():
    today = "2026-10-09"
    assert voice.health_ongoing([{"kind": "lesao", "start": "2026-09-01", "end": None}], today)
    assert voice.health_ongoing([{"kind": "doenca", "start": "2026-10-01", "end": "2026-10-20"}], today)
    assert not voice.health_ongoing([{"kind": "lesao", "start": "2026-07-15", "end": "2026-09-15"}], today)  # back: voice returns
    assert not voice.health_ongoing([{"kind": "pausa", "start": "2026-10-01", "end": None}], today)


def test_scrub_drops_remarks_on_the_athletes_writing():
    t = "TSB -25, zona produtiva.\n\nSe for outra coisa, diz. Se quiseres análise, escreve direito."
    assert voice.scrub(t) == "TSB -25, zona produtiva.\n\nSe for outra coisa, diz."
    assert voice.scrub("Não me venhas com erros de digitação na próxima sessão.") == ""
    assert voice.scrub("Cumpre os 75 min amanhã.") == "Cumpre os 75 min amanhã."

import os

os.environ.setdefault("TRIAPP_SECRET", "x")
import pytest
from app import auth, config


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB", tmp_path / "app.sqlite")


def test_signup_waits_for_the_coach(db):
    uid = auth.create_athlete_account("Ana@X.pt ", "Ana Silva", "segredo123")
    assert [p["email"] for p in auth.pending_athletes()] == ["ana@x.pt"]
    assert auth.verify("ana@x.pt", "segredo123")["athlete"] is None
    with pytest.raises(ValueError):
        auth.create_athlete_account("ana@x.pt", "Outra", "segredo123")
    auth.set_athlete(uid, "Ana")
    assert auth.pending_athletes() == [] and auth.get_user(uid)["athlete"] == "Ana"


def test_coach_accounts_cannot_be_reassigned(db):
    auth.upsert_user("coach@x.pt", "Miguel", "coach", None, "coachpw123")
    cid = [u["id"] for u in auth.list_users() if u["email"] == "coach@x.pt"][0]
    auth.set_athlete(cid, "Marcelo")
    assert auth.get_user(cid)["athlete"] is None and auth.pending_athletes() == []


def test_password_reset_token(db, monkeypatch):
    import time

    auth.create_athlete_account("ana@x.pt", "Ana Silva", "segredo123")
    assert auth.create_reset("nao@existe.pt") is None
    tok = auth.create_reset("ANA@x.pt")
    assert auth.reset_user(tok)["email"] == "ana@x.pt" and auth.reset_user("errado") is None
    assert auth.use_reset(tok, "novasenha99") and auth.verify("ana@x.pt", "novasenha99")
    assert not auth.use_reset(tok, "outra12345")  # used once
    tok2 = auth.create_reset("ana@x.pt")
    monkeypatch.setattr(time, "time", lambda: 10**10)  # far in the future: expired
    assert auth.reset_user(tok2) is None


def test_background_answer_and_push(db, monkeypatch):
    from app import assistant
    import push, llm

    sent = []
    monkeypatch.setattr(llm, "chat", lambda msgs, **kw: "Faz 1h30 de corrida em Z2.")
    monkeypatch.setattr(assistant, "build_messages", lambda *a, **k: [])
    monkeypatch.setattr(push, "devices_of", lambda email: ["tok"] if email == "m@x" else [])
    monkeypatch.setattr(
        push,
        "send",
        lambda tokens, title, body, url="/", thread=None: sent.append((tokens, title, url)) or {"sent": 1, "failed": 0},
    )

    class Now:  # run the background thread inline
        def __init__(self, target, daemon=True):
            self.t = target

        def start(self):
            self.t()

    monkeypatch.setattr(assistant.threading, "Thread", Now)
    jid = assistant.ask_later("Marcelo", "3h de bike equivalem a quanto de corrida?", [], "m@x")
    (a,) = assistant.answers("Marcelo", "m@x")
    assert a["id"] == jid and a["status"] == "done" and a["answer"].startswith("Faz 1h30")
    assert sent == [(["tok"], "Resposta pronta", f"/?resposta={jid}")]
    assert assistant.answers("Marcelo", "outro@x") == []


def test_router_shares_the_facts_prefix_and_fails_quick(monkeypatch):
    from app import assistant
    import llm

    prefix = [{"role": "system", "content": "S"}, {"role": "user", "content": "FACTOS"}, {"role": "assistant", "content": "ok"}]
    monkeypatch.setattr(assistant, "build_messages", lambda a, q, h, t="": prefix + [{"role": "user", "content": q}])
    seen = []
    monkeypatch.setattr(llm, "chat", lambda msgs, **kw: seen.append((msgs, kw)) or "PENSAR")
    assert assistant.needs_thinking("Marcelo", "Planeia-me a semana", [])[0] is True
    msgs, kw = seen[0]
    assert msgs[:3] == prefix and "Planeia-me a semana" in msgs[3]["content"] and not kw.get("think")
    monkeypatch.setattr(llm, "chat", lambda msgs, **kw: "SIMPLES")
    assert assistant.needs_thinking("Marcelo", "Qual é o meu FTP?", [])[0] is False

    def boom(msgs, **kw):
        raise TimeoutError

    monkeypatch.setattr(llm, "chat", boom)
    assert assistant.needs_thinking("Marcelo", "Planeia-me a semana", [])[0] is False

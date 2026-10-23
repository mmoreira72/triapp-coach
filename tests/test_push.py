import httpx, pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
import push, notify

TOKEN = "a" * 64
ATH = {"email": "m@x", "role": "athlete", "athlete": "Marcelo"}
COACH = {"email": "c@x", "role": "coach", "athlete": None}


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("TRIAPP_DB", str(tmp_path / "app.sqlite"))
    return tmp_path


def test_register_and_route_devices(db):
    push.register(TOKEN, ATH)
    push.register("b" * 64, COACH)
    assert [d["token"] for d in push.devices(athlete="Marcelo")] == [TOKEN]
    assert push.devices(athlete="Athlete2") == []
    assert [d["token"] for d in push.devices(coaches=True)] == ["b" * 64]
    with pytest.raises(ValueError):
        push.register("short", ATH)


def test_each_event_is_sent_once(db):
    assert not push.already_sent("note|Marcelo|1")
    assert push.already_sent("note|Marcelo|1")


def test_send_without_apns_does_nothing(db, monkeypatch):
    monkeypatch.delenv("TRIAPP_APNS_KEY_PATH", raising=False)
    assert push.send([TOKEN], "t", "b")["sent"] == 0


def test_send_signs_and_drops_dead_tokens(db, tmp_path, monkeypatch):
    key = ec.generate_private_key(ec.SECP256R1()).private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    )
    (tmp_path / "k.p8").write_bytes(key)
    monkeypatch.setenv("TRIAPP_APNS_KEY_PATH", str(tmp_path / "k.p8"))
    monkeypatch.setenv("TRIAPP_APNS_KEY_ID", "KEY1234567")
    monkeypatch.setenv("TRIAPP_APNS_TEAM_ID", "TEAM123456")
    monkeypatch.setenv("TRIAPP_APNS_ENV", "development")
    push._jwt.clear()
    dead = "d" * 64
    push.register(TOKEN, ATH)
    push.register(dead, ATH)
    seen = []

    def apns(req):
        seen.append(req)
        if req.url.path.endswith(dead):
            return httpx.Response(410, json={"reason": "Unregistered"})
        if req.url.host != "api.sandbox.push.apple.com":
            return httpx.Response(400, json={"reason": "BadDeviceToken"})
        return httpx.Response(200)

    real = httpx.Client
    monkeypatch.setattr(httpx, "Client", lambda **kw: real(transport=httpx.MockTransport(apns)))
    r = push.send([TOKEN, dead], "Bike · 3x10'", "Nota pronta.")
    assert r == {"sent": 1, "failed": 1}
    assert seen[0].url.host == "api.sandbox.push.apple.com"
    assert seen[0].headers["apns-topic"] == "ai.laloop.trisquad" and seen[0].headers["authorization"].startswith("bearer ")
    assert [d["token"] for d in push.devices(athlete="Marcelo")] == [TOKEN]  # Apple said the other one is gone


def test_notify_events():
    dash = {
        "athletes": {
            "Marcelo": {
                "recent": [
                    {"key": "k1", "date": "2026-10-09 07:00", "sport": "bike", "title": "3x10'"},
                    {"key": "k0", "date": "2026-10-01 07:00", "sport": "run", "title": "old"},
                ]
            }
        }
    }
    notes = {"Marcelo|k1": {"text": "Neutro. Segunda.", "text_voice": "Hmpf. Não está mal."}, "Marcelo|k0": {"text": "x"}}
    ev = list(notify.note_events(dash, notes, "2026-10-09"))
    assert [(e[0], e[3]) for e in ev] == [("note|Marcelo|k1", "Bike · 3x10'")]
    assert ev[0][4].startswith("Hmpf.")  # the athlete hears their voice
    an = {"Marcelo": {"findings": [{"skill": "load_monitor", "severity": "alert", "title": "Rampa", "detail": "CTL subiu 9."}]}}
    ((key, name, coaches, title, body),) = notify.alert_events(an, "2026-10-09")
    assert key == "alert|Marcelo|load_monitor|Rampa" and coaches and title == "Atenção: Rampa"


def test_a_key_for_one_environment_never_drops_tokens(db, tmp_path, monkeypatch):
    key = ec.generate_private_key(ec.SECP256R1()).private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    )
    (tmp_path / "k.p8").write_bytes(key)
    for k, v in {
        "TRIAPP_APNS_KEY_PATH": str(tmp_path / "k.p8"),
        "TRIAPP_APNS_KEY_ID": "K",
        "TRIAPP_APNS_TEAM_ID": "T",
        "TRIAPP_APNS_ENV": "production",
    }.items():
        monkeypatch.setenv(k, v)
    push._jwt.clear()
    push.register(TOKEN, ATH)  # a TestFlight (production) token, key only valid for sandbox
    real = httpx.Client

    def apns(req):
        if req.url.host == "api.push.apple.com":
            return httpx.Response(403, json={"reason": "BadEnvironmentKeyInToken"})
        return httpx.Response(400, json={"reason": "BadDeviceToken"})

    monkeypatch.setattr(httpx, "Client", lambda **kw: real(transport=httpx.MockTransport(apns)))
    assert push.send([TOKEN], "t", "b") == {"sent": 0, "failed": 1}
    assert [d["token"] for d in push.devices(athlete="Marcelo")] == [TOKEN]


def test_quiet_hours():
    from datetime import datetime

    assert notify.quiet(datetime(2026, 10, 14, 5, 53)) and notify.quiet(datetime(2026, 10, 14, 23, 0))
    assert not notify.quiet(datetime(2026, 10, 14, 7, 30)) and not notify.quiet(datetime(2026, 10, 14, 15, 0))


def test_morning_round_sends_one_per_person(db, tmp_path, monkeypatch):
    import json

    monkeypatch.setattr(notify, "OUT", tmp_path)
    monkeypatch.setattr(push, "configured", lambda: True)
    sent = []
    monkeypatch.setattr(
        push,
        "send",
        lambda tokens, title, body, url="/", thread=None: sent.append((tuple(tokens), title, body))
        or {"sent": len(tokens), "failed": 0},
    )
    push.register(TOKEN, ATH)
    push.register("b" * 64, COACH)
    (tmp_path / "dashboard_data.json").write_text(
        json.dumps(
            {
                "athletes": {
                    "Marcelo": {"recent": [{"key": "k1", "date": "2026-10-13 18:00", "sport": "run", "title": "1h Z2"}]},
                    "Athlete2": {"recent": []},
                }
            }
        )
    )
    (tmp_path / "session_notes.json").write_text(json.dumps({"Marcelo|k1": {"text": "Boa corrida."}}))
    (tmp_path / "analysis.json").write_text(
        json.dumps(
            {
                "Marcelo": {
                    "findings": [
                        {"skill": "consistency", "severity": "alert", "title": "Consistência baixa", "detail": "40%."},
                        {"skill": "race_readiness", "severity": "alert", "title": "Sessões longas", "detail": "0."},
                    ]
                },
                "Athlete2": {"findings": [{"skill": "load_monitor", "severity": "alert", "title": "Rampa", "detail": "CTL."}]},
            }
        )
    )
    (tmp_path / "daily_brief.json").write_text(
        json.dumps({"Marcelo": {"date": "2026-10-14", "today": [{"title": "FTP Booster"}], "text": "Ontem correste. Hoje bike."}})
    )
    notify.deliver("2026-10-14", morning=True)
    mine = [x for x in sent if x[0] == (TOKEN,)]
    coach = [x for x in sent if x[0] == ("b" * 64,)]
    assert len(mine) == 1 and mine[0][1] == "Hoje: FTP Booster" and "Atenção: Consistência baixa (+1)" in mine[0][2]
    assert len(coach) == 1 and coach[0][1] == "Equipa: 3 alertas novos" and "Athlete2: Rampa" in coach[0][2]
    sent.clear()
    notify.deliver("2026-10-14", morning=True)  # nothing new: nothing sent
    assert sent == []

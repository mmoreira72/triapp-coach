import pytest
import userdata, notify, tp_calendar


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("TRIAPP_DB", str(tmp_path / "app.sqlite"))


def test_integrations_validate_keep_and_clear(db):
    with pytest.raises(ValueError):
        userdata.save_integrations("Marcelo", "m", tp_ical="https://example.com/cal.ics")
    with pytest.raises(ValueError):
        userdata.save_integrations("Marcelo", "m", intervals_id="abc")
    userdata.save_integrations(
        "Marcelo", "m", tp_ical="webcal://www.trainingpeaks.com/ical/X.ics", intervals_key="k", intervals_id="i123"
    )
    userdata.save_integrations("Marcelo", "m", tp_ical=None, intervals_key=None, intervals_id=None)  # nothing sent: kept
    st = userdata.integration_status("Marcelo")
    assert st["tp"] and st["intervals"] and st["intervals_id"] == "i123" and "k" not in str(st.values())
    userdata.save_integrations("Marcelo", "m", tp_ical="")  # cleared
    assert not userdata.integration_status("Marcelo")["tp"]


def test_saved_links_feed_the_pipeline(db, monkeypatch):
    monkeypatch.delenv("TRIAPP_TP_ICAL_Marcelo", raising=False)
    userdata.save_integrations("Marcelo", "m", tp_ical="webcal://www.trainingpeaks.com/ical/X.ics")
    assert tp_calendar.configured()["Marcelo"] == "https://www.trainingpeaks.com/ical/X.ics"


def test_race_marks_and_reports(db):
    userdata.mark_race("Marcelo", "k1", "2026-09-14", "Triatlo de Setúbal", "Marcelo")
    assert [m["session_key"] for m in userdata.race_marks("Marcelo")] == ["k1"]
    userdata.save_race_report("Marcelo", "2026-09-14", "athlete", "Bike forte, corrida dura.", "Marcelo")
    userdata.save_race_report("Marcelo", "2026-09-14", "coach", "Ritmo de bike alto demais.", "Miguel")
    r = userdata.race_reports("Marcelo")["2026-09-14"]
    assert r["athlete"]["text"].startswith("Bike") and r["coach"]["author"] == "Miguel"
    userdata.mark_race("Marcelo", "k1", "", "", "Marcelo", on=False)
    assert userdata.race_marks("Marcelo") == []


PLAN = [
    {"date": "2026-10-12", "sport": "swim", "title": "Nx100", "completed": True},
    {"date": "2026-10-13", "sport": "run", "title": "1h Z2", "completed": False},
    {"date": "2026-10-14", "sport": "bike", "title": "FTP Booster", "completed": False},
    {"date": "2026-10-15", "sport": "run", "title": "hoje", "completed": False},
]


def test_missed_sessions_and_reasons(db):
    assert [m["title"] for m in userdata.missed_sessions(PLAN, "2026-10-15")] == ["1h Z2", "FTP Booster"]
    userdata.save_missed_reason("Marcelo", "2026-10-13", "1h Z2", "trabalho", "reunião")
    m = userdata.missed_with_reasons("Marcelo", PLAN, "2026-10-15")
    assert m[0]["reason"]["reason"] == "trabalho" and m[1]["reason"] is None
    with pytest.raises(ValueError):
        userdata.save_missed_reason("Marcelo", "2026-10-14", "FTP Booster", "preguiça")


def test_two_unexplained_misses_tell_the_coach(db):
    (ev,) = notify.missed_events({"Marcelo": PLAN}, "2026-10-15")
    assert ev[1] == "Marcelo" and ev[2] and ev[3] == "Falhou 2 treinos" and "ter 1h Z2; qua FTP Booster" in ev[4]
    userdata.save_missed_reason("Marcelo", "2026-10-13", "1h Z2", "doenca")
    assert list(notify.missed_events({"Marcelo": PLAN}, "2026-10-15")) == []  # one explained: only one left


def test_trainingpeaks_marks_done_workouts():
    from datetime import date

    ics = (
        "BEGIN:VEVENT\r\nDTSTART:20261012T180059\r\nSUMMARY:Swim: Nx100\r\nDESCRIPTION:Workout type: Swim\\nPlanned Time: 0:35\\n"
        "Actual Time: 0:37\\n\r\nEND:VEVENT\r\nBEGIN:VEVENT\r\nDTSTART:20261013\r\nSUMMARY:Run: 1h Z2\r\n"
        "DESCRIPTION:Workout type: Run\\nPlanned Time: 1:00\\n\r\nEND:VEVENT\r\n"
    )
    rows = tp_calendar.planned_rows(ics, date(2026, 10, 15))
    assert [(r["title"], r["completed"]) for r in rows] == [("Nx100", True), ("1h Z2", False)]


def test_session_comments(db):
    userdata.add_comment("Marcelo", "k1", "2026-10-18", "Encurtei por desconforto no pé", "Marcelo", "athlete")
    userdata.add_comment("Marcelo", "k1", "2026-10-18", "Ok, adaptar a sapatilha primeiro", "Miguel", "coach")
    c = userdata.comments("Marcelo")["k1"]
    assert [x["author"] for x in c] == ["Marcelo", "Miguel"]
    userdata.delete_comment("Marcelo", c[1]["id"], "Marcelo")  # not the author: stays
    assert len(userdata.comments("Marcelo")["k1"]) == 2
    userdata.delete_comment("Marcelo", c[0]["id"], "Marcelo")
    assert [x["author"] for x in userdata.comments("Marcelo")["k1"]] == ["Miguel"]
    with pytest.raises(ValueError):
        userdata.add_comment("Marcelo", "k1", "2026-10-18", "  ", "Marcelo", "athlete")


def test_assistant_reply_goes_with_the_comment(db):
    cid = userdata.add_comment("Marcelo", "k1", "2026-10-20", "Cortei o cool down", "Marcelo", "athlete")
    rid = userdata.add_reply("Marcelo", cid, "Sem impacto no objetivo do treino.")
    c = userdata.comments("Marcelo")["k1"]
    assert [x["role"] for x in c] == ["athlete", "assistant"] and c[1]["reply_to"] == cid and rid
    userdata.delete_comment("Marcelo", cid, "Marcelo")
    assert not userdata.comments("Marcelo")
    assert userdata.add_reply("Marcelo", cid, "tarde demais") is None  # the comment was deleted before the reply was ready


def test_tp_plan_history_keeps_what_the_feed_drops(tmp_path):
    path = tmp_path / "h.json"
    row = lambda d, t, done: {"date": d, "title": t, "completed": done, "source": "tp"}
    tp_calendar.keep_history({"Marcelo": [row("2026-10-17", "Bike", False)]}, path)
    tp_calendar.keep_history({"Marcelo": [row("2026-10-17", "Bike", True), row("2026-10-24", "Run", False)]}, path)
    import json

    h = json.load(open(path))["Marcelo"]
    assert h["2026-10-17|Bike"]["completed"] and set(h) == {"2026-10-17|Bike", "2026-10-24|Run"}


def test_thresholds_set_in_the_app(db):
    with pytest.raises(ValueError):
        userdata.set_thresholds("Marcelo", "2026-10-08", "m", "x", ftp=2000)
    with pytest.raises(ValueError):
        userdata.set_thresholds("Marcelo", "2026-10-08", "m", "x")
    userdata.set_thresholds("Marcelo", "2026-10-08", "m", "projeção", ftp=190, lthr_run=160, lthr_bike=155)
    userdata.set_thresholds("Marcelo", "2026-11-01", "m", "teste", ftp=200)
    t = userdata.thresholds("Marcelo")
    assert [x["ftp"] for x in t] == [190, 200] and t[1]["lthr_run"] is None and not userdata.thresholds("Athlete2")


def test_race_nutrition_saved_by_the_coach(db):
    with pytest.raises(ValueError):
        userdata.save_race_nutrition("Marcelo", "salou", {"carbs_bike": 500}, "Miguel")
    userdata.save_race_nutrition("Marcelo", "salou", {"carbs_bike": "90", "fluid_ml": "", "note": " gel a cada 20' "}, "Miguel")
    n = userdata.race_nutrition("Marcelo")["salou"]
    assert n["carbs_bike"] == 90 and "fluid_ml" not in n and n["note"] == "gel a cada 20'" and n["updated_by"] == "Miguel"


def test_race_plan_and_the_15_day_notice(db, monkeypatch, tmp_path):
    import json

    userdata.save_race_plan("Marcelo", "salou", "Miguel", entered=True)
    userdata.save_race_plan("Marcelo", "salou", "Miguel", bike_if=0.72)
    p = userdata.race_plans()["Marcelo"]["salou"]
    assert p["entered"] == 1 and p["bike_if"] == 0.72
    userdata.save_race_plan("Marcelo", "salou", "Miguel", clear_if=True)
    assert userdata.race_plans("Marcelo")["Marcelo"]["salou"]["bike_if"] is None
    with pytest.raises(ValueError):
        userdata.save_race_plan("Marcelo", "salou", "Miguel", bike_if=2)
    (tmp_path / "race_impact.json").write_text(
        json.dumps(
            {
                "salou": {
                    "race": {"name": "Salou", "date": "2027-05-09"},
                    "athletes": {"Marcelo": {"total": {"time": "5:23"}}, "Athlete2": {}},
                }
            }
        )
    )
    monkeypatch.setattr(notify, "OUT", tmp_path)
    assert notify.race_soon_events("2027-04-20") == []  # 19 days
    (ev,) = notify.race_soon_events("2027-04-24")
    assert "15 dias" in ev[1] and "Marcelo (5:23)" in ev[2] and "Athlete2" not in ev[2]

import intervals_sync, morning_brief


def test_planned_rows_keep_only_workouts():
    ev = [
        {
            "category": "WORKOUT",
            "start_date_local": "2026-10-13T00:00:00",
            "type": "Swim",
            "name": "Nx100",
            "description": "5 palas P15",
            "moving_time": 2100,
        },
        {"category": "NOTE", "start_date_local": "2026-10-13T00:00:00", "name": "viagem"},
    ]
    (p,) = intervals_sync.planned_rows(ev)
    assert p == {
        "date": "2026-10-13",
        "sport": "swim",
        "title": "Nx100",
        "description": "5 palas P15",
        "duration_s": 2100,
        "load": None,
    }


def test_brief_input_has_yesterday_and_todays_plan():
    A = {"recent": [{"date": "2026-10-12 18:00", "sport": "run", "title": "Z2", "key": "k", "kind": {"type": "Aeróbio"}}]}
    notes = {"Marcelo|k": {"text": "Corrida fácil."}}
    planned = {
        "Marcelo": [
            {"date": "2026-10-13", "sport": "swim", "title": "Nx100", "description": "5 palas", "duration_s": 2100, "load": None},
            {"date": "2026-10-14", "sport": "bike", "title": "outro", "description": "", "duration_s": None, "load": None},
        ]
    }
    y, plan = morning_brief.today_input("Marcelo", A, notes, planned, "2026-10-13")
    assert y == [{"sport": "run", "title": "Z2", "type": "Aeróbio", "note": "Corrida fácil.", "comments": []}] and [
        p["title"] for p in plan
    ] == ["Nx100"]
    txt = morning_brief.describe(y, plan)
    assert "Corrida fácil." in txt and "35 min" in txt and "5 palas" in txt
    assert "descanso" in morning_brief.describe([], []) and "nada no plano" in morning_brief.describe([], [])


def test_trainingpeaks_feed_rows():
    import tp_calendar
    from datetime import date

    ics = (
        "BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nDTSTART;VALUE=DATE:20261014\r\nSUMMARY:Bike: 1 FTP Booster\r\n"
        "DESCRIPTION:Workout type: Bike\\nPlanned Time: 1:18\\n\r\nEND:VEVENT\r\n"
        "BEGIN:VEVENT\r\nDTSTART:20261012T180059\r\nSUMMARY:Swim: Nx100\r\nDESCRIPTION:Workout type: Swim\\nNx100 I=15''\\n5 palas P15\\n"
        "Planned Time: 0:35\\nActual Time: 0:37\\nDistance Planned: 2500 m\\n\r\nEND:VEVENT\r\n"
        "BEGIN:VEVENT\r\nDTSTART:20261001\r\nSUMMARY:Run: old\r\nDESCRIPTION:Workout type: Run\\n\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n"
    )
    rows = tp_calendar.planned_rows(ics, date(2026, 10, 13))
    assert [(r["date"], r["sport"], r["title"], r["duration_s"]) for r in rows] == [
        ("2026-10-12", "swim", "Nx100", 2100),
        ("2026-10-14", "bike", "1 FTP Booster", 4680),
    ]
    assert rows[0]["description"] == "Nx100 I=15''\n5 palas P15" and rows[0]["distance_km"] == 2.5


def test_report_input_covers_the_day(tmp_path, monkeypatch):
    monkeypatch.setenv("TRIAPP_DB", str(tmp_path / "app.sqlite"))
    A = {
        "recent": [
            {
                "date": "2026-10-12 18:00",
                "sport": "swim",
                "title": "Natação",
                "h": 0.66,
                "km": 1.4,
                "tss": 45.0,
                "if": 0.83,
                "hr": 135.0,
                "key": "k",
                "kind": {"type": "Natação", "zones": {}},
            }
        ],
        "executions": [],
        "pmc": {
            "daily": [
                {"d": "2026-10-11", "ctl": 33.0, "atl": 31.0, "tsb": 1.0, "tss": 0},
                {"d": "2026-10-12", "ctl": 32.5, "atl": 33.5, "tsb": 0.6, "tss": 45.0},
            ]
        },
    }
    planned = {
        "Marcelo": [
            {"date": "2026-10-12", "sport": "swim", "title": "Nx100", "description": "5 palas P15", "duration_s": 2100},
            {"date": "2026-10-13", "sport": "run", "title": "1h Z2", "description": "", "duration_s": 3600},
            {"date": "2026-10-14", "sport": "bike", "title": "FTP Booster", "description": "", "duration_s": 4680},
        ]
    }
    analysis = {"Marcelo": {"findings": [{"severity": "alert", "title": "Consistência baixa", "detail": "40%, 50%, 50%"}]}}
    import health
    from datetime import date, timedelta

    d = date.today()
    health.save(
        "Marcelo",
        [{"date": (d - timedelta(days=i)).isoformat(), "rhr": 60, "hrv_rmssd": 30, "sleep_h": 7.4} for i in range(1, 6)],
        "intervals",
    )
    health.save("Marcelo", [{"date": d.isoformat(), "rhr": 57, "hrv_rmssd": 34, "sleep_h": 9.1}], "intervals")
    txt = morning_brief.report_input("Marcelo", A, {"Marcelo|k": {"text": "Boa sessão."}}, planned, analysis, "2026-10-13")
    assert "CTL desceu, ATL subiu, TSB desceu" in txt and "RECUPERA" not in txt  # recovery is written by code
    rec = morning_brief.recovery_section("Marcelo")
    assert rec == (
        "**Recuperação**\nDormiste 9,1 h, acima da tua média (7,4); FC de repouso 57 bpm, abaixo da tua média (60): bom sinal; "
        "HRV 34 ms, acima da tua média (30): bom sinal."
    )
    out = morning_brief.assemble("**Treinos**\nx\n**Carga**\ny\n**Atenção**\nz", rec)
    assert out.index("**Recuperação**") < out.index("**Atenção**") and out.index("**Carga**") < out.index("**Recuperação**")
    for want in (
        "Planeado: natação Nx100 (35 min)",
        "5 palas P15",
        "Feito: natação Natação: 40 min",
        "TSS 45",
        "Nota: Boa sessão.",
        "CTL 32.5 (antes 33.0)",
        "Consistência baixa",
        "hoje: corrida 1h Z2 (60 min)",
        "amanhã: bike FTP Booster (78 min)",
    ):
        assert want in txt, want


def test_intensity_cap_is_decided_by_code(tmp_path, monkeypatch):
    monkeypatch.setenv("TRIAPP_DB", str(tmp_path / "app.sqlite"))
    monkeypatch.setattr(morning_brief, "load_context", lambda name: [])
    A = lambda tsb: {"pmc": {"daily": [{"d": "2026-10-17", "ctl": 30, "atl": 30, "tsb": tsb, "tss": 0}]}}
    assert morning_brief.intensity_cap("Ana", A(5), "2026-10-17")[0] == "livre"
    assert morning_brief.intensity_cap("Ana", A(-15), "2026-10-17")[0] == "moderada"
    assert morning_brief.intensity_cap("Ana", A(-25), "2026-10-17")[0] == "leve"
    monkeypatch.setattr(morning_brief, "load_context", lambda name: [{"kind": "lesao", "start": "2026-10-01", "end": None}])
    assert morning_brief.intensity_cap("Ana", A(5), "2026-10-17") == ("leve", ["lesão ou doença em curso"])


def test_suggestion_is_validated(monkeypatch, tmp_path):
    monkeypatch.setenv("TRIAPP_DB", str(tmp_path / "app.sqlite"))
    monkeypatch.setattr(morning_brief, "load_context", lambda name: [])
    A = {
        "pmc": {"daily": [{"d": "2026-10-17", "ctl": 30, "atl": 30, "tsb": -25, "tss": 0}]},
        "recent": [{"date": "2026-10-10 09:00", "sport": "bike", "h": 2.0, "tss": 90, "kind": {"type": "Resistência aeróbia"}}],
    }
    an = {
        "Ana": {
            "findings": [
                {"skill": "race_readiness", "severity": "alert", "title": "Escada de sessões longas", "detail": "0 pedais ≥ 3 h"},
                {"skill": "context", "severity": "info", "title": "Lesão", "detail": "x"},
            ]
        }
    }
    seen = {}

    def fake(system, user):
        seen["user"] = user
        return 'Aqui está: {"sport": "bike", "title": "Pedal longo Z2", "minutes": 400, "description": "Z2 contínuo", "why": "durabilidade", "skill": "race_readiness"}'

    monkeypatch.setattr(morning_brief, "write", fake)
    s = morning_brief.suggestion("Ana", A, {}, an, "2026-10-17", "Salou")
    assert s["sport"] == "bike" and s["minutes"] == 150 and s["cap"] == "leve"  # 1.25 × the longest ride of the last 6 weeks
    assert s["skill"] == "race_readiness" and s["gap"] == "Escada de sessões longas"
    assert "só leve" in seen["user"] and "Escada de sessões longas" in seen["user"] and "Lesão" not in seen["user"]
    monkeypatch.setattr(morning_brief, "write", lambda system, user: "não sei")
    assert morning_brief.suggestion("Ana", A, {}, an, "2026-10-17", "Salou") is None


def test_focus_picks_from_the_week(monkeypatch):
    planned = {
        "Ana": [
            {"date": "2026-10-17", "sport": "run", "title": "Z2", "duration_s": 3600, "description": ""},
            {"date": "2026-10-18", "sport": "bike", "title": "Pedal 3h", "duration_s": 10800, "description": ""},
        ]
    }
    an = {
        "Ana": {
            "findings": [{"skill": "race_readiness", "severity": "alert", "title": "Escada de sessões longas", "detail": "x"}]
        }
    }
    monkeypatch.setattr(morning_brief, "write", lambda system, user: '{"index": 2, "why": "é a sessão longa que te falta"}')
    f = morning_brief.focus("Ana", planned, an, "2026-10-17")
    assert f["title"] == "Pedal 3h" and f["date"] == "2026-10-18"


def test_yesterday_falls_back_to_trainingpeaks_done(monkeypatch):
    planned = {"Marcelo": [{"date": "2026-10-17", "sport": "run", "title": "Zona 4 -5x4'", "completed": True}]}
    y, _ = morning_brief.today_input("Marcelo", {"recent": []}, {}, planned, "2026-10-18")
    assert y and y[0]["title"] == "Zona 4 -5x4'" and "TrainingPeaks" in y[0]["note"]


def test_suggestion_seen_the_day_before_is_kept(monkeypatch):
    prev = {"sport": "bike", "title": "Pedal 3h", "minutes": 180, "cap": "livre"}
    monkeypatch.setattr(morning_brief, "intensity_cap", lambda name, A, today: ("livre", ["ok"]))
    assert morning_brief.reuse(prev, "Marcelo", {}, "2026-10-18")["minutes"] == 180
    monkeypatch.setattr(morning_brief, "intensity_cap", lambda name, A, today: ("leve", ["HRV baixa"]))
    assert morning_brief.reuse(prev, "Marcelo", {}, "2026-10-18") is None  # recovery got worse: a new, easier one


def test_recent_load_rules_out_yesterdays_long_sport():
    A = {
        "recent": [
            {"date": "2026-10-18 09:34", "sport": "bike", "h": 2.14, "tss": 86, "kind": {"type": "Resistência aeróbia"}},
            {"date": "2026-10-17 19:42", "sport": "run", "h": 0.8, "tss": 54, "kind": {"type": "Limiar"}},
        ]
    }
    lines, avoid = morning_brief.recent_load(A, [], "2026-10-19")
    assert avoid == {"bike"} and any("bike 128 min" in l for l in lines)
    _, avoid = morning_brief.recent_load(A, [], "2026-10-18")  # the day after the threshold run: no hard run again
    assert avoid == {"run"}
    _, avoid = morning_brief.recent_load(
        {"recent": []}, [], "2026-10-19", extra={"date": "2026-10-18", "sport": "bike", "minutes": 180}
    )
    assert avoid == {"bike"}  # today's own suggestion counts for tomorrow


def test_suggestion_retries_when_the_model_ignores_the_rule(monkeypatch, tmp_path):
    monkeypatch.setenv("TRIAPP_DB", str(tmp_path / "app.sqlite"))
    monkeypatch.setattr(morning_brief, "load_context", lambda name: [])
    A = {
        "pmc": {"daily": [{"d": "2026-10-18", "ctl": 36, "atl": 50, "tsb": -9, "tss": 86}]},
        "recent": [{"date": "2026-10-18 09:34", "sport": "bike", "h": 2.1, "tss": 86, "kind": {"type": "Resistência aeróbia"}}],
    }
    answers = iter(
        ['{"sport": "bike", "title": "Mais bike", "minutes": 90}', '{"sport": "swim", "title": "Natação técnica", "minutes": 45}']
    )
    monkeypatch.setattr(morning_brief, "write", lambda system, user: next(answers))
    s = morning_brief.suggestion("Ana", A, {}, {}, "2026-10-19", "Salou")
    assert s["sport"] == "swim" and s["title"] == "Natação técnica"


def test_empty_intervals_calendar_keeps_the_trainingpeaks_plan(monkeypatch):
    planned = {"Marcelo": [{"date": "2026-10-17", "title": "Zona 4", "source": "tp"}]}
    monkeypatch.setattr(intervals_sync, "get", lambda *a, **k: [])
    assert intervals_sync.sync_planned("Marcelo", "k", "i1", planned) == 0
    assert planned["Marcelo"][0]["title"] == "Zona 4"
    ev = [{"category": "WORKOUT", "start_date_local": "2026-10-19T00:00:00", "type": "Ride", "name": "Plano Intervals"}]
    monkeypatch.setattr(intervals_sync, "get", lambda *a, **k: ev)
    intervals_sync.sync_planned("Ana", "k", "i2", planned)
    assert planned["Ana"][0]["title"] == "Plano Intervals" and planned["Marcelo"][0]["source"] == "tp"


def test_after_a_long_ride_the_run_is_short_and_easy(monkeypatch, tmp_path):
    monkeypatch.setenv("TRIAPP_DB", str(tmp_path / "app.sqlite"))
    monkeypatch.setattr(morning_brief, "load_context", lambda name: [])
    A = {
        "pmc": {"daily": [{"d": "2026-10-18", "ctl": 36, "atl": 50, "tsb": -9, "tss": 86}]},
        "recent": [
            {"date": "2026-10-18 09:34", "sport": "bike", "h": 2.1, "tss": 86, "kind": {"type": "Resistência aeróbia"}},
            {"date": "2026-10-15 18:16", "sport": "run", "h": 1.2, "tss": 69, "kind": {"type": "Tempo"}},
        ],
    }
    seen = {}

    def fake(system, user):
        seen["u"] = user
        return '{"sport": "run", "title": "Corrida longa", "minutes": 120}'

    monkeypatch.setattr(morning_brief, "write", fake)
    s = morning_brief.suggestion("Ana", A, {}, {}, "2026-10-19", "Salou")
    assert s["sport"] == "run" and s["minutes"] == 45  # legs tired: the other leg sport only short
    assert "NÃO SUGERIR: bike" in seen["u"] and "corrida 45 min" in seen["u"]

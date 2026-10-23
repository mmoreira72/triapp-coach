import skills_engine as se


def finding(evidence, severity="alert"):
    return {
        "skill": "threshold_audit",
        "severity": severity,
        "title": "Limiar baixo",
        "detail": "3 sessões.",
        "evidence": evidence,
    }


ACK = [
    {
        "title": "Limiar baixo",
        "evidence": ["2026-08-29 10:31", "2026-09-11 19:03", "2026-09-18 18:56"],
        "who": "Marcelo",
        "at": "2026-10-12",
    }
]


def test_ignored_sessions_turn_the_alert_into_info():
    (f,) = se.apply_acks([finding(["2026-08-29 10:31", "2026-09-11 19:03", "2026-09-18 18:56"])], ACK)
    assert f["severity"] == "info" and f["ignored"]["was"] == "alert" and "Ignorado por Marcelo" in f["detail"]


def test_sessions_leaving_the_window_keep_it_ignored():
    (f,) = se.apply_acks([finding(["2026-09-11 19:03", "2026-09-18 18:56"])], ACK)
    assert f["severity"] == "info"


def test_a_new_session_brings_the_alert_back():
    (f,) = se.apply_acks([finding(["2026-09-18 18:56", "2026-10-20 18:00"])], ACK)
    assert f["severity"] == "alert" and "ignored" not in f


def test_findings_without_sessions_or_other_titles_are_untouched():
    other = dict(finding(["2026-08-29 10:31"]), title="Outro")
    plain = {"skill": "load_monitor", "severity": "alert", "title": "Limiar baixo", "detail": "x"}
    out = se.apply_acks([other, plain], ACK)
    assert [f["severity"] for f in out] == ["alert", "alert"]


def test_evidence_is_recorded_as_minutes():
    import pandas as pd

    f = se.F("threshold_audit", "alert", "t", "d", "coggan_np", evidence=pd.Series(pd.to_datetime(["2026-09-18 18:56:01"])))
    assert f["evidence"] == ["2026-09-18 18:56"]

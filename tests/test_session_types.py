"""Session type from the time in zones."""

import json
import pandas as pd
import session_types as st

LTHR, FTP = 160, 250


def session(sport="run", minutes=60, hr=None, power=None):
    """hr / power: {bpm or watts: minutes}."""
    hist = lambda d: json.dumps({str(k): v * 60 for k, v in d.items()}) if d else None
    return pd.Series(
        {
            "sport_n": sport,
            "duration_h": minutes / 60,
            "lthr_used": LTHR,
            "ftp_used": FTP,
            "hr_hist": hist(hr),
            "pw_hist": hist(power),
        }
    )


def kind(**kw):
    return st.classify(session(**kw))["type"]


def test_easy_sessions():
    assert kind(minutes=40, hr={120: 32, 138: 8}) == "Recuperação"  # 80% in Z1, under 75 min
    assert kind(minutes=60, hr={130: 30, 138: 30}) == "Resistência aeróbia"
    assert kind(minutes=100, hr={130: 50, 138: 50}) == "Resistência aeróbia longa"  # runs from 1h30
    assert kind(sport="bike", minutes=100, hr={110: 50, 135: 50}) == "Resistência aeróbia"  # rides only from 2h30


def test_quality_sessions_by_heart_rate():
    assert kind(minutes=60, hr={130: 25, 146: 35}) == "Tempo"  # Z3 is the main block
    assert kind(minutes=60, hr={138: 45, 146: 15}) == "Resistência aeróbia"  # 25% in Z3 is not enough
    assert kind(minutes=60, hr={130: 45, 154: 15}) == "Limiar"
    assert kind(minutes=60, hr={130: 45, 161: 15}) == "Limiar"  # 100-102% of LTHR is still threshold
    assert kind(minutes=60, hr={130: 50, 168: 10}) == "VO2max"  # 5+ min at 103% or more


def test_bike_uses_power_when_the_session_has_it():
    s = st.classify(session(sport="bike", minutes=60, hr={120: 60}, power={150: 50, 280: 10}))  # 112% of FTP for 10 min
    assert (s["type"], s["basis"]) == ("VO2max", "potência")
    assert st.classify(session(sport="bike", minutes=60, hr={120: 60}))["basis"] == "FC"
    assert kind(sport="bike", minutes=60, power={150: 52, 330: 8}) == "Anaeróbio / sprints"  # 132% of FTP


def test_sessions_without_zones():
    assert kind(sport="swim") == "Natação" and kind(sport="strength") == "Força"
    assert kind(minutes=60) == "Sem dados de intensidade"
    assert st.classify(session(minutes=5, hr={120: 5})) is None  # too short to classify


def test_zone_shares_are_reported_with_friel_labels():
    z = st.classify(session(minutes=60, hr={130: 30, 161: 15, 168: 15}))["zones"]
    assert z == {"Z1": 0.5, "Z5a": 0.25, "Z5b": 0.25}


def test_swim_zones_use_a_threshold_10_bpm_below_the_run_one():
    # LTHR 160 on the run -> swim threshold 150: 132 bpm is Z2 (90-95% is Z2 on the run edges: 135-142), 145 bpm is Z3
    r = st.classify(session(sport="swim", minutes=43, hr={132: 30, 145: 13}))
    assert r["basis"] == "FC" and r["zones"] and r["type"] != "Natação"
    assert st.classify(session(sport="swim", minutes=43))["type"] == "Natação"  # no heart rate: no zones


def test_threshold_reps_with_noisy_power_are_not_vo2max():
    # 60 min with threshold reps ridden on noisy power: 12 min in Z4 and 12 min just over 105% -> threshold, not VO2max
    f = lambda pct: int(round(FTP * pct / 10)) * 10
    assert kind(sport="bike", power={f(0.6): 22, f(0.8): 14, f(0.97): 12, f(1.10): 12}) == "Limiar"
    # a real VO2max session: Z5 clearly outweighs Z4
    assert kind(sport="bike", power={f(0.6): 36, f(0.97): 4, f(1.15): 20}) == "VO2max"

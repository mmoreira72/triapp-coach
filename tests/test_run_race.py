import numpy as np
import race_impact as ri
import nutrition


def athlete(p20=5.0, p60=5.4, races=()):
    return {"bests": {"run": [{"year": ri.TODAY.year, "pace1200": p20, "pace3600": p60}]}, "races": list(races), "weight_kg": 70}


def flat(km, grade=0.0):
    n = int(km * 1000 / 50)
    return {
        "dist": np.full(n, 50.0),
        "grade": np.full(n, grade),
        "km": np.arange(n) * 0.05,
        "ele": np.zeros(n),
        "bearing": np.zeros(n),
    }


W = {"main_site": "p", "p": {"temp_c_p10_p50_p90": [8, 12, 16], "wind_kmh_p50_p90": [0.0, 5.0]}}


def test_riegel_from_training_bests_and_hills_cost_time():
    race = {"name": "Meia", "run_distance_km": 21.0975}
    p = ri.predict_run("M", athlete(), None, flat(21.0975), W, race)
    k = p["run"]["riegel"]
    assert 1.04 <= k <= 1.12 and 1.9 < p["total"]["hours"] < 2.1 and "treinos" in p["run"]["anchor"]
    assert len(p["splits"]) == 22 and abs(p["splits"][-1]["cum"] - p["total"]["hours"] * 60) < 1.5
    hilly = ri.predict_run("M", athlete(), None, flat(21.0975, 0.02), W, race)
    assert hilly["total"]["hours"] > p["total"]["hours"] * 1.05 and hilly["run"]["grade_pct"] > 5


def test_a_recent_run_race_is_the_anchor():
    r = {"kind": "race", "date": ri.TODAY.strftime("%Y-%m-%d"), "by": {"run": {"km": 10.0, "h": 0.8}}}
    p = ri.predict_run("M", athlete(races=[r]), None, flat(10), W, {"name": "10k", "run_distance_km": 10.0})
    assert abs(p["total"]["hours"] - 0.8) < 0.02 and "prova" in p["run"]["anchor"]


def test_run_only_fuelling():
    pred = {"sport": "run", "total": {"hours": 3.6}, "run": {"distance_km": 42.195}}
    race = {"name": "Maratona", "run_distance_km": 42.195, "aid_run_km": [5, 10, 15, 20, 25, 30, 35, 40]}
    p = nutrition.plan(pred, race, 14, 70)
    assert p["mode"] == "run" and p["run"]["carbs_g_h"] == 60 and p["run"]["first_gel_min"] == 30
    assert len(p["run"]["aids"]) == 8 and any(x["gel"] for x in p["run"]["aids"])
    short = nutrition.plan({"sport": "run", "total": {"hours": 0.8}}, {"run_distance_km": 10}, 14, 70)
    assert short["run"]["carbs_g_h"] == 0 and any("Menos de 1 h" in w for w in short["warnings"])

import nutrition


def track(minutes=150, km=80):
    # flat, a 3 km climb at 6% from km 20, a fast descent after it
    pts = []
    for i in range(int(km * 2) + 1):
        k = i / 2
        grade = 6.0 if 20 <= k < 23 else (-6.0 if 23 <= k < 26 else 0.0)
        pts.append({"km": k, "min": minutes * k / km, "w": 200 if grade > 0 else (0 if grade < 0 else 180), "grade": grade})
    return pts


def pred(bike_h=2.5, run_h=1.4):
    return {
        "swim": {"hours": 0.6},
        "bike": {"recommended_if": 0.75, "options": {"0.75": {"hours": bike_h}}, "track": track(bike_h * 60)},
        "run": {"hours": run_h},
        "total": {"hours": 0.6 + bike_h + run_h + 0.1},
    }


def test_feeds_avoid_climbs_and_fast_descents_and_keep_the_rhythm():
    p = nutrition.plan(pred(), {"name": "T100"}, 21, 87)
    feeds = p["bike"]["feeds"]
    assert feeds[0]["min"] == 0 and "T1" in feeds[0]["why"]  # the first one in T1
    assert len(feeds) >= 6 and all(-3.5 < f["grade"] <= 3 for f in feeds)
    gaps = [b["min"] - a["min"] for a, b in zip(feeds, feeds[1:])]
    assert all(12 <= g <= 28 for g in gaps)
    assert feeds[0]["g"] == round(80 * 20 / 60)


def test_targets_follow_duration_and_heat():
    p = nutrition.plan(pred(), {"name": "T100"}, 21, 87)
    assert p["bike"]["carbs_g_h"] == 80 and p["run"]["carbs_g_h"] == 60
    assert p["before"]["caffeine_mg"] == 261 and p["before"]["carbs_3h_g"] == [87, 174]
    hot = nutrition.plan(pred(), {"name": "T100"}, 29, 87)
    assert hot["bike"]["fluid_ml_h"] > p["bike"]["fluid_ml_h"] and hot["bike"]["sodium_mg_h"] >= 1000
    assert any("teste de suor" in w for w in hot["warnings"])


def test_the_coach_numbers_win():
    over = {"carbs_bike": 95, "sodium_mg": 1200, "caffeine": False, "note": "Maurten 160 no bidon", "updated_by": "Miguel"}
    p = nutrition.plan(pred(), {"name": "T100"}, 21, 87, over)
    assert p["bike"]["carbs_g_h"] == 95 and p["bike"]["sodium_mg_h"] == 1200 and p["before"]["caffeine_mg"] is None
    assert p["note"] == "Maurten 160 no bidon" and "carbs_bike" in p["coach_set"]
    assert any("intestino treinado" in w for w in p["warnings"])


def test_bottle_carbs_and_a_later_start_from_the_coach():
    over = {"from_note": {"drink_g_h": 60, "start_min": 60}, "carbs_bike": 90, "note": "2 scoops/h, géis a partir da 1h"}
    p = nutrition.plan(pred(), {"name": "T100"}, 21, 87, over)
    b = p["bike"]
    assert b["drink_g_h"] == 60 and b["gel_g_h"] == 30 and b["feeds"][0]["min"] >= 54 and b["feeds"][0]["g"] == 10
    assert set(p["coach_set"]) >= {"carbs_bike", "drink_g_h", "start_min"} and p["coach_values"] == {"carbs_bike": 90}
    assert p["run"]["gel_every_min"] and nutrition.plan(pred(), {}, 21, 87, {"drink_g_h": 80})["bike"]["feeds"] == []


def test_from_note_keeps_only_numbers_in_range(monkeypatch):
    import llm

    monkeypatch.setattr(llm, "URL", "http://box")
    monkeypatch.setattr(
        llm,
        "chat",
        lambda *a, **k: 'Aqui está: {"carbs_bike": 90, "start_min": 60, "sodium_mg": 99999, "caffeine": false, "unread": ["2 scoops sem gramas"]}',
    )
    vals, unread = nutrition.from_note("90 g/h, géis a partir da 1h, 2 scoops, sem cafeína")
    assert vals == {"carbs_bike": 90.0, "start_min": 60.0, "caffeine": False} and unread == ["2 scoops sem gramas"]


def test_gels_that_do_not_add_up_are_flagged():
    over = {"carbs_bike": 90, "drink_g_h": 40, "gel_g": 40, "feed_min": 25}
    p = nutrition.plan(pred(), {}, 21, 87, over)
    assert p["bike"]["feeds"][1]["g"] == 21 and any("dão 96 g/h" in w for w in p["warnings"])
    ok = nutrition.plan(pred(), {}, 21, 87, {"carbs_bike": 80, "gel_g": 27, "feed_min": 20})
    assert not any("dão" in w for w in ok["warnings"])


def test_the_coach_if_moves_power_and_total():
    from app import render

    P = {
        "bike": {
            "recommended_if": 0.75,
            "flat_w": 150,
            "options": {"0.70": {"target_w": 140, "hours": 2.8}, "0.75": {"target_w": 150, "hours": 2.6}},
        },
        "total": {"hours": 5.4, "time": "5:24", "range": ["5:20", "5:28"]},
    }
    Q = render.with_coach_if(P, 0.70)
    assert Q["bike"]["recommended_if"] == 0.70 and Q["bike"]["flat_w"] == 140 and Q["bike"]["model_if"] == 0.75
    assert Q["total"]["time"] == "5:36" and Q["total"]["range"] == ["5:32", "5:40"]
    assert render.with_coach_if(P, 0.66) is P  # outside the simulated options: unchanged
    R = render.with_coach_if(P, 0.72)  # between two options: interpolated
    assert R["bike"]["flat_w"] == 144 and R["bike"]["options"]["0.72"]["interpolated"] and R["total"]["time"] == "5:31"


def test_bottles_and_feeds_follow_the_aid_stations():
    race = {"name": "70.3", "aid_bike_km": [20, 45, 70], "aid_run_km": [2.5, 5, 7.5, 10, 12.5, 15, 17.5], "run_distance_km": 21.1}
    p = nutrition.plan(pred(bike_h=2.6, run_h=1.9), race, 24, 80)  # 700 mL/h on a warm day
    b = p["bike"]
    assert [x["km"] for x in b["bottles"]] == [0.0, 20.0, 45.0, 70.0] and b["bottles"][0]["take"] >= 1
    assert all(x["take"] <= 2 for x in b["bottles"])
    assert not any(-0.3 <= f["km"] - a < 0.3 for f in b["feeds"] for a in race["aid_bike_km"])  # never in the aid zone
    aids = p["run"]["aids"]
    assert len(aids) == 7 and any(x["gel"] for x in aids) and all(x["ml"] > 0 for x in aids)
    assert not any("Sem abastecimentos" in w for w in p["warnings"])


def test_no_aid_stations_on_a_long_hot_ride_is_flagged():
    p = nutrition.plan(pred(bike_h=2.6), {"name": "x"}, 28, 80)  # 850 mL/h x 2.6 h > 2 bottles
    assert any("Sem abastecimentos" in w for w in p["warnings"])

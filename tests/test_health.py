import health


def test_clean_drops_implausible_values_and_bad_dates():
    assert health.clean({"date": "2026-10-08", "sleep_h": 7.2, "rhr": 48, "hrv": 61}) == {
        "date": "2026-10-08",
        "sleep_h": 7.2,
        "rhr": 48.0,
        "hrv": 61.0,
        "hrv_rmssd": None,
        "sleep_score": None,
    }
    assert health.clean({"date": "2026-10-08", "sleep_h": 30, "rhr": 48})["sleep_h"] is None
    assert health.clean({"date": "ontem", "rhr": 48}) is None
    assert health.clean({"date": "2026-10-08"}) is None  # nothing to keep


def test_resend_fills_in_without_erasing(tmp_path, monkeypatch):
    monkeypatch.setenv("TRIAPP_DB", str(tmp_path / "app.sqlite"))
    from datetime import date

    d = date.today().isoformat()
    assert health.save("Marcelo", [{"date": d, "rhr": 50}]) == 1
    health.save("Marcelo", [{"date": d, "sleep_h": 6.5}])  # the night arrives later; the morning HR stays
    assert health.recent("Marcelo") == [
        {"date": d, "sleep_h": 6.5, "rhr": 50.0, "hrv": None, "hrv_rmssd": None, "sleep_score": None, "source": "apple_health"}
    ]
    assert health.recent("Athlete2") == []


def test_intervals_wellness_keeps_rmssd_apart_from_sdnn(tmp_path, monkeypatch):
    monkeypatch.setenv("TRIAPP_DB", str(tmp_path / "app.sqlite"))
    import intervals_sync
    from datetime import date

    d = date.today().isoformat()
    rows = intervals_sync.wellness_rows([{"id": d, "restingHR": 57, "hrv": 35.0, "sleepSecs": 27000, "sleepScore": 83.0}])
    assert health.save("Marcelo", rows, source="intervals") == 1
    (r,) = health.recent("Marcelo")
    assert (r["sleep_h"], r["rhr"], r["hrv_rmssd"], r["hrv"], r["sleep_score"], r["source"]) == (
        7.5,
        57.0,
        35.0,
        None,
        83.0,
        "intervals",
    )

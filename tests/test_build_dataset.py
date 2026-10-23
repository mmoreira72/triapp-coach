"""The rules that decide what counts as a done session, which copy of a session stays, and how it is scored."""

import json
import numpy as np, pandas as pd
import pytest
import build_dataset as bd

UTC = "UTC"


def wko(**cols):
    """WKO index rows with the columns the rules read; one row per value given."""
    n = len(next(iter(cols.values())))
    base = {
        "athlete": ["A"] * n,
        "code_4046": [0] * n,
        "tp_duration_s": [np.nan] * n,
        "tp_4038": [np.nan] * n,
        "timer_s": [np.nan] * n,
        "has_file": [False] * n,
        "sport_tp": ["Run"] * n,
        "sport": [np.nan] * n,
        "t_local": [pd.NaT] * n,
    }
    base.update(cols)
    w = pd.DataFrame(base)
    w["t_local"] = pd.to_datetime(w.t_local)
    if "completed" not in w:
        w["completed"] = bd.wko_done(w)
    return w


def fit(rows):
    """FIT sessions: (file, start UTC, sport, timer_s, n_records)."""
    a = pd.DataFrame(rows, columns=["file", "start_time", "sport", "timer_s", "n_records"])
    a["start_time"] = pd.to_datetime(a.start_time, utc=True)
    a["athlete"], a["session_index"], a["has_session"] = "A", 0, True
    return a


# ---------------------------------------------------------------- done
def test_done_is_any_trace_of_execution():
    w = wko(
        code_4046=[2, 0, 0, 0, 0],  # file attached | by hand | virtual ride | file, no flag | planned only
        tp_duration_s=[np.nan, 3600, np.nan, np.nan, np.nan],
        tp_4038=[np.nan, np.nan, 84, np.nan, np.nan],
        has_file=[True, False, False, True, False],
        timer_s=[3600, np.nan, np.nan, 2100, np.nan],
    )
    assert w.completed.tolist() == [True, True, True, True, False]


def test_day_off_is_never_done():
    assert wko(sport_tp=["Day Off"], tp_duration_s=[3600]).completed.tolist() == [False]


# ---------------------------------------------------------------- one copy of each session
def test_fit_with_the_same_start_as_a_wko_entry_is_a_duplicate():
    w = wko(t_local=["2026-08-03 06:24"], code_4046=[2], has_file=[True], timer_s=[3600])
    a = fit([("x.fit", "2026-08-03 05:25", "running", 3600, 100)])  # 06:25 in Lisbon
    kept, fate = bd.drop_duplicate_fits(a, w)
    assert len(kept) == 0 and fate == {0: "duplicado do WKO (mesma hora)"}


def test_fit_recorded_in_another_time_zone_matches_by_day_and_duration():
    w = wko(t_local=["2026-08-03 10:00"], code_4046=[2], has_file=[True], timer_s=[3600])
    a = fit(
        [
            ("abroad.fit", "2026-08-03 14:00", "running", 3650, 100),  # 5 h off, same duration: duplicate
            ("other.fit", "2026-08-03 14:00", "cycling", 3650, 100),  # other sport: a different session
            ("second.fit", "2026-08-03 17:00", "running", 1800, 100),
        ]
    )  # same sport, other duration: a second run
    kept, fate = bd.drop_duplicate_fits(a, w)
    assert kept.file.tolist() == ["other.fit", "second.fit"]
    assert fate == {0: "duplicado do WKO (mesmo dia, desporto e duração)"}


def test_wko_entry_without_its_file_does_not_beat_a_fit():
    w = wko(t_local=["2026-08-03 06:24"], tp_duration_s=[3600], tp_4038=[60])
    a = fit([("x.fit", "2026-08-03 05:24", "running", 3600, 100)])
    kept, _ = bd.drop_duplicate_fits(a, w)
    assert kept.file.tolist() == ["x.fit"]


def test_empty_fit_and_double_export():
    w = wko(t_local=["2020-01-01 10:00"], code_4046=[2], has_file=[True], timer_s=[600])
    a = fit(
        [
            ("empty.fit", None, "cycling", np.nan, 0),
            ("tp-1.FIT.gz", "2026-10-02 16:46:24", "running", 2948, 2900),
            ("intervals-i1.fit", "2026-10-02 16:46:24", "running", 2948, 2900),
        ]
    )
    a.loc[0, "has_session"] = False
    kept, fate = bd.drop_duplicate_fits(a, w)
    assert kept.file.tolist() == ["intervals-i1.fit"]  # the Intervals copy carries the activity name
    assert fate == {0: "sem dados no ficheiro", 1: "duplicado de outro FIT (TP e Intervals)"}


def test_fileless_wko_entry_gives_way_to_the_fit_of_the_same_session():
    # Athlete2, 3 Aug 2026: TP has the indoor ride with duration and TSS, the file comes from TrainingPeaks Virtual
    w = wko(
        t_local=["2026-08-03 06:24", "2026-08-03 00:00", "2026-08-03 00:00"],
        sport_tp=["Indoor Bike", "Strength", "Indoor Bike"],
        tp_duration_s=[7288, 3600, 3000],
        tp_4038=[84, np.nan, 40],
    )
    a = fit([("virtual.fit", "2026-08-03 05:22:15", "cycling", 7288, 8202)])
    assert bd.fileless_wko_with_fit(w, a) == [0]  # strength has no FIT; the 50 min ride is another session


# ---------------------------------------------------------------- races in separate files
def legs(rows):
    df = pd.DataFrame(rows, columns=["t_local", "sport_n", "duration_h", "distance_km"])
    df["t_local"] = pd.to_datetime(df.t_local)
    df["athlete"], df["date"], df["multisport"] = "A", df.t_local.dt.normalize(), False
    return df


def test_swim_bike_run_chained_within_20_minutes_is_one_race():
    df = legs(
        [
            ("2026-04-12 08:01", "swim", 0.63, 2.1),
            ("2026-04-12 08:43", "bike", 2.61, 89.8),
            ("2026-04-12 11:20", "run", 1.41, 21.0),
        ]
    )
    bd.mark_chained_races(df)
    assert df.multisport.all() and df.chained.all()


def test_sessions_hours_apart_or_of_the_same_sport_are_not_a_race():
    df = legs(
        [
            ("2026-04-12 08:00", "bike", 2.0, 60.0),
            ("2026-04-12 15:00", "run", 1.0, 10.0),  # 5 h gap
            ("2026-04-13 08:00", "run", 0.5, 5.0),
            ("2026-04-13 08:35", "run", 0.5, 5.0),
        ]
    )  # two runs
    bd.mark_chained_races(df)
    assert not df.multisport.any()


def test_short_bike_before_a_run_is_a_brick_not_a_race():
    df = legs([("2026-04-12 08:00", "bike", 0.5, 12.0), ("2026-04-12 08:35", "run", 0.3, 3.0)])
    bd.mark_chained_races(df)
    assert not df.multisport.any()


# ---------------------------------------------------------------- scoring
def test_one_hour_at_lthr_is_100_tss():
    lthr = 160
    hist = json.dumps({str(lthr - 1): 3600})  # the bin 159-160 is read as 160 bpm
    assert bd.hr_tss(hist, lthr, rest=60, hr_max=185) == pytest.approx(100)
    assert bd.hr_tss(json.dumps({str(lthr - 1): 1800}), lthr, 60, 185) == pytest.approx(50)


def test_easy_hour_scores_well_under_100_and_women_use_their_own_constant():
    easy = json.dumps({"119": 3600})
    assert 30 < bd.hr_tss(easy, 160, 60, 185) < 60
    assert bd.hr_tss(easy, 160, 60, 185, female=True) > bd.hr_tss(easy, 160, 60, 185)


def test_hr_tss_without_data_is_nan():
    assert (
        np.isnan(bd.hr_tss(None, 160, 60, 185))
        and np.isnan(bd.hr_tss("{}", 160, 60, 185))
        and np.isnan(bd.hr_tss('{"150": 60}', np.nan, 60, 185))
    )


def session(**kw):
    row = {
        "athlete": "A",
        "sport_n": "bike",
        "duration_h": 1.0,
        "np": np.nan,
        "ftp_used": np.nan,
        "lthr_used": np.nan,
        "avg_hr": np.nan,
        "hr_hist": None,
    }
    row.update(kw)
    return pd.Series(row)


def test_tss_method_falls_back_from_power_to_heart_rate_to_duration():
    limits = dict(rest={"A": 60}, hr_max={"A": 185}, females=set())
    assert bd.estimate_tss(session(np=200, ftp_used=250), **limits) == (pytest.approx(64), "power")
    assert bd.estimate_tss(session(lthr_used=160, hr_hist=json.dumps({"159": 3600})), **limits) == (pytest.approx(100), "hr")
    assert bd.estimate_tss(session(sport_n="swim", lthr_used=160, avg_hr=128), **limits) == (
        pytest.approx(64),
        "hr",
    )  # swims: average HR
    assert bd.estimate_tss(session(sport_n="strength"), **limits) == (25, "duration")
    assert bd.estimate_tss(session(duration_h=0), **limits)[1] is None


def test_dead_power_meter_loses_its_power():
    df = pd.DataFrame(
        {
            "athlete": ["A", "A"],
            "t_local": pd.to_datetime(["2026-10-03", "2026-10-04"]),
            "sport_n": ["bike", "bike"],
            "power_cov": [0.05, 0.93],
            "np": [112.0, 180.0],
            "avg_power": [9.0, 170.0],
            "max_power": [400.0, 600.0],
            "pw_hist": ["{}", "{}"],
            "p_best_60s": [150.0, 300.0],
            "has_power": [True, True],
        }
    )
    bd.drop_dead_power(df)
    assert df.power_dropout.tolist() == [True, False]
    assert np.isnan(df.np[0]) and np.isnan(df.p_best_60s[0]) and not df.has_power[0] and df.np[1] == 180


def test_ftp_fallback_from_athlete_overrides():
    table = {"Other": {"2026Q3": 250.0}}
    row = lambda athlete, day: pd.Series({"athlete": athlete, "quarter": "2026Q3", "t_local": pd.Timestamp(day)})
    assert bd.ftp_on(row("Other", "2026-08-01"), table) == 250
    assert np.isnan(bd.ftp_on(row("Nobody", "2026-08-01"), table))  # no TP value, no fallback
    assert bd.ftp_on(row("Athlete2", "2026-06-01"), table) == 238  # athlete_overrides.json
    assert bd.ftp_on(row("Athlete2", "2026-08-01"), table) == 247


def test_resting_hr_does_not_follow_the_daily_reading():
    df = pd.DataFrame({"athlete": ["A"], "max_hr": [170.0], "t_local": pd.to_datetime(["2026-10-01"])})
    history = {"A": [{"date": "2026-10-04", "settings": {"run": {"max_hr": 180}, "resting_hr": 47}}]}
    rest, top = bd.hr_limits(df, history)
    assert rest == {"A": 60} and top == {"A": 180}


def test_parser_reads_records_without_or_out_of_order_timestamps(monkeypatch):
    """Pool swims of older Garmins write one record per length without a timestamp; FORM goggles write some out of order."""
    import parse_fit
    from datetime import datetime, timedelta, timezone

    t0 = datetime(2022, 1, 3, 8, 0, tzinfo=timezone.utc)
    session = {
        "sport": "swimming",
        "start_time": t0,
        "total_timer_time": 600.0,
        "total_elapsed_time": 600.0,
        "total_distance": 500.0,
    }
    cases = {
        "no timestamps": [{"distance": 25.0 * i} for i in range(20)],
        "out of order": [{"timestamp": t0 + timedelta(seconds=s), "heart_rate": 120} for s in (0, 10, 30, 20, 40, 600, 590)],
    }
    for name, records in cases.items():
        msgs = {"session_mesgs": [session], "record_mesgs": records}
        monkeypatch.setattr(parse_fit.Decoder, "read", lambda self, msgs=msgs, **kw: (msgs, []))  # the decoder is not under test
        rows, _ = parse_fit.parse_fit_bytes(b"\x0e\x10" + b"\x00" * 12, "A", name)
        assert rows[0].get("error") is None, name
        assert rows[0]["timer_s"] == 600 and rows[0]["sport"] == "swimming"


def recordings(rows):
    """Unified-table rows: (source, file, start, sport, hours, np, avg_hr, n_records)."""
    df = pd.DataFrame(rows, columns=["source", "file", "t_local", "sport_n", "duration_h", "np", "avg_hr", "n_records"])
    df["t_local"] = pd.to_datetime(df.t_local)
    df["athlete"], df["completed"], df["avg_power"], df["session_index"] = "A", True, np.nan, 0
    df["multisport"], df["n_sessions"] = False, 1
    for c in bd.PLAN_COLS:
        df[c] = np.nan
    return df


def test_watch_and_zwift_on_at_once_keep_the_watch_copy():
    # Marcelo, 7 Oct 2026: the watch read the trainer's power and the chest strap; Zwift has power only
    df = recordings(
        [
            ("fit", "intervals-zwift.fit", "2026-10-07 07:38:06", "bike", 1.337, 170, np.nan, 4812),
            ("fit", "intervals-watch.fit", "2026-10-07 07:40:25", "bike", 1.254, 172, 139, 4519),
        ]
    )
    assert bd.double_recordings(df) == {0}


def test_edge_and_watch_in_tp_keep_the_one_with_power():
    df = recordings(
        [
            ("wko", "a.wko4", "2024-07-14 10:12", "bike", 1.50, np.nan, 156, 5400),
            ("wko", "b.wko4", "2024-07-14 10:13", "bike", 1.51, 178, np.nan, 5430),
            ("wko", "c.wko4", "2024-07-14 11:50", "run", 1.40, np.nan, 160, 5000),
        ]
    )  # the run is another session
    assert bd.double_recordings(df) == {0}


def test_short_ride_inside_a_long_one_and_two_rides_in_a_day_are_kept():
    df = recordings(
        [
            ("wko", "a.wko4", "2025-02-09 09:45", "bike", 2.57, 155, 112, 9000),
            ("wko", "b.wko4", "2025-02-09 12:01", "bike", 0.26, 160, 119, 900),
            ("fit", "c.fit", "2025-02-09 18:00", "bike", 1.00, 150, 120, 3600),
        ]
    )
    assert bd.double_recordings(df) == set()


def test_the_plan_moves_to_the_copy_that_stays():
    df = recordings(
        [
            ("wko", "edge.wko4", "2025-01-28 21:32:38", "bike", 1.01, 137, 122, 3600),
            ("wko", "zwift.wko4", "2025-01-28 21:33:21", "bike", 1.00, 137.7, np.nan, 3600),
        ]
    )
    df["title_n"], df["plan_desc"] = ["Road Cycling", "1h Z2"], [np.nan, "Z2 todo"]
    df["planned_tss"], df["planned_duration_s"], df["planned_distance_km"] = [np.nan, 45], [np.nan, 3600], [np.nan, np.nan]
    assert bd.double_recordings(df) == {1}
    assert df.loc[0, "title_n"] == "1h Z2" and df.loc[0, "planned_tss"] == 45 and df.loc[0, "plan_desc"] == "Z2 todo"


def test_race_leg_recorded_twice_stays_part_of_the_race():
    # Hamburg 2025: the watch's multisport file and the Edge both recorded the bike leg; the Edge copy (power) stays
    df = recordings(
        [
            ("wko", "edge.wko4", "2025-06-01 08:45:23", "bike", 5.99, 169, 124, 21000),
            ("wko", "watch.wko4", "2025-06-01 08:45:30", "bike", 5.99, np.nan, 124, 21000),
        ]
    )
    df["multisport"], df["n_sessions"] = [False, True], [1, 5]
    assert bd.double_recordings(df) == {1}
    assert bool(df.loc[0, "multisport"]) and df.loc[0, "n_sessions"] == 5


def test_zwift_power_and_watch_heart_rate_become_one_session():
    import pandas as pd
    import build_dataset as bd

    t = pd.Timestamp("2026-10-18 09:00")
    df = pd.DataFrame(
        [  # Zwift: power, no heart rate · watch: heart rate, no power · same 2 h ride
            {
                "athlete": "M",
                "completed": True,
                "sport_n": "bike",
                "t_local": t,
                "duration_h": 2.0,
                "np": 160,
                "avg_power": 150,
                "avg_hr": float("nan"),
                "max_hr": float("nan"),
                "has_hr": False,
                "hr_hist": None,
                "n_records": 7200,
                "planned_tss": float("nan"),
                "planned_duration_s": float("nan"),
                "multisport": False,
                "n_sessions": 1,
            },
            {
                "athlete": "M",
                "completed": True,
                "sport_n": "bike",
                "t_local": t + pd.Timedelta(minutes=1),
                "duration_h": 1.98,
                "np": 0,
                "avg_power": 0,
                "avg_hr": 128.0,
                "max_hr": 150.0,
                "has_hr": True,
                "hr_hist": '{"128": 3600}',
                "n_records": 7100,
                "planned_tss": float("nan"),
                "planned_duration_s": float("nan"),
                "multisport": False,
                "n_sessions": 1,
            },
        ]
    )
    for c in bd.PLAN_COLS:
        if c not in df:
            df[c] = None
    drop = bd.double_recordings(df)
    assert drop == {1}
    kept = df.loc[0]
    assert kept.avg_power == 150 and kept.avg_hr == 128.0 and kept.max_hr == 150.0 and kept.has_hr

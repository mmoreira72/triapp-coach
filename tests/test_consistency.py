"""The coach's consistency rule: one week at 50% or less of the plan is yellow; repeated, or a very irregular load, red."""

import skills_engine as se

WEEKS = ["2026-08-17", "2026-08-24", "2026-08-31", "2026-09-07", "2026-09-14", "2026-09-21"]  # complete weeks before 28 Sep


def athlete(done_of_planned, hours=None, last_date="2026-10-03"):
    hours = hours or [8] * len(WEEKS)
    return {
        "last_date": last_date,
        "weekly52": [{"w": w, "h": h} for w, h in zip(WEEKS, hours)] + [{"w": "2026-09-28", "h": 1}],
        "compliance": {
            "weeks": [{"w": w, "planned": p, "planned_done": d, "matched": 0} for w, (d, p) in zip(WEEKS, done_of_planned)]
            + [{"w": "2026-09-28", "planned": 6, "planned_done": 0, "matched": 0}]
        },
    }  # the running week never counts


def verdict(A):
    f = se.skill_consistency(None, A)
    return f[0]["severity"] if f else None


def test_plan_followed():
    assert verdict(athlete([(5, 6)] * 6)) == "ok"


def test_one_low_week_is_yellow():
    assert verdict(athlete([(6, 6)] * 5 + [(2, 4)])) == "watch"


def test_two_low_weeks_of_the_last_three_are_red():
    assert verdict(athlete([(6, 6)] * 3 + [(2, 4), (6, 6), (1, 4)])) == "alert"


def test_a_low_week_earlier_on_is_forgiven():
    assert verdict(athlete([(1, 6)] + [(6, 6)] * 5)) == "ok"


def test_irregular_load_alone_is_yellow_and_red_with_a_low_week():
    swing = [2, 14, 1, 15, 2, 14]
    assert verdict(athlete([(6, 6)] * 6, hours=swing)) == "watch"
    assert verdict(athlete([(6, 6)] * 5 + [(2, 4)], hours=swing)) == "alert"


def test_sessions_done_but_not_paired_in_tp_count_as_done():
    A = athlete([(6, 6)] * 5 + [(2, 6)])
    A["compliance"]["weeks"][5]["matched"] = 3
    assert verdict(A) == "ok"


def test_a_week_cut_by_the_end_of_the_export_is_not_a_missed_week():
    # data ends on Wed 23 Sep: the week of 21 Sep is incomplete and must not count, although it shows 1 of 6
    A = athlete([(6, 6)] * 5 + [(1, 6)], last_date="2026-09-23")
    assert verdict(A) == "ok"


def test_no_plan_only_judges_regularity_and_no_data_gives_no_card():
    A = athlete([(6, 6)] * 6)
    A["compliance"] = None
    assert verdict(A) == "info"
    assert verdict({"last_date": "2025-09-20", "weekly52": [{"w": w, "h": 0} for w in WEEKS], "compliance": None}) is None

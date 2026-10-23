import numpy as np, pandas as pd
import matching

T = pd.Timestamp("2026-08-03 06:24")


def starts(*times):
    return pd.Series(pd.to_datetime(list(times)))


def test_near_start():
    s = starts("2026-08-03 06:22", "2026-08-03 06:40")
    assert matching.near_start(s, T, minutes=3).tolist() == [True, False]
    assert matching.near_start(s, T, minutes=20).tolist() == [True, True]


def test_same_day_duration_survives_a_time_zone_shift():
    # recorded abroad: the clocks differ by hours, the day is within one and the duration is the same
    s = starts("2026-08-02 23:24")
    assert matching.same_day_duration(s, pd.Series([7300.0]), T, 7288).tolist() == [True]


def test_duration_tolerance_is_3_percent_or_2_minutes():
    s = starts("2026-08-03 07:00", "2026-08-03 07:00", "2026-08-03 07:00")
    durations = pd.Series([600.0 + 119, 600.0 + 121, 7288 * 1.029])
    assert matching.same_day_duration(s, durations, T, 600).tolist() == [True, False, False]
    assert matching.same_day_duration(s, durations, T, 7288).tolist() == [False, False, True]


def test_other_day_or_unknown_duration_do_not_match():
    s = starts("2026-08-05 06:24")
    assert matching.same_day_duration(s, pd.Series([7288.0]), T, 7288).tolist() == [False]
    assert matching.same_day_duration(starts("2026-08-03 06:24"), pd.Series([7288.0]), T, np.nan).tolist() == [False]
    assert matching.same_day_duration(starts("2026-08-03 06:24"), pd.Series([np.nan]), T, 7288).tolist() == [False]


def test_overlap_share_is_measured_against_the_longer_window():
    t = lambda s: pd.Timestamp(f"2026-10-07 {s}")
    # watch 07:40:25-08:55:40, Zwift 07:38:06-08:58:18 (Marcelo, 7 Oct 2026)
    assert matching.overlap_share(t("07:40:25"), t("08:55:40"), t("07:38:06"), t("08:58:18")) > 0.9
    # a 15 min ride inside a 2 h 30 ride is not a second recording of it
    assert matching.overlap_share(t("09:45"), t("12:19"), t("12:01"), t("12:17")) < 0.2
    assert matching.overlap_share(t("07:00"), t("08:00"), t("09:00"), t("10:00")) == 0

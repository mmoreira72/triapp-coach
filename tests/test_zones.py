import zones


def test_heart_rate_zones_run():
    lthr = 160
    assert zones.hr_zone(130, lthr, "run") == 1  # 81%
    assert zones.hr_zone(136, lthr, "run") == 2  # 85%
    assert zones.hr_zone(144, lthr, "run") == 3  # 90%
    assert zones.hr_zone(152, lthr, "run") == 4  # 95%
    assert zones.hr_zone(160, lthr, "run") == 5  # 100% = 5a
    assert zones.hr_zone(166, lthr, "run") == 6  # 103.75% = 5b
    assert zones.hr_zone(170, lthr, "run") == 7  # 106.25% = 5c


def test_bike_zone_2_starts_lower_than_run():
    assert zones.hr_zone(130, 160, "bike") == 2 and zones.hr_zone(130, 160, "run") == 1


def test_single_zone_5_when_not_split():
    assert zones.hr_zone(170, 160, "run", split_z5=False) == 5


def test_power_zones():
    ftp = 200
    assert [zones.power_zone(w, ftp) for w in (100, 110, 152, 182, 212, 242, 302)] == [1, 2, 3, 4, 5, 6, 7]


def test_missing_values_give_no_zone():
    assert zones.hr_zone(None, 160, "run") is None
    assert zones.power_zone(200, None) is None
    assert zones.power_zone(0, 200) is None


def test_labels():
    assert zones.label(6, "FC") == "Z5b" and zones.label(6, "potência") == "Z6" and zones.label(3, "FC") == "Z3"


def test_seconds_by_zone_places_bins_by_their_centre():
    # LTHR 160 run: Z2 starts at 136 bpm. The bin 134-135 (centre 135) is Z1, the bin 136-137 is Z2.
    assert zones.seconds_by_zone({134: 60, 136: 30}, 160, zones.HR_EDGES["run"], 2) == {1: 60, 2: 30}
    assert zones.seconds_by_zone({}, 160, zones.HR_EDGES["run"], 2) == {}
    assert zones.seconds_by_zone({134: 60}, None, zones.HR_EDGES["run"], 2) == {}

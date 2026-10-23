import numpy as np
import threshold_projection as tp


def test_reps_read_the_last_minute_of_each_hard_block():
    easy, rest = np.full(600, 135.0), np.full(120, 138.0)
    rep = lambda end: np.linspace(145, end, 240)
    hr = np.concatenate([easy, rep(152), rest, rep(156), rest, rep(160), rest, rep(162), easy])
    ends = tp.reps(hr)
    assert len(ends) == 4 and ends[-1] > ends[0] and 158 < max(ends) <= 162


def test_ftp_scales_a_steady_20_min_ridden_below_threshold():
    p = np.concatenate([np.full(900, 120.0), np.full(1200, 185.0), np.full(600, 150.0), np.full(300, 200.0), np.full(600, 120.0)])
    hr = np.full(len(p), 148.0)
    out = tp.bike([{"start": __import__("pandas").Timestamp("2026-10-14"), "power": p, "hr": hr}], lthr_bike=155, rest_hr=58)
    f = out["ftp"]
    assert f["low"] >= 185 * 0.95 and f["low"] <= f["value"] <= f["high"] <= 185 * 1.10 + 1 and f["confidence"] == "média"
    out = tp.bike([{"start": __import__("pandas").Timestamp("2026-10-14"), "power": p, "hr": None}], lthr_bike=155, rest_hr=58)
    assert out["ftp"]["confidence"] == "baixa" and out["ftp"]["value"] == out["ftp"]["low"]


def test_a_steady_run_has_no_reps():
    rng = np.random.default_rng(1)
    hr = 140 + np.cumsum(rng.normal(0, 0.05, 3600)) + 3 * np.sin(np.arange(3600) / 300)
    assert tp.reps(hr) == []

"""The power-duration model recovers the parameters of a known curve and gives a sensible TTE."""

import numpy as np
import pd_model as pm

T = np.array(
    [
        1,
        2,
        3,
        5,
        10,
        15,
        20,
        30,
        45,
        60,
        90,
        120,
        180,
        240,
        300,
        360,
        480,
        600,
        720,
        900,
        1200,
        1500,
        1800,
        2400,
        3000,
        3600,
        4500,
        5400,
        7200,
        9000,
        10800,
    ],
    float,
)
TRUE = dict(pmax=900.0, cp=250.0, w=20000.0, a=20.0)


def test_fit_recovers_a_known_curve():
    p = pm.ompd(T, **TRUE)
    r = pm.fit(T, p)
    assert abs(r["mftp"] - 250) <= 3 and abs(r["pmax"] - 900) <= 40 and abs(r["frc_kj"] - 20) <= 1.5 and r["rmse_pct"] < 1


def test_tte_is_where_the_mmp_falls_below_cp():
    t = [60, 300, 900, 1200, 3600]
    p = [400, 280, 255, 245, 200]
    x = pm.tte(t, p, 250)
    assert 900 < x < 1200  # between the last duration at or above CP and the first below it
    assert pm.tte(t, p, 190) == 3600  # held CP for the longest window available
    assert pm.tte(t, p, 450) is None  # never reached CP


def test_noisy_submaximal_points_still_give_a_close_cp():
    rng = np.random.default_rng(1)
    p = pm.ompd(T, **TRUE) * (1 - rng.uniform(0, 0.04, len(T)))  # best efforts are a little under the true capacity
    r = pm.fit(T, p)
    assert 235 <= r["mftp"] <= 252


def test_too_thin_a_curve_is_not_modelled():
    assert pm.fit([5, 60, 300], [800, 400, 300]) is None
    assert pm.fit([1, 5, 10, 30, 60, 90], [900, 850, 700, 500, 420, 380]) is None  # nothing at 20 min or longer

"""Power-duration model: the open counterpart of the WKO5 metrics Pmax, mFTP, FRC and TTE.

WKO5's model is proprietary. This module fits the Omni Power-Duration model (OmPD; Puchowicz, Baker & Clarke, 2020, J Sports
Sci), which gives the same four quantities from the athlete's mean-maximal power (MMP) curve:

  P(t) = W'/t * (1 - exp(-t * (Pmax - CP) / W')) + CP                      t <= 1800 s
  P(t) = W'/t * (1 - exp(-t * (Pmax - CP) / W')) + CP - A * ln(t / 1800)   t >  1800 s

  Pmax  power over the first seconds                  (WKO5: Pmax)
  CP    critical power, the aerobic ceiling            (WKO5: mFTP)
  W'    work capacity above CP, in kJ                  (WKO5: FRC)
  A     decline of power past 30 min, W per ln-unit    (endurance)
  TTE   longest duration the athlete actually held mFTP: where the MMP curve crosses mFTP (WKO5: TTE). OmPD's own curve
        only falls below CP after 30 min, so a model-derived TTE would read 31-35 min for everyone

The fit is least squares on the MMP points (log-spaced durations, so short and long efforts weigh alike), with a Nelder-Mead
search (no SciPy on the box). The MMP curve should hold maximal efforts at many durations: gaps (no hard 3-8 min efforts, no
long rides) bias the parameters, as they do in WKO5.
"""

import math
import numpy as np

TCP_MAX = 1800.0  # s: OmPD's switch to the long-duration decline
TTE_CAP = 4 * 3600.0  # s: longest TTE reported


def ompd(t, pmax, cp, w, a):
    t = np.asarray(t, float)
    p = w / t * (1 - np.exp(-t * (pmax - cp) / w)) + cp
    return np.where(t > TCP_MAX, p - a * np.log(t / TCP_MAX), p)


def tte(t, p, cp):
    """Seconds at which the MMP curve falls below CP, interpolated in log time (None when no effort reaches CP)."""
    if not len(t) or p[0] < cp:
        return None
    for t0, p0, t1, p1 in zip(t, p, t[1:], p[1:]):
        if p0 >= cp > p1:
            f = (p0 - cp) / (p0 - p1)
            return float(math.exp(math.log(t0) + f * (math.log(t1) - math.log(t0))))
    return min(float(t[-1]), TTE_CAP)


def _nelder_mead(f, x0, steps, iters=2000, tol=1e-9):
    n = len(x0)
    pts = [np.array(x0, float)] + [np.array(x0, float) + np.eye(n)[i] * steps[i] for i in range(n)]
    vals = [f(p) for p in pts]
    for _ in range(iters):
        order = np.argsort(vals)
        pts, vals = [pts[i] for i in order], [vals[i] for i in order]
        if abs(vals[-1] - vals[0]) < tol:
            break
        centre = np.mean(pts[:-1], axis=0)
        refl = centre + (centre - pts[-1])
        fr = f(refl)
        if fr < vals[0]:
            exp_ = centre + 2 * (centre - pts[-1])
            fe = f(exp_)
            pts[-1], vals[-1] = (exp_, fe) if fe < fr else (refl, fr)
        elif fr < vals[-2]:
            pts[-1], vals[-1] = refl, fr
        else:
            con = centre + 0.5 * (pts[-1] - centre)
            fc = f(con)
            if fc < vals[-1]:
                pts[-1], vals[-1] = con, fc
            else:
                pts = [pts[0]] + [pts[0] + 0.5 * (p - pts[0]) for p in pts[1:]]
                vals = [vals[0]] + [f(p) for p in pts[1:]]
    i = int(np.argmin(vals))
    return pts[i], vals[i]


def fit(durations, powers):
    """OmPD parameters from MMP points. None when the curve is too thin to model (fewer than 6 durations, nothing at
    2-15 min, or nothing at 20 min or longer)."""
    t = np.asarray(durations, float)
    p = np.asarray(powers, float)
    ok = np.isfinite(p) & (p > 0) & (t > 0)
    t, p = t[ok], p[ok]
    if len(t) < 6 or not ((t >= 120) & (t <= 900)).any() or not (t >= 1200).any():
        return None
    p20 = float(np.interp(1200, t, p))
    x0 = [float(p.max()) * 1.05, p20 * 0.95, 20000.0, 15.0]

    def loss(x):
        pmax, cp, w, a = x
        if not (cp > 50 and pmax > cp * 1.2 and 2000 < w < 60000 and -5 < a < 200):
            return 1e12
        r = (ompd(t, pmax, cp, w, a) - p) / p  # relative error: every duration counts alike
        return float(np.mean(r**2))

    best, val = _nelder_mead(loss, x0, steps=[x0[0] * 0.1, x0[1] * 0.05, 4000.0, 5.0])
    best, val = _nelder_mead(loss, best, steps=[best[0] * 0.05, best[1] * 0.02, 2000.0, 2.0])  # restart: escape flat spots
    pmax, cp, w, a = (float(v) for v in best)
    t_ex = tte(t, p, cp)
    return {
        "pmax": round(pmax),
        "mftp": round(cp),
        "frc_kj": round(w / 1000, 1),
        "a": round(a, 1),
        "tte_min": round(t_ex / 60) if t_ex else None,
        "rmse_pct": round(100 * math.sqrt(val), 1),
    }


def mmp(sessions, windows):
    """Mean-maximal power: the best of each window across the sessions (rows with p_best_<w>s columns), made non-increasing
    with duration (a best 25 min can never be below a best 30 min; files with gaps can break that by a few watts)."""
    out = []
    for w in windows:
        c = f"p_best_{w}s"
        if c in sessions and sessions[c].notna().any():
            out.append([w, float(sessions[c].max())])
    for i in range(len(out) - 2, -1, -1):
        out[i][1] = max(out[i][1], out[i + 1][1])
    return [tuple(x) for x in out]

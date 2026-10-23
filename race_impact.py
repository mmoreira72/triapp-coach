"""Race impact: predict each athlete's splits on every course in courses/<slug>/ and what the course demands of them.

Inputs: courses/<slug>/{race.json, bike.gpx, course_summary.json, weather_summary.json}, TRIAPP_OUT/data/{activities_all.csv,
dashboard_data.json}, and the athletes' past race files (embedded FIT in the WKO bundle) to calibrate CdA.
Output: TRIAPP_OUT/data/race_impact.json  {slug: {race, course, weather, athletes: {name: prediction}}}

Model (documented so the coach can challenge it):
- Bike: physics per 50 m segment (gravity, rolling Crr 0.004, aero 0.5*rho*CdA*v_air^2, drivetrain 97.5%), mass = rider + 9 kg.
  Pacing: target power on flats, x1.10 on grades > 3 %, x0.6 on grades < -3 %, no pedalling above 55 km/h.
  CdA per athlete = the value that reproduces the actual bike time of each past half-distance race on its own GPS course at
  the actual NP (median over races; the spread between races gives the uncertainty band, which mostly reflects race-day wind).
- Air density from the race-window temperature and the bike course's mean altitude.
- Wind: 10 m wind scaled x0.75 to rider height, component along each segment's bearing.
- Swim, transitions, run: medians of the athlete's past races of the same distance (most recent 3), with the run adjusted
  for the out-and-back wind cost. A distance never raced: the half-distance races scaled (DISTANCES[...]['from_half']). Rules that
  depend on the distance (IF range, long sessions) are in DISTANCES. No heat penalty (the TP science review gives no
  per-degree formula); the heat shows as a gap instead.
"""

import os, re, json, math, glob, gzip, sys
from pathlib import Path
from datetime import datetime
import numpy as np, pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from athletes import discover

HERE = Path(__file__).resolve().parent
ROOT = os.environ.get("TRIAPP_ROOT") or str(HERE.parent)
OUT = Path(os.environ.get("TRIAPP_OUT", str(HERE / "out")))
TODAY = pd.Timestamp(os.environ.get("TRIAPP_TODAY", datetime.now().strftime("%Y-%m-%d")))
G, CRR, ETA, BIKE_KG = 9.81, 0.004, 0.975, 9.0
# what changes with the race distance
DISTANCES = {
    "70.3": {
        "label": "meio-ironman",
        "bike_km": (70, 100),
        "if_clip": (0.70, 0.85),
        "if_options": [0.72, 0.76, 0.80, 0.84],
        "if_default": 0.78,
        "long_ride_h": 2.5,
        "long_run_km": 16,
        "run_label": "uma meia maratona",
        "trans_min": 8.0,
        "long_power": ("2h", 1.0),
        "run_from_90min": 1.06,
        "run_from_60min": 1.10,
    },
    "140.6": {
        "label": "Ironman",
        "bike_km": (160, 200),
        "if_clip": (0.62, 0.78),
        "if_options": [0.64, 0.68, 0.72, 0.76],
        "if_default": 0.70,
        "long_ride_h": 4.5,
        "long_run_km": 25,
        "run_label": "uma maratona",
        "trans_min": 12.0,
        "long_power": ("3h", 0.93),
        "run_from_90min": 1.18,
        "run_from_60min": 1.24,
        # never raced at this distance: start from the half-distance races
        "from_half": {"if_minus": 0.08, "run_factor": 1.13, "trans_factor": 1.5},
    },
    # T100 (PTO): 2 km swim, 80 km bike (about 2 h), 18 km run; a little harder than a half on the bike, a little faster on foot
    "T100": {
        "label": "T100",
        "bike_km": (75, 88),
        "run_km": (16, 19.5),  # a half with a short bike is still a half: the 18 km run tells a T100 apart
        "if_clip": (0.72, 0.88),
        "if_options": [0.75, 0.79, 0.83, 0.87],
        "if_default": 0.80,
        "long_ride_h": 2.0,
        "long_run_km": 14,
        "run_label": "uma corrida de 18 km",
        "trans_min": 8.0,
        "long_power": ("2h", 1.0),
        "run_from_90min": 1.04,
        "run_from_60min": 1.08,
        "from_half": {"if_minus": -0.03, "run_factor": 0.98, "trans_factor": 1.0},
    },
}
SWIM_PACE_RANGE = (1.25, 3.0)  # min/100 m: race swim legs outside this are GPS errors
CDA_PLAUSIBLE = (0.19, 0.42)  # a race CdA outside this range means the race's power or GPS is wrong, not the rider


# ------------------------------------------------------------------ geometry
def read_gpx(path):
    t = Path(path).read_text()
    # track and route points only: waypoints (<wpt>: aid stations, mechanics) carry coordinates too but are not the course
    pts = re.findall(r'<(?:trkpt|rtept)\s+lat="([-\d.]+)"[^>]*lon="([-\d.]+)"[^>]*>\s*(?:<ele>([-\d.]+)</ele>)?', t)
    lat = np.array([float(p[0]) for p in pts])
    lon = np.array([float(p[1]) for p in pts])
    ele = np.array([float(p[2]) if p[2] else np.nan for p in pts])
    return lat, lon, ele


def segments(lat, lon, ele, step=50.0):
    R = 6371000
    dl = np.radians(np.diff(lat))
    dn = np.radians(np.diff(lon))
    a = np.sin(dl / 2) ** 2 + np.cos(np.radians(lat[:-1])) * np.cos(np.radians(lat[1:])) * np.sin(dn / 2) ** 2
    d = np.concatenate([[0], np.cumsum(2 * R * np.arcsin(np.sqrt(a)))])
    brg = (
        np.degrees(
            np.arctan2(
                np.sin(dn) * np.cos(np.radians(lat[1:])),
                np.cos(np.radians(lat[:-1])) * np.sin(np.radians(lat[1:]))
                - np.sin(np.radians(lat[:-1])) * np.cos(np.radians(lat[1:])) * np.cos(dn),
            )
        )
        % 360
    )
    grid = np.arange(0, d[-1], step)
    e = np.interp(grid, d, np.nan_to_num(ele, nan=np.nanmean(ele)))
    k = 5
    e = np.convolve(np.pad(e, (k // 2, k // 2), mode="edge"), np.ones(k) / k, mode="valid")
    seg_brg = np.interp(grid[:-1] + step / 2, d[1:], np.unwrap(np.radians(brg)))
    grade = np.clip(np.diff(e) / step, -0.15, 0.15)
    return {
        "dist": np.full(len(grade), step),
        "grade": grade,
        "bearing": np.degrees(seg_brg) % 360,
        "km": grid[:-1] / 1000,
        "ele": e[:-1],
    }


# ------------------------------------------------------------------ physics
def speed_for_power(P, grade, mass, cda, rho, headwind):
    """Solve P*eta = v*(m*g*(sin+Crr*cos)) + 0.5*rho*CdA*(v+w)^2*v for v (m/s) by bisection."""
    th = math.atan(grade)
    fg = mass * G * (math.sin(th) + CRR * math.cos(th))
    lo, hi = 0.5, 30.0
    for _ in range(50):
        v = (lo + hi) / 2
        va = v + headwind
        need = v * fg + 0.5 * rho * cda * va * abs(va) * v
        if need > P * ETA:
            hi = v
        else:
            lo = v
    return (lo + hi) / 2


def simulate_bike(seg, target_w, mass, cda, rho=1.22, wind_kmh=0.0, wind_from=0.0):
    w10 = wind_kmh / 3.6 * 0.75
    t = 0.0
    p_series = []
    t_series = []
    for dist, g, b in zip(seg["dist"], seg["grade"], seg["bearing"]):
        head = w10 * math.cos(math.radians(wind_from - b))
        P = target_w * (1.10 if g > 0.03 else (0.6 if g < -0.03 else 1.0))
        v = speed_for_power(P, g, mass, cda, rho, head)
        if v > 55 / 3.6:  # coasting on fast descents
            P = 0.0
            v = max(speed_for_power(1.0, g, mass, cda, rho, head), 55 / 3.6)
        dt = dist / v
        t += dt
        p_series.append(P)
        t_series.append(dt)
    p = np.repeat(np.array(p_series), np.maximum(1, np.round(t_series).astype(int)))
    np_w = float(np.mean(np.convolve(p, np.ones(30) / 30, mode="valid") ** 4) ** 0.25) if len(p) > 30 else float(np.mean(p))
    return t / 3600, np_w, float(np.mean(p))


def simulate_track(seg, target_w, mass, cda, rho=1.22, every_m=500.0):
    """The bike leg at the target power, sampled every `every_m`: km, minutes from the start, power and grade. The race page
    places the fuelling on the elevation profile from it (nutrition.py: by time, away from climbs and fast descents)."""
    out, t, d, next_at = [], 0.0, 0.0, 0.0
    for dist, g in zip(seg["dist"], seg["grade"]):
        P = target_w * (1.10 if g > 0.03 else (0.6 if g < -0.03 else 1.0))
        v = speed_for_power(P, g, mass, cda, rho, 0.0)
        if v > 55 / 3.6:
            P, v = 0.0, max(speed_for_power(1.0, g, mass, cda, rho, 0.0), 55 / 3.6)
        t += dist / v
        d += dist
        if d >= next_at:
            out.append({"km": round(d / 1000, 2), "min": round(t / 60, 1), "w": round(P), "grade": round(float(g) * 100, 1)})
            next_at += every_m
    return out


# ------------------------------------------------------------------ athlete calibration
def race_bike_records(profile, row, need_power=True):
    """Per-second bike records of a past race leg, from the FIT embedded in the WKO entry."""
    from wko_decode import load, top_level
    from garmin_fit_sdk import Decoder, Stream

    if not profile.get("wko"):
        return None
    p = os.path.join(ROOT, profile["wko"], str(pd.Timestamp(row.t_local).year), row.file)
    if not os.path.exists(p):
        hits = glob.glob(os.path.join(ROOT, profile["wko"], "*", row.file))
        if not hits:
            return None
        p = hits[0]
    b, st = load(p)
    blob = next((v for fid, wt, v in top_level(b, st) if fid == 4049), None)
    if blob is None:
        return None
    data = gzip.decompress(blob) if bytes(blob[:2]) == b"\x1f\x8b" else bytes(blob)
    m, _ = Decoder(Stream.from_byte_array(bytearray(data))).read(enable_crc_check=False, convert_datetimes_to_dates=True)
    sess = [s for s in m.get("session_mesgs", []) if s.get("sport") in ("cycling", 2)]
    recs = pd.DataFrame(m.get("record_mesgs", []))
    if recs.empty or (need_power and "power" not in recs):
        return None
    if sess:
        st_ = pd.Timestamp(sess[0]["start_time"])
        en = st_ + pd.Timedelta(seconds=sess[0]["total_elapsed_time"])
        recs = recs[(pd.to_datetime(recs.timestamp) >= st_) & (pd.to_datetime(recs.timestamp) <= en)]
    return recs


def cda_from_race_time(recs, mass, actual_h, actual_np):
    """CdA that makes the model reproduce the real bike time of a past race on its own GPS course at its NP."""
    if recs is None or "position_lat" not in recs or not actual_np:
        return None
    g = recs.dropna(subset=["position_lat"])
    alt = g.get("enhanced_altitude", g.get("altitude"))
    if alt is None or len(g) < 600:
        return None
    seg = segments(g.position_lat.values * 180 / 2**31, g.position_long.values * 180 / 2**31, alt.astype(float).values)

    def time_for(cda):
        lo, hi = 50, 450
        for _ in range(22):
            tw = (lo + hi) / 2
            h, npw, _ = simulate_bike(seg, tw, mass, cda, 1.2)
            lo, hi = (lo, tw) if npw > actual_np else (tw, hi)
        return h

    lo, hi = 0.16, 0.45
    for _ in range(14):
        c = (lo + hi) / 2
        lo, hi = (lo, c) if time_for(c) > actual_h else (c, hi)
    return round((lo + hi) / 2, 3)


def estimate_cda(recs, mass):
    if recs is None or len(recs) < 600:
        return None, 0
    alt = recs.get("enhanced_altitude", recs.get("altitude"))
    spd = recs.get("enhanced_speed", recs.get("speed"))
    if alt is None or spd is None:
        return None, 0
    df = pd.DataFrame(
        {"v": spd.astype(float), "p": recs.power.astype(float), "alt": alt.astype(float), "d": recs.distance.astype(float)}
    ).dropna()
    df["v10"] = df.v.rolling(10, center=True).mean()
    df["p10"] = df.p.rolling(10, center=True).mean()
    dd = df.d.diff(10).shift(-5)
    da = df.alt.diff(10).shift(-5)
    df["grade"] = da / dd.replace(0, np.nan)
    df["acc"] = df.v10.diff(5).abs()
    ok = df[(df.v10 > 7.5) & (df.grade.abs() < 0.01) & (df.p10 > 80) & (df.acc < 0.15)]
    if len(ok) < 120:
        return None, len(ok)
    cda = (ok.p10 * ETA - ok.v10 * mass * G * CRR) / (0.5 * 1.2 * ok.v10**3)
    return float(np.clip(cda.median(), 0.20, 0.42)), int(len(ok))


# ------------------------------------------------------------------ prediction
def speed_derived_power(prof, g_all, mass, cda=0.30, max_rides=15):
    """Best 90 min / 2 h power estimated from speed and gradient on recent outdoor rides WITHOUT a power meter.
    Same physics (and CdA) as the course model, so the CdA assumption largely cancels when predicting the course."""
    rides = (
        g_all[
            (g_all.sport_n == "bike")
            & (g_all.has_gps.fillna(False).astype(bool))
            & (g_all.sub_sport.fillna("") != "virtual_activity")
            & ~(g_all["np"] > 0)
            & (g_all.duration_h >= 1.0)
            & (g_all.date >= TODAY - pd.Timedelta(days=365))
        ]
        .sort_values("duration_h", ascending=False)
        .head(max_rides)
    )
    best = {"90min": None, "2h": None}
    used = 0
    for _, row in rides.iterrows():
        try:
            recs = race_bike_records(prof, row, need_power=False)
            if recs is None or len(recs) < 600:
                continue
            alt = recs.get("enhanced_altitude", recs.get("altitude"))
            spd = recs.get("enhanced_speed", recs.get("speed"))
            if alt is None or spd is None:
                continue
            raw = pd.DataFrame(
                {
                    "ts": pd.to_datetime(recs.timestamp),
                    "v": spd.astype(float),
                    "alt": alt.astype(float),
                    "dist": recs.distance.astype(float),
                }
            ).dropna()
            # 1 Hz on moving time: smart recording (1 point every 2-5 s) is interpolated, pauses > 5 s are removed
            sec = (raw.ts - raw.ts.iloc[0]).dt.total_seconds().values
            dt = np.diff(sec, prepend=sec[0])
            dt = np.where(dt > 5, 1.0, dt)
            t = np.cumsum(dt) - dt[0]
            grid = np.arange(0, t[-1], 1.0)
            d = pd.DataFrame(
                {
                    "v": np.interp(grid, t, raw.v.values),
                    "alt": np.interp(grid, t, raw.alt.values),
                    "dist": np.interp(grid, t, raw.dist.values),
                }
            )
            d = d[d.v > 1.5]  # moving only
            v = d.v.rolling(5, center=True, min_periods=1).mean().values
            dz = pd.Series(d.alt.values).rolling(15, center=True, min_periods=1).mean().diff().fillna(0).values
            dx = np.maximum(np.diff(d.dist.values, prepend=d.dist.values[0]), 0.1)
            grade = np.clip(dz / dx, -0.15, 0.15)
            acc = np.gradient(v)
            p = (mass * G * (np.sin(np.arctan(grade)) + CRR) * v + 0.5 * 1.2 * cda * v**3 + mass * acc * v) / ETA
            p = np.clip(p, 0, 1500)
            for lbl, w in (("90min", 5400), ("2h", 7200)):
                if len(p) >= w:
                    m = float((np.convolve(p, np.ones(w) / w, mode="valid")).max())
                    best[lbl] = max(best[lbl] or 0, m)
            used += 1
        except Exception:
            continue
    return best, used


def training_cda(prof, g_all, mass, max_rides=6):
    """CdA from flat, steady seconds of recent outdoor training rides (road position, so usually a bit higher than race)."""
    rides = (
        g_all[
            (g_all.sport_n == "bike")
            & (g_all.has_gps.fillna(False).astype(bool))
            & (g_all.sub_sport.fillna("") != "virtual_activity")
            & (g_all["np"] > 0)
            & (g_all.duration_h >= 1.0)
            & (g_all.date >= TODAY - pd.Timedelta(days=365))
        ]
        .sort_values("t_local", ascending=False)
        .head(max_rides)
    )
    vals = []
    for _, row in rides.iterrows():
        try:
            c, n = estimate_cda(race_bike_records(prof, row), mass)
            if c:
                vals.append(c)
        except Exception:
            pass
    return (float(np.median(vals)), len(vals)) if vals else (None, 0)


def fmt_h(h):
    m = int(round(h * 60))
    return f"{m // 60}:{m % 60:02d}"


def run_wind_factor(pace_min_km, mass, wind_kmh, rho=1.22):
    """Relative time increase on a flat out-and-back run with wind (half into, half with), constant metabolic power."""
    v = 1000 / (pace_min_km * 60)
    cda_run = 0.24
    cost = 4.0  # J/kg/m

    def power(v_, w):
        return cost * mass * v_ + 0.5 * rho * cda_run * (v_ + w) * abs(v_ + w) * v_

    P = power(v, 0.0)
    w = wind_kmh / 3.6 * 0.75

    def v_at(wd):
        lo, hi = 0.5, 8.0
        for _ in range(40):
            m_ = (lo + hi) / 2
            if power(m_, wd) > P:
                hi = m_
            else:
                lo = m_
        return (lo + hi) / 2

    t0 = 2 / v
    t1 = 1 / v_at(w) + 1 / v_at(-w)
    return t1 / t0 - 1


def weather_site(weather, role="main_site"):
    """Summary of the weather site for a role (main, run, inland); the first site when the summary predates the role keys."""
    key = (
        weather.get(role)
        or weather.get("main_site")
        or next(k for k in weather if isinstance(weather[k], dict) and "temp_c_p10_p50_p90" in weather[k])
    )
    return weather[key]


def bike_km(r):
    return (r["by"].get("bike") or {}).get("km") or 0


def predict(name, A, g_all, prof, course, seg, weather, race=None):
    race = race or {"distance": "70.3", "name": "a prova"}
    DIST = DISTANCES.get(str(race.get("distance")), DISTANCES["70.3"])
    full = DIST is DISTANCES["140.6"]
    FROM_HALF = DIST.get("from_half")
    out = {"name": name, "notes": []}
    weight = A.get("weight_kg") or 75.0
    if not A.get("weight_kg"):
        out["notes"].append("Sem peso nos ficheiros: usados 75 kg.")
    mass = weight + BIKE_KG
    ftp = A.get("ftp")
    all_races = [r for r in A.get("races", []) if r.get("kind") == "race" and r["by"].get("bike")]
    run_lo, run_hi = DIST.get("run_km", (0, 99))
    same = [
        r
        for r in all_races
        if DIST["bike_km"][0] <= bike_km(r) <= DIST["bike_km"][1]
        and run_lo <= ((r["by"].get("run") or {}).get("km") or 0) <= run_hi
    ][-3:]
    half = [r for r in all_races if DISTANCES["70.3"]["bike_km"][0] <= bike_km(r) <= DISTANCES["70.3"]["bike_km"][1]][-3:]
    from_half = bool(FROM_HALF) and not same and bool(half)  # distance never raced: scale the half-distance races
    races = same if same else (half if from_half else [])
    out["reference_distance"] = "mesma distância" if same else ("meio-ironman (ajustado)" if from_half else None)
    # CdA calibration from the reference races' bike legs (position and equipment: the same at any distance)
    cda_races = races
    cdas, bad_power = [], set()
    for r in cda_races:
        row = g_all[
            (g_all.multisport.fillna(False).astype(bool)) & (g_all.sport_n == "bike") & (g_all.date == pd.Timestamp(r["date"]))
        ]
        if not len(row):
            continue
        try:
            c = cda_from_race_time(race_bike_records(prof, row.iloc[0]), mass, r["by"]["bike"]["h"], r["by"]["bike"].get("np"))
            if c and not (CDA_PLAUSIBLE[0] <= c <= CDA_PLAUSIBLE[1]):
                # the time could not be reproduced with a physical CdA: the race's power is incomplete (meter dropped out)
                out["notes"].append(f"CdA de {r['date']} descartado ({c}): a potência dessa prova parece incompleta.")
                bad_power.add(r["date"])
                c = None
            if c:
                cdas.append((r["date"], c, None))
        except Exception as e:
            out["notes"].append(f"CdA de {r['date']} não calculado ({type(e).__name__}).")
    vals = [c for _, c, _ in cdas]
    cda = float(np.median(vals)) if vals else 0.28
    cda_lo, cda_hi = (float(min(vals)), float(max(vals))) if len(vals) >= 2 else (cda * 0.9, cda * 1.1)
    out["cda"] = round(cda, 3)
    out["cda_range"] = [round(cda_lo, 3), round(cda_hi, 3)]
    out["cda_source"] = [
        {"date": d, "cda": c} for d, c, _ in cdas
    ] or "valor por defeito 0,28 (sem provas com potência e GPS para calibrar)"
    if not ftp:
        out["error"] = "Sem FTP: não é possível prever o bike."
        return out

    # IF of each past race of this distance with the FTP in use on that day; recommendation anchored on the most recent one
    def ftp_on(date):
        row = g_all[(g_all.sport_n == "bike") & (g_all.date == pd.Timestamp(date))]
        return float(row.ftp_used.dropna().iloc[0]) if len(row) and row.ftp_used.notna().any() else ftp

    past = [
        (r["date"], r["by"]["bike"]["np"], ftp_on(r["date"]))
        for r in races
        if r["by"]["bike"].get("np") and r["date"] not in bad_power
    ]
    out["if_reference"] = [round(n / f, 2) for _, n, f in past]
    out["if_reference_detail"] = [{"date": d, "np": n, "ftp": f, "if": round(n / f, 2)} for d, n, f in past]
    recent = [p for p in past if (TODAY - pd.Timestamp(p[0])).days <= 400]
    shift = FROM_HALF["if_minus"] if from_half else 0.0
    if recent:
        d, n, f = recent[-1]
        rec_if = float(np.clip(n / f - shift, *DIST["if_clip"]))
        out["if_anchor"] = {"date": d, "np": n, "ftp": f}
    elif past:
        rec_if = float(np.clip(np.median([n / f for _, n, f in past]) - shift, *DIST["if_clip"]))
        out["if_anchor"] = None
    else:
        rec_if = DIST["if_default"]
        out["if_anchor"] = None
    if from_half and past:
        out["notes"].append(
            f"Sem provas de {DIST['label']} no histórico: a previsão parte dos teus meio-ironman, com IF {-FROM_HALF['if_minus']:+.2f}, "
            f"ritmo de corrida {100 * (FROM_HALF['run_factor'] - 1):+.0f}% e transições {100 * (FROM_HALF['trans_factor'] - 1):+.0f}%."
        )
    # long-duration evidence: best 90 min / 2 h / 3 h power in the last 12 months (spike-filtered)
    lb = g_all[
        (g_all.sport_n == "bike")
        & (g_all.date >= TODAY - pd.Timedelta(days=365))
        & (~g_all.power_suspect.fillna(False).astype(bool))
    ]
    out["long_power"] = {
        lbl: (round(float(lb[c].max())) if c in lb and lb[c].notna().any() else None)
        for lbl, c in (("90min", "p_best_5400s"), ("2h", "p_best_7200s"), ("3h", "p_best_10800s"))
    }
    W = weather_site(weather)
    temp = W["temp_c_p10_p50_p90"][1]
    # air density at the race temperature and at the course's mean altitude (scale height 8.5 km): 6% thinner at 550 m
    rho = 1.225 * 288.15 / (273.15 + temp) * math.exp(-float(np.nanmean(seg["ele"])) / 8500)
    bike = {}
    for ifx in sorted(set(DIST["if_options"] + [round(rec_if, 2)])):
        # find constant target power whose simulated NP gives the chosen IF
        lo, hi = 0.4 * ftp, 1.1 * ftp
        for _ in range(25):
            tw = (lo + hi) / 2
            h, npw, ap = simulate_bike(seg, tw, mass, cda, rho)
            if npw / ftp > ifx:
                hi = tw
            else:
                lo = tw
        bike[f"{ifx:.2f}"] = {
            "target_w": round(tw),
            "np": round(npw),
            "avg_w": round(ap),
            "hours": round(h, 3),
            "time": fmt_h(h),
            "kmh": round(course["bike"]["distance_km"] / h, 1),
        }
    rb = bike[f"{rec_if:.2f}"]
    # uncertainty: same target power with the lowest and highest CdA seen across the athlete's races
    h_fast, _, _ = simulate_bike(seg, rb["target_w"], mass, cda_lo, rho)
    h_slow, _, _ = simulate_bike(seg, rb["target_w"], mass, cda_hi, rho)
    rb["range"] = [fmt_h(min(h_fast, h_slow)), fmt_h(max(h_fast, h_slow))]
    rb["range_h"] = [round(min(h_fast, h_slow), 3), round(max(h_fast, h_slow), 3)]
    out["bike"] = {
        "recommended_if": round(rec_if, 2),
        "options": bike,
        "climb_cap_w": round(rb["target_w"] * 1.10),
        "flat_w": rb["target_w"],
        "descent_w": round(rb["target_w"] * 0.6),
    }
    # wind scenarios at the recommended power
    ws = []
    sectors = weather.get("bike_wind_exposure", {})
    med, p90 = W["wind_kmh_p50_p90"]
    secdeg = {"N": 0, "NE": 45, "E": 90, "SE": 135, "S": 180, "SW": 225, "W": 270, "NW": 315}
    for s in sectors:
        for spd, lbl in ((med, "típico"), (p90, "forte")):
            h, _, _ = simulate_bike(seg, rb["target_w"], mass, cda, rho, spd, secdeg[s])
            ws.append({"from": s, "kmh": spd, "label": lbl, "delta_min": round((h - rb["hours"]) * 60, 1), "time": fmt_h(h)})
    out["wind"] = ws
    # swim pace from any race; transitions and run from races of this distance (or the half-distance ones, scaled)
    # race swim legs with a plausible pace only: open-water GPS often loses or adds distance (1.4 km for an Ironman swim)
    sw = [
        pace
        for r in cda_races
        if r["by"].get("swim") and (r["by"]["swim"]["km"] or 0) > 1.2
        for pace in [r["by"]["swim"]["h"] * 60 / r["by"]["swim"]["km"] / 10]
        if SWIM_PACE_RANGE[0] <= pace <= SWIM_PACE_RANGE[1]
    ]
    swim_pace = float(np.median(sw)) if sw else None
    if swim_pace is None:
        bs = [b for b in A.get("bests", {}).get("swim", []) if b.get("median_pace100")]
        swim_pace = bs[-1]["median_pace100"] * 1.08 if bs else 2.2
        out["notes"].append("Natação estimada da piscina (+8% para mar) por falta de provas.")
    tr = [sum(l["h"] or 0 for l in r["legs"] if l["sport"] == "transition") * 60 for r in races]
    trans = (
        float(np.median(tr)) * (FROM_HALF["trans_factor"] if from_half else 1.0)
        if tr and np.median(tr) > 0
        else DIST["trans_min"]
    )
    rp = [r["by"]["run"]["pace"] for r in races if r["by"].get("run") and r["by"]["run"].get("pace")]
    run_pace = float(np.median(rp)) * (FROM_HALF["run_factor"] if from_half else 1.0) if rp else None
    if run_pace is None:
        bests = [b for b in A.get("bests", {}).get("run", []) if b.get("pace3600")]
        k60 = DIST["run_from_60min"] + 0.02
        run_pace = bests[-1]["pace3600"] * k60 if bests else (6.5 if full else 6.0)
        out["notes"].append(
            f"Corrida estimada do melhor ritmo de 60 min (+{round(100 * (k60 - 1))}% para correr depois do bike) por falta de provas."
        )
    wind_cost = run_wind_factor(run_pace, weight, med)
    run_h = run_pace * course["run"]["distance_km"] * (1 + wind_cost) / 60
    swim_h = swim_pace * 10 * course["swim"]["distance_km"] / 60
    out["swim"] = {"pace100": round(swim_pace, 2), "hours": round(swim_h, 3), "time": fmt_h(swim_h)}
    out["transitions"] = {"minutes": round(trans, 1)}
    out["run"] = {
        "pace": round(run_pace * (1 + wind_cost), 2),
        "base_pace": round(run_pace, 2),
        "wind_cost_pct": round(100 * wind_cost, 1),
        "hours": round(run_h, 3),
        "time": fmt_h(run_h),
    }
    total = swim_h + trans / 60 + rb["hours"] + run_h
    rest = swim_h + trans / 60 + run_h
    out["total"] = {
        "hours": round(total, 3),
        "time": fmt_h(total),
        "range": [fmt_h(rest + rb["range_h"][0]), fmt_h(rest + rb["range_h"][1])],
    }
    # second reading from current training when there is no comparable race in the last 400 days
    no_recent_race = not recent
    if no_recent_race:
        tr_out = {
            "used": True,
            "why": "sem prova comparável nos últimos 400 dias" if past else f"sem provas de {DIST['label']} com potência",
        }
        lp = out["long_power"]
        if full:  # the best 3 h, or the best 2 h less 7%, as the power an Ironman bike can hold
            tgt_np = lp.get("3h") or (lp.get("2h") * DIST["long_power"][1] if lp.get("2h") else None) or ftp * DIST["if_default"]
        else:
            tgt_np = lp.get("2h") or (lp.get("90min") * 0.97 if lp.get("90min") else None) or ftp * 0.75
        # power only from the indoor trainer but outdoor rides exist without a power meter: use speed-derived outdoor power
        bike12 = g_all[(g_all.sport_n == "bike") & (g_all.date >= TODAY - pd.Timedelta(days=365))]
        pw = bike12[bike12["np"] > 0]
        indoor_only = len(pw) > 0 and (pw.sub_sport.fillna("") == "virtual_activity").mean() > 0.9
        outdoor_nopower = int(
            (
                (bike12.has_gps.fillna(False).astype(bool))
                & (bike12.sub_sport.fillna("") != "virtual_activity")
                & ~(bike12["np"] > 0)
            ).sum()
        )
        speed_mode = False
        if (indoor_only or not len(pw)) and outdoor_nopower >= 3 and not cdas:
            sp, n_sp = speed_derived_power(prof, g_all, mass)
            if sp.get("2h") or sp.get("90min"):
                tgt_np = (sp.get("2h") or sp["90min"] * 0.97) * (DIST["long_power"][1] if full else 1.0)
                speed_mode = True
                out["speed_derived"] = {
                    "90min": round(sp["90min"]) if sp.get("90min") else None,
                    "2h": round(sp["2h"]) if sp.get("2h") else None,
                    "rides": n_sp,
                    "cda": 0.30,
                }
        if (
            full and not speed_mode
        ):  # an Ironman bike is not ridden at the best 2-3 h of a training ride above the distance's ceiling
            tgt_np = min(tgt_np, ftp * DIST["if_clip"][1])
        if cdas:  # race-day position/equipment known: keep it, only the power comes from training
            c_tr, n_tr, cda_tr = None, 0, cda
        elif speed_mode:
            c_tr, n_tr, cda_tr = None, 0, 0.30
        else:
            c_tr, n_tr = training_cda(prof, g_all, mass)
            cda_tr = c_tr if c_tr else cda
        lo, hi = 0.4 * ftp, 1.1 * ftp
        for _ in range(25):
            tw = (lo + hi) / 2
            h_tr, np_tr, _ = simulate_bike(seg, tw, mass, cda_tr, rho)
            lo, hi = (lo, tw) if np_tr > tgt_np else (tw, hi)
        run_b = [b for b in A.get("bests", {}).get("run", []) if b.get("year", 0) >= TODAY.year - 1]
        p90 = min([b["pace5400"] for b in run_b if b.get("pace5400")], default=None)
        p60 = min([b["pace3600"] for b in run_b if b.get("pace3600")], default=None)
        run_tr = p90 * DIST["run_from_90min"] if p90 else (p60 * DIST["run_from_60min"] if p60 else run_pace)
        sw_b = [b for b in A.get("bests", {}).get("swim", []) if b.get("year", 0) >= TODAY.year - 1 and b.get("median_pace100")]
        swim_tr = (
            swim_pace if sw else (min(b["median_pace100"] for b in sw_b) * 1.10 if sw_b else swim_pace)
        )  # pool pace excludes wall rests: +10% for open water
        run_tr_h = run_tr * course["run"]["distance_km"] * (1 + run_wind_factor(run_tr, weight, med)) / 60
        swim_tr_h = swim_tr * 10 * course["swim"]["distance_km"] / 60
        tot_tr = swim_tr_h + trans / 60 + h_tr + run_tr_h
        tr_out.update(
            {
                "bike": {
                    "np_target": round(tgt_np),
                    "flat_w": round(tw),
                    "time": fmt_h(h_tr),
                    "hours": round(h_tr, 3),
                    "if": round(tgt_np / ftp, 2),
                    "cda": round(cda_tr, 3),
                    "cda_source": (
                        "das tuas provas (posição de prova)"
                        if cdas
                        else (
                            "mesmo valor usado para estimar a potência pela velocidade"
                            if speed_mode
                            else (f"{n_tr} pedais na estrada" if c_tr else "valor por defeito")
                        )
                    ),
                    "power_source": (
                        f"estimada pela velocidade e declive em {out['speed_derived']['rides']} pedais na estrada sem medidor (a potência só existe no rolo)"
                        if speed_mode
                        else (
                            "melhor potência de 3 h dos últimos 12 meses (ou de 2 h −7%)"
                            if full
                            else "melhor potência de 2 h dos últimos 12 meses"
                        )
                    ),
                },
                "run": {
                    "pace": round(run_tr, 2),
                    "time": fmt_h(run_tr_h),
                    "source": (
                        f"melhor 90 min +{round(100 * (DIST['run_from_90min'] - 1))}%"
                        if p90
                        else (f"melhor 60 min +{round(100 * (DIST['run_from_60min'] - 1))}%" if p60 else "provas")
                    ),
                },
                "swim": {
                    "pace100": round(swim_tr, 2),
                    "time": fmt_h(swim_tr_h),
                    "source": "das tuas provas" if sw else ("ritmo típico de piscina +10%" if sw_b else "provas"),
                },
                "total": {"time": fmt_h(tot_tr), "hours": round(tot_tr, 3)},
            }
        )
        out["training_reading"] = tr_out
        if not past:  # no races at all: the training reading becomes the main prediction
            out["bike"]["options"].setdefault(
                f"{tgt_np/ftp:.2f}",
                {
                    "target_w": round(tw),
                    "np": round(np_tr),
                    "avg_w": round(tw),
                    "hours": round(h_tr, 3),
                    "time": fmt_h(h_tr),
                    "kmh": round(course["bike"]["distance_km"] / h_tr, 1),
                    "range": [fmt_h(h_tr * 0.95), fmt_h(h_tr * 1.05)],
                    "range_h": [h_tr * 0.95, h_tr * 1.05],
                },
            )
            out["bike"]["recommended_if"] = round(tgt_np / ftp, 2)
            out["bike"]["flat_w"] = round(tw)
            out["bike"]["climb_cap_w"] = round(tw * 1.10)
            out["bike"]["descent_w"] = round(tw * 0.6)
            out["swim"] = {"pace100": round(swim_tr, 2), "hours": round(swim_tr_h, 3), "time": fmt_h(swim_tr_h)}
            out["run"] = {
                **out["run"],
                "pace": round(run_tr * (1 + run_wind_factor(run_tr, weight, med)), 2),
                "base_pace": round(run_tr, 2),
                "hours": round(run_tr_h, 3),
                "time": fmt_h(run_tr_h),
            }
            out["total"] = {
                "hours": round(tot_tr, 3),
                "time": fmt_h(tot_tr),
                "range": [fmt_h(tot_tr - 0.05 * h_tr), fmt_h(tot_tr + 0.05 * h_tr)],
            }
            out["notes"] = [n for n in out["notes"] if "por falta de provas" not in n]
            out["notes"].append(
                f"Sem provas de {DIST['label']}: previsão feita a partir dos treinos do último ano. Em prova os atletas costumam render mais do que em treino, por isso trata-a como limite inferior."
            )
            if speed_mode:
                out["notes"].append(
                    f"A potência registada é só do rolo; no bike usou-se a potência estimada pela velocidade nos pedais de estrada (melhor 2 h ≈ {out['speed_derived']['2h']} W)."
                )
                if tgt_np > ftp * 0.95:
                    out["ftp_warning"] = (
                        f"O FTP em uso no TrainingPeaks ({ftp:.0f} W) vem do rolo e está abaixo do que sustentas 2 h na estrada (≈ {tgt_np:.0f} W). Os IF desta página ficam acima de 1 por isso; convém refazer o teste de FTP na estrada."
                    )
                    out["notes"].insert(0, out["ftp_warning"])
    # reference: past race times of this distance (or the half-distance ones) and course difficulty
    ref = []
    for r in races:
        rt = sum(v["h"] for v in r["by"].values()) + sum(l["h"] or 0 for l in r["legs"] if l["sport"] == "transition")
        ref.append(
            {
                "date": r["date"],
                "title": r["title"],
                "total": fmt_h(rt),
                "bike_time": fmt_h(r["by"]["bike"]["h"]),
                "bike_kmh": r["by"]["bike"].get("kmh"),
                "bike_if": round(r["by"]["bike"]["np"] / ftp, 2) if r["by"]["bike"].get("np") else None,
            }
        )
    out["reference_races"] = ref
    # course demands vs recent training
    g = g_all[g_all.date >= TODAY - pd.Timedelta(weeks=8)]
    b = g[g.sport_n == "bike"]
    s = g[g.sport_n == "swim"]
    r_ = g[g.sport_n == "run"]
    climb_w = b.ascent_m.sum() / 8
    long_rides = int((b.duration_h >= DIST["long_ride_h"]).sum())
    ow = int((s.sub_sport == "open_water").sum())
    long_runs = int((r_.distance_km >= DIST["long_run_km"]).sum())
    need_h = rb["hours"]
    bk = course["bike"]
    climb_need = bk["ascent_m"] / max(bk["distance_km"], 1) * 90  # metres per 90 km of bike: compares courses of any length
    lr_h = DIST["long_ride_h"]
    water = race.get("water_temp_c")
    month = pd.Timestamp(race["date"]).month
    gaps = [
        {
            "topic": "Subida",
            "severity": "ok" if climb_w >= climb_need else ("watch" if climb_w >= climb_need / 2 else "alert"),
            "text": f"{climb_w:.0f} m de subida por semana nas últimas 8 semanas; o bike de {race['name']} tem {bk['ascent_m']} m em {bk['distance_km']:.0f} km"
            + (
                f", com rampas até {max(c.get('max_500m_grade_pct') or 0 for c in bk['climbs']):.0f}%."
                if bk.get("climbs")
                else "."
            ),
        },
        {
            "topic": "Pedais longos",
            "severity": "ok" if long_rides >= 4 else ("watch" if long_rides >= 2 else "alert"),
            "text": f"{long_rides} pedais de {int(lr_h)}h{int(round((lr_h % 1) * 60)):02d} ou mais nas últimas 8 semanas; a previsão de bike é {fmt_h(need_h)}.",
        },
        {
            "topic": "Corridas longas",
            "severity": "ok" if long_runs >= 3 else ("watch" if long_runs >= 1 else "alert"),
            "text": f"{long_runs} corridas de {DIST['long_run_km']} km ou mais nas últimas 8 semanas para {DIST['run_label']}"
            + (f" de {course['run']['distance_km']:.0f} km" if course.get("run") else "")
            + ".",
        },
        {
            "topic": "Águas abertas",
            "severity": "ok" if ow >= 2 else "watch",
            "text": f"{ow} natações em águas abertas nas últimas 8 semanas"
            + (f"; a água na época da prova anda pelos {water[0]} a {water[1]} °C" if water else "")
            + ("." if not water or water[0] >= 20 else " (fato obrigatório, frio na T1)."),
        },
    ]
    run_temp = weather_site(weather, "run_site")
    if run_temp.get("day_tmax_p50", 0) >= 24 or run_temp["temp_c_p10_p50_p90"][2] >= 26:
        gaps.append(
            {
                "topic": "Calor",
                "severity": "watch",
                "text": f"Máxima típica do dia {run_temp['day_tmax_p50']} °C (p90 da hora de prova {run_temp['temp_c_p10_p50_p90'][2]} °C): treina a hidratação e alguma corrida ao calor.",
            }
        )
    key, factor = DIST["long_power"]
    lp_best = out["long_power"].get(key) or (
        out["long_power"].get("2h") * factor if full and out["long_power"].get("2h") else None
    )
    if lp_best:
        gaps.insert(
            0,
            {
                "topic": "Potência longa",
                "severity": "ok" if rb["np"] <= lp_best * 1.03 else ("watch" if rb["np"] <= lp_best * 1.10 else "alert"),
                "text": f"O plano pede {rb['np']} W de NP durante {fmt_h(rb['hours'])}; o teu melhor de 2 h nos últimos 12 meses é {out['long_power'].get('2h')} W"
                + (f" e o de 3 h {out['long_power']['3h']} W." if out["long_power"].get("3h") else "."),
            },
        )
    out["gaps"] = gaps
    ctl_now = A["pmc"]["now"]["ctl"]
    if races:
        hist = {h["d"]: h["ctl"] for h in A["pmc"]["history"]}
        last = races[-1]["date"]
        near = min(hist, key=lambda d: abs(pd.Timestamp(d) - pd.Timestamp(last))) if hist else None
        out["fitness"] = {"ctl_now": ctl_now, "ctl_reference": hist.get(near), "reference_date": last}
        out["notes"].append(
            f"Previsão assume a forma da última prova de referência ({last}, CTL {hist.get(near)}); CTL hoje {ctl_now}."
        )
    if out.get("bike", {}).get("flat_w"):  # where the fuelling goes on the elevation profile (nutrition.py)
        out["bike"]["track"] = simulate_track(seg, out["bike"]["flat_w"], mass, out.get("cda") or 0.28, rho)
    return out


# ------------------------------------------------------------------ running races (10 km, half, marathon, any distance)
RUN_RIEGEL = (1.04, 1.12)  # Riegel exponent between the athlete's 20 and 60 min bests, kept in a sane range (1.06 = classic)


def grade_cost(g):
    """Time factor of running a stretch at grade g (fraction) at the same effort: about +3.3% per 1% up, a little more than
    half of that back per 1% down, no gain past -10% (rule of thumb behind grade-adjusted pace)."""
    pct = g * 100
    return 1 + 0.033 * pct if pct >= 0 else 1 - 0.018 * min(-pct, 10)


def run_anchor(A, g_all):
    """(time h, distance km, label, exponent) to extrapolate from: a run race of the last 2 years, else the 60 min (or 20 min)
    best of the last 2 calendar years, with the exponent fitted between the 20 and 60 min bests."""
    bests = [b for b in A.get("bests", {}).get("run", []) if b.get("year", 0) >= TODAY.year - 1]
    best = lambda key: min((b[key] for b in bests if b.get(key)), default=None)
    p20, p60 = best("pace1200"), best("pace3600")
    k = 1.06
    if p20 and p60:
        d20, d60 = 20 / p20, 60 / p60
        k = float(np.clip(math.log(60 / 20) / math.log(d60 / d20), *RUN_RIEGEL)) if d60 > d20 else RUN_RIEGEL[1]
    since = (TODAY - pd.Timedelta(days=730)).strftime("%Y-%m-%d")
    runs = [
        r
        for r in A.get("races", [])
        if r.get("kind") == "race" and set(r["by"]) == {"run"} and (r["by"]["run"].get("km") or 0) >= 5 and r["date"] >= since
    ]
    if runs:
        r = runs[-1]
        return r["by"]["run"]["h"], r["by"]["run"]["km"], f"prova de {r['date']} ({r['by']['run']['km']:.1f} km)", k, True
    if p60:
        return 1.0, 60 / p60, "melhor hora de corrida dos treinos (último ano)", k, False
    if p20:
        return 1 / 3, 20 / p20, "melhores 20 min de corrida dos treinos (último ano)", k, False
    return None


def predict_run(name, A, g_all, seg, weather, race):
    """A running race: Riegel from the anchor, then the course's climbs (grade cost km by km), the wind and, from the half
    marathon up, the heat. A pacing plan km by km at the same effort."""
    out = {"name": name, "sport": "run", "notes": []}
    D = float(race["run_distance_km"])
    a = run_anchor(A, g_all)
    if not a:
        out["error"] = "Sem corridas suficientes para prever (precisa de pelo menos 20 min seguidos a correr no último ano)."
        return out
    t_a, d_a, label, k, from_race = a
    t_flat = t_a * (D / d_a) ** k
    base_pace = t_flat * 60 / D
    factors = np.array([grade_cost(g) for g in seg["grade"]]) if len(seg["grade"]) else np.array([1.0])
    dist = seg["dist"] if len(seg["grade"]) else np.array([D * 1000])
    grade_f = float((factors * dist).sum() / dist.sum())
    W = weather_site(weather)
    temp = W["temp_c_p10_p50_p90"][1]
    weight = A.get("weight_kg") or 70.0
    wind_f = 1 + run_wind_factor(base_pace, weight, W["wind_kmh_p50_p90"][0])
    heat_f = 1 + 0.004 * max(0.0, temp - 15) if D >= 20 else 1.0  # about 2% per 5 °C above 15 °C from the half up
    h = t_flat * grade_f * wind_f * heat_f
    spread = 0.03 if from_race else 0.05
    out["run"] = {
        "distance_km": D,
        "hours": round(h, 3),
        "time": fmt_h(h),
        "pace": round(h * 60 / D, 2),
        "flat_pace": round(base_pace, 2),
        "grade_pct": round(100 * (grade_f - 1), 1),
        "wind_pct": round(100 * (wind_f - 1), 1),
        "heat_pct": round(100 * (heat_f - 1), 1),
        "anchor": label,
        "riegel": round(k, 3),
    }
    out["total"] = {"hours": round(h, 3), "time": fmt_h(h), "range": [fmt_h(h * (1 - spread)), fmt_h(h * (1 + spread))]}
    # pacing km by km: the same effort, so the pace follows the grade of each km
    km = seg["km"] if len(seg["grade"]) else np.array([0.0])
    flat = h * 60 / D / grade_f  # min/km on the flat at race effort
    splits, cum = [], 0.0
    for i in range(int(math.ceil(D))):
        sel = (km >= i) & (km < i + 1)
        f = float((factors[sel] * dist[sel]).sum() / dist[sel].sum()) if sel.any() else 1.0
        g = float((seg["grade"][sel] * dist[sel]).sum() / dist[sel].sum() * 100) if sel.any() else 0.0
        length = min(1.0, D - i)
        cum += flat * f * length
        splits.append({"km": round(i + length, 2), "pace": round(flat * f, 2), "grade": round(g, 1), "cum": round(cum, 1)})
    out["splits"] = splits
    out["track"] = [{"km": s["km"], "min": s["cum"]} for s in splits]
    if not from_race:
        out["notes"].append(
            "Sem provas de corrida recentes: a previsão parte dos teus melhores esforços de treino, que raramente são a fundo. "
            "Trata o tempo como prudente: em prova costuma sair mais rápido."
        )
    if heat_f > 1.005:
        out["notes"].append(f"Calor na janela da prova (~{temp:.0f} °C): +{100 * (heat_f - 1):.1f}% no tempo.")
    return out


def run_race_entry(race, base, course, weather, dash, done):
    """The race page entry of a running race: the run course stretched to the official distance and every runner's prediction."""
    if (base / "run.gpx").exists():
        lat, lon, ele = read_gpx(base / "run.gpx")
        seg = segments(lat, lon, ele)
        k = race["run_distance_km"] / max(seg["dist"].sum() / 1000, 0.1)
        seg["dist"], seg["km"], seg["grade"] = seg["dist"] * k, seg["km"] * k, seg["grade"] / k
    else:
        seg = {"dist": np.array([]), "km": np.array([]), "grade": np.array([]), "ele": np.array([]), "bearing": np.array([])}
    course["run"]["distance_km"] = race["run_distance_km"]
    entry = {
        "race": race,
        "days_to_race": int((pd.Timestamp(race["date"]) - TODAY).days),
        "course": course,
        "weather": weather,
        "profile": [{"km": round(float(k), 2), "ele": round(float(e))} for k, e in zip(seg["km"][::4], seg["ele"][::4])],
        "athletes": {},
    }
    for name, A in dash["athletes"].items():
        if not any(b.get("pace1200") for b in A.get("bests", {}).get("run", [])):
            continue
        try:
            entry["athletes"][name] = predict_run(name, A, done[done.athlete == name], seg, weather, race)
            p = entry["athletes"][name]
            print(f"{race['slug']} {name}: {p.get('total', {}).get('time')} ({p.get('run', {}).get('anchor')})")
        except Exception as e:
            entry["athletes"][name] = {"name": name, "sport": "run", "error": f"{type(e).__name__}: {e}"}
            print(race["slug"], name, "ERROR", e)
    return entry


def main():
    dash = json.loads((OUT / "data" / "dashboard_data.json").read_text(encoding="utf-8"))
    df = pd.read_csv(OUT / "data" / "activities_all.csv", parse_dates=["t_local", "date"], low_memory=False)
    done = df[df.completed & (df.sport_n != "dayoff") & (df.duration_h > 0)].copy()
    profiles = discover()
    result = {}
    # the courses in the repo and the ones the coach created on the admin page (course_builder.py, kept with the data)
    for cdir in sorted(list((HERE / "courses").glob("*/race.json")) + list((OUT / "courses").glob("*/race.json"))):
        race = json.loads(cdir.read_text(encoding="utf-8"))
        base = cdir.parent
        course = json.loads((base / "course_summary.json").read_text())
        weather = json.loads((base / "weather_summary.json").read_text())
        if race.get("sport") == "run":
            result[race["slug"]] = run_race_entry(race, base, course, weather, dash, done)
            continue
        lat, lon, ele = read_gpx(base / "bike.gpx")
        seg = segments(lat, lon, ele)
        for leg in ("swim", "run"):  # official distances when the published line is shorter (sparse or resampled)
            if race.get(f"{leg}_distance_km"):
                course[leg]["distance_km"] = race[f"{leg}_distance_km"]
        official = race.get("bike_distance_km")
        if official:  # a sparse official track is shorter than the race: stretch every segment to the official distance
            k = official / (seg["dist"].sum() / 1000)
            seg["dist"], seg["km"], seg["grade"] = seg["dist"] * k, seg["km"] * k, seg["grade"] / k
            course["bike"]["distance_km"] = official
        prof_500 = [{"km": round(float(k), 1), "ele": round(float(e))} for k, e in zip(seg["km"][::10], seg["ele"][::10])]
        entry = {
            "race": race,
            "days_to_race": int((pd.Timestamp(race["date"]) - TODAY).days),
            "course": course,
            "weather": weather,
            "profile": prof_500,
            "athletes": {},
        }
        for name, A in dash["athletes"].items():
            if (A["kpi52"]["hours"] or 0) < 20:
                continue
            try:
                entry["athletes"][name] = predict(
                    name, A, done[done.athlete == name], profiles.get(name, {}), course, seg, weather, race
                )
                p = entry["athletes"][name]
                print(
                    f"{race['slug']} {name}: CdA {p['cda']} ({len(p['cda_source']) if isinstance(p['cda_source'], list) else 'default'}) total {p.get('total', {}).get('time')} "
                    f"swim {p.get('swim', {}).get('time')} bike {p.get('bike', {}).get('options', {}).get(f'{p.get('bike', {}).get('recommended_if', 0):.2f}', {}).get('time')} "
                    f"@IF {p.get('bike', {}).get('recommended_if')} run {p.get('run', {}).get('time')}"
                )
            except Exception as e:
                entry["athletes"][name] = {"name": name, "error": f"{type(e).__name__}: {e}"}
                print(race["slug"], name, "ERROR", e)
        result[race["slug"]] = entry
    (OUT / "data" / "race_impact.json").write_text(json.dumps(result, ensure_ascii=False, default=float), encoding="utf-8")
    print("written race_impact.json")


if __name__ == "__main__":
    main()

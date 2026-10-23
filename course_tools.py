"""Tools to build a race course folder (courses/<slug>/) when the organiser publishes no GPX, and its summaries.

  route(points, profile)        road geometry through waypoints (OSRM public servers)
  elevation(lat, lon)           ground elevation (Open-Meteo, Copernicus 90 m DEM)
  road_elevation(d, ele)        DEM elevation along a road: tunnels cut through hills and bridges span valleys, so the
                                DEM overstates the climbing; peaks and dips steeper than the road's maximum grade are removed
  write_gpx(path, lat, lon, ele)
  summarize(path)               distance, ascent, climbs, grade shares, 10 km profile (course_summary.json format)
  weather(sites, ...)           hourly ERA5 reanalysis for the race window over past years, and its summary
  wind_exposure(...)            km of head/tail/cross wind on the bike for the most frequent wind sectors

Network services are free and keyless; results are written to files and the pipeline only reads those files.
"""

import json, math, time, urllib.error, urllib.parse, urllib.request
import numpy as np, pandas as pd

OSRM = {
    "driving": "https://router.project-osrm.org/route/v1/driving/",
    "foot": "https://routing.openstreetmap.de/routed-foot/route/v1/foot/",
}
UA = {"User-Agent": "triapp/1.0 (personal training app)"}
SECTORS = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]


def _get(url, timeout=120, retries=4):
    """GET JSON; temporary server errors (429, 502, 503, 504) and dropped or timed-out connections are retried with a growing
    pause."""
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            if e.code not in (429, 502, 503, 504) or attempt == retries:
                raise
            time.sleep(5 * (attempt + 1))
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            if attempt == retries:
                raise
            time.sleep(10 * (attempt + 1))


# ---------------------------------------------------------------- geometry
def route(points, profile="driving"):
    """(lat, lon) arrays of the road route through [(lat, lon), ...]."""
    coords = ";".join(f"{lon:.6f},{lat:.6f}" for lat, lon in points)
    d = _get(OSRM[profile] + coords + "?overview=full&geometries=geojson&continue_straight=false")
    xy = np.array(d["routes"][0]["geometry"]["coordinates"])
    return xy[:, 1], xy[:, 0]


def cumdist(lat, lon):
    """Cumulative distance in metres along a polyline."""
    R = 6371000
    la, lo = np.radians(lat), np.radians(lon)
    a = np.sin(np.diff(la) / 2) ** 2 + np.cos(la[:-1]) * np.cos(la[1:]) * np.sin(np.diff(lo) / 2) ** 2
    return np.concatenate([[0], np.cumsum(2 * R * np.arcsin(np.sqrt(a)))])


def resample(lat, lon, step=50.0):
    """Points every `step` metres along the polyline."""
    d = cumdist(lat, lon)
    grid = np.arange(0, d[-1], step)
    return np.interp(grid, d, lat), np.interp(grid, d, lon)


def nearest(lat, lon, point):
    """Index of the polyline vertex closest to (lat, lon)."""
    return int(np.argmin((lat - point[0]) ** 2 + ((lon - point[1]) * math.cos(math.radians(point[0]))) ** 2))


CACHE = {}  # (lat, lon) rounded to 5 decimals -> metres; build scripts load and save it next to the course files


def load_cache(path):
    try:
        CACHE.update({tuple(map(float, k.split(","))): v for k, v in json.load(open(path)).items()})
    except Exception:
        pass


def save_cache(path):
    json.dump({f"{a},{b}": v for (a, b), v in CACHE.items()}, open(path, "w"))


def elevation(lat, lon, batch=100):
    """Ground elevation in metres: Open-Meteo (Copernicus 90 m DEM); OpenTopoData SRTM 30 m when Open-Meteo refuses (rate limit).
    Points already in CACHE are not requested again."""
    keys = [(round(float(a), 5), round(float(b), 5)) for a, b in zip(lat, lon)]
    todo = [k for k in dict.fromkeys(keys) if k not in CACHE]
    fetched = _fetch_elevation(np.array([k[0] for k in todo]), np.array([k[1] for k in todo]), batch) if todo else []
    CACHE.update(zip(todo, fetched))
    return np.array([CACHE[k] for k in keys], float)


def _fetch_elevation(lat, lon, batch):
    out = []
    for i in range(0, len(lat), batch):
        la, lo = lat[i : i + batch], lon[i : i + batch]
        try:
            q = f"latitude={','.join(f'{x:.5f}' for x in la)}&longitude={','.join(f'{x:.5f}' for x in lo)}"
            out += _get(f"https://api.open-meteo.com/v1/elevation?{q}")["elevation"]
            time.sleep(1.5)
        except urllib.error.HTTPError as e:
            if e.code != 429:
                raise
            locs = "|".join(f"{a:.5f},{b:.5f}" for a, b in zip(la, lo))
            out += [r["elevation"] for r in _get(f"https://api.opentopodata.org/v1/srtm30m?locations={locs}")["results"]]
            time.sleep(1.2)
    return np.array(out, float)


def road_elevation(d, ele, window=1000.0, smooth_m=200.0):
    """DEM elevation turned into a road profile. The DEM follows the ground, so it climbs over the hills a road tunnels through
    and dives into the valleys it bridges: those stretches are interpolated first (tunnels_and_bridges); then a rolling median
    over `window` metres removes the remaining noise (trees, cuttings) and a short moving average the steps. Pick the window
    with fit_window when the official ascent is known."""
    d = np.asarray(d, float)
    step = max(np.median(np.diff(d)), 1.0)
    k = max(1, int(window / step / 2))
    e = pd.Series(tunnels_and_bridges(d, ele)).rolling(2 * k + 1, center=True, min_periods=1).median().to_numpy()
    m = max(1, int(smooth_m / step / 2))
    return pd.Series(e).rolling(2 * m + 1, center=True, min_periods=1).mean().to_numpy()


def tunnels_and_bridges(d, ele, rise_m=40.0, dip_m=30.0, margin_m=150.0):
    """Interpolate straight across tunnels and bridges. A tunnel shows in the DEM as a hump far above the ground the road runs
    on (here: more than rise_m above the 20th percentile of the surrounding 5 km); a bridge as a dip below the 80th percentile
    of the surrounding 2 km. Each such stretch, widened by margin_m, is replaced by a line between its two ends."""
    d = np.asarray(d, float)
    e = np.asarray(ele, float).copy()
    step = max(np.median(np.diff(d)), 1.0)
    roll = lambda w, q: pd.Series(e).rolling(int(w / step) | 1, center=True, min_periods=1).quantile(q).to_numpy()
    mask = (e > roll(5000, 0.2) + rise_m) | (e < roll(2000, 0.8) - dip_m)
    grow = int(margin_m / step)
    mask = pd.Series(mask).rolling(2 * grow + 1, center=True, min_periods=1).max().astype(bool).to_numpy()
    if mask.all() or not mask.any():
        return e
    good = ~mask
    e[mask] = np.interp(d[mask], d[good], e[good])
    return e


def ascent(e):
    de = np.diff(e)
    return float(de[de > 0].sum())


def fit_window(d, ele, official_ascent, windows=(250, 500, 750, 1000, 1500, 2000, 3000)):
    """Median window whose road profile gives the ascent closest to the organiser's figure."""
    return min(windows, key=lambda w: abs(ascent(road_elevation(d, ele, w)) - official_ascent))


def write_gpx(path, lat, lon, ele=None, name="course"):
    pts = "\n".join(
        f'<trkpt lat="{a:.6f}" lon="{b:.6f}">' + (f"<ele>{z:.1f}</ele>" if ele is not None else "") + "</trkpt>"
        for a, b, z in zip(lat, lon, ele if ele is not None else [None] * len(lat))
    )
    with open(path, "w") as f:
        f.write(
            f'<?xml version="1.0" encoding="UTF-8"?>\n<gpx version="1.1" creator="triapp course_tools">\n<trk><name>{name}</name><trkseg>\n{pts}\n</trkseg></trk>\n</gpx>\n'
        )


# ---------------------------------------------------------------- summary
def _read_gpx(path):
    import re

    t = open(path).read()
    # track and route points only: waypoints (<wpt>: aid stations, mechanics) carry coordinates too but are not the course
    pts = re.findall(r'<(?:trkpt|rtept)\s+lat="([-\d.]+)"[^>]*lon="([-\d.]+)"[^>]*>\s*(?:<ele>([-\d.]+)</ele>)?', t)
    lat = np.array([float(p[0]) for p in pts])
    lon = np.array([float(p[1]) for p in pts])
    ele = np.array([float(p[2]) if p[2] else np.nan for p in pts])
    return lat, lon, ele


def climbs(km, ele, min_gain=50, max_dip=25):
    """Climbs of at least `min_gain` m, tolerating dips of up to `max_dip` m inside a climb."""
    out, i, n = [], 0, len(ele)
    while i < n - 1:
        lo_i = i
        hi_i = i
        j = i
        while j < n - 1:
            j += 1
            if ele[j] > ele[hi_i]:
                hi_i = j
            if ele[hi_i] - ele[j] > max_dip:
                break
        gain = ele[hi_i] - ele[lo_i]
        if gain >= min_gain:
            length = km[hi_i] - km[lo_i]
            sub = slice(lo_i, hi_i + 1)
            k500 = max(1, int(round(0.5 / max(np.median(np.diff(km)), 1e-3))))
            g500 = [
                (ele[t + k500] - ele[t]) / ((km[t + k500] - km[t]) * 1000) * 100
                for t in range(lo_i, max(lo_i + 1, hi_i - k500 + 1))
                if t + k500 < n
            ]
            out.append(
                {
                    "from_km": round(km[lo_i], 1),
                    "to_km": round(km[hi_i], 1),
                    "gain_m": round(gain),
                    "len_km": round(length, 1),
                    "avg_grade_pct": round(gain / (length * 1000) * 100, 1) if length > 0 else None,
                    "max_500m_grade_pct": round(max(g500), 1) if g500 else None,
                }
            )
            i = hi_i + 1
        else:
            i = max(i + 1, j if ele[j] < ele[lo_i] else i + 1)
    return out


def summarize(path, step=50.0, with_climbs=True):
    lat, lon, ele = _read_gpx(path)
    d = cumdist(lat, lon)
    out = {
        "points": int(len(lat)),
        "distance_km": round(d[-1] / 1000, 2),
        "start": [round(lat[0], 4), round(lon[0], 4)],
        "loop_closed_m": round(float(cumdist(np.array([lat[0], lat[-1]]), np.array([lon[0], lon[-1]]))[-1])),
    }
    if np.isnan(ele).all():
        return out
    grid = np.arange(0, d[-1], step)
    e = np.interp(grid, d, np.nan_to_num(ele, nan=np.nanmean(ele)))
    de = np.diff(e)
    g = de / step
    out.update(
        {
            "ascent_m": round(float(de[de > 0].sum())),
            "descent_m": round(float(-de[de < 0].sum())),
            "ele_min": round(float(e.min())),
            "ele_max": round(float(e.max())),
        }
    )
    if with_climbs:
        out["climbs"] = climbs(grid / 1000, e)
    out["time_share_by_grade_pct"] = {
        "descent<-3%": round(100 * (g < -0.03).mean()),
        "flat -3..3%": round(100 * (abs(g) <= 0.03).mean()),
        "3-6%": round(100 * ((g > 0.03) & (g <= 0.06)).mean()),
        ">6%": round(100 * (g > 0.06).mean()),
    }
    km = grid / 1000
    stride = 10 if d[-1] > 30000 else 2
    out["profile_10km"] = [
        {"km": int(k), "ele": round(float(np.interp(k * 1000, grid, e)))} for k in range(0, int(km[-1]) + 1, stride)
    ]
    return out


# ---------------------------------------------------------------- weather
def era5(lat, lon, start, end, tz):
    v = "temperature_2m,relative_humidity_2m,apparent_temperature,precipitation,wind_speed_10m,wind_direction_10m,wind_gusts_10m,cloud_cover"
    q = urllib.parse.urlencode(
        {"latitude": lat, "longitude": lon, "start_date": start, "end_date": end, "hourly": v, "timezone": tz}
    )
    h = _get("https://archive-api.open-meteo.com/v1/archive?" + q, timeout=180)["hourly"]
    return pd.DataFrame(h).rename(columns={"time": "time"})


def weather(sites, md_from, md_to, years, hours, tz):
    """Hourly ERA5 for each site ({label: (lat, lon)}) between month-day md_from and md_to of each year, race hours only."""
    frames = []
    for label, (la, lo) in sites.items():
        for y in years:
            df = era5(la, lo, f"{y}-{md_from}", f"{y}-{md_to}", tz)
            df["time"] = pd.to_datetime(df.time)
            df = df[df.time.dt.hour.isin(hours)]
            df["site"], df["md"], df["hour"], df["year"] = label, df.time.dt.strftime("%m-%d"), df.time.dt.hour, y
            frames.append(df)
            time.sleep(0.4)
    return pd.concat(frames, ignore_index=True)


def sector(deg):
    return SECTORS[int(((deg % 360) + 22.5) // 45) % 8]


def site_summary(df):
    s = df.wind_direction_10m.map(sector).value_counts(normalize=True).round(2)
    rad = np.radians(df.wind_direction_10m)
    u, v = (df.wind_speed_10m * np.sin(rad)).mean(), (df.wind_speed_10m * np.cos(rad)).mean()
    days = df.groupby(df.time.dt.date)
    return {
        "temp_c_p10_p50_p90": [round(float(df.temperature_2m.quantile(q)), 1) for q in (0.1, 0.5, 0.9)],
        "humidity_pct_median": round(float(df.relative_humidity_2m.median())),
        "wind_kmh_p50_p90": [round(float(df.wind_speed_10m.quantile(q)), 1) for q in (0.5, 0.9)],
        "gust_kmh_p90": round(float(df.wind_gusts_10m.quantile(0.9)), 1),
        "wind_from_sector_share": s.to_dict(),
        "wind_resultant_from_deg": round(float(np.degrees(np.arctan2(u, v)) % 360)),
        "wind_resultant_kmh": round(float(math.hypot(u, v)), 1),
        "wind_speed_by_hour_kmh": {str(h): round(float(x), 1) for h, x in df.groupby("hour").wind_speed_10m.median().items()},
        "days_with_rain_pct": round(100 * float((days.precipitation.sum() >= 1).mean())),
        "days_wind_over_25kmh_pct": round(100 * float((days.wind_speed_10m.max() > 25).mean())),
        "day_tmax_p50": round(float(days.temperature_2m.max().median()), 1),
    }


def wind_exposure(bike_gpx, sectors_share, top=3, stretch_km=5):
    """For the most frequent wind sectors: km of headwind / tailwind / crosswind on the bike and the main headwind stretches."""
    lat, lon, _ = _read_gpx(bike_gpx)
    d = cumdist(lat, lon) / 1000
    la, lo = np.radians(lat), np.radians(lon)
    brg = (
        np.degrees(
            np.arctan2(
                np.sin(np.diff(lo)) * np.cos(la[1:]),
                np.cos(la[:-1]) * np.sin(la[1:]) - np.sin(la[:-1]) * np.cos(la[1:]) * np.cos(np.diff(lo)),
            )
        )
        % 360
    )
    seg = np.diff(d)
    out = {}
    for s, share in sorted(sectors_share.items(), key=lambda x: -x[1])[:top]:
        wind_from = SECTORS.index(s) * 45
        rel = np.cos(np.radians(brg - wind_from))  # +1 riding straight into the wind
        head, tail = seg[rel > 0.5].sum(), seg[rel < -0.5].sum()
        bins = {}
        for k, r, l in zip(d[:-1], rel, seg):
            if r > 0.5:
                bins[int(k // stretch_km)] = bins.get(int(k // stretch_km), 0) + l
        worst = sorted(bins.items(), key=lambda x: -x[1])[:2]
        out[s] = {
            "share_of_hours": share,
            "headwind_km": round(float(head), 1),
            "tailwind_km": round(float(tail), 1),
            "crosswind_km": round(float(seg.sum() - head - tail), 1),
            "headwind_stretches": [f"{b * stretch_km}-{(b + 1) * stretch_km} km" for b, _ in sorted(worst)],
        }
    return out

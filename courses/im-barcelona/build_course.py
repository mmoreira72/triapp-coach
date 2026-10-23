"""Course files of IRONMAN Barcelona (Calella), https://www.ironman.com/races/im-barcelona/course

  bike  the organiser's GPX (bike_official.gpx, a Wikiloc track: 2 loops Calella - Badalona along the coast) drawn as a smooth
        curve through its points (176.9 km; race.json carries the official 180.2 km and race_impact.py stretches the segments to
        it), resampled every 50 m; its barometric elevation is noisy (1,920 m of ascent), so it is smoothed to the official 750 m
  run   3 laps of 14 km between Calella and Pineda de Mar, along the seafront (no GPX published: traced on the road network)
  swim  one 3.8 km semicircle off Calella beach (schematic)

Run from the repo root: python courses/im-barcelona/build_course.py
"""

import os, sys, json
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(HERE)))
import course_tools as ct

OFFICIAL_BIKE_ASCENT = 750
T = (41.6146, 2.6666)  # transition, Camp de Mar football field by Calella beach
RUN_LAP = [T, (41.6330, 2.7080), T, (41.6040, 2.6440), T]  # east to Pineda de Mar, back, west end of Calella, back: 14 km


def catmull_rom(x, n=6):
    """Smooth curve through the track points (the track has a point every 84 m and cuts the corners)."""
    p = np.concatenate([[x[0]], x, [x[-1]]])
    out = []
    for i in range(1, len(p) - 2):
        p0, p1, p2, p3 = p[i - 1], p[i], p[i + 1], p[i + 2]
        for t in np.linspace(0, 1, n, endpoint=False):
            out.append(
                0.5 * (2 * p1 + (-p0 + p2) * t + (2 * p0 - 5 * p1 + 4 * p2 - p3) * t * t + (-p0 + 3 * p1 - 3 * p2 + p3) * t**3)
            )
    out.append(x[-1])
    return np.array(out)


def bike():
    lat0, lon0, ele0 = ct._read_gpx(os.path.join(HERE, "bike_official.gpx"))
    lat, lon = catmull_rom(lat0), catmull_rom(lon0)
    ele = np.interp(np.linspace(0, len(ele0) - 1, len(lat)), np.arange(len(ele0)), ele0)
    d = ct.cumdist(lat, lon)
    la, lo = ct.resample(lat, lon, 50.0)
    dd = ct.cumdist(la, lo)
    e = np.interp(dd, d, ele)  # the GPX elevation at each resampled point
    w = ct.fit_window(dd, e, OFFICIAL_BIKE_ASCENT, windows=(150, 250, 500, 750, 1000, 1500, 2000))
    road = ct.road_elevation(dd, e, w)
    print(
        f"bike {dd[-1] / 1000:.1f} km; ascent GPX {ct.ascent(e):.0f} m, smoothed {ct.ascent(road):.0f} m (window {w} m, official {OFFICIAL_BIKE_ASCENT} m)"
    )
    ct.write_gpx(os.path.join(HERE, "bike.gpx"), la, lo, road, "IRONMAN Barcelona bike (official track, elevation smoothed)")


def run():
    lat, lon = ct.route(RUN_LAP, "foot")
    lap = ct.cumdist(lat, lon)[-1] / 1000
    la, lo = ct.resample(lat, lon, 25.0)
    ele = ct.road_elevation(ct.cumdist(la, lo), ct.elevation(la, lo), 250)
    la, lo, ele = (np.concatenate([x] * 3) for x in (la, lo, ele))
    print(f"run lap {lap:.1f} km (official 14), total {3 * lap:.1f} km")
    ct.write_gpx(os.path.join(HERE, "run.gpx"), la, lo, ele, "IRONMAN Barcelona run (reconstructed, 3 laps)")


def swim():
    # rectangle of 3.8 km out from the beach and back (only the length is used by the model)
    lat0, lon0 = T[0] - 0.0012, T[1] - 0.0010
    dlat, dlon = -0.0070, -0.0270  # about 780 m out, 2,240 m along the shore, 780 m back
    box = [(lat0, lon0), (lat0 + dlat, lon0), (lat0 + dlat, lon0 + dlon), (lat0, lon0 + dlon)]
    la, lo = np.array([p[0] for p in box]), np.array([p[1] for p in box])
    print(f"swim {ct.cumdist(la, lo)[-1] / 1000:.2f} km")
    ct.write_gpx(os.path.join(HERE, "swim.gpx"), la, lo, None, "IRONMAN Barcelona swim (schematic)")


def summaries_and_weather():
    summary = {k: ct.summarize(os.path.join(HERE, f"{k}.gpx"), with_climbs=(k == "bike")) for k in ("swim", "bike", "run")}
    json.dump(summary, open(os.path.join(HERE, "course_summary.json"), "w"), ensure_ascii=False)
    sites = {"coast (Calella)": T, "bike (Badalona turn)": (41.4635, 2.2729)}
    df = ct.weather(sites, "09-24", "10-14", range(2016, 2026), range(7, 19), "Europe/Madrid")
    df.to_csv(os.path.join(HERE, "weather_window_2016_2025.csv"), index=False)
    out = {
        "main_site": "coast (Calella)",
        "run_site": "coast (Calella)",
        "inland_site": "bike (Badalona turn)",
        "window": "24 set a 14 out, 7h às 18h, 2016 a 2025",
    }
    for label in sites:
        out[label] = ct.site_summary(df[df.site == label])
    out["bike_wind_exposure"] = ct.wind_exposure(os.path.join(HERE, "bike.gpx"), out["coast (Calella)"]["wind_from_sector_share"])
    json.dump(out, open(os.path.join(HERE, "weather_summary.json"), "w"), ensure_ascii=False)
    print(
        "bike",
        {k: summary["bike"].get(k) for k in ("distance_km", "ascent_m", "ele_min", "ele_max")},
        "climbs",
        len(summary["bike"].get("climbs", [])),
    )
    w = out["coast (Calella)"]
    print(
        "weather",
        w["temp_c_p10_p50_p90"],
        w["day_tmax_p50"],
        w["wind_kmh_p50_p90"],
        w["wind_from_sector_share"],
        w["days_with_rain_pct"],
    )


if __name__ == "__main__":
    cache = os.path.join(HERE, "elevation_cache.json")
    ct.load_cache(cache)
    try:
        bike()
        run()
        swim()
    finally:
        ct.save_cache(cache)
    summaries_and_weather()

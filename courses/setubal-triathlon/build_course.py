"""Course files of the Lidl Setúbal Triathlon (half distance), https://setubaltriathlon.pt/site/?local=info#routes

The organiser publishes the three courses as KMZ (unpacked in official/): swim 1.9 km, one lap with an Australian exit at the
Albarquel urban park beach; bike two 45 km loops Albarquel - Av. Luísa Todi - Mitrena, turns at km 13 (Mitrena) and km 25
(Alto da Guerra), highest point 55 m at km 33.7 and 74.4, about 672 m of ascent; run four flat laps on Av. Luísa Todi.
The KML lines have no elevation: it comes from the DEM, with tunnels and bridges interpolated and the noise removed until the
ascent matches the organiser's 672 m.

Run from the repo root: python courses/setubal-triathlon/build_course.py
"""

import os, re, sys, json
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(HERE)))
import course_tools as ct

OFFICIAL_BIKE_ASCENT = 672
T1 = (38.5157, -8.9084)  # Albarquel urban park
T2_RUN = (38.5220, -8.8990)  # Av. Luísa Todi


def read_kml(path):
    t = open(path, encoding="utf-8").read()
    c = re.findall(r"<LineString>.*?<coordinates>(.*?)</coordinates>", t, re.S)[0]
    pts = [tuple(map(float, x.split(",")[:2])) for x in c.split() if x.strip()]
    return np.array([p[1] for p in pts]), np.array([p[0] for p in pts])


def build(kind, step, official_ascent=None, window=250):
    lat, lon = read_kml(os.path.join(HERE, "official", f"{kind}.kml"))
    la, lo = ct.resample(lat, lon, step)
    if kind == "swim":
        ct.write_gpx(os.path.join(HERE, "swim.gpx"), lat, lon, None, "Setúbal Triathlon swim (official)")
        print(f"swim {ct.cumdist(lat, lon)[-1] / 1000:.2f} km")
        return
    dd = ct.cumdist(la, lo)
    dem = ct.elevation(la, lo)
    w = ct.fit_window(dd, dem, official_ascent) if official_ascent else window
    ele = ct.road_elevation(dd, dem, w)
    print(
        f"{kind} {dd[-1] / 1000:.1f} km; ascent DEM {ct.ascent(dem):.0f} m, road {ct.ascent(ele):.0f} m (window {w} m"
        + (f", official {official_ascent} m)" if official_ascent else ")")
        + f", highest {ele.max():.0f} m at km {dd[ele.argmax()] / 1000:.1f}"
    )
    ct.write_gpx(os.path.join(HERE, f"{kind}.gpx"), la, lo, ele, f"Setúbal Triathlon {kind} (official line, DEM elevation)")


def summaries_and_weather():
    summary = {k: ct.summarize(os.path.join(HERE, f"{k}.gpx"), with_climbs=(k == "bike")) for k in ("swim", "bike", "run")}
    json.dump(summary, open(os.path.join(HERE, "course_summary.json"), "w"), ensure_ascii=False)
    sites = {"Setúbal (Albarquel, T1)": T1, "bike (Alto da Guerra)": (38.5480, -8.7860)}
    df = ct.weather(sites, "04-22", "05-12", range(2016, 2026), range(7, 15), "Europe/Lisbon")
    df.to_csv(os.path.join(HERE, "weather_window_2016_2025.csv"), index=False)
    out = {
        "main_site": "Setúbal (Albarquel, T1)",
        "run_site": "Setúbal (Albarquel, T1)",
        "inland_site": "bike (Alto da Guerra)",
        "window": "22 abr a 12 mai, 7h às 14h, 2016 a 2025",
    }
    for label in sites:
        out[label] = ct.site_summary(df[df.site == label])
    out["bike_wind_exposure"] = ct.wind_exposure(
        os.path.join(HERE, "bike.gpx"), out["Setúbal (Albarquel, T1)"]["wind_from_sector_share"]
    )
    json.dump(out, open(os.path.join(HERE, "weather_summary.json"), "w"), ensure_ascii=False)
    print(
        "bike",
        {k: summary["bike"].get(k) for k in ("distance_km", "ascent_m", "ele_min", "ele_max")},
        "climbs",
        summary["bike"].get("climbs"),
    )
    w = out["Setúbal (Albarquel, T1)"]
    print(
        "weather",
        w["temp_c_p10_p50_p90"],
        "tmax",
        w["day_tmax_p50"],
        w["wind_kmh_p50_p90"],
        w["wind_from_sector_share"],
        "rain",
        w["days_with_rain_pct"],
    )


if __name__ == "__main__":
    cache = os.path.join(HERE, "elevation_cache.json")
    ct.load_cache(cache)
    try:
        build("swim", 25)
        build("bike", 50, OFFICIAL_BIKE_ASCENT)
        build("run", 25, window=2000)  # "totally flat" (organiser): only the DEM noise to remove
    finally:
        ct.save_cache(cache)
    summaries_and_weather()

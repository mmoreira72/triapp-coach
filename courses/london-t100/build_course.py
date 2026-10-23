"""Course files of the London T100 Triathlon, T100 distance (2 km swim, 80 km bike, 18 km run), 21-22 August 2027.
https://t100triathlon.com/london/participate/event-info/ ("Courses, Location Maps, & GPX Files", 2026 courses)

The organiser publishes the courses on RideWithGPS (routes 55574533 swim, 55345361 bike, 55502117 run; the JSON copies are in
official/): swim 2 laps with an Australian exit in Royal Victoria Dock; bike laps from ExCeL through the City and Westminster on
closed roads (84.1 km, 343 m); run 18.3 km, flat. The 2027 courses may change.

Run from the repo root: python courses/london-t100/build_course.py
"""

import os, sys, json
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(HERE)))
import course_tools as ct


def from_rwgps(kind, window):
    r = json.load(open(os.path.join(HERE, "official", f"{kind}_rwgps.json")))
    r = r.get("route", r)
    tp = r["track_points"]
    lat = np.array([p["y"] for p in tp])
    lon = np.array([p["x"] for p in tp])
    ele = np.array([p.get("e", np.nan) for p in tp], float)
    if window:
        ele = ct.road_elevation(ct.cumdist(lat, lon), ele, window)
    ct.write_gpx(os.path.join(HERE, f"{kind}.gpx"), lat, lon, ele, f"London T100 {kind} ({r.get('name')})")
    print(
        f"{kind}: {r.get('name')} {ct.cumdist(lat, lon)[-1] / 1000:.2f} km, RWGPS ascent {r.get('elevation_gain'):.0f} m, ours {ct.ascent(ele):.0f} m"
    )


def main():
    from_rwgps("swim", None)
    from_rwgps("bike", 150)
    from_rwgps("run", 150)
    summary = {k: ct.summarize(os.path.join(HERE, f"{k}.gpx"), with_climbs=(k == "bike")) for k in ("swim", "bike", "run")}
    json.dump(summary, open(os.path.join(HERE, "course_summary.json"), "w"), ensure_ascii=False)
    sites = {"ExCeL (Royal Victoria Dock)": (51.5085, 0.0295), "Westminster (bike)": (51.5007, -0.1246)}
    df = ct.weather(sites, "08-11", "08-31", range(2016, 2026), range(6, 15), "Europe/London")
    df.to_csv(os.path.join(HERE, "weather_window_2016_2025.csv"), index=False)
    out = {
        "main_site": "ExCeL (Royal Victoria Dock)",
        "run_site": "ExCeL (Royal Victoria Dock)",
        "inland_site": "Westminster (bike)",
        "window": "11 a 31 ago, 6h às 14h, 2016 a 2025",
    }
    for label in sites:
        out[label] = ct.site_summary(df[df.site == label])
    out["bike_wind_exposure"] = ct.wind_exposure(
        os.path.join(HERE, "bike.gpx"), out["ExCeL (Royal Victoria Dock)"]["wind_from_sector_share"]
    )
    json.dump(out, open(os.path.join(HERE, "weather_summary.json"), "w"), ensure_ascii=False)
    print(
        "bike",
        {k: summary["bike"].get(k) for k in ("distance_km", "ascent_m", "ele_min", "ele_max")},
        "climbs",
        summary["bike"].get("climbs"),
    )
    w = out["ExCeL (Royal Victoria Dock)"]
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
    main()

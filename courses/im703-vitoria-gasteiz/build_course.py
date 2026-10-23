"""Course files of IRONMAN 70.3 Vitoria-Gasteiz, https://www.ironman.com/races/im703-vitoria/course

The organiser publishes the three GPX files (swim.gpx, bike.gpx, run.gpx, downloaded 2026-10-07): 1.9 km in the
Ulibarri-Gamboa (Landa) lake, one 90 km loop through Álava from Landa (T1) to Vitoria (T2), two run loops downtown to the
finish in Plaza España. They also carry waypoints (aid stations, mechanics) that the GPX readers skip. This script only
writes the summaries and the weather window.

Run from the repo root: python courses/im703-vitoria-gasteiz/build_course.py
"""

import os, sys, json

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(HERE)))
import course_tools as ct


def main():
    summary = {k: ct.summarize(os.path.join(HERE, f"{k}.gpx"), with_climbs=(k == "bike")) for k in ("swim", "bike", "run")}
    json.dump(summary, open(os.path.join(HERE, "course_summary.json"), "w"), ensure_ascii=False)
    lat, lon, ele = ct._read_gpx(os.path.join(HERE, "bike.gpx"))
    top = int(ele.argmax())
    sites = {
        "Vitoria (T2, run)": (42.8467, -2.6727),
        "Landa lake (swim, T1)": (42.9530, -2.5880),
        "bike high point": (round(float(lat[top]), 4), round(float(lon[top]), 4)),
    }
    df = ct.weather(sites, "07-01", "07-21", range(2016, 2026), range(7, 18), "Europe/Madrid")
    df.to_csv(os.path.join(HERE, "weather_window_2016_2025.csv"), index=False)
    out = {
        "main_site": "Vitoria (T2, run)",
        "run_site": "Vitoria (T2, run)",
        "inland_site": "bike high point",
        "window": "1 a 21 jul, 7h às 17h, 2016 a 2025",
    }
    for label in sites:
        out[label] = ct.site_summary(df[df.site == label])
    out["bike_wind_exposure"] = ct.wind_exposure(os.path.join(HERE, "bike.gpx"), out["bike high point"]["wind_from_sector_share"])
    json.dump(out, open(os.path.join(HERE, "weather_summary.json"), "w"), ensure_ascii=False)
    for k in ("swim", "bike", "run"):
        print(k, {x: summary[k].get(x) for x in ("distance_km", "ascent_m", "ele_min", "ele_max")})
    print("climbs", summary["bike"].get("climbs"))
    w = out["Vitoria (T2, run)"]
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

"""Course files of Challenge Salou-Costa Daurada, T100 distance (2 km swim, 80 km bike, 18 km run), 9 May 2027.
https://challenge-salou.com/en/distances/t100-distance/

The organiser's Google My Maps (official/map.kml; titled "maps to test before publishing", so it may still change) draws one
bike loop of 26.7 km on the highway (3 laps = 80 km) and one run lap of 4.25 km (4 laps plus the link to transition = 18 km).
The swim (2 laps off Salou beach) is not drawn: a schematic 2 km. The lines have no elevation: it comes from the DEM.

Run from the repo root: python courses/challenge-salou-t100/build_course.py
"""

import os, re, sys, json
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(HERE)))
import course_tools as ct

T = (41.0742, 1.1389)  # expo / transition, Salou seafront


def line(name):
    t = open(os.path.join(HERE, "official", "map.kml"), encoding="utf-8").read()
    for pm in re.findall(r"<Placemark>(.*?)</Placemark>", t, re.S):
        if re.search(r"<name>\s*" + re.escape(name), pm):
            c = re.findall(r"<coordinates>(.*?)</coordinates>", pm, re.S)[0]
            pts = [tuple(map(float, x.split(",")[:2])) for x in c.split() if x.strip()]
            return np.array([p[1] for p in pts]), np.array([p[0] for p in pts])
    raise KeyError(name)


def write(kind, lat, lon, step, window):
    la, lo = ct.resample(lat, lon, step)
    dd = ct.cumdist(la, lo)
    ele = ct.road_elevation(dd, ct.elevation(la, lo), window)
    ct.write_gpx(os.path.join(HERE, f"{kind}.gpx"), la, lo, ele, f"Challenge Salou T100 {kind}")
    print(f"{kind} {dd[-1] / 1000:.1f} km, ascent {ct.ascent(ele):.0f} m, {ele.min():.0f}-{ele.max():.0f} m")


def main():
    b_lat, b_lon = line("bike T100 SALOU")
    la, lo = ct.resample(b_lat, b_lon, 50.0)
    write("bike", np.concatenate([la] * 3), np.concatenate([lo] * 3), 50.0, 1500)  # highway: only long rises are real
    r_lat, r_lon = line("RUN 4 LAPS")
    k_lat, k_lon = line("Línea 5")  # link between the run loop and transition
    write("run", np.concatenate([r_lat] * 4 + [k_lat]), np.concatenate([r_lon] * 4 + [k_lon]), 25.0, 500)
    lat0, lon0 = T[0] - 0.0020, T[1] + 0.0005
    box = [(lat0, lon0), (lat0 - 0.0018, lon0), (lat0 - 0.0018, lon0 + 0.0033), (lat0, lon0 + 0.0033), (lat0, lon0)]
    sl, so = np.array([p[0] for p in box] * 2), np.array([p[1] for p in box] * 2)
    ct.write_gpx(os.path.join(HERE, "swim.gpx"), sl, so, None, "Challenge Salou T100 swim (schematic, 2 laps)")
    print(f"swim {ct.cumdist(sl, so)[-1] / 1000:.2f} km")
    summary = {k: ct.summarize(os.path.join(HERE, f"{k}.gpx"), with_climbs=(k == "bike")) for k in ("swim", "bike", "run")}
    json.dump(summary, open(os.path.join(HERE, "course_summary.json"), "w"), ensure_ascii=False)
    far = int(np.argmax(np.hypot(la - T[0], (lo - T[1]) * 0.75)))  # bike point farthest from transition
    sites = {"Salou (seafront)": T, "bike (far end)": (round(float(la[far]), 4), round(float(lo[far]), 4))}
    df = ct.weather(sites, "04-29", "05-19", range(2016, 2026), range(8, 15), "Europe/Madrid")
    df.to_csv(os.path.join(HERE, "weather_window_2016_2025.csv"), index=False)
    out = {
        "main_site": "Salou (seafront)",
        "run_site": "Salou (seafront)",
        "inland_site": "bike (far end)",
        "window": "29 abr a 19 mai, 8h às 14h, 2016 a 2025",
    }
    for label in sites:
        out[label] = ct.site_summary(df[df.site == label])
    out["bike_wind_exposure"] = ct.wind_exposure(
        os.path.join(HERE, "bike.gpx"), out["Salou (seafront)"]["wind_from_sector_share"]
    )
    json.dump(out, open(os.path.join(HERE, "weather_summary.json"), "w"), ensure_ascii=False)
    print(
        "bike",
        {k: summary["bike"].get(k) for k in ("distance_km", "ascent_m", "ele_min", "ele_max")},
        "climbs",
        summary["bike"].get("climbs"),
    )
    w = out["Salou (seafront)"]
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
        main()
    finally:
        ct.save_cache(cache)

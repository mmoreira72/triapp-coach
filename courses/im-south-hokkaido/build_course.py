"""Rebuilds the course files of IRONMAN Japan South Hokkaido. The organiser publishes PDF maps only, so the courses are
traced on the road network from the maps (https://www.ironman.com/races/im-south-hokkaido/course):

  bike  T1 (Hokuto, Kamiiso) -> Hokuto-Tomigawa IC (A) -> on the Hakodate-Esashi Expressway, closed to traffic, out-and-backs
        between Hokuto-Chuo IC (B), Hokuto-Mohechi IC (D) and Kikonai IC (C): S A B D C B D C B D C D C F (180 km, 1,450 m)
  run   3 loops of 14 km in Kikonai: west on the north bank of the Kikonai River to Yoshibori, back on the Esashi-Kikonai
        road, south to the Kikonai view spot, back to the town hall
  swim  2 loops in Hakodate Bay off T1

Run from the repo root: python courses/im-south-hokkaido/build_course.py
"""

import os, sys, json
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(HERE)))
import course_tools as ct

OFFICIAL_BIKE_ASCENT = 1450
NORTH_RAMP_M = 1700  # length of the Hokuto-Chuo IC ramp at the start of the OSRM route (steps without a road name)
T1 = (41.8160, 140.6420)  # beach transition by the swim area, Kamiiso (Hokuto)
A = (41.80986, 140.62053)  # Hokuto-Tomigawa IC
B = (41.83542, 140.65260)  # Hokuto-Chuo IC (turnaround)
D = (41.76879, 140.59289)  # Hokuto-Mohechi IC (turnaround), north section
D_SOUTH = (41.76230, 140.58744)  # Hokuto-Mohechi IC, start of the Mohechi-Kikonai section (the two sections meet here)
C = (41.69616, 140.44872)  # Kikonai IC (turnaround)
T2 = (41.6800, 140.4330)  # Furusatonomori Park, Kikonai
NORTH = [B, A, (41.78471, 140.61520), D]  # southbound, on the motorway
SOUTH = [
    D_SOUTH,
    (41.75930, 140.58351),
    (41.74509, 140.54949),
    (41.72946, 140.53526),
    (41.70840, 140.50342),
    (41.70311, 140.48858),
    (41.70242, 140.46317),
    C,
]
RUN_LOOP = [
    (41.67847, 140.43762),
    (41.6845, 140.4150),
    (41.6875, 140.3800),
    (41.6830, 140.4000),
    (41.6740, 140.4120),
    (41.6700, 140.4200),
    (41.6730, 140.4300),
    (41.67847, 140.43762),
]  # town hall, Yoshibori, view spot: 14 km


def with_road_elevation(lat, lon, step=50.0, window=None):
    """Resampled polyline with its DEM elevation (window None: raw DEM, cleaned later on the whole course)."""
    la, lo = ct.resample(lat, lon, step)
    return la, lo, ct.elevation(la, lo)


def rev(p):
    return tuple(x[::-1] for x in p)


def bike():
    n_lat, n_lon = ct.route(NORTH, "driving")
    on_main = ct.cumdist(n_lat, n_lon) > NORTH_RAMP_M  # OSRM starts on the IC ramp: the course turns on the main line
    north = with_road_elevation(n_lat[on_main], n_lon[on_main])
    s_lat, s_lon = ct.route(SOUTH, "driving")
    cut = ct.nearest(s_lat, s_lon, C)  # stop at the IC: OSRM leaves the motorway to turn there
    south = with_road_elevation(s_lat[: cut + 1], s_lon[: cut + 1])
    link = with_road_elevation(np.array([D[0], D_SOUTH[0]]), np.array([D[1], D_SOUTH[1]]))
    lat, lon, _ = north
    iA = ct.nearest(lat, lon, A)
    t1a = with_road_elevation(*ct.route([T1, A], "driving"))
    c_t2 = with_road_elevation(np.array([C[0], (C[0] + T2[0]) / 2, T2[0]]), np.array([C[1], 140.4420, T2[1]]))
    a_b = tuple(x[: iA + 1][::-1] for x in north)
    b_c = tuple(np.concatenate([n, l, s]) for n, l, s in zip(north, link, south))
    d_c = tuple(np.concatenate([l, s]) for l, s in zip(link, south))
    legs = [t1a, a_b, b_c, rev(b_c), b_c, rev(b_c), b_c, rev(d_c), d_c, c_t2]
    marks, acc = [], 0.0
    for l in legs:
        acc += ct.cumdist(l[0], l[1])[-1] / 1000
        marks.append(round(acc, 1))
    print("cumulative km at each turn (map: A, B 9, C 36, B 63, C 91, B 118, C 145, D, C 178, F 180):", marks)
    la, lo, dem = (np.concatenate([l[i] for l in legs]) for i in range(3))
    dd = ct.cumdist(la, lo)
    w = ct.fit_window(dd, dem, OFFICIAL_BIKE_ASCENT)
    ele = ct.road_elevation(dd, dem, w)
    print(
        f"bike {dd[-1] / 1000:.1f} km; ascent DEM {ct.ascent(dem):.0f} m, road profile {ct.ascent(ele):.0f} m (median window {w} m, official {OFFICIAL_BIKE_ASCENT} m)"
    )
    ct.write_gpx(os.path.join(HERE, "bike.gpx"), la, lo, ele, "IRONMAN Japan South Hokkaido bike (reconstructed)")


def run():
    lat, lon = ct.route(RUN_LOOP, "foot")
    loop = ct.cumdist(lat, lon)[-1] / 1000
    la, lo = ct.resample(lat, lon, 25.0)
    ele = ct.road_elevation(ct.cumdist(la, lo), ct.elevation(la, lo), 250)
    la, lo, ele = (np.concatenate([x] * 3) for x in (la, lo, ele))
    print(f"run loop {loop:.1f} km (map: 14), total {3 * loop:.1f} km")
    ct.write_gpx(os.path.join(HERE, "run.gpx"), la, lo, ele, "IRONMAN Japan South Hokkaido run (reconstructed, 3 loops)")


def swim():
    # rectangle of 1.9 km off the beach, swum twice; only its length is used by the model
    lat0, lon0 = T1[0] - 0.0012, T1[1] - 0.0010
    dlat, dlon = 0.0035, -0.0068
    box = [(lat0, lon0), (lat0 - dlat, lon0), (lat0 - dlat, lon0 + dlon), (lat0, lon0 + dlon), (lat0, lon0)]
    la = np.array([p[0] for p in box] * 2)
    lo = np.array([p[1] for p in box] * 2)
    print(f"swim {ct.cumdist(la, lo)[-1] / 1000:.2f} km")
    ct.write_gpx(os.path.join(HERE, "swim.gpx"), la, lo, None, "IRONMAN Japan South Hokkaido swim (schematic, 2 loops)")


def summaries_and_weather():
    summary = {k: ct.summarize(os.path.join(HERE, f"{k}.gpx"), with_climbs=(k == "bike")) for k in ("swim", "bike", "run")}
    json.dump(summary, open(os.path.join(HERE, "course_summary.json"), "w"), ensure_ascii=False)
    sites = {"coast (Hokuto)": T1, "Kikonai (T2, run)": T2, "expressway (bike, Mohechi)": D}
    df = ct.weather(sites, "09-01", "09-25", range(2016, 2026), range(6, 19), "Asia/Tokyo")
    df.to_csv(os.path.join(HERE, "weather_window_2016_2025.csv"), index=False)
    out = {"main_site": "coast (Hokuto)", "run_site": "Kikonai (T2, run)", "inland_site": "expressway (bike, Mohechi)"}
    for label in sites:
        out[label] = ct.site_summary(df[df.site == label])
    out["bike_wind_exposure"] = ct.wind_exposure(
        os.path.join(HERE, "bike.gpx"), out["expressway (bike, Mohechi)"]["wind_from_sector_share"]
    )
    json.dump(out, open(os.path.join(HERE, "weather_summary.json"), "w"), ensure_ascii=False)
    print(
        "bike",
        {k: summary["bike"].get(k) for k in ("distance_km", "ascent_m", "ele_min", "ele_max")},
        "climbs",
        len(summary["bike"].get("climbs", [])),
    )
    print(
        "weather coast",
        out["coast (Hokuto)"]["temp_c_p10_p50_p90"],
        out["coast (Hokuto)"]["wind_kmh_p50_p90"],
        out["coast (Hokuto)"]["wind_from_sector_share"],
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

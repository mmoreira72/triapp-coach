"""Builds a target race from what the coach enters on the admin page: the course files race_impact.py reads.

Input: <OUT>/courses/<slug>/form.json (name, date, distance, start hour, official distances and laps, water, aid stations)
and the uploaded GPX files (bike required, run optional). Output, in the same folder:
  bike.gpx / run.gpx      the laps joined, resampled every 50 m, elevation from the GPX or else from the terrain model (DEM)
  course_summary.json     distance, ascent, climbs, profile (course_tools.summarize)
  weather_summary.json    10 years of ERA5 at the start and at the bike's far point, in the race window and hours
  race.json               what the race page and race_impact.py read
  build.json              state of the build (running / ok / error) for the admin page

Usage: python course_builder.py <slug>
"""

import os, sys, json, re, unicodedata, traceback
from datetime import date, timedelta, datetime
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import course_tools as ct

OUT = os.environ.get("TRIAPP_OUT", os.path.join(HERE, "out"))
COURSES = os.path.join(OUT, "courses")
DISTANCES = {"70.3": "meio-ironman 70.3", "T100": "T100", "140.6": "Ironman 140.6"}
RUN_DISTANCES = {"10k": ("10 km", 10.0), "21.1": ("Meia maratona", 21.0975), "42.2": ("Maratona", 42.195), "outra": (None, None)}
WINDOW_DAYS = 10  # weather window: race date +- 10 days
YEARS = 10


def slugify(name, day):
    s = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")[:50] or "prova"
    return f"{s}-{day[:4]}"


def _status(folder, **kw):
    path = os.path.join(folder, "build.json")
    try:
        cur = json.load(open(path))
    except Exception:
        cur = {}
    cur.update(kw, at=datetime.now().isoformat(timespec="seconds"))
    json.dump(cur, open(path, "w"), ensure_ascii=False)


def leg(folder, kind, laps, smooth_window):
    """Join the laps of an uploaded GPX, resample it and give it an elevation; None when there is no file."""
    src = os.path.join(folder, f"upload_{kind}.gpx")
    if not os.path.exists(src):
        return None
    lat, lon, ele = ct._read_gpx(src)
    if len(lat) < 10:
        raise ValueError(f"o GPX da {'bike' if kind == 'bike' else 'corrida'} tem menos de 10 pontos")
    laps = max(1, int(laps or 1))
    gap = ct.cumdist(lat[[0, -1]], lon[[0, -1]])[-1]
    if laps > 1 and gap > 300:  # repeating an open line would add a straight jump back to the start on every lap
        raise ValueError(
            f"o GPX da {'bike' if kind == 'bike' else 'corrida'} não fecha a volta (fim a {gap / 1000:.1f} km do início): "
            "carrega o percurso completo com 1 volta"
        )
    la, lo = ct.resample(lat, lon, 50.0)  # one lap; the laps are joined after it has an elevation
    d = ct.cumdist(la, lo)
    if np.isfinite(ele).mean() > 0.9:  # the GPX has its own elevation: carried onto the resampled points, then smoothed like a
        # road (watch and app tracks carry metres of noise that add up to hundreds of metres of false climbing)
        e = np.interp(d, ct.cumdist(lat, lon), np.nan_to_num(ele, nan=np.nanmean(ele)))
        e = ct.road_elevation(d, e, smooth_window)
    else:  # no elevation in the file: the terrain model, smoothed like a road
        e = ct.road_elevation(d, ct.elevation(la, lo), smooth_window)
    la, lo, e = np.tile(la, laps), np.tile(lo, laps), np.tile(e, laps)
    ct.write_gpx(os.path.join(folder, f"{kind}.gpx"), la, lo, e, kind)
    return la, lo


def build(slug):
    folder = os.path.join(COURSES, slug)
    f = json.load(open(os.path.join(folder, "form.json"), encoding="utf-8"))
    if f.get("sport") == "run":
        return build_run(slug, folder, f)
    _status(folder, state="running", step="percurso", error=None)
    cache = os.path.join(folder, "elevation_cache.json")
    ct.load_cache(cache)
    try:
        bike = leg(folder, "bike", f.get("bike_laps"), 1000)
        if not bike:
            raise ValueError("falta o GPX da bike")
        b_lat, b_lon = bike
        run = leg(folder, "run", f.get("run_laps"), 500)
        start = (round(float(b_lat[0]), 4), round(float(b_lon[0]), 4))
        summary = {"bike": ct.summarize(os.path.join(folder, "bike.gpx"), with_climbs=True)}
        summary["run"] = (
            ct.summarize(os.path.join(folder, "run.gpx"), with_climbs=False)
            if run
            else {
                "points": 0,
                "distance_km": float(f["run_km"]),
                "start": list(start),
                "loop_closed_m": 0,
                "ascent_m": 0,
                "descent_m": 0,
                "ele_min": 0,
                "ele_max": 0,
            }
        )
        summary["swim"] = {"points": 0, "distance_km": float(f["swim_km"]), "start": list(start), "loop_closed_m": 0}
        json.dump(summary, open(os.path.join(folder, "course_summary.json"), "w"), ensure_ascii=False)

        _status(folder, step="meteorologia (10 anos de ERA5)")
        far = int(np.argmax(np.hypot(b_lat - start[0], (b_lon - start[1]) * np.cos(np.radians(start[0])))))
        sites = {"partida": start, "bike (ponto mais longe)": (round(float(b_lat[far]), 4), round(float(b_lon[far]), 4))}
        day = date.fromisoformat(f["date"])
        md_from, md_to = (day - timedelta(days=WINDOW_DAYS)).strftime("%m-%d"), (day + timedelta(days=WINDOW_DAYS)).strftime(
            "%m-%d"
        )
        if md_from > md_to:  # a window across New Year: keep the part in the race's year
            md_from, md_to = ("01-01", md_to) if day.month == 1 else (md_from, "12-31")
        h0 = int(f.get("start_hour") or 8)
        hours = range(h0, min(h0 + {"140.6": 12, "70.3": 7, "T100": 6}.get(f["distance"], 7), 23) + 1)
        last = date.today().year - 1
        df = ct.weather(sites, md_from, md_to, range(last - YEARS + 1, last + 1), hours, "auto")
        w = {
            "main_site": "partida",
            "run_site": "partida",
            "inland_site": "bike (ponto mais longe)",
            "window": f"{(day - timedelta(days=WINDOW_DAYS)):%d/%m} a {(day + timedelta(days=WINDOW_DAYS)):%d/%m}, {hours[0]}h às {hours[-1]}h, {last - YEARS + 1} a {last}",
        }
        for label in sites:
            w[label] = ct.site_summary(df[df.site == label])
        w["bike_wind_exposure"] = ct.wind_exposure(os.path.join(folder, "bike.gpx"), w["partida"]["wind_from_sector_share"])
        json.dump(w, open(os.path.join(folder, "weather_summary.json"), "w"), ensure_ascii=False)

        water = f.get("water_temp_c")
        estimated = False
        if water in (None, ""):  # rough guess from the air in the race window, flagged on the page
            air = w["partida"]["temp_c_p10_p50_p90"][1]
            water, estimated = round(air - 2), True
        water = float(water)
        race = {
            "slug": slug,
            "name": f["name"],
            "date": f["date"],
            "distance": f["distance"],
            "url": f.get("url") or "",
            "water_temp_c": [round(water - 1), round(water + 1)],
            "water_temp_estimated": estimated,
            "swim": f"{f.get('swim_type') or 'mar'}"
            + (f", {int(f['swim_laps'])} voltas" if int(f.get("swim_laps") or 1) > 1 else ""),
            "swim_distance_km": float(f["swim_km"]),
            "bike_distance_km": float(f["bike_km"]),
            "run_distance_km": float(f["run_km"]),
            "start_hour": h0,
            "aid_bike_km": f.get("aid_bike_km") or [],
            "aid_run_km": f.get("aid_run_km") or [],
            "course_reconstructed": False,
            "created_by": f.get("created_by"),
            "custom": True,
        }
        json.dump(race, open(os.path.join(folder, "race.json"), "w"), ensure_ascii=False, indent=1)
        _status(folder, state="ok", step=None, bike=summary["bike"].get("distance_km"), ascent=summary["bike"].get("ascent_m"))
    except Exception as e:
        _status(folder, state="error", step=None, error=f"{type(e).__name__}: {e}", trace=traceback.format_exc()[-1500:])
        raise
    finally:
        ct.save_cache(cache)


def race_weather(folder, f, lat, lon, hours_long):
    """10 years of ERA5 at the start and at the course's far point, race window and hours; the summary race_impact reads."""
    start = (round(float(lat[0]), 4), round(float(lon[0]), 4))
    far = int(np.argmax(np.hypot(lat - start[0], (lon - start[1]) * np.cos(np.radians(start[0])))))
    sites = {"partida": start, "ponto mais longe": (round(float(lat[far]), 4), round(float(lon[far]), 4))}
    day = date.fromisoformat(f["date"])
    md_from, md_to = (day - timedelta(days=WINDOW_DAYS)).strftime("%m-%d"), (day + timedelta(days=WINDOW_DAYS)).strftime("%m-%d")
    if md_from > md_to:
        md_from, md_to = ("01-01", md_to) if day.month == 1 else (md_from, "12-31")
    h0 = int(f.get("start_hour") or 8)
    hours = range(h0, min(h0 + hours_long, 23) + 1)
    last = date.today().year - 1
    df = ct.weather(sites, md_from, md_to, range(last - YEARS + 1, last + 1), hours, "auto")
    w = {
        "main_site": "partida",
        "run_site": "partida",
        "inland_site": "ponto mais longe",
        "window": f"{(day - timedelta(days=WINDOW_DAYS)):%d/%m} a {(day + timedelta(days=WINDOW_DAYS)):%d/%m}, {hours[0]}h às {hours[-1]}h, {last - YEARS + 1} a {last}",
    }
    for label in sites:
        w[label] = ct.site_summary(df[df.site == label])
    return w


def build_run(slug, folder, f):
    """A running race: the run GPX (laps joined, elevation), 10 years of weather, race.json with sport "run"."""
    _status(folder, state="running", step="percurso", error=None)
    cache = os.path.join(folder, "elevation_cache.json")
    ct.load_cache(cache)
    try:
        run = leg(folder, "run", f.get("run_laps"), 300)
        if not run:
            raise ValueError("falta o GPX da corrida")
        r_lat, r_lon = run
        summary = {"run": ct.summarize(os.path.join(folder, "run.gpx"), with_climbs=True)}
        json.dump(summary, open(os.path.join(folder, "course_summary.json"), "w"), ensure_ascii=False)
        _status(folder, step="meteorologia (10 anos de ERA5)")
        km = float(f["run_km"])
        w = race_weather(folder, f, r_lat, r_lon, max(2, int(km / 8) + 1))
        w["run_wind_exposure"] = ct.wind_exposure(os.path.join(folder, "run.gpx"), w["partida"]["wind_from_sector_share"])
        json.dump(w, open(os.path.join(folder, "weather_summary.json"), "w"), ensure_ascii=False)
        label = RUN_DISTANCES.get(f.get("distance"), (None, None))[0] or f"{km:g} km"
        race = {
            "slug": slug,
            "name": f["name"],
            "date": f["date"],
            "sport": "run",
            "distance": f.get("distance") or "outra",
            "distance_label": label,
            "url": f.get("url") or "",
            "run_distance_km": km,
            "start_hour": int(f.get("start_hour") or 9),
            "aid_run_km": f.get("aid_run_km") or [],
            "course_reconstructed": False,
            "created_by": f.get("created_by"),
            "custom": True,
        }
        json.dump(race, open(os.path.join(folder, "race.json"), "w"), ensure_ascii=False, indent=1)
        _status(folder, state="ok", step=None, run=summary["run"].get("distance_km"), ascent=summary["run"].get("ascent_m"))
    except Exception as e:
        _status(folder, state="error", step=None, error=f"{type(e).__name__}: {e}", trace=traceback.format_exc()[-1500:])
        raise
    finally:
        ct.save_cache(cache)


if __name__ == "__main__":
    build(sys.argv[1])

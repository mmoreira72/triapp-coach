# IRONMAN Barcelona, Calella (next edition: early October 2027, date to be confirmed; the 2026 race was on 4 October)

Built by `build_course.py` (see its docstring) from https://www.ironman.com/races/im-barcelona/course:

- `bike_official.gpx`: the organiser's file (a Wikiloc track, 2 loops Calella - Badalona along the coast, point every 84 m).
  `bike.gpx` draws a smooth curve through it (176.9 km); `race.json` carries the official 180.2 km and `race_impact.py`
  stretches the segments to it. Elevation from the track, smoothed from 1,920 m of noisy ascent to 774 m (official 750 m).
- `run.gpx`: 3 laps of 14.0 km between Calella and Pineda de Mar along the seafront, traced on the road network (no GPX
  published). Flat.
- `swim.gpx`: schematic 3.8 km out-and-back from Calella beach (one lap in the race; only the length is used).
- `weather_window_2016_2025.csv` / `weather_summary.json`: ERA5 reanalysis (Open-Meteo), 24 September - 14 October,
  07:00-18:00 local, 2016-2025, at Calella and at the Badalona turn. Water 21-24 °C (organiser: average 23 °C).

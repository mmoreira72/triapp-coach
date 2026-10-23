# IRONMAN Japan South Hokkaido (next edition: September 2027, date to be confirmed; the 2026 race was on 13 September)

The organiser publishes PDF maps only, so the course files are reconstructed by `build_course.py` (see its docstring) from the
maps at https://www.ironman.com/races/im-south-hokkaido/course, on the OpenStreetMap road network (OSRM routing):

- `bike.gpx`: T1 Kamiiso (Hokuto) → Hakodate-Esashi Expressway, closed to traffic, out-and-backs between Hokuto-Chuo IC,
  Hokuto-Mohechi IC and Kikonai IC → T2 Kikonai. 181.8 km against the official 180; the turn marks match the map to 1-2 km.
- Elevation: Open-Meteo / OpenTopoData DEM along the road, with tunnels and bridges interpolated (the DEM gives the hill above
  the 2.2 km tunnel near km 21) and noise removed by a rolling median. Result: 1,273 m of ascent against the official 1,450 m,
  highest point 96 m (official profile: under 100 m). Treat climbing figures as about 10% low.
- `run.gpx`: 3 loops of 14.0 km in Kikonai (town hall, north bank of the Kikonai River to Yoshibori, Esashi-Kikonai road,
  Kikonai view spot). Flat. The exact streets may differ from the race; the distance and the terrain are what the model uses.
- `swim.gpx`: schematic 2 × 1.9 km rectangle in Hakodate Bay off T1 (only the distance is used).
- `weather_window_2016_2025.csv` / `weather_summary.json`: ERA5 reanalysis (Open-Meteo), 1-25 September, 06:00-18:00 local,
  2016-2025, at the coast (Hokuto), at Kikonai (T2, run) and on the expressway (Mohechi). `elevation_cache.json` keeps the DEM
  answers so a rebuild does not hit the services' rate limits.

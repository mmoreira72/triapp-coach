# Lidl Setúbal Triathlon, half distance (2 May 2027)

Official KMZ routes from https://setubaltriathlon.pt/site/?local=info#routes, unpacked in `official/` (downloaded 2026-10-07).
`build_course.py` turns them into GPX with DEM elevation (the KML lines have none):

- `bike.gpx`: two 45 km loops from the Albarquel urban park through Av. Luísa Todi and the Mitrena road, turns at Mitrena (km 13)
  and Alto da Guerra (km 25). 89.9 km, 672 m of ascent (organiser: about 672 m), highest point 53 m at km 34 (organiser: 55 m
  at km 33.7). Rolling, no climb of 50 m or more.
- `run.gpx`: four flat laps on Av. Luísa Todi (20.5 km after resampling; official 21.1 km).
- `swim.gpx`: one 1.9 km lap with an Australian exit at the Albarquel beach (Sado estuary). Water temperature is not published:
  16-18 °C assumed for early May.
- Weather: ERA5 reanalysis (Open-Meteo), 22 April - 12 May, 07:00-14:00 local, 2016-2025, at Albarquel and Alto da Guerra.

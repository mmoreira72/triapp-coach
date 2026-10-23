# IRONMAN 70.3 Vilamoura-Algarve (next edition 3 April 2027)

- `swim.gpx`, `bike.gpx`, `run.gpx`: official course files from https://www.ironman.com/races/im703-vilamoura-algarve/course (downloaded 2026-09-30).
- `course_summary.json`: distance, ascent, grade shares, climbs (≥ 50 m gain, dips ≤ 25 m tolerated) and a 10 km elevation profile.
- `weather_window_2016_2026.csv`: hourly Open-Meteo ERA5 reanalysis (no API key) for 25 Mar - 15 Apr, 07:00-14:00 local, 2016-2026,
  at the coast (Vilamoura) and at the bike high point (37.230, -8.142, 281 m).
- `weather_summary.json`: temperature, humidity, wind by hour and sector, rain days, and bike-course wind exposure for the three most
  frequent wind sectors.

OpenWeatherMap was not used: historical data there needs a paid key. ERA5 is modelled (about 30 km grid), not station data.

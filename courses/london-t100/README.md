# London T100 Triathlon, T100 distance (21-22 August 2027)

Courses from the organiser's RideWithGPS routes (2026 edition; the 2027 courses may change), listed under "Courses, Location
Maps, & GPX Files" at https://t100triathlon.com/london/participate/event-info/ and saved in `official/`:
swim 2.03 km (2 laps, Australian exit, Royal Victoria Dock), bike 84.1 km (closed roads from ExCeL through the City and
Westminster; RWGPS counts 343 m of ascent, mostly noise: the course stays between -1 and 13 m), run 18.3 km, flat.
`build_course.py` turns them into GPX and writes the summaries and the weather window (ERA5, 11-31 August, 06:00-14:00,
2016-2025, at ExCeL and Westminster). Water 18-24 °C (organiser), wetsuit.

T100 is a distance of its own in `race_impact.py` (DISTANCES["T100"]); nobody in the squad has raced it, so predictions
start from the half-distance races with the adjustments listed there.

import json, math
import course_builder as cb


def gpx(points):
    pts = "".join(f'<trkpt lat="{la:.6f}" lon="{lo:.6f}"><ele>{e:.1f}</ele></trkpt>' for la, lo, e in points)
    return f"<gpx><trk><trkseg>{pts}</trkseg></trk></gpx>"


def test_slug_and_laps_keep_the_gpx_elevation(tmp_path):
    assert cb.slugify("IRONMAN 70.3 Cascais", "2027-10-10") == "ironman-70-3-cascais-2027"
    # a 5 km loop (2.5 km out, 2.5 km back on a parallel road) with a 50 m hill, ridden 3 times
    out = [(41.0 + 0.0225 * i / 25, 1.0, 50 * math.sin(math.pi * i / 50)) for i in range(26)]
    back = [(41.0225 - 0.0225 * i / 25, 1.0003, 50 * math.sin(math.pi * (25 + i) / 50)) for i in range(26)]
    pts = out + back
    (tmp_path / "upload_bike.gpx").write_text(gpx(pts))
    la, lo = cb.leg(str(tmp_path), "bike", 3, 1000)
    assert 280 < len(la) < 320  # ~15 km at 50 m
    import course_tools as ct

    s = ct.summarize(str(tmp_path / "bike.gpx"))
    assert 14 < s["distance_km"] < 16 and 110 < s["ascent_m"] < 160 and s["ele_max"] >= 40  # road smoothing trims hill tops
    assert cb.leg(str(tmp_path), "run", 1, 500) is None  # no run GPX


def test_an_open_line_cannot_be_repeated(tmp_path):
    import pytest

    pts = [(41.0 + 0.045 * i / 50, 1.0, 0.0) for i in range(51)]
    (tmp_path / "upload_bike.gpx").write_text(gpx(pts))
    with pytest.raises(ValueError, match="não fecha"):
        cb.leg(str(tmp_path), "bike", 3, 1000)
    assert cb.leg(str(tmp_path), "bike", 1, 1000)  # the whole course in one go is fine

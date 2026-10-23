"""Training zones, TrainingPeaks defaults. The one place zones are defined.

  heart rate, % of LTHR (Friel)  run : Z1 <85, Z2 85-89, Z3 90-94, Z4 95-99, Z5a 100-102, Z5b 103-105, Z5c >=106
                                 bike: Z1 <81, Z2 81-89, Z3 90-93, Z4 94-99, Z5a 100-102, Z5b 103-105, Z5c >=106
  bike power, % of FTP (Coggan)  Z1 <55, Z2 55-75, Z3 76-90, Z4 91-105, Z5 106-120, Z6 121-150, Z7 >150

A zone is an index from 1: the number of lower edges the value has reached, plus one. Heart-rate zones 5, 6, 7 are
Friel's 5a, 5b, 5c.
"""

HR_EDGES = {"run": [85, 90, 95, 100, 103, 106], "bike": [81, 90, 94, 100, 103, 106]}
POWER_EDGES = [55, 76, 91, 106, 121, 151]
HR_LABELS = {5: "Z5a", 6: "Z5b", 7: "Z5c"}


def zone(value, threshold, edges):
    """Zone index (1-based) of `value` against `threshold`, or None when either is missing."""
    if not (value and threshold and value > 0 and threshold > 0):
        return None
    pct = 100 * value / threshold
    return 1 + sum(pct >= e for e in edges)


def hr_zone(hr, lthr, sport, split_z5=True):
    """Heart-rate zone. With split_z5=False, 5a/5b/5c collapse into a single zone 5."""
    z = zone(hr, lthr, HR_EDGES.get(sport, HR_EDGES["run"]))
    return z if (z is None or split_z5) else min(z, 5)


def power_zone(watts, ftp):
    return zone(watts, ftp, POWER_EDGES)


def label(z, basis):
    """'Z3', or 'Z5a'/'Z5b'/'Z5c' for heart rate."""
    return HR_LABELS.get(z, f"Z{z}") if basis == "FC" else f"Z{z}"


def seconds_by_zone(hist, threshold, edges, bin_width):
    """{zone: seconds} from a histogram {bin start: seconds}; each bin is placed by its centre."""
    if not hist or not (threshold and threshold > 0):
        return {}
    out = {}
    for start, sec in hist.items():
        z = zone(start + bin_width / 2, threshold, edges)
        out[z] = out.get(z, 0) + sec
    return out

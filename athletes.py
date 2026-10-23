"""Athlete discovery: every folder named <First>_<Last>_wko or <First>_<Last>_Fit_Files under TRIAPP_ROOT is an athlete.
A folder without the suffix (<First>_<Last>) also counts, classified by its content: WKO5 export (*.wko5athlete or */*.wko4)
or FIT files. When two folders give the same athlete and kind, the one with more files wins.

Key = first name (capitalised), unless two athletes share it, then "First Last". Keys must stay stable because user
accounts point at them. Birth year and sex come from the WKO5 athlete profile (<First>_<Last>.wko5athlete) when present.
"""

import os, re, glob, json
from functools import lru_cache

ROOT = os.environ.get("TRIAPP_ROOT") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SUFFIX = re.compile(r"^(?P<base>.+?)_(?P<kind>wko|fit_files)$", re.I)


def _nice(base):
    return " ".join(p.capitalize() for p in re.split(r"[_\s]+", base) if p)


@lru_cache(maxsize=1)
def discover(root=None):
    root = root or ROOT
    found = {}
    best = {}
    for d in sorted(os.listdir(root)) if os.path.isdir(root) else []:
        path = os.path.join(root, d)
        if not os.path.isdir(path) or d.startswith(".") or d.lower() in ("analysis", "courses"):
            continue
        m = _SUFFIX.match(d)
        if m:
            base_raw, kind = m.group("base"), ("wko" if m.group("kind").lower() == "wko" else "fit")
            n = _count(path, kind)
        else:
            base_raw, kind, n = d, *_by_content(path)
            if not kind:
                continue
        base = (
            re.sub(r"[_\s]+", " ", base_raw).strip().lower()
        )  # "Athlete2_Pessoa_wko" and the WKO5 library folder "Athlete2 Pessoa" are one athlete
        a = found.setdefault(base, {"full_name": _nice(base_raw), "wko": None, "fit": None})
        if n >= best.get((base, kind), -1):
            a[kind] = d
            best[(base, kind)] = n
    firsts = {}
    for base, a in found.items():
        firsts.setdefault(a["full_name"].split()[0], []).append(base)
    out = {}
    for base, a in found.items():
        first = a["full_name"].split()[0]
        key = first if len(firsts[first]) == 1 else a["full_name"]
        a["key"] = key
        a.update(_profile(root, a))
        out[key] = a
    return out


def _count(path, kind):
    if kind == "wko":
        return len(glob.glob(os.path.join(path, "*.wko4")) + glob.glob(os.path.join(path, "*", "*.wko4")))
    return len(
        glob.glob(os.path.join(path, "**", "*.fit"), recursive=True)
        + glob.glob(os.path.join(path, "**", "*.FIT"), recursive=True)
    )


def _by_content(path):
    """(kind, n_files) of an unsuffixed folder: a WKO5 export has an athlete profile or yearly *.wko4 files."""
    n = _count(path, "wko")
    if n or glob.glob(os.path.join(path, "*.wko5athlete")):
        return "wko", n
    n = _count(path, "fit")
    return ("fit", n) if n else (None, 0)


def _profile(root, a):
    """Birth year and sex from the .wko5athlete file (fields 3033 = DOB ISO string, 3017 = sex)."""
    info = {"birth_year": None, "sex": None}
    if not a.get("wko"):
        return info
    files = sorted(glob.glob(os.path.join(root, a["wko"], "*.wko5athlete")))
    if not files:
        return info
    try:
        from wko_decode import load, top_level

        b, start = load(files[0])
        for fid, wt, v in top_level(b, start, stop_fields=(), want={3033, 3017}):
            if fid == 3033 and isinstance(v, str) and re.match(r"\d{4}-", v):
                info["birth_year"] = int(v[:4])
            elif fid == 3017 and isinstance(v, str) and v:
                info["sex"] = v
    except (Exception, SystemExit):
        pass
    return info


def fit_dirs():
    return {k: a["fit"] for k, a in discover().items() if a["fit"]}


def wko_dirs():
    return {k: a["wko"] for k, a in discover().items() if a["wko"]}


if __name__ == "__main__":
    print(json.dumps(discover(), indent=1, ensure_ascii=False))


@lru_cache(maxsize=1)
def overrides():
    """athlete_overrides.json: per-athlete values that cannot be read from the data (see the file)."""
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "athlete_overrides.json")
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"order": [], "athletes": {}}


def override(athlete, key, default=None):
    return overrides().get("athletes", {}).get(athlete, {}).get(key, default)

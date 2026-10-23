"""Pipeline step: FTP, mFTP and LTHR (run, bike) projected from the last two weeks of second-by-second data.

For an athlete coming back from an injury or a break, the thresholds in use are often months old. This reads the FIT files
of the last PROJ_DAYS days (the Intervals.icu downloads and the coach's FIT exports) and estimates, without a test:

  mFTP       the power-duration model (pd_model.py) fitted on the mean-max power of those days
  FTP        at least the best of mFTP, 0.95 x best 20 min and the best hour; when the best 20 min were ridden below the
             bike LTHR (steady heart rate), scaled up by the heart-rate reserve that was left, at most +10%
  LTHR run   the heart rate of the last minute of the hardest interval reps (3+ min), mean of the top two; a steady 20 min
             only gives a floor
  LTHR bike  the run one minus 5 bpm (bike heart rate runs lower), never below the hardest steady 20 min on the bike

Every value carries its evidence and a confidence; nothing is applied: the athlete or the coach applies it in the app
(userdata.thresholds), and build_dataset.py uses it from that date on. Output: <OUT>/data/threshold_projection.json.

Usage: python threshold_projection.py [Athlete ...]
"""

import os, sys, glob, gzip, json
from datetime import date, datetime, timedelta, timezone
import numpy as np, pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pd_model
from athletes import ROOT, fit_dirs, override

OUT = os.path.join(os.environ.get("TRIAPP_OUT", os.path.join(os.path.dirname(os.path.abspath(__file__)), "out")), "data")
PROJ_DAYS = int(os.environ.get("TRIAPP_PROJ_DAYS", "14"))
MMP_WINDOWS = [5, 10, 20, 30, 60, 120, 180, 300, 480, 600, 900, 1200, 1800, 2400, 3600]
BIKE_BELOW_RUN = 5  # bpm
FTP_HR_STRETCH = 1.10  # the heart-rate extrapolation adds at most 10% to what was ridden


def roll(x, w):
    x = np.nan_to_num(np.asarray(x, float))
    if len(x) < w:
        return np.array([])
    c = np.cumsum(np.insert(x, 0, 0))
    return (c[w:] - c[:-w]) / w


def read(path):
    """{start, sport, power, hr} at 1 s from a FIT file (gzip or not); None when it has no records."""
    from garmin_fit_sdk import Decoder, Stream

    raw = open(path, "rb").read()
    if path.endswith(".gz"):
        raw = gzip.decompress(raw)
    msgs, _ = Decoder(Stream.from_byte_array(bytearray(raw))).read()
    recs = msgs.get("record_mesgs") or []
    if not recs:
        return None
    df = pd.DataFrame(recs)
    if "timestamp" not in df:
        return None
    df = df.set_index(pd.to_datetime(df.timestamp)).resample("1s").mean(numeric_only=True)
    sport = str(((msgs.get("session_mesgs") or [{}])[0]).get("sport") or "")
    col = lambda c: df[c].to_numpy(float) if c in df and np.nanmax(df[c].fillna(0)) > 0 else None
    hr = col("heart_rate")
    if hr is not None:
        hr = np.where(hr > 60, hr, np.nan)
    return {"start": df.index[0], "sport": sport, "power": col("power"), "hr": hr}


def sessions(folder, since):
    """Sessions in the athlete's FIT folder that started on or after `since`, one per start time (the Intervals.icu download and
    the coach's export of the same ride count once: the one with power and heart rate wins)."""
    out = {}
    cutoff = since.timestamp() - 3 * 86400
    for f in glob.glob(os.path.join(folder, "**", "*.fit*"), recursive=True):
        if os.path.getmtime(f) < cutoff:
            continue
        try:
            s = read(f)
        except Exception:
            continue
        if not s or s["start"] < since:
            continue
        key = s["start"].floor("5min")
        score = (s["power"] is not None) + (s["hr"] is not None)
        if key not in out or score > out[key][0]:
            out[key] = (score, s)
    return [s for _, s in sorted(out.values(), key=lambda x: x[1]["start"])]


def hr_drift(hr):
    """bpm between the first and the last quarter of a block (a steady effort below threshold barely drifts)."""
    q = max(len(hr) // 4, 1)
    return float(np.nanmean(hr[-q:]) - np.nanmean(hr[:q]))


def bike(rides, lthr_bike, rest_hr):
    rides = [r for r in rides if r["power"] is not None and len(r["power"]) >= 600]
    if not rides:
        return {}
    best = {}
    for w in MMP_WINDOWS:
        vals = [(float(roll(r["power"], w).max()), r) for r in rides if len(r["power"]) >= w]
        if vals:
            best[w] = max(vals, key=lambda x: x[0])
    out = {"mmp": {str(w): round(v) for w, (v, _) in best.items()}}
    model = pd_model.fit(np.array(list(best), float), np.array([v for v, _ in best.values()], float))
    if model:
        out["mftp"] = {"value": model["mftp"], "tte_min": model["tte_min"], "pmax": model["pmax"], "frc_kj": model["frc_kj"]}
    ev = []
    floor = []
    if model:
        floor.append(model["mftp"])
        ev.append(f"Modelo potência-duração das últimas {PROJ_DAYS} dias: mFTP {model['mftp']} W, TTE {model['tte_min']} min.")
    if 1200 in best:
        p20, r = best[1200]
        floor.append(0.95 * p20)
        i = int(roll(r["power"], 1200).argmax())
        hr20 = r["hr"][i : i + 1200] if r["hr"] is not None else None
        cov = float(np.isfinite(hr20).mean()) if hr20 is not None else 0
        txt = f"Melhores 20 min: {p20:.0f} W ({r['start']:%d/%m})"
        if cov >= 0.8:
            h, drift = float(np.nanmean(hr20)), hr_drift(hr20)
            out["best20"] = {"w": round(p20), "hr": round(h), "drift": round(drift, 1), "date": f"{r['start']:%Y-%m-%d}"}
            txt += f", FC média {h:.0f} bpm, deriva {drift:+.0f} bpm."
        else:
            txt += ", sem FC."
        ev.append(txt)
    if 3600 in best:
        floor.append(best[3600][0])
    if not floor:
        return out
    ftp_floor = max(floor)
    value, conf, high = ftp_floor, "baixa", ftp_floor * 1.05
    b20 = out.get("best20")
    if b20 and lthr_bike and b20["drift"] <= 4 and b20["hr"] < lthr_bike:
        # ridden below threshold: the power the heart-rate reserve that was left would add, at most +10%
        stretch = min((lthr_bike - rest_hr) / (b20["hr"] - rest_hr), FTP_HR_STRETCH)
        up = b20["w"] * stretch
        value, conf, high = (ftp_floor + max(up, ftp_floor)) / 2, "média", max(up, ftp_floor)
        ev.append(
            f"Esses 20 min foram a {b20['hr']} bpm, abaixo do limiar da bike ({lthr_bike:.0f}) e sem deriva: o limiar estará até "
            f"{up:.0f} W."
        )
    elif b20 and b20["drift"] > 4 and b20["hr"] >= (lthr_bike or 999) - 3:
        value, conf, high = ftp_floor, "média", ftp_floor * 1.03  # a hard, drifting 20 min near threshold: close to the value
    if conf == "baixa":
        ev.append("Sem um esforço máximo nem FC que o situe: é um mínimo; um teste de 20 min confirma.")
    out["ftp"] = {"value": round(value), "low": round(ftp_floor), "high": round(high), "confidence": conf, "evidence": ev}
    return out


def reps(hr, min_len=120):
    """Last-minute heart rate of each hard block (a 3+ min rep: heart rate lags, so 2+ min above the cut). Cuts from 20 to 3 bpm
    below the session's peak are tried; the one that splits the session into the most blocks wins, the higher on a tie (too
    low merges reps with their recoveries, too high splits a rep or drops the first ones)."""
    s = pd.Series(hr).rolling(30, center=True, min_periods=15).mean().to_numpy()
    top = np.nanmax(s)
    best = []
    for cut in np.arange(top - 20, top - 2, 1.0):
        found = _blocks(hr, s, cut, min_len)
        if len(found) >= len(best):
            best = found
    return best


def _blocks(hr, s, above, min_len):
    on = s > above
    out, i = [], 0
    while i < len(on):
        if on[i]:
            j = i
            while j < len(on) and on[j]:
                j += 1
            end = float(np.nanmean(hr[j - 60 : j]))
            after = s[j : j + 120]
            # a rep ends in a recovery: heart rate falls 8+ bpm within 2 min (a steady run drifting around the cut does not)
            if j - i >= min_len and len(after) >= 60 and np.nanmin(after) <= end - 8:
                out.append(end)
            i = j
        i += 1
    return out


def run(runs):
    runs = [r for r in runs if r["hr"] is not None and np.isfinite(r["hr"]).sum() >= 900]
    if not runs:
        return {}
    best20 = max(
        ((float(pd.Series(r["hr"]).rolling(1200, min_periods=1080).mean().max()), r) for r in runs if len(r["hr"]) >= 1200),
        key=lambda x: x[0] if x[0] == x[0] else 0,
        default=(None, None),
    )
    ends = sorted((h for r in runs for h in reps(r["hr"])), reverse=True)
    ev = []
    if best20[0]:
        ev.append(f"Melhor FC média de 20 min a correr: {best20[0]:.0f} bpm ({best20[1]['start']:%d/%m}): é um mínimo.")
    if len(ends) >= 2:
        value = (ends[0] + ends[1]) / 2
        ev.append(f"FC no último minuto das repetições mais duras (3+ min): " + ", ".join(f"{h:.0f}" for h in ends[:4]) + " bpm.")
        value = max(value, best20[0] or 0)
        return {
            "lthr_run": {
                "value": round(value),
                "low": round(value - 3),
                "high": round(value + 3),
                "confidence": "média",
                "evidence": ev,
            }
        }
    if best20[0]:
        return {
            "lthr_run": {
                "value": None,
                "low": round(best20[0]),
                "high": None,
                "confidence": "baixa",
                "evidence": ev + ["Sem repetições duras: só se sabe o mínimo."],
            }
        }
    return {}


def project(athlete, folder, today=None, in_use=None):
    today = today or date.today()
    since = pd.Timestamp(today - timedelta(days=PROJ_DAYS), tz="UTC")
    S = sessions(folder, since)
    in_use = in_use or {}
    rest = float(override(athlete, "resting_hr", 60))
    out = {"athlete": athlete, "from": str(since.date()), "to": str(today), "sessions": len(S), "in_use": in_use}
    r = run([s for s in S if s["sport"] == "running"])
    out.update(r)
    lthr_run = (r.get("lthr_run") or {}).get("value") or in_use.get("lthr")
    rides = [s for s in S if s["sport"] == "cycling"]
    floor_bike = [
        float(pd.Series(x["hr"]).rolling(1200, min_periods=1080).mean().max())
        for x in rides
        if x["hr"] is not None and len(x["hr"]) >= 1200
    ]
    floor_bike = max([f for f in floor_bike if f == f], default=None)
    if r.get("lthr_run") and lthr_run:  # only from a run projection: the old run LTHR minus 5 would project nothing new
        lb = max(lthr_run - BIKE_BELOW_RUN, floor_bike or 0)
        ev = [
            f"LTHR da corrida ({lthr_run:.0f}) menos {BIKE_BELOW_RUN} bpm: a FC na bike fica abaixo da corrida no mesmo esforço."
        ]
        if floor_bike:
            ev.append(f"Nunca abaixo da FC média dos 20 min mais duros na bike: {floor_bike:.0f} bpm.")
        out["lthr_bike"] = {
            "value": round(lb),
            "low": round(lb - 3),
            "high": round(lb + 3),
            "confidence": "média" if r.get("lthr_run") else "baixa",
            "evidence": ev,
        }
    out.update(bike(rides, (out.get("lthr_bike") or {}).get("value") or in_use.get("lthr_bike"), rest))
    return out


def main(only=None):
    dash = {}
    try:
        dash = json.load(open(os.path.join(OUT, "dashboard_data.json"))).get("athletes", {})
    except Exception:
        pass
    result = {}
    for athlete, folder in fit_dirs().items():
        if only and athlete not in only:
            continue
        A = dash.get(athlete, {})
        in_use = {
            "ftp": A.get("ftp"),
            "lthr": A.get("lthr"),
            "lthr_bike": A.get("lthr_bike"),
            "mftp": ((A.get("power_duration") or {}).get("model") or {}).get("mftp"),
        }
        try:
            p = project(athlete, os.path.join(ROOT, folder), in_use=in_use)
        except Exception as e:
            print(f"threshold projection {athlete}: {type(e).__name__} {e}")
            continue
        if p["sessions"]:
            result[athlete] = p
            print(
                f"threshold projection {athlete}: {p['sessions']} sessions, FTP {(p.get('ftp') or {}).get('value')}, "
                f"mFTP {(p.get('mftp') or {}).get('value')}, LTHR run {(p.get('lthr_run') or {}).get('value')}, "
                f"bike {(p.get('lthr_bike') or {}).get('value')}"
            )
    result["_generated"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with open(os.path.join(OUT, "threshold_projection.json"), "w") as f:
        json.dump(result, f, ensure_ascii=False, indent=1)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:] or None))

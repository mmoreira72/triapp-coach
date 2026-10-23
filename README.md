# triapp · Tri Squad

Training analysis for a triathlon squad and its coach: reads what comes out of TrainingPeaks (TP), Garmin and
Intervals.icu, computes load, fitness, session types and race predictions, and serves one page per athlete at
tri.laloop.ai. Architecture diagram: https://claude.ai/artifact/4g7uQ4Susn4k3sVsMzVgqN

## How it fits together

Sources → pipeline (scripts that talk through CSV/JSON files in `TRIAPP_OUT/data`) → FastAPI app that renders them.
Everything runs in one Docker container on the Beelink; the LLM is the host's Ollama.

| Step (in order) | Reads | Writes |
|---|---|---|
| `rclone copy` (in `app/pipeline.py`) | the coach's Drive folder | `TRIAPP_ROOT/<Athlete>_wko`, `<Athlete>_Fit_Files` |
| `intervals_sync.py` | Intervals.icu API, per-athlete key | `intervals-*.fit`, `intervals_thresholds.json`, `intervals_activities.json` |
| `parse_fit.py` | FIT files | `activities.csv`, `laps.csv` |
| `parse_wko.py` | WKO5 bundles (`wko_decode.py`) | `wko_index.csv`, `wko_laps.csv` |
| `build_dataset.py` | the four files above | `activities_all.csv`, `ingest_ledger.csv` |
| `reconcile.py` | disk, Drive listing, Intervals API, ledger | `reconciliation.json` (shown in `/admin`) |
| `build_dashboard_data.py` | `activities_all.csv`, laps | `dashboard_data.json` |
| `race_impact.py` | `courses/*/race.json`, GPX, weather | `race_impact.json` |
| `skills_engine.py` (LLM for the narrative) | the JSON above, context in `app.sqlite` | `analysis.json` |
| `session_notes.py` (LLM) | `dashboard_data.json` | `session_notes.json` |
| `wko_planned.py` | `activities_all.csv` (WKO plan rows) | `intervals_planned.json` for athletes without a TP calendar link |
| `tp_calendar.py` | TrainingPeaks iCal feed (`TRIAPP_TP_ICAL_<Athlete>`) | `intervals_planned.json` (coach's plan, yesterday to 2 weeks ahead) |
| `morning_brief.py` (LLM) | recent sessions, notes, `intervals_planned.json` | `daily_brief.json` (pushed at 07:30) |
| `notify.py` (push) | `session_notes.json`, `analysis.json` | APNs alerts to the native app (`native/`) |

The app runs the pipeline daily at 05:00 (`TRIAPP_DAILY_AT`) and from the Sync button in `/admin`. Only the last two steps
call the LLM, and they only write prose from numbers the earlier steps computed.

Shared rules live in one place each:

| Module | What |
|---|---|
| `zones.py` | heart-rate (Friel) and power (Coggan) zones |
| `matching.py` | "is it the same session?" (start time, or day + duration) |
| `session_types.py` | session type from time in zones |
| `execution.py` | planned vs executed: blocks from laps, target from the coach's title |
| `llm.py` | Ollama call and output clean-up |
| `athletes.py` | athlete discovery from the folders; `athlete_overrides.json` for values the data cannot give |

## Running it

```bash
# tests (no athlete data needed)
pip install -r requirements.txt -r requirements-app.txt -r requirements-dev.txt
pytest

# pipeline outside the app
TRIAPP_ROOT=<folder with the athlete folders> TRIAPP_OUT=out ./run_all.sh

# app, local
TRIAPP_OUT=out TRIAPP_ROOT=<data> TRIAPP_COOKIE_SECURE=0 uvicorn app.main:app --port 8010

# Beelink: the image bakes the code in, so a git pull alone changes nothing
cd ~/triapp && git pull --ff-only && docker compose up -d --build
docker compose exec triapp python -m app.manage create-user athlete@email "Name" athlete <AthleteKey>
```

Configuration is environment variables (`docker-compose.yml`, `.env` on the box): `TRIAPP_RCLONE_REMOTE`, `TRIAPP_LLM_URL`,
`TRIAPP_LLM_MODEL`, `TRIAPP_INTERVALS_KEY_<Athlete>` / `TRIAPP_INTERVALS_ID_<Athlete>`, `TRIAPP_DAILY_AT`.
Formatting: `black .` (line length 130); `dashboard_template.html` with `npx prettier --parser html --print-width 140`.

## Web app (`app/`)

FastAPI with its own login (argon2 hashes in SQLite) and two roles:

- **coach**: `/board` (team and every athlete), `/admin` (sync, users, reconciliation report).
- **athlete**: only `/board/<own athlete>`; the page never receives other athletes' data.

`render.py` fills `dashboard_template.html` (one self-contained page: CSS, charts, tabs) with the JSON of the pipeline.
`assistant.py` holds the athlete context (injury, break) and the Q&A grounded in the athlete's facts and `skills/*.md`.
The page is installable (PWA: `app/static/`).

## Data rules worth knowing

- **Athletes** are the folders under `TRIAPP_ROOT`: `<First>_<Last>_wko`, `<First>_<Last>_Fit_Files`, or an unsuffixed folder
  classified by its content. The Drive copy never deletes: remove an athlete by deleting the folder on the box.
- **Done** = any trace of execution in TP: attached file, actual duration or TP TSS (see `wko_done`).
- **One copy per session**: a FIT that matches a WKO entry with its file is dropped; a WKO entry without its file gives way
  to the FIT of the same session; of the same FIT from TP and Intervals, the Intervals copy stays (it has the title).
  Every source item gets one fate in `ingest_ledger.csv`; `reconcile.py` checks nothing is unaccounted for.
- **Thresholds per day**: bike FTP backed out of TP's TSS by quarter (`FTP = NP × √(h×100/TSS)`); run LTHR from the device
  setting; bike LTHR fitted from steady rides with power; Intervals.icu settings win from the day they are seen.
- **TSS**: TP's own when present; else power (`h × (NP/FTP)² × 100`), heart rate (TRIMP from the time at each HR, one hour at
  LTHR = 100), or a default per hour. A TP TSS implying IF > 1.15 for 20+ min is replaced by the estimate ("hr-corrected").
- **Dead power meter** (power > 0 in under 40% of the moving time): power dropped, TSS from heart rate.
- **Races** recorded as separate files (bike computer + watch) are chained when each leg starts within 20 min of the
  previous one's end.
- **Skills**: nine rule-based skills (`skills/*.md`, sources in `reports/`); registered context turns load and consistency
  alarms into context.

## Source formats

**FIT** (parsed with `garmin-fit-sdk`): per-second heart rate, power, speed, cadence, GPS, laps, the workout pushed to the
watch and the device settings (FTP, threshold HR). Multisport files hold one session per leg. `parse_fit.py` patches the
SDK to read TrainingPeaks Virtual files (zero-size developer fields); the SDK version is pinned for that reason.

**WKO4 files** are a proprietary binary but the layout was decoded (see `wko_decode.py`):
magic `wko4` + `0x1a` + a protobuf-like stream of `varint key` + value, where `key >> 3` is the
field id and `key & 7` the wire type (0/1/4 varint, 2 float64, 3 string/bytes, 6 nested message).
Useful top-level fields:

| Field | Meaning |
|---|---|
| 4020 / 4003 | Coach's workout title / description (the prescription, e.g. `10x600 A2 I=1'`) |
| 4005 | TP sport (`Run`, `Road Bike`, `Indoor Bike`, `Swim`, `Strength`, `Day Off`, …) |
| 4044 | Local date-time of the entry |
| 4035 / 4036 / 4037 | Planned TSS / duration (s) / distance (km) |
| 4038 / 4039 / 4055 / 4053 | TP's TSS / duration (s) / distance (km) / work (J) of the completed session |
| 4046 | 2 = a recorded file is attached. **Not** TP's completed flag: sessions completed by hand or synced without their file have 0 |
| 4049 / 4050 | **The original device file, embedded** (bytes) / its format (`fit`, `pwx`, `gpx`, `tcx`) |
| 4009 / 4061 | Weight, height in the TP profile |
| 4010 / 4011 / 4012 / 4063 | WKO defaults (250 W / 180 / 160 bpm / pace) — identical for every athlete, do not use |


The `.wko5athlete` file holds the profile (name, date of birth, sex).

## Known gaps

- TCX, PWX and GPX files are indexed but not parsed (a handful, mostly 2017-2019).
- Time zone assumed Europe/Lisbon for FIT timestamps (WKO gives local time).
- What TP has but the coach's WKO5 did not export cannot be detected; `reconcile.py` only warns when an athlete's last
  session is more than 7 days old.
- `race_impact.py` and `build_dashboard_data.py` are still long scripts without tests.

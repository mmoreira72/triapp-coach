"""Render the dashboard template with the data slice a user is allowed to see."""

import json, os, html, sys
from datetime import datetime
from . import config, demo

sys.path.insert(0, str(config.REPO_DIR))
import voice, health, userdata  # noqa: E402  (repo root, shared with the pipeline)

TEMPLATE = config.REPO_DIR / "dashboard_template.html"
_cache = {"mtime": None, "data": None}


ANALYSIS_JSON = config.DATA_JSON.parent / "analysis.json"
RACE_JSON = config.DATA_JSON.parent / "race_impact.json"
NOTES_JSON = config.DATA_JSON.parent / "session_notes.json"
BRIEF_JSON = config.DATA_JSON.parent / "daily_brief.json"
PLANNED_JSON = config.DATA_JSON.parent / "intervals_planned.json"
PROJ_JSON = config.DATA_JSON.parent / "threshold_projection.json"


def load_data():
    m = tuple(
        os.path.getmtime(p) if p.exists() else 0
        for p in (config.DATA_JSON, ANALYSIS_JSON, RACE_JSON, NOTES_JSON, BRIEF_JSON, PLANNED_JSON, PROJ_JSON)
    )
    if _cache["mtime"] != m:
        d = json.loads(config.DATA_JSON.read_text(encoding="utf-8"))
        d["analysis"] = json.loads(ANALYSIS_JSON.read_text(encoding="utf-8")) if ANALYSIS_JSON.exists() else {}
        d["races_target"] = json.loads(RACE_JSON.read_text(encoding="utf-8")) if RACE_JSON.exists() else {}
        notes = json.loads(NOTES_JSON.read_text(encoding="utf-8")) if NOTES_JSON.exists() else {}
        for a, A in d.get("athletes", {}).items():  # session benefit texts (session_notes.py) onto the session lists
            for s in A.get("recent", []) + A.get("executions", []):
                n = notes.get(f"{a}|{s.get('key')}")
                if n:
                    s["note"] = n["text"]
                    if n.get("text_voice"):
                        s["note_voice"] = n["text_voice"]
        # the coach's plan for the next days (Intervals.icu calendar) and the morning brief (morning_brief.py)
        planned = json.loads(PLANNED_JSON.read_text(encoding="utf-8")) if PLANNED_JSON.exists() else {}
        briefs = json.loads(BRIEF_JSON.read_text(encoding="utf-8")) if BRIEF_JSON.exists() else {}
        proj = json.loads(PROJ_JSON.read_text(encoding="utf-8")) if PROJ_JSON.exists() else {}
        for a, A in d.get("athletes", {}).items():
            A["planned"] = planned.get(a, [])
            if proj.get(a):  # thresholds projected from the last two weeks (threshold_projection.py)
                A["threshold_projection"] = proj[a]
            if briefs.get(a):
                A["brief"] = briefs[a]
        _cache["data"] = d
        _cache["mtime"] = m
    return _cache["data"]


def data_mtime():
    try:
        return datetime.fromtimestamp(os.path.getmtime(config.DATA_JSON)).strftime("%Y-%m-%d %H:%M")
    except OSError:
        return None


def slice_for(athletes):
    d = load_data()
    if athletes is None:
        return d
    if athletes == [demo.DEMO]:
        return demo.slice_for(d)
    out = {
        "generated": d["generated"],
        "athletes": {a: d["athletes"][a] for a in athletes if a in d["athletes"]},
        "notes": {a: d.get("notes", {}).get(a, []) for a in athletes},
        "analysis": {a: d.get("analysis", {}).get(a) for a in athletes if d.get("analysis", {}).get(a)},
        "races_target": {
            slug: {
                **{k: v for k, v in r.items() if k != "athletes"},
                "athletes": {a: r["athletes"][a] for a in athletes if a in r.get("athletes", {})},
            }
            for slug, r in d.get("races_target", {}).items()
        },
    }
    return out  # no team section, no insights about other athletes


PWA_HEAD = (
    '<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">'
    '<link rel="manifest" href="/manifest.webmanifest"><meta name="theme-color" content="#111311">'
    '<link rel="icon" type="image/png" sizes="32x32" href="/static/icons/favicon-32.png">'
    '<link rel="apple-touch-icon" href="/static/icons/apple-touch-icon.png">'
    '<meta name="apple-mobile-web-app-capable" content="yes"><meta name="mobile-web-app-capable" content="yes">'
    '<meta name="apple-mobile-web-app-title" content="Tri Squad"><meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">'
    "<script>if('serviceWorker' in navigator) addEventListener('load', () => navigator.serviceWorker.register('/sw.js').then(()=>console.log('tri sw ok'),e=>console.log('tri sw error',String(e))));"
    # an installed app is not reloaded when it comes back to the screen: reload after 10+ min in the background
    "(()=>{let hid=0;document.addEventListener('visibilitychange',()=>{if(document.hidden){hid=Date.now();}"
    "else if(hid&&Date.now()-hid>600000&&navigator.onLine!==false){location.reload();}});})();</script>"
)


# Inside the native app (native/, Capacitor) the page has window.Capacitor: register for push and send the token to the box;
# tapping a notification opens its url. In a browser or the PWA this does nothing.
NATIVE_JS = (
    "<script>(()=>{const C=window.Capacitor;if(!C||!C.isNativePlatform||!C.isNativePlatform())return;"
    "document.documentElement.classList.add('native');"
    "const P=(C.Plugins&&C.Plugins.PushNotifications)||(C.registerPlugin&&C.registerPlugin('PushNotifications'));if(!P)return;"
    "P.addListener('registration',t=>fetch('/api/push/register',{method:'POST',headers:{'Content-Type':'application/json'},"
    "body:JSON.stringify({token:t.value,platform:C.getPlatform()})}).catch(()=>{}));"
    "P.addListener('pushNotificationActionPerformed',a=>{const u=a.notification&&a.notification.data&&a.notification.data.url;"
    # the page is already loading when the app opens from a notification: navigate only to a different page, never to "/"
    # (a second load cancels the first one, which the service worker reads as being offline)
    "if(u&&u.startsWith('/')&&u!=='/'&&u!==location.pathname)location.href=u;});"
    "P.checkPermissions().then(p=>p.receive==='prompt'?P.requestPermissions():p).then(p=>{if(p.receive==='granted')P.register();})"
    ".catch(()=>{});})();</script>"
)
# The athlete's own Apple Health days, sent at most every 6 h (the plugin lives in native/ios/App/App/HealthPlugin.swift)
HEALTH_JS = (
    "<script>(()=>{const C=window.Capacitor,A=__ATHLETE__;if(!A||!C||!C.isNativePlatform||!C.isNativePlatform())return;"
    "const H=(C.Plugins&&C.Plugins.Health)||new Proxy({},{get:(_,m)=>o=>C.nativePromise('Health',m,o||{})});"
    "let last=0;try{last=+localStorage.getItem('tri-health-sync-v2')||0}catch(e){}if(Date.now()-last<216e5)return;"
    "H.isAvailable().then(r=>r.available?H.requestAuthorization():Promise.reject()).then(()=>H.daily({days:30}))"
    ".then(r=>r.days&&r.days.length?fetch('/api/health/'+encodeURIComponent(A),{method:'POST',headers:{'Content-Type':'application/json'},"
    "body:JSON.stringify({days:r.days})}):Promise.reject('Apple Health sem dados ou sem permissão'))"
    # only a sync that brought data waits 6 h; an empty read (access off) tries again on the next open
    ".then(r=>{if(r.ok)try{localStorage.setItem('tri-health-sync-v2',String(Date.now()))}catch(e){}})"
    ".catch(e=>console.log('tri health error',String(e)));})();</script>"
)


ROLE = {"athlete": "atleta", "coach": "treinador"}


def with_user_entries(data, user):
    """Per-request entries from the app database: races marked by hand, race reports, missed sessions with their reasons."""
    data["athletes"] = dict(data.get("athletes", {}))  # the cached data is shared between requests: never change it in place
    for a, A in list(data["athletes"].items()):
        src = demo.source(a)
        if a == demo.DEMO:
            continue  # the demo shows the data only
        marks = {m["session_key"]: m for m in userdata.race_marks(src)}
        A = data["athletes"][a] = dict(A)
        A["recent"] = [dict(s, race=s.get("key") in marks) for s in A.get("recent", [])]
        A["marked_races"] = list(marks.values())
        A["race_reports"] = userdata.race_reports(src)
        A["missed"] = userdata.missed_with_reasons(src, A.get("planned", []))
        A["comments"] = userdata.comments(src)
        A["thresholds_set"] = userdata.thresholds(src)
    data["races_target"] = with_nutrition(data.get("races_target") or {}, data["athletes"])
    return data


def with_coach_if(P, bike_if):
    """The prediction with the bike IF the coach chose: power targets, bike time and total follow the chosen option."""
    bike = P.get("bike") or {}
    opts = dict(bike.get("options") or {})
    key = f"{bike_if:.2f}" if bike_if else None
    if not key or not bike.get("recommended_if"):
        return P
    if key not in opts:  # between two simulated options: power and time interpolated (close enough within a few hundredths)
        xs = sorted(float(k) for k in opts)
        lo = max((x for x in xs if x < bike_if), default=None)
        hi = min((x for x in xs if x > bike_if), default=None)
        if lo is None or hi is None:
            return P
        a, b, f = opts[f"{lo:.2f}"], opts[f"{hi:.2f}"], (bike_if - lo) / (hi - lo)
        mix = lambda k: (a.get(k) or 0) + ((b.get(k) or 0) - (a.get(k) or 0)) * f
        h = mix("hours")
        opts[key] = {
            "target_w": round(mix("target_w")),
            "np": round(mix("np")),
            "avg_w": round(mix("avg_w")),
            "hours": round(h, 3),
            "time": f"{int(h)}:{round((h - int(h)) * 60):02d}",
            "kmh": round(mix("kmh"), 1),
            "interpolated": True,
        }
        P = {**P, "bike": {**bike, "options": opts}}
        bike = P["bike"]
    old, new = opts.get(f"{bike['recommended_if']:.2f}") or {}, opts[key]
    P = {**P, "bike": {**bike, "recommended_if": bike_if, "model_if": bike["recommended_if"], "coach_if": True}}
    P["bike"].update(flat_w=new["target_w"], climb_cap_w=round(new["target_w"] * 1.10), descent_w=round(new["target_w"] * 0.6))
    tot = P.get("total") or {}
    if tot.get("hours") and old.get("hours") and new.get("hours"):
        h = tot["hours"] - old["hours"] + new["hours"]
        d = h - tot["hours"]
        fmt = lambda x: f"{int(x)}:{round((x - int(x)) * 60):02d}"
        rng = tot.get("range")
        P["total"] = {**tot, "hours": round(h, 3), "time": fmt(h)}
        if rng and tot.get("range_h"):
            P["total"]["range_h"] = [x + d for x in tot["range_h"]]
        if rng:  # shift the range strings by the same difference

            def shift(t):
                hh, mm = t.split(":")
                return fmt(int(hh) + int(mm) / 60 + d)

            P["total"]["range"] = [shift(t) for t in rng]
    return P


def with_nutrition(races, athletes):
    """Each athlete's fuelling plan for each target race (nutrition.py), with the coach's numbers and advice from the app
    database. Per request: the coach's edits show at once."""
    import nutrition

    out = {}
    for slug, R in races.items():
        R = out[slug] = dict(R)
        w = R.get("weather") or {}
        site = w.get(w.get("main_site")) or {}
        temp = (site.get("temp_c_p10_p50_p90") or [None, None])[1]
        R["athletes"] = dict(R.get("athletes") or {})
        for a, P in R["athletes"].items():
            if a == demo.DEMO or not isinstance(P, dict) or P.get("error"):
                continue
            try:
                choice = userdata.race_plans(demo.source(a)).get(demo.source(a), {}).get(slug) or {}
                P = R["athletes"][a] = with_coach_if({**P, "plan": choice}, choice.get("bike_if"))
                over = userdata.race_nutrition(demo.source(a)).get(slug) or {}
                R["athletes"][a] = {
                    **P,
                    "nutrition": nutrition.plan(P, R.get("race") or {}, temp, (athletes.get(a) or {}).get("weight_kg"), over),
                }
            except Exception as e:
                print(f"nutrition {slug} {a}: {type(e).__name__} {e}")
    return out


def page(user, athletes):
    if not config.DATA_JSON.exists():
        return "<title>Tri Squad</title><p style='font-family:system-ui;padding:40px'>No data yet. The coach needs to run the first sync from /admin.</p>"
    tpl = TEMPLATE.read_text(encoding="utf-8")
    data = dict(slice_for(athletes))
    data["viewer"] = {"role": user["role"], "athlete": user.get("athlete"), "name": user["name"]}
    data["health"] = {a: health.recent(a) for a in data["athletes"]}  # Apple Health (native app), read per request
    data = with_user_entries(data, user)
    data["reasons"] = userdata.REASONS
    if user["role"] == "athlete":  # the athlete reads their own voice (voice.py); the coach always reads the neutral texts
        data["viewer"]["voice"] = voice.get(user["athlete"])
        import athletes as _ath  # repo root: home coordinates for the widget's sunset (athlete_overrides.json)

        data["viewer"]["home"] = _ath.override(user["athlete"], "home")
        data["voices"] = {k: {"label": v["label"], "hint": v["hint"]} for k, v in voice.VOICES.items()}
    raw = json.dumps(data, separators=(",", ":")).replace("</", "<\\/")
    links = ""
    if user["role"] == "coach":
        names = config.athletes()
        # more than a handful of athletes: a dropdown instead of a link each
        pick = (
            '<select aria-label="Atleta" onchange="if(this.value)location.href=\'/board/\'+encodeURIComponent(this.value)">'
            '<option value="">Atleta…</option>'
            + "".join(f'<option value="{html.escape(a)}">{html.escape(a)}</option>' for a in names)
            + "</select>"
            if len(names) > 3
            else "".join(f'<a href="/board/{a}">{html.escape(a)}</a>' for a in names)
        )
        links = '<a href="/board">Equipa</a>' + pick + '<a href="/admin">Admin</a>'

    if user["role"] == "athlete" and user.get("athlete") != demo.DEMO:
        links = '<a href="/ligacoes">Ligações</a>'
    userbar = (
        f'<div class="userbar"><span>{html.escape(user["name"])} · {ROLE.get(user["role"], user["role"])}</span><nav>{links}</nav>'
        f'<form method="post" action="/logout"><button type="submit" class="ghost">Sair</button></form></div>'
    )
    css = (
        "<style>.userbar{display:flex;flex-wrap:wrap;gap:8px 16px;align-items:center;justify-content:space-between;font-size:13px;"
        "color:var(--ink-2);padding:8px 0;border-bottom:1px solid var(--grid);max-width:1240px;margin:0 auto}"
        ".userbar nav{display:flex;gap:12px;flex-wrap:wrap}.userbar a{color:var(--ink);text-decoration:none;font-weight:500}"
        ".userbar form{margin:0}.userbar select{font:inherit;font-size:13px;padding:2px 6px;border-radius:6px;border:1px solid var(--grid);"
        "background:var(--surface);color:var(--ink)}</style>"
    )
    out = tpl.replace("/*__DATA__*/", raw).replace(
        "/*__BOOT__*/", "startApp(JSON.parse(document.getElementById('dash-data').textContent));"
    )
    out = out.replace("<!--__USERBAR__-->", css + userbar, 1)
    hjs = HEALTH_JS.replace("__ATHLETE__", json.dumps(user.get("athlete") if user["role"] == "athlete" else None))
    out = out.replace("<!--__HEAD__-->", PWA_HEAD + NATIVE_JS + hjs, 1)
    return out

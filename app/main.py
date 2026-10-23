"""Tri Squad app: login, role-based dashboard rendering, Drive sync + pipeline trigger.

Roles: coach (sees the team and every athlete, runs sync, manages users) and athlete (sees only their own page).
"""

import json, os, re, time, urllib.parse
from datetime import date
from pathlib import Path
from fastapi import FastAPI, Request, Form, HTTPException, Depends, UploadFile, File, Body
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from itsdangerous import TimestampSigner, BadSignature, SignatureExpired

from . import config, auth, pipeline, render

app = FastAPI(title="Tri Squad", docs_url=None, redoc_url=None)


@app.on_event("startup")
def _daily_refresh():
    pipeline.start_daily()


templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
templates.env.globals["pwa_head"] = render.PWA_HEAD
STATIC = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=str(STATIC)), name="static")


@app.get("/sw.js", include_in_schema=False)
def service_worker():
    return FileResponse(
        STATIC / "sw.js",
        media_type="application/javascript",
        headers={"Cache-Control": "no-cache", "Service-Worker-Allowed": "/"},
    )


@app.get("/manifest.webmanifest", include_in_schema=False)
def manifest():
    return FileResponse(STATIC / "manifest.webmanifest", media_type="application/manifest+json")


signer = TimestampSigner(config.SECRET)
NO_STORE = {"Cache-Control": "no-store"}  # board pages carry the latest data: never serve a browser-cached copy
SESSION_MAX_AGE = 60 * 60 * 24 * 60  # 60 days: the installed app should not ask for the password every two weeks


def current_user(request: Request):
    tok = request.cookies.get("tri_session")
    if not tok:
        return None
    try:
        uid = signer.unsign(tok, max_age=SESSION_MAX_AGE).decode()
    except (BadSignature, SignatureExpired):
        return None
    return auth.get_user(int(uid))


def require_user(request: Request):
    u = current_user(request)
    if not u:
        raise HTTPException(status_code=307, headers={"Location": "/login?next=" + request.url.path})
    return u


def require_coach(user=Depends(require_user)):
    if user["role"] != "coach":
        raise HTTPException(status_code=403, detail="Coach only")
    return user


@app.get("/health")
def health():
    return {"ok": True, "data": os.path.exists(config.DATA_JSON), "pipeline": pipeline.status().get("state")}


@app.get("/sobre", response_class=HTMLResponse)
@app.get("/privacidade", response_class=HTMLResponse)
@app.get("/suporte", response_class=HTMLResponse)
def info(request: Request):
    """Public pages (no login): about, privacy policy and support, the URLs the App Store asks for."""
    page = request.url.path.strip("/")
    titles = {"sobre": "sobre", "privacidade": "privacidade", "suporte": "suporte"}
    return templates.TemplateResponse(
        request,
        "info.html",
        {
            "page": page,
            "title": titles[page],
            "updated": "15 de outubro de 2026",
            "email": os.environ.get("TRIAPP_SUPPORT_EMAIL", ""),
            "pwa_head": render.PWA_HEAD,
        },
    )


@app.get("/login", response_class=HTMLResponse)
def login_form(request: Request, next: str = "/", error: str = ""):
    return templates.TemplateResponse(request, "login.html", {"next": next, "error": error})


@app.post("/login")
def login(request: Request, email: str = Form(...), password: str = Form(...), next: str = Form("/")):
    user = auth.verify(email, password)
    if not user:
        time.sleep(1.0)  # slow down guessing
        return RedirectResponse(f"/login?error=1&next={next}", status_code=303)
    resp = RedirectResponse(next if next.startswith("/") else "/", status_code=303)
    resp.set_cookie(
        "tri_session",
        signer.sign(str(user["id"])).decode(),
        max_age=SESSION_MAX_AGE,
        httponly=True,
        samesite="lax",
        secure=config.COOKIE_SECURE,
    )
    return resp


@app.post("/logout")
def logout():
    resp = RedirectResponse("/login", status_code=303)
    resp.delete_cookie("tri_session")
    return resp


@app.get("/", response_class=HTMLResponse)
def home(request: Request, user=Depends(require_user)):
    if user["role"] == "coach":
        return RedirectResponse("/board", status_code=303)
    if not user.get("athlete"):  # signed up, waiting for the coach to associate the account
        return _pending(request, user, waiting_coach=True)
    q = request.url.query  # keep ?resposta=<id> from the "Resposta pronta" notification
    return RedirectResponse(f"/board/{user['athlete']}" + (f"?{q}" if q else ""), status_code=303)


def _pending(request, user, waiting_coach):
    return templates.TemplateResponse(
        request,
        "pending.html",
        {"name": user["name"].split()[0], "waiting_coach": waiting_coach, "pwa_head": render.PWA_HEAD},
        headers=NO_STORE,
    )


_signups = {}  # ip -> recent sign-up times (a light brake on scripted sign-ups)


def _reset_page(request, step, **kw):
    return templates.TemplateResponse(request, "reset.html", {"step": step, "pwa_head": render.PWA_HEAD, **kw}, headers=NO_STORE)


@app.get("/recuperar", response_class=HTMLResponse)
def forgot_form(request: Request):
    return _reset_page(request, "ask")


@app.post("/recuperar", response_class=HTMLResponse)
def forgot(request: Request, email: str = Form("")):
    """Email a one-hour reset link. The answer is the same whether the account exists or not."""
    import mailer

    tok = auth.create_reset(email)
    if tok:
        u = next((x for x in auth.list_users() if x["email"] == email.strip().lower()), None)
        mailer.send(
            email.strip().lower(),
            "Tri Squad: nova palavra-passe",
            f"Olá {(u or {}).get('name', '').split(' ')[0]},\n\nPediste para mudar a palavra-passe da tua conta Tri Squad. "
            "O link abaixo vale durante 1 hora e só pode ser usado uma vez.\n\nSe não foste tu, ignora este email: a palavra-passe não muda.",
            ("Escolher palavra-passe nova", f"{mailer.PUBLIC_URL}/repor?t={tok}"),
        )
    else:
        time.sleep(0.5)  # same timing either way
    return _reset_page(request, "sent")


@app.get("/repor", response_class=HTMLResponse)
def reset_form(request: Request, t: str = ""):
    u = auth.reset_user(t)
    return _reset_page(request, "new", token=t, email=u["email"]) if u else _reset_page(request, "invalid")


@app.post("/repor", response_class=HTMLResponse)
def reset(request: Request, t: str = Form(""), password: str = Form(""), password2: str = Form("")):
    u = auth.reset_user(t)
    if not u:
        return _reset_page(request, "invalid")
    if len(password) < 8 or password != password2:
        err = "A palavra-passe tem de ter 8 caracteres ou mais." if len(password) < 8 else "As palavras-passe não coincidem."
        return _reset_page(request, "new", token=t, email=u["email"], error=err)
    auth.use_reset(t, password)
    return _reset_page(request, "done")


@app.get("/registar", response_class=HTMLResponse)
def register_form(request: Request):
    return templates.TemplateResponse(request, "register.html", {"pwa_head": render.PWA_HEAD})


@app.post("/registar", response_class=HTMLResponse)
def register(
    request: Request,
    name: str = Form(""),
    email: str = Form(""),
    password: str = Form(""),
    password2: str = Form(""),
    privacy: str = Form(""),
):
    """An athlete creates their own account; it sees nothing until the coach associates it with the athlete's data."""
    import re as _re

    ip = request.headers.get("cf-connecting-ip") or (request.client.host if request.client else "?")
    now = time.time()
    _signups[ip] = [t for t in _signups.get(ip, []) if now - t < 3600]
    err = None
    if len(_signups[ip]) >= 5:
        err = "Demasiadas contas criadas a partir desta rede. Tenta mais tarde."
    elif len(name.strip()) < 2:
        err = "Escreve o teu nome."
    elif not _re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email.strip()):
        err = "O email não parece válido."
    elif len(password) < 8:
        err = "A palavra-passe tem de ter 8 caracteres ou mais."
    elif password != password2:
        err = "As palavras-passe não coincidem."
    elif privacy != "1":
        err = "Tens de aceitar a política de privacidade."
    if not err:
        try:
            uid = auth.create_athlete_account(email, name, password)
        except ValueError as e:
            err = str(e).capitalize() + ". Entra com essa conta ou usa outro email."
    if err:
        return templates.TemplateResponse(
            request, "register.html", {"pwa_head": render.PWA_HEAD, "error": err, "name": name, "email": email}
        )
    _signups[ip].append(now)
    try:  # tell the coaches: they associate the account in /admin (push, and email when Resend is set up)
        import mailer

        for c in (u for u in auth.list_users() if u["role"] == "coach"):
            mailer.send(
                c["email"],
                f"Tri Squad: {name.strip()} criou conta",
                f"{name.strip()} ({email.strip().lower()}) criou uma conta de atleta no Tri Squad.\n\n"
                "Associa-a aos dados dele (ou cria um atleta novo) em Pedidos de acesso.",
                ("Abrir pedidos de acesso", f"{mailer.PUBLIC_URL}/admin"),
            )
        coach = [d["token"] for d in push.devices(coaches=True)]
        push.send(
            coach,
            f"Novo atleta: {name.strip()}",
            f"{email.strip().lower()} criou conta. Associa-a aos dados dele em /admin.",
            url="/admin",
        )
    except Exception:
        pass
    resp = RedirectResponse("/", status_code=303)
    resp.set_cookie(
        "tri_session",
        signer.sign(str(uid)).decode(),
        max_age=SESSION_MAX_AGE,
        httponly=True,
        samesite="lax",
        secure=config.COOKIE_SECURE,
    )
    return resp


@app.get("/board", response_class=HTMLResponse)
def board(user=Depends(require_coach)):
    return HTMLResponse(render.page(user, athletes=None), headers=NO_STORE)


@app.get("/board/{athlete}", response_class=HTMLResponse)
def board_athlete(request: Request, athlete: str, user=Depends(require_user)):
    if user["role"] != "coach" and user["athlete"] != athlete:
        raise HTTPException(status_code=403, detail="This page belongs to another athlete")
    if user["role"] == "athlete" and athlete != demo.DEMO and athlete not in render.load_data().get("athletes", {}):
        return _pending(request, user, waiting_coach=False)  # associated, but no data until the next sync
    return HTMLResponse(render.page(user, athletes=[athlete]), headers=NO_STORE)


def _wko_status():
    """The coach agent's last night (coach-agent writes _triapp-wko-status.json to the Drive) and how old each athlete's WKO5
    library is on the box: a crash in WKO5's TrainingPeaks sync stops every library after the bad workout."""
    out = {"agent": None, "libraries": []}
    try:
        out["agent"] = json.loads((config.ROOT / "_triapp-wko-status.json").read_text(encoding="utf-8"))
    except Exception:
        pass
    now = time.time()
    import athletes as _ath  # repo root: every WKO5 folder, "Name_wko" or the library's own "First Last"

    dirs = {}
    for a in _ath.discover(str(config.ROOT)).values():
        if a.get("wko"):
            dirs[a["wko"]] = config.ROOT / a["wko"]
    for d in [config.ROOT / n for n in sorted(set(dirs) | {p.name for p in config.ROOT.glob("*_wko")})]:
        profs = list(d.glob("*.wko5athlete"))
        if profs:
            m = max(p.stat().st_mtime for p in profs)
            out["libraries"].append(
                {"name": d.name, "updated": time.strftime("%Y-%m-%d", time.localtime(m)), "days": int((now - m) // 86400)}
            )
    return out


def _custom_races():
    """Races created on the admin page (course_builder.py): form, build state and race file of each, soonest first."""
    out = []
    for f in sorted((config.OUT / "courses").glob("*/form.json")):
        d = f.parent
        load = lambda n: json.loads((d / n).read_text(encoding="utf-8")) if (d / n).exists() else {}
        out.append({"slug": d.name, "form": load("form.json"), "build": load("build.json"), "race": load("race.json")})
    return sorted(out, key=lambda r: r["form"].get("date", ""))


def _num(v, lo, hi, what):
    try:
        x = float(str(v).replace(",", "."))
    except ValueError:
        raise ValueError(f"{what}: número inválido")
    if not lo <= x <= hi:
        raise ValueError(f"{what}: fora do intervalo ({lo} a {hi})")
    return x


def _kms(text, top):
    """'20, 45,5 70' -> [20.0, 45.5, 70.0] (aid stations)"""
    vals = [float(x) for x in re.findall(r"\d+(?:[.,]\d+)?", (text or "").replace(",", ".").replace(". ", " ")) if x]
    return sorted(v for v in vals if 0 < v <= top)


@app.post("/admin/race")
async def admin_race(
    user=Depends(require_coach),
    name: str = Form(...),
    race_date: str = Form(...),
    sport: str = Form("tri"),
    distance: str = Form("70.3"),
    run_distance: str = Form("21.1"),
    url: str = Form(""),
    start_hour: str = Form("8"),
    swim_km: str = Form(""),
    swim_laps: str = Form("1"),
    swim_type: str = Form("mar"),
    water_temp_c: str = Form(""),
    bike_km: str = Form(""),
    bike_laps: str = Form("1"),
    run_km: str = Form(""),
    run_laps: str = Form("1"),
    aid_bike: str = Form(""),
    aid_run: str = Form(""),
    athletes: list[str] = Form([]),
    bike_gpx: UploadFile | None = File(None),
    run_gpx: UploadFile | None = File(None),
):
    """A new target race from the coach: the GPX and the race facts; the course, the weather and every athlete's prediction
    are built in the background (course_builder.py, race_impact.py)."""
    import course_builder

    try:
        day = date.fromisoformat(race_date)
        if day < date.today():
            raise ValueError("a data já passou")
        if sport == "run":
            return await _admin_run_race(
                user, name, day, run_distance, run_km, run_laps, url, start_hour, aid_run, athletes, run_gpx
            )
        if distance not in course_builder.DISTANCES:
            raise ValueError("distância sem modelo de previsão")
        if not name.strip():
            raise ValueError("falta o nome")
        form = {
            "name": " ".join(name.split())[:120],
            "date": day.isoformat(),
            "distance": distance,
            "url": url.strip()[:300],
            "start_hour": int(_num(start_hour, 4, 18, "hora de partida")),
            "swim_km": _num(swim_km, 0.2, 5, "natação km"),
            "swim_laps": int(_num(swim_laps or 1, 1, 6, "voltas na natação")),
            "swim_type": swim_type if swim_type in ("mar", "lago", "rio", "albufeira") else "mar",
            "water_temp_c": _num(water_temp_c, 8, 32, "temperatura da água") if water_temp_c.strip() else None,
            "bike_km": _num(bike_km, 5, 200, "bike km"),
            "bike_laps": int(_num(bike_laps or 1, 1, 12, "voltas na bike")),
            "run_km": _num(run_km, 1, 50, "corrida km"),
            "run_laps": int(_num(run_laps or 1, 1, 12, "voltas na corrida")),
            "aid_bike_km": _kms(aid_bike, 200),
            "aid_run_km": _kms(aid_run, 50),
            "created_by": user["name"],
        }
        slug = course_builder.slugify(form["name"], form["date"])
        if (config.REPO_DIR / "courses" / slug).exists():
            raise ValueError("já existe uma prova com este nome e ano")
        bike = await bike_gpx.read() if bike_gpx and bike_gpx.filename else b""
        if b"<trkpt" not in bike and b"<rtept" not in bike:
            raise ValueError("o ficheiro da bike não parece um GPX (sem pontos de percurso)")
        run = await run_gpx.read() if run_gpx and run_gpx.filename else b""
        if run and b"<trkpt" not in run and b"<rtept" not in run:
            raise ValueError("o ficheiro da corrida não parece um GPX")
    except ValueError as e:
        return RedirectResponse("/admin?msg=" + urllib.parse.quote(f"Prova não criada: {e}") + "#provas", status_code=303)
    folder = config.OUT / "courses" / slug
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "upload_bike.gpx").write_bytes(bike[:30_000_000])
    if run:
        (folder / "upload_run.gpx").write_bytes(run[:30_000_000])
    (folder / "form.json").write_text(json.dumps(form, ensure_ascii=False, indent=1), encoding="utf-8")
    (folder / "build.json").write_text(json.dumps({"state": "queued"}), encoding="utf-8")
    for a in athletes:
        if a in config.athletes():
            userdata.save_race_plan(a, slug, user["name"], entered=True)
    pipeline.build_race(slug, user["name"])
    return RedirectResponse(
        "/admin?msg="
        + urllib.parse.quote(f"A criar {form['name']}: percurso, meteorologia e previsões levam uns minutos.")
        + "#provas",
        status_code=303,
    )


async def _admin_run_race(user, name, day, run_distance, run_km, run_laps, url, start_hour, aid_run, athletes, run_gpx):
    """A running race (10 km, half, marathon, any distance): the run GPX and the race facts."""
    import course_builder

    try:
        if not name.strip():
            raise ValueError("falta o nome")
        if run_distance not in course_builder.RUN_DISTANCES:
            raise ValueError("distância inválida")
        km = course_builder.RUN_DISTANCES[run_distance][1] or _num(run_km, 1, 250, "distância da corrida")
        form = {
            "sport": "run",
            "name": " ".join(name.split())[:120],
            "date": day.isoformat(),
            "distance": run_distance,
            "url": url.strip()[:300],
            "start_hour": int(_num(start_hour or 9, 4, 20, "hora de partida")),
            "run_km": km,
            "run_laps": int(_num(run_laps or 1, 1, 30, "voltas")),
            "aid_run_km": _kms(aid_run, km),
            "created_by": user["name"],
        }
        slug = course_builder.slugify(form["name"], form["date"])
        if (config.REPO_DIR / "courses" / slug).exists():
            raise ValueError("já existe uma prova com este nome e ano")
        run = await run_gpx.read() if run_gpx and run_gpx.filename else b""
        if b"<trkpt" not in run and b"<rtept" not in run:
            raise ValueError("falta o GPX da corrida (ou o ficheiro não é um GPX)")
    except ValueError as e:
        return RedirectResponse("/admin?msg=" + urllib.parse.quote(f"Prova não criada: {e}") + "#provas", status_code=303)
    folder = config.OUT / "courses" / slug
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "upload_run.gpx").write_bytes(run[:30_000_000])
    (folder / "form.json").write_text(json.dumps(form, ensure_ascii=False, indent=1), encoding="utf-8")
    (folder / "build.json").write_text(json.dumps({"state": "queued"}), encoding="utf-8")
    for a in athletes:
        if a in config.athletes():
            userdata.save_race_plan(a, slug, user["name"], entered=True)
    pipeline.build_race(slug, user["name"])
    return RedirectResponse(
        "/admin?msg="
        + urllib.parse.quote(f"A criar {form['name']}: percurso, meteorologia e previsões levam uns minutos.")
        + "#provas",
        status_code=303,
    )


@app.post("/admin/race/{slug}/delete")
def admin_race_delete(slug: str, user=Depends(require_coach)):
    """Remove a race created on the admin page (the repo's courses cannot be removed here)."""
    import shutil

    folder = config.OUT / "courses" / slug
    if not (folder / "form.json").exists() or "/" in slug or slug.startswith("."):
        return RedirectResponse("/admin?msg=" + urllib.parse.quote("Prova não encontrada") + "#provas", status_code=303)
    shutil.rmtree(folder)
    pipeline.refresh_races()
    return RedirectResponse("/admin?msg=" + urllib.parse.quote("Prova apagada.") + "#provas", status_code=303)


def _reconciliation():
    try:
        return json.loads((config.DATA_JSON.parent / "reconciliation.json").read_text(encoding="utf-8"))
    except Exception:
        return None


@app.get("/admin", response_class=HTMLResponse)
def admin(request: Request, user=Depends(require_coach), msg: str = ""):
    return templates.TemplateResponse(
        request,
        "admin.html",
        {
            "user": user,
            "users": auth.list_users(),
            "links": {a: userdata.integration_status(a) for a in config.athletes()},
            "pending": auth.pending_athletes(),
            "athlete_keys": sorted(config.athletes()),
            "status": pipeline.status(),
            "msg": msg,
            "remote": config.RCLONE_REMOTE,
            "data_mtime": render.data_mtime(),
            "athletes": config.athletes(),
            "recon": _reconciliation(),
            "wko": _wko_status(),
            "agent": __import__("app.agent", fromlist=["status"]).status(),
            "custom_races": _custom_races(),
            "repo_races": sorted(p.parent.name for p in (config.REPO_DIR / "courses").glob("*/race.json")),
            "today": date.today().isoformat(),
        },
    )


@app.post("/admin/sync")
def admin_sync(user=Depends(require_coach), skip_drive: str = Form("")):
    started = pipeline.start(skip_drive=bool(skip_drive), who=user["email"])
    return RedirectResponse("/admin?msg=" + ("started" if started else "already-running"), status_code=303)


@app.get("/admin/status")
def admin_status(user=Depends(require_coach)):
    return JSONResponse(pipeline.status())


@app.get("/admin/coach-agent.zip")
def coach_agent_zip(user=Depends(require_coach)):
    """The nightly WKO5 sync for the coach's Mac (coach-agent/), as a zip to unpack in Downloads."""
    import io, zipfile
    from fastapi.responses import Response

    from . import agent

    buf = io.BytesIO()
    src = config.REPO_DIR / "coach-agent"
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for f in sorted(src.iterdir()):
            if f.is_file():
                info = zipfile.ZipInfo(f"coach-agent/{f.name}")
                info.external_attr = (0o755 if f.suffix == ".sh" else 0o644) << 16
                z.writestr(info, f.read_bytes())
        # where the agent asks for "sync now" and reports back; a new download issues a new token (the old one stops working)
        info = zipfile.ZipInfo("coach-agent/server.conf")
        info.external_attr = 0o600 << 16
        z.writestr(info, f'TRIAPP_URL="{mailer.PUBLIC_URL}"\nTRIAPP_AGENT_TOKEN="{agent.new_token()}"\n')
    return Response(
        buf.getvalue(), media_type="application/zip", headers={"Content-Disposition": "attachment; filename=coach-agent.zip"}
    )


@app.post("/admin/wko-sync")
def admin_wko_sync(user=Depends(require_coach)):
    """Ask the coach's Mac to open WKO5 and sync now (picked up on its next poll, within about 5 minutes)."""
    from . import agent

    agent.request(user["name"])
    return RedirectResponse(
        "/admin?msg="
        + urllib.parse.quote("Pedido enviado: o Mac sincroniza no próximo contacto (até 5 min), se estiver ligado.")
        + "#wko",
        status_code=303,
    )


@app.get("/api/agent/poll")
def api_agent_poll(token: str = "", v: str = ""):
    """The coach agent asks whether to sync now (token from the zip, no login)."""
    from . import agent

    if not agent.check(token):
        raise HTTPException(status_code=403)
    return {"run": agent.poll(v)}


@app.post("/api/agent/report")
def api_agent_report(token: str = "", payload: dict = Body(...)):
    """The coach agent's report after a sync; the box pulls the Drive and runs the pipeline a few minutes later."""
    from . import agent

    if not agent.check(token):
        raise HTTPException(status_code=403)
    agent.report(payload, lambda: pipeline.start(who="coach-agent"))
    return {"ok": True}


@app.post("/admin/associate")
def admin_associate(user=Depends(require_coach), user_id: int = Form(...), athlete: str = Form(""), new_name: str = Form("")):
    """Tie a self-created athlete account to an athlete's data, or to a new athlete without history (a new folder the pipeline
    picks up and Intervals.icu fills)."""
    import athletes as _ath

    acc = auth.get_user(user_id)
    if not acc or acc["role"] != "athlete":
        return RedirectResponse("/admin?msg=no-such-account", status_code=303)
    key = athlete.strip()
    if new_name.strip():
        full = " ".join(new_name.split())[:80]
        folder = os.path.join(str(config.ROOT), full.replace(" ", "_") + "_Fit_Files")
        os.makedirs(folder, exist_ok=True)
        key = next(
            (k for k, a in _ath.discover(str(config.ROOT)).items() if a.get("fit") == os.path.basename(folder)), full.split()[0]
        )
    elif key not in config.athletes():
        return RedirectResponse("/admin?msg=unknown-athlete", status_code=303)
    auth.set_athlete(user_id, key)
    con = push._db()  # devices registered while the account was waiting carry no athlete yet
    con.execute("UPDATE push_devices SET athlete=? WHERE email=?", (key, acc["email"]))
    con.commit()
    con.close()
    try:
        push.send(
            [d["token"] for d in push.devices(athlete=key)],
            "Conta associada",
            "Já podes ver os teus treinos. Liga o Intervals.icu e o TrainingPeaks em Ligações.",
            url="/",
        )
    except Exception:
        pass
    return RedirectResponse("/admin?msg=associated", status_code=303)


@app.post("/admin/users")
def admin_users(
    user=Depends(require_coach),
    email: str = Form(...),
    name: str = Form(...),
    role: str = Form(...),
    athlete: str = Form(""),
    password: str = Form(...),
):
    if role == "athlete" and athlete not in config.athletes():
        return RedirectResponse("/admin?msg=unknown-athlete", status_code=303)
    auth.upsert_user(email.strip().lower(), name.strip(), role, athlete or None, password)
    return RedirectResponse("/admin?msg=user-saved", status_code=303)


@app.post("/account/password")
def change_password(user=Depends(require_user), current: str = Form(...), new: str = Form(...)):
    if not auth.verify(user["email"], current) or len(new) < 10:
        return RedirectResponse("/?msg=password-not-changed", status_code=303)
    auth.set_password(user["id"], new)
    return RedirectResponse("/?msg=password-changed", status_code=303)


# ------------------------------------------------------------------ athlete context + assistant API
from fastapi.responses import StreamingResponse
from fastapi import Body
from . import assistant
import voice, push, health, userdata  # noqa: E402  (repo root; assistant put it on the path)
from . import demo


def can_access(user, athlete):
    return user["role"] == "coach" or user.get("athlete") == athlete


def refresh_athlete_analysis(athlete):
    """Re-run the skills (and narrative) for one athlete in the background after their context changes."""
    import subprocess

    if pipeline.status().get("state") == "running":
        return False  # the running pipeline will pick the context up
    env = dict(os.environ, TRIAPP_ROOT=str(config.ROOT), TRIAPP_OUT=str(config.OUT))
    subprocess.Popen(
        [config.PY, "skills_engine.py", athlete],
        cwd=str(config.REPO_DIR),
        env=env,
        stdout=open(config.OUT / "skills_refresh.log", "a"),
        stderr=subprocess.STDOUT,
    )
    return True


def athlete_tone(user, athlete):
    """The athlete hears their own voice; the coach always gets the neutral one, as does anyone during an ongoing injury."""
    if user["role"] != "athlete":
        return ""
    if voice.health_ongoing(assistant.list_context(athlete), time.strftime("%Y-%m-%d")):
        return ""
    return voice.tone(voice.get(athlete))


def refresh_voice_texts(athlete):
    """Rewrite the athlete's recent session notes and skills reading in the new voice, in the background."""
    import subprocess

    if pipeline.status().get("state") == "running":
        return False
    env = dict(os.environ, TRIAPP_ROOT=str(config.ROOT), TRIAPP_OUT=str(config.OUT))
    subprocess.Popen(
        ["sh", "-c", f'{config.PY} session_notes.py "$1" && {config.PY} skills_engine.py "$1"', "sh", athlete],
        cwd=str(config.REPO_DIR),
        env=env,
        stdout=open(config.OUT / "skills_refresh.log", "a"),
        stderr=subprocess.STDOUT,
    )
    return True


@app.post("/api/ack/{athlete}")
def api_ack(athlete: str, payload: dict = Body(...), user=Depends(require_user)):
    """Ignore an alert for the sessions behind it (athlete or coach); the skills are re-run for the athlete."""
    if not can_access(user, athlete):
        raise HTTPException(status_code=403)
    try:
        if payload.get("undo"):
            assistant.unack_finding(athlete, payload.get("title", ""))
        else:
            assistant.ack_finding(athlete, payload.get("title", ""), payload.get("evidence"), user["name"])
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True, "refreshing": refresh_athlete_analysis(athlete)}


def _links_page(request, user, msg="", error=False):
    a = user.get("athlete")
    return templates.TemplateResponse(
        request,
        "links.html",
        {"name": user["name"], "st": userdata.integration_status(a), "msg": msg, "error": error, "pwa_head": render.PWA_HEAD},
        headers=NO_STORE,
    )


@app.get("/ligacoes", response_class=HTMLResponse)
def links(request: Request, user=Depends(require_user)):
    """The athlete's own connections: TrainingPeaks calendar (plan) and Intervals.icu (what was done), with instructions."""
    if user["role"] != "athlete" or user.get("athlete") in (None, demo.DEMO):
        return RedirectResponse("/admin" if user["role"] == "coach" else "/", status_code=303)
    return _links_page(request, user)


@app.post("/ligacoes", response_class=HTMLResponse)
def links_save(
    request: Request,
    tp_ical: str = Form(""),
    intervals_key: str = Form(""),
    intervals_id: str = Form(""),
    user=Depends(require_user),
):
    if user["role"] != "athlete" or user.get("athlete") in (None, demo.DEMO):
        raise HTTPException(status_code=403)
    a = user["athlete"]
    field = lambda v: "" if v.strip().lower() == "apagar" else (v.strip() or None)  # empty = keep, "apagar" = clear
    tp, key, iid = field(tp_ical), field(intervals_key), field(intervals_id)
    notes = []
    try:
        if tp:
            notes.append(f"TrainingPeaks: {userdata.test_tp(tp)} treinos no calendário")
        if key or iid:
            cur = userdata.integrations().get(a, {})
            k, i = key or cur.get("intervals_key"), iid or cur.get("intervals_id")
            if k and i:
                import intervals_sync

                prof = intervals_sync.get(k, f"/athlete/{i}")
                notes.append(f"Intervals.icu: ligado como {prof.get('name') or i}")
        userdata.save_integrations(a, user["email"], tp, key, iid)
    except Exception as e:
        msg = str(e) if isinstance(e, ValueError) else "não consegui ler: confirma o link ou a chave e o ID"
        return _links_page(request, user, f"Não guardado: {msg}.", error=True)
    return _links_page(request, user, ("Guardado. " + "; ".join(notes) + ".") if notes else "Guardado.")


@app.post("/conta/apagar", response_class=HTMLResponse)
def delete_account(request: Request, confirm: str = Form(""), user=Depends(require_user)):
    """The athlete deletes their own account and what they linked or wrote (App Store guideline 5.1.1)."""
    if user["role"] != "athlete" or user.get("athlete") == demo.DEMO:
        raise HTTPException(status_code=403)
    if confirm.strip().upper() != "APAGAR":
        if not user.get("athlete"):
            return _pending(request, user, waiting_coach=True)
        return _links_page(request, user, "Para apagar a conta escreve APAGAR no campo de confirmação.", error=True)
    userdata.delete_athlete_entries(user["athlete"], user["email"])
    auth.delete_user(user["id"])
    resp = HTMLResponse(
        "<!doctype html><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>"
        "<title>Tri Squad · conta apagada</title><body style='font-family:system-ui;padding:32px;max-width:520px;margin:auto'>"
        "<h1>Conta apagada</h1><p>A tua conta e os dados que ligaste ou escreveste foram apagados. Os treinos que o teu treinador "
        "importou dos exports dele ficam com ele: pede-lhe para os remover se quiseres.</p><p><a href='/login'>Voltar</a></p></body>",
        headers=NO_STORE,
    )
    resp.delete_cookie("tri_session")
    return resp


def own_or_coach(user, athlete):
    if not can_access(user, athlete) or athlete == demo.DEMO and user["role"] != "athlete":
        raise HTTPException(status_code=403)


@app.post("/api/race_mark/{athlete}")
def api_race_mark(athlete: str, payload: dict = Body(...), user=Depends(require_user)):
    """Mark (or unmark) a session as a race: it joins the race list and gets a race report."""
    own_or_coach(user, athlete)
    key = (payload.get("key") or "").strip()
    if not key:
        raise HTTPException(status_code=400, detail="sessão em falta")
    userdata.mark_race(
        athlete, key, (payload.get("date") or "")[:10], payload.get("title"), user["name"], bool(payload.get("on", True))
    )
    return {"ok": True}


@app.post("/api/comment/{athlete}")
def api_comment(athlete: str, payload: dict = Body(...), user=Depends(require_user)):
    """A comment on a session by the athlete or the coach (why it was shortened, how it felt…); both read it."""
    own_or_coach(user, athlete)
    try:
        if payload.get("delete"):
            userdata.delete_comment(athlete, int(payload["delete"]), user["name"])
        else:
            cid = userdata.add_comment(
                athlete, payload.get("key") or "", payload.get("date") or "", payload.get("text"), user["name"], user["role"]
            )
            if user["role"] == "athlete":  # the box replies in the background; the page asks for it below
                assistant.reply_to_comment(athlete, cid, user["email"])
    except (ValueError, KeyError) as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True, "comments": userdata.comments(athlete).get(payload.get("key") or "", [])}


@app.post("/api/race_plan/{athlete}/{race}")
def api_race_plan(athlete: str, race: str, payload: dict = Body(...), user=Depends(require_coach)):
    """The coach marks the athlete as entered in a target race and/or picks the bike IF of the plan ("model" = back to it)."""
    try:
        bif = payload.get("bike_if")
        userdata.save_race_plan(
            athlete,
            race,
            user["name"],
            entered=payload.get("entered"),
            bike_if=None if bif in (None, "", "model") else float(bif),
            clear_if=bif == "model",
        )
    except (ValueError, TypeError) as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True}


@app.post("/api/nutrition/{athlete}/{race}")
def api_nutrition(athlete: str, race: str, payload: dict = Body(...), user=Depends(require_coach)):
    """The coach's numbers and advice for the athlete's fuelling plan in a target race; returns the recomputed plan."""
    import nutrition  # repo root

    prev = userdata.race_nutrition(athlete).get(race) or {}
    note = (payload.get("note") or "").strip()
    if note and note != (prev.get("note") or ""):  # a new or changed note: the box reads the numbers in it
        payload["from_note"], payload["unread"] = nutrition.from_note(note)
    elif note:
        payload["from_note"], payload["unread"] = prev.get("from_note") or {}, prev.get("unread") or []
    try:
        userdata.save_race_nutrition(athlete, race, payload, user["name"])
    except (ValueError, TypeError) as e:
        raise HTTPException(status_code=400, detail=str(e))
    d = render.load_data()
    R = {race: d.get("races_target", {}).get(race)} if d.get("races_target", {}).get(race) else {}
    R = {k: {**v, "athletes": {athlete: v["athletes"].get(athlete)}} for k, v in R.items() if v["athletes"].get(athlete)}
    plans = render.with_nutrition(R, d.get("athletes", {}))
    return {"ok": True, "nutrition": (plans.get(race, {}).get("athletes", {}).get(athlete) or {}).get("nutrition")}


@app.post("/api/thresholds/{athlete}")
def api_thresholds(athlete: str, payload: dict = Body(...), user=Depends(require_user)):
    """The athlete or the coach applies thresholds (a projection, a test) from a date on; the numbers are recalculated."""
    own_or_coach(user, athlete)
    num = lambda k: float(payload[k]) if payload.get(k) not in (None, "") else None
    try:
        userdata.set_thresholds(
            athlete,
            (payload.get("from_date") or "")[:10],
            user["name"],
            payload.get("source") or "manual",
            ftp=num("ftp"),
            lthr_run=num("lthr_run"),
            lthr_bike=num("lthr_bike"),
        )
    except (ValueError, TypeError) as e:
        raise HTTPException(status_code=400, detail=str(e))
    pipeline.start_light(user["email"])  # TSS, zones and form recalculated with the new values
    return {"ok": True}


@app.get("/api/comment/{athlete}")
def api_comments(athlete: str, key: str, user=Depends(require_user)):
    """The comments of one session (the page polls it for the assistant's reply)."""
    own_or_coach(user, athlete)
    return {"comments": userdata.comments(athlete).get(key, [])}


@app.post("/api/race_report/{athlete}")
def api_race_report(athlete: str, payload: dict = Body(...), user=Depends(require_user)):
    """The athlete writes the athlete's report, the coach the coach's; both read both."""
    own_or_coach(user, athlete)
    role = "coach" if user["role"] == "coach" else "athlete"
    try:
        userdata.save_race_report(athlete, payload.get("date") or "", role, payload.get("text"), user["name"])
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True, "role": role}


@app.post("/api/missed/{athlete}")
def api_missed(athlete: str, payload: dict = Body(...), user=Depends(require_user)):
    """Why a planned session was skipped (the athlete answers; the coach reads it on the athlete's page)."""
    if user["role"] != "athlete" or user.get("athlete") != athlete:
        raise HTTPException(status_code=403)
    try:
        userdata.save_missed_reason(
            athlete, payload.get("date") or "", payload.get("title") or "", payload.get("reason"), payload.get("note")
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True}


@app.post("/api/push/register")
def api_push_register(payload: dict = Body(...), user=Depends(require_user)):
    """The native app sends its APNs device token after login."""
    try:
        push.register(payload.get("token", ""), user, payload.get("platform") or "ios")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True, "configured": push.configured()}


@app.post("/api/push/test")
def api_push_test(user=Depends(require_user)):
    """Send a test notification to the caller's own devices."""
    con = push._db()
    tokens = [r[0] for r in con.execute("SELECT token FROM push_devices WHERE email=?", (user["email"],))]
    con.close()
    return push.send(tokens, "Tri Squad", "As notificações estão a funcionar.", url="/")


@app.post("/api/health/{athlete}")
def api_health(athlete: str, payload: dict = Body(...), user=Depends(require_user)):
    """Daily sleep, resting HR and HRV from Apple Health, sent by the native app (the athlete's own data only)."""
    if user["role"] != "athlete" or user.get("athlete") != athlete:
        raise HTTPException(status_code=403)
    return {"ok": True, "saved": health.save(athlete, payload.get("days"))}


@app.get("/api/facts/{athlete}")
def api_facts(athlete: str, user=Depends(require_user)):
    """Compact grounding for the on-device model (Apple Foundation Models, 4k-token window): the app caches it and uses it
    when the box does not answer."""
    if not can_access(user, athlete):
        raise HTTPException(status_code=403)
    return assistant.compact_facts(athlete, athlete_tone(user, athlete))


@app.post("/api/voice/{athlete}")
def api_voice(athlete: str, payload: dict = Body(...), user=Depends(require_user)):
    if user["role"] != "athlete" or user.get("athlete") != athlete:
        raise HTTPException(status_code=403)  # each athlete picks their own; the coach's view is always neutral
    try:
        voice.set(athlete, payload.get("voice", ""))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True, "voice": voice.get(athlete), "refreshing": refresh_voice_texts(athlete)}


@app.get("/api/context/{athlete}")
def api_context_list(athlete: str, user=Depends(require_user)):
    if not can_access(user, athlete):
        raise HTTPException(status_code=403)
    return {"kinds": assistant.KINDS, "items": assistant.list_context(athlete)}


@app.post("/api/context/{athlete}")
def api_context_add(athlete: str, payload: dict = Body(...), user=Depends(require_user)):
    if not can_access(user, athlete):
        raise HTTPException(status_code=403)
    try:
        assistant.add_context(
            athlete,
            payload.get("kind", ""),
            payload.get("start", ""),
            payload.get("end") or None,
            payload.get("note", ""),
            user["email"],
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True, "items": assistant.list_context(athlete), "refreshing": refresh_athlete_analysis(athlete)}


@app.post("/api/context/{athlete}/{cid}/delete")
def api_context_delete(athlete: str, cid: int, user=Depends(require_user)):
    if not can_access(user, athlete):
        raise HTTPException(status_code=403)
    assistant.delete_context(athlete, cid)
    return {"ok": True, "items": assistant.list_context(athlete), "refreshing": refresh_athlete_analysis(athlete)}


@app.post("/api/warm/{athlete}")
def api_warm(athlete: str, user=Depends(require_user)):
    if not can_access(user, athlete):
        raise HTTPException(status_code=403)
    assistant.warm(athlete)
    return {"ok": True}


@app.post("/api/ask_later/{athlete}")
def api_ask_later(athlete: str, payload: dict = Body(...), user=Depends(require_user)):
    """The box answers in the background; the asker gets a push when it is ready (the app can be closed meanwhile)."""
    if not can_access(user, athlete):
        raise HTTPException(status_code=403)
    q = (payload.get("question") or "").strip()
    if not q:
        raise HTTPException(status_code=400, detail="pergunta vazia")
    jid = assistant.ask_later(athlete, q, payload.get("history") or [], user["email"], athlete_tone(user, athlete))
    return {"id": jid}


@app.get("/api/answers/{athlete}")
def api_answers(athlete: str, user=Depends(require_user)):
    if not can_access(user, athlete):
        raise HTTPException(status_code=403)
    return {"answers": assistant.answers(athlete, user["email"])}


@app.get("/api/chat/{athlete}")
def api_chat(athlete: str, user=Depends(require_user)):
    """The asker's saved conversation about this athlete (the chat panel rebuilds itself from it)."""
    if not can_access(user, athlete):
        raise HTTPException(status_code=403)
    return {"items": assistant.chat_history(athlete, user["email"])}


@app.post("/api/ask/{athlete}")
def api_ask(athlete: str, payload: dict = Body(...), user=Depends(require_user)):
    if not can_access(user, athlete):
        raise HTTPException(status_code=403)
    q = (payload.get("question") or "").strip()
    if not q:
        raise HTTPException(status_code=400, detail="pergunta vazia")
    return StreamingResponse(
        assistant.stream_answer(athlete, q, payload.get("history") or [], user["email"], athlete_tone(user, athlete)),
        media_type="text/plain; charset=utf-8",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )

"""Race-day fuelling plan for a target race, from the TrainingPeaks "Science" articles (research_notes/…/05_nutrition_heat_environment.md).

Inputs are the race prediction (race_impact.py: split times and the bike leg at the target power, km by km), the race-day
weather (temperature) and the athlete's weight. The coach can override any number and write his own advice (userdata
race_nutrition); the plan is recomputed with his numbers.

Rules (with the article behind each one):
  carbohydrate   under 1 h: water; 1-2 h: 30-60 g/h; over 2 h: as much as tolerated, 60-90 g/h and 90-120 g/h only for a trained
                 gut with glucose:fructose (Epton, "How to Break the 90g of Carbs Rule"; Inpyn, "The Science of Sugars").
                 Running absorbs less than riding, so the run target is lower (inference, flagged).
  fluid          475-950 mL/h in the heat, drink to thirst, never forced (Goeringer, "How to Train and Race in Hot Conditions");
                 the cooler the day, the lower in the range (inference from the same article and Nehr).
  sodium         600-1200 mg/h in the heat (Goeringer); a mild day uses the low end (inference); a sweat test replaces both.
  caffeine       3-6 mg/kg about 60 min before; the plan uses the low end (2019 Nutrients review, cited in the category).
  before/after   1-2 g/kg of carbohydrate 3 h before, about 30 g within 15 min of the start (Kattouf); 1-1.5 g/kg right after and
                 150% of the fluid lost (Kattouf; Wegerif, "The Whole Picture: Nutrition").
  on the bike    a feed every FEED_MIN minutes, placed where the effort is lowest near that time: not on climbs over 3% nor on fast
                 descents (hands on the bars), so on flats, false flats and just before climbs.
"""

import math

FEED_MIN = 20
BOTTLE_ML = 750
CAGES = 2  # bottles the bike carries
EDITABLE = (
    "carbs_bike",
    "carbs_run",
    "fluid_ml",
    "sodium_mg",
    "feed_min",
    "gel_g",
    "drink_g_h",
    "start_min",
    "bottle_ml",
    "cages",
)
RANGES = {
    "carbs_bike": (0, 150),
    "carbs_run": (0, 120),
    "fluid_ml": (0, 1500),
    "sodium_mg": (0, 2500),
    "feed_min": (10, 60),
    "gel_g": (10, 60),
    "drink_g_h": (0, 120),
    "start_min": (0, 180),
    "bottle_ml": (300, 1200),
    "cages": (1, 4),
}
GEL_G = 25  # g of carbohydrate in a typical gel
WINDOW_MIN = 6  # a feed can move this much from its time to find an easy stretch
CLIMB, DESCENT = 3.0, -3.5  # grade %: no feeding above / below


def carbs_per_hour(total_h, leg):
    """(low, high, default) g/h for a leg of a race lasting total_h."""
    if total_h < 1:
        return (0, 0, 0)
    if total_h < 2:
        return (30, 60, 45)
    return (60, 90, 80) if leg == "bike" else (45, 75, 60)


def fluid_sodium(temp_c):
    """(mL/h, mg sodium/h) for the race-day temperature (median of the race window)."""
    if temp_c is None:
        return 600, 700
    if temp_c >= 27:
        return 850, 1100
    if temp_c >= 22:
        return 700, 900
    if temp_c >= 16:
        return 550, 700
    return 450, 500


def bike_clock(track, bike_h):
    """The track's points with their minute on the predicted bike time."""
    k = bike_h * 60 / track[-1]["min"] if track and track[-1]["min"] else 1.0
    return [dict(p, min=p["min"] * k) for p in track]


def place_feeds(track, bike_h, every_min=FEED_MIN, grams=None, start_min=0, t1=True, aids_km=()):
    """Feeding points on the bike. The first one is in T1 (standing, hands free: the easiest moment of the day) unless the
    coach starts later (start_min); then every `every_min` minutes, each moved within WINDOW_MIN minutes to the easiest
    stretch (lowest power, not climbing, not a fast descent). Not in an aid station (hands busy with bottles): just after it."""
    if not track:
        return []
    pts = bike_clock(track, bike_h)
    end = pts[-1]["min"]
    feeds, t = [], max(float(start_min or 0), 10.0)
    if t1 and not start_min:
        feeds.append({"km": 0.0, "min": 0, "grade": 0.0, "g": grams, "why": "na T1 (parado, mãos livres)"})
        t = float(every_min)
    while t < end - 10:
        near = [p for p in pts if abs(p["min"] - t) <= WINDOW_MIN] or [min(pts, key=lambda p: abs(p["min"] - t))]

        def cost(p):
            c = p["w"] / max(1, max(q["w"] for q in pts))
            if p["grade"] > CLIMB:
                c += 2
            if p["grade"] <= DESCENT:
                c += 2
            for a in aids_km:
                if -0.3 <= p["km"] - a < 0.3:  # in the aid zone: hands busy with bottles
                    c += 1
                elif 0.3 <= p["km"] - a <= 1.5:  # just after it: already slow, bottles stowed
                    c -= 0.3
            return c + abs(p["min"] - t) / 60  # the closer to the planned time, the better, all else equal

        best = min(near, key=cost)
        ahead = [p for p in pts if best["min"] < p["min"] <= best["min"] + 10]
        climb_next = ahead and sum(p["grade"] > CLIMB for p in ahead) / len(ahead) >= 0.5
        after_aid = any(0.3 <= best["km"] - a <= 1.5 for a in aids_km)
        feeds.append(
            {
                "km": best["km"],
                "min": round(best["min"]),
                "grade": best["grade"],
                "g": grams,
                "why": (
                    "depois do abastecimento"
                    if after_aid
                    else (
                        "antes da subida"
                        if climb_next
                        else (
                            "plano" if abs(best["grade"]) <= 1.5 else ("a subir suave" if best["grade"] > 0 else "a descer suave")
                        )
                    )
                ),
            }
        )
        t = best["min"] + every_min
    return feeds


def bottles(track, bike_h, fluid_ml_h, aids_km, bottle_ml=BOTTLE_ML, cages=CAGES):
    """Bottles at the start and at each aid station: each stretch to the next aid station (or T2) needs fluid x time; more than
    the cages hold is flagged."""
    pts = bike_clock(track, bike_h)
    end_km, end_min = (pts[-1]["km"], pts[-1]["min"]) if pts else (0, bike_h * 60)
    at = lambda km: next((p["min"] for p in pts if p["km"] >= km), end_min)
    stops = [(a, at(a)) for a in sorted(aids_km) if 0 < a < end_km]
    marks = [(0.0, 0.0)] + stops + [(end_km, end_min)]
    plan, short = [], []
    for (k0, m0), (k1, m1) in zip(marks, marks[1:]):
        need = fluid_ml_h * (m1 - m0) / 60
        n = max(1, math.ceil(need / bottle_ml - 0.15))  # 15% of a bottle short is fine: drink to thirst
        if n > cages:
            short.append((k0, k1, need))
        plan.append(
            {"km": round(k0, 1), "min": round(m0), "to_km": round(k1, 1), "need_ml": round(need, -1), "take": min(n, cages)}
        )
    return plan, short


def run_aids(aids_km, run_km, run_h, fluid_ml_h, gel_every, first_gel=None):
    """What to take at each run aid station: water (the share of the hourly fluid until the next one) and a gel when the gel
    clock says so (in a triathlon the first gel was in T2; in a running race the first one is at `first_gel` minutes)."""
    if not aids_km or not run_km or not run_h:
        return []
    per_km = run_h * 60 / run_km
    out, next_gel = [], (first_gel if first_gel is not None else gel_every) or 1e9
    marks = sorted(a for a in aids_km if 0 < a < run_km)
    for i, a in enumerate(marks):
        m = a * per_km
        gap = ((marks[i + 1] if i + 1 < len(marks) else run_km) - a) * per_km
        gel = gel_every and m >= next_gel - 4
        if gel:
            next_gel = m + gel_every
        out.append({"km": a, "min": round(m), "ml": round(fluid_ml_h * gap / 60, -1), "gel": bool(gel)})
    return out


def plan(pred, race, weather_temp, weight, over=None):
    """The fuelling plan of one athlete for one race; `over` holds the coach's numbers and note (all optional)."""
    if pred.get("sport") == "run":
        return run_plan(pred, race, weather_temp, weight, over)
    raw = over or {}
    explicit = {k: raw[k] for k in EDITABLE if raw.get(k) not in (None, "")}  # typed in the coach's fields
    from_text = raw.get("from_note") or {}  # read from the coach's advice (from_note); a typed field wins
    over = {**from_text, **{k: v for k, v in raw.items() if v not in (None, "")}}
    swim_h = (pred.get("swim") or {}).get("hours") or 0
    bike = pred.get("bike") or {}
    opt = (bike.get("options") or {}).get(f"{bike.get('recommended_if', 0):.2f}") or {}
    bike_h = opt.get("hours") or 0
    run_h = (pred.get("run") or {}).get("hours") or 0
    total_h = (pred.get("total") or {}).get("hours") or (swim_h + bike_h + run_h)
    if not total_h or not bike_h:
        return None
    weight = weight or 75
    cb = carbs_per_hour(total_h, "bike")
    cr = carbs_per_hour(total_h, "run")
    fluid, sodium = fluid_sodium(weather_temp)
    g_bike = float(over.get("carbs_bike") or cb[2])
    g_run = float(over.get("carbs_run") or cr[2])
    fluid = float(over.get("fluid_ml") or fluid)
    sodium = float(over.get("sodium_mg") or sodium)
    every = int(over.get("feed_min") or FEED_MIN)
    gel = float(over.get("gel_g") or GEL_G)
    caffeine = over.get("caffeine", True) not in (False, "0", "false", 0)
    drink = min(float(over.get("drink_g_h") or 0), g_bike)  # carbohydrate the coach puts in the bottle
    start = float(over.get("start_min") or 0)
    t1 = over.get("t1", True) not in (False, "0", "false", 0)
    per_feed = round((g_bike - drink) * every / 60)
    aids_b = [float(a) for a in (race.get("aid_bike_km") or [])]
    aids_r = [float(a) for a in (race.get("aid_run_km") or [])]
    track = bike.get("track") or []
    feeds = place_feeds(track, bike_h, every, per_feed, start, t1, aids_b) if per_feed > 0 else []
    bottle_ml = float(over.get("bottle_ml") or BOTTLE_ML)
    cages = int(over.get("cages") or CAGES)
    bottle_plan, short = bottles(track, bike_h, fluid, aids_b, bottle_ml, cages) if track else ([], [])
    run_gels = round(g_run * run_h / gel, 1)
    run_every = round(run_h * 60 / max(run_gels, 1)) if run_gels >= 1 else None
    run_km = float(race.get("run_distance_km") or 0)
    r_aids = run_aids(aids_r, run_km, run_h, fluid * 0.8, run_every)
    return {
        "race": race.get("name"),
        "hours": {"swim": round(swim_h, 2), "bike": round(bike_h, 2), "run": round(run_h, 2), "total": round(total_h, 2)},
        "temp_c": weather_temp,
        "coach_set": sorted(set(explicit) | {k for k in from_text if k in EDITABLE}),
        "coach_values": explicit,
        "from_note": from_text,
        "unread": raw.get("unread") or [],
        "note": over.get("note") or "",
        "note_by": over.get("updated_by"),
        "before": {
            "carbs_3h_g": [round(1 * weight), round(2 * weight)],
            "carbs_15min_g": 30,
            "caffeine_mg": round(3 * weight) if caffeine else None,
        },
        "bike": {
            "carbs_g_h": round(g_bike),
            "range_g_h": [cb[0], cb[1]],
            "fluid_ml_h": round(fluid),
            "sodium_mg_h": round(sodium),
            "total_carbs_g": round(g_bike * bike_h),
            "total_fluid_ml": round(fluid * bike_h, -1),
            "gels_equiv": round(g_bike * bike_h / gel, 1),
            "feed_every_min": every,
            "drink_g_h": round(drink),
            "gel_g_h": round(g_bike - drink),
            "start_min": round(start),
            "t1": t1,
            "feeds": feeds,
            "aids_km": aids_b,
            "bottles": bottle_plan,
            "bottle_ml": bottle_ml,
            "cages": cages,
        },
        "run": {
            "carbs_g_h": round(g_run),
            "range_g_h": [cr[0], cr[1]],
            "fluid_ml_h": round(fluid * 0.8),
            "sodium_mg_h": round(sodium),
            "total_carbs_g": round(g_run * run_h),
            "gels_equiv": run_gels,
            "gel_every_min": run_every,
            "aids": r_aids,
        },
        "after": {"carbs_g": [round(1 * weight), round(1.5 * weight)], "fluid": "150% do peso perdido"},
        "gel_g": gel,
        "warnings": [
            w
            for w in (
                (
                    f"{round(g_bike)} g/h no bike pede o intestino treinado: testa em treinos longos, subindo 5-10% por semana."
                    if g_bike > 90
                    else None
                ),
                (
                    "Acima de 60 g/h usa misturas de glucose e frutose (2:1): só glucose não passa de ~60 g/h."
                    if max(g_bike, g_run) > 60
                    else None
                ),
                (
                    f"Géis de {gel:.0f} g de {every} em {every} min dão {gel * 60 / every:.0f} g/h, mas o plano pede "
                    f"{g_bike - drink:.0f} g/h em géis: ou {per_feed} g de cada vez (parte de um gel), ou um gel inteiro de "
                    f"{round(gel * 60 / (g_bike - drink))} em {round(gel * 60 / (g_bike - drink))} min."
                    if per_feed > 0 and abs(gel * 60 / every - (g_bike - drink)) > 0.2 * (g_bike - drink)
                    else None
                ),
                (
                    "Sem abastecimentos indicados na bike: "
                    + f"precisas de ~{fluid * bike_h / 1000:.1f} L e levas {cages} bidões de {bottle_ml:.0f} mL."
                    if track and not aids_b and fluid * bike_h > cages * bottle_ml * 1.15
                    else None
                ),
                (
                    "Entre "
                    + " e ".join(f"km {a:.0f}-{b:.0f}" for a, b, _ in short)
                    + f" precisas de mais líquido do que {cages} bidões: bebe mais no abastecimento anterior ou leva mais um."
                    if short and aids_b
                    else None
                ),
                (
                    f"Dia quente ({weather_temp:.0f} °C): faz um teste de suor (peso antes e depois) para acertar líquidos e sódio."
                    if weather_temp and weather_temp >= 22
                    else None
                ),
            )
            if w
        ],
    }


NOTE_PROMPT = """Lês a recomendação de nutrição de um treinador de triatlo para a prova de um atleta e extrais SÓ os números
que ele escreveu explicitamente. Responde só com um objeto JSON com estas chaves (omite as que o texto não diz):
carbs_bike (g de hidratos por hora no bike), carbs_run (g/h na corrida), fluid_ml (mL de líquido por hora), sodium_mg
(mg de sódio por hora), feed_min (comer de x em x minutos no bike), gel_g (gramas de hidratos por gel), drink_g_h (g de
hidratos por hora que vêm do bidon/bebida), bottle_ml (mL de cada bidão), cages (quantos bidões leva na bike), start_min (minuto do bike em que começam os géis; 60 para "a partir da 1h"),
caffeine (true/false), t1 (false se ele disser para não comer na transição), unread (lista curta, em português, do que
ele pede e não consegues pôr em números, por exemplo "2 scoops: faltam as gramas por scoop").
Nunca inventes nem converta unidades que o texto não dá (uma scoop, uma barra ou um bidon sem gramas não têm número)."""


def from_note(note):
    """(numbers, unread) the coach wrote in his free text, read by the box model; ranges checked, anything else dropped."""
    import json, re
    import llm

    if not (note or "").strip() or not llm.available():
        return {}, []
    out = llm.chat(
        [{"role": "system", "content": NOTE_PROMPT}, {"role": "user", "content": note[:3000]}], timeout=120, temperature=0
    )
    m = re.search(r"\{.*\}", out, re.S)
    try:
        d = json.loads(m.group(0)) if m else {}
    except ValueError:
        d = {}
    vals = {}
    for k, (lo, hi) in RANGES.items():
        try:
            v = float(d[k])
        except (KeyError, TypeError, ValueError):
            continue
        if lo <= v <= hi:
            vals[k] = v
    for k in ("caffeine", "t1"):
        if isinstance(d.get(k), bool):
            vals[k] = d[k]
    unread = [str(x)[:200] for x in (d.get("unread") or []) if str(x).strip()][:5]
    return vals, unread


def run_plan(pred, race, weather_temp, weight, over=None):
    """A running race: carbohydrate by the race time (none under 1 h, 30-60 g/h to 2 h, 45-90 g/h beyond, running absorbs less
    than riding), the first gel at 30 min (coach: start_min), fluid and sodium by temperature, what to take at each aid station.
    """
    raw = over or {}
    explicit = {k: raw[k] for k in EDITABLE if raw.get(k) not in (None, "")}
    from_text = raw.get("from_note") or {}
    over = {**from_text, **{k: v for k, v in raw.items() if v not in (None, "")}}
    h = (pred.get("total") or {}).get("hours") or 0
    if not h:
        return None
    weight = weight or 70
    lo, hi, default = (0, 0, 0) if h < 1 else ((30, 60, 45) if h < 2 else (45, 90, 60))
    fluid, sodium = fluid_sodium(weather_temp)
    g = float(over.get("carbs_run") or default)
    fluid = float(over.get("fluid_ml") or fluid * 0.8)
    sodium = float(over.get("sodium_mg") or sodium)
    gel = float(over.get("gel_g") or GEL_G)
    caffeine = over.get("caffeine", True) not in (False, "0", "false", 0)
    gels = round(g * h / gel, 1)
    every = round(h * 60 / max(gels, 1)) if gels >= 1 else None
    first = float(over.get("start_min") or 30)
    km = float(race.get("run_distance_km") or (pred.get("run") or {}).get("distance_km") or 0)
    return {
        "mode": "run",
        "race": race.get("name"),
        "hours": {"run": round(h, 2), "total": round(h, 2)},
        "temp_c": weather_temp,
        "coach_set": sorted(set(explicit) | {k for k in from_text if k in EDITABLE}),
        "coach_values": explicit,
        "from_note": from_text,
        "unread": raw.get("unread") or [],
        "note": over.get("note") or "",
        "note_by": over.get("updated_by"),
        "before": {
            "carbs_3h_g": [round(1 * weight), round(2 * weight)],
            "carbs_15min_g": 30 if h >= 1 else 0,
            "caffeine_mg": round(3 * weight) if caffeine else None,
        },
        "run": {
            "carbs_g_h": round(g),
            "range_g_h": [lo, hi],
            "fluid_ml_h": round(fluid),
            "sodium_mg_h": round(sodium),
            "total_carbs_g": round(g * h),
            "gels_equiv": gels,
            "gel_every_min": every,
            "first_gel_min": round(first) if every else None,
            "aids": run_aids([float(a) for a in (race.get("aid_run_km") or [])], km, h, fluid, every, first if every else None),
        },
        "after": {"carbs_g": [round(1 * weight), round(1.5 * weight)], "fluid": "150% do peso perdido"},
        "gel_g": gel,
        "warnings": [
            w
            for w in (
                "Menos de 1 h: água chega; um gel 15 min antes é opcional." if h < 1 else None,
                ("Acima de 60 g/h usa misturas de glucose e frutose (2:1) e treina o intestino nos longos." if g > 60 else None),
                (
                    f"Dia quente ({weather_temp:.0f} °C): faz um teste de suor (peso antes e depois) para acertar líquidos e sódio."
                    if weather_temp and weather_temp >= 22
                    else None
                ),
            )
            if w
        ],
    }

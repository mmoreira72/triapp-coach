"""Analysis skills: deterministic rules from the TrainingPeaks science review, applied per athlete.

Reads TRIAPP_OUT/data/activities_all.csv and dashboard_data.json, writes TRIAPP_OUT/data/analysis.json:
{athlete: {generated, findings: [{skill, severity, title, detail, metrics, source, url}], narrative, narrative_model}}
Severity: "alert" (act now), "watch" (keep an eye), "ok" (healthy signal), "info" (context / no judgement).
An optional LLM (Ollama or any OpenAI-compatible endpoint) writes the narrative from the findings only.
"""

import os, json, math, sys
from datetime import datetime
from pathlib import Path
import numpy as np, pandas as pd

import llm, voice
from athletes import override

ROOT = os.environ.get("TRIAPP_ROOT") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = Path(os.environ.get("TRIAPP_OUT", os.path.join(os.path.dirname(os.path.abspath(__file__)), "out")))
SKILLS_DIR = Path(__file__).resolve().parent / "skills"
TODAY = pd.Timestamp(os.environ.get("TRIAPP_TODAY", datetime.now().strftime("%Y-%m-%d")))
DEFAULT_BIRTH_YEAR = (
    1985  # only for the "45+" note of week_structure when neither the WKO profile nor athlete_overrides.json has it
)

SRC = {
    "coach_consistency": ("Critério do treinador (Miguel Medeiros)", ""),
    "simmons_total_load": (
        "Simmons, Total Load [Science]",
        "https://www.trainingpeaks.com/coach-blog/designing-an-athletes-total-load/",
    ),
    "simmons_pmc": (
        "Simmons, Coach's guide to ATL/CTL/TSB [Coach blog]",
        "https://www.trainingpeaks.com/coach-blog/a-coachs-guide-to-atl-ctl-tsb/",
    ),
    "friel_tsb": (
        "Friel, Applying the numbers part 3 [TP Learn]",
        "https://www.trainingpeaks.com/learn/articles/applying-the-numbers-part-3-training-stress-balance/",
    ),
    "coggan_np": (
        "Coggan, NP/IF/TSS [TP Learn]",
        "https://www.trainingpeaks.com/learn/articles/normalized-power-intensity-factor-training-stress/",
    ),
    "epton_np": (
        "Epton, Normalized Power [Science]",
        "https://www.trainingpeaks.com/coach-blog/normalized-power-how-coaches-use/",
    ),
    "epton_runpower": (
        "Epton, Running power [Science]",
        "https://www.trainingpeaks.com/coach-blog/coaching-running-power-better/",
    ),
    "wells": (
        "Wells, Age-group triathlon load [Science]",
        "https://www.trainingpeaks.com/coach-blog/age-group-triathlon-training-load/",
    ),
    "friel_decoupling": (
        "Friel, Aerobic endurance and decoupling [Coach blog]",
        "https://www.trainingpeaks.com/coach-blog/aerobic-endurance-and-decoupling/",
    ),
    "help_ef": (
        "TP Help Center, EF and decoupling",
        "https://help.trainingpeaks.com/hc/en-us/articles/204071724-Aerobic-Decoupling-Pw-Hr-and-Pa-HR-and-Efficiency-Factor-EF",
    ),
    "johnson_kona": (
        "Johnson, Lessons from Kona [TP blog]",
        "https://www.trainingpeaks.com/blog/lessons-from-kona-how-to-nail-an-ironman-bike/",
    ),
    "thomas_703": (
        "Thomas, 70.3 bike pacing [TP blog]",
        "https://www.trainingpeaks.com/blog/taking-risks-ironman-70-3-world-championships-bike-pacing/",
    ),
    "simmons_running": (
        "Simmons, Athletes' running data [Science]",
        "https://www.trainingpeaks.com/coach-blog/athletes-running-data/",
    ),
    "rowe_kj": (
        "Rowe, Kilojoules and durability [Science]",
        "https://www.trainingpeaks.com/coach-blog/predicting-measuring-kilojoules-trainingpeaks/",
    ),
    "wegerif_stress": (
        "Wegerif, The whole picture: physical stress [Science]",
        "https://www.trainingpeaks.com/coach-blog/the-whole-picture-physical-stress/",
    ),
    "stephenson_mmp": (
        "Stephenson, Mean max power curve [Science]",
        "https://www.trainingpeaks.com/coach-blog/all-about-the-mean-max-power-curve/",
    ),
    "novak_ot": (
        "Novak, 4 signs of overtraining [Science]",
        "https://www.trainingpeaks.com/coach-blog/the-4-signs-of-overtraining/",
    ),
    "wolfe": (
        "Wolfe, Fitness on limited time [Science]",
        "https://www.trainingpeaks.com/coach-blog/maximizing-athlete-fitness-on-limited-time/",
    ),
}


def F(skill, severity, title, detail, src, evidence=None, **metrics):
    """A finding. `evidence`: the sessions behind it (start times); only such findings can be ignored (finding_ack)."""
    name, url = SRC[src]
    clean = {
        k: (
            None
            if (isinstance(v, float) and (math.isnan(v) or math.isinf(v)))
            else (round(float(v), 2) if isinstance(v, (float, np.floating)) else (int(v) if isinstance(v, (np.integer,)) else v))
        )
        for k, v in metrics.items()
    }
    out = {"skill": skill, "severity": severity, "title": title, "detail": detail, "source": name, "url": url, "metrics": clean}
    if evidence is not None:
        out["evidence"] = sorted(str(t)[:16] for t in evidence)
    return out


def week_start(ts):
    return ts - pd.to_timedelta(ts.weekday(), unit="D")


# ---------------------------------------------------------------- skills
def skill_threshold_audit(g, A):
    out = []
    last90 = g[g.date >= TODAY - pd.Timedelta(days=90)]
    hot = last90[(last90.tss_tp.notna()) & (last90.duration_h >= 0.75)].copy()
    hot["if_tp"] = np.sqrt(hot.tss_tp / (hot.duration_h * 100))
    high = hot[hot.if_tp > 1.05]
    if len(high):
        by = high.groupby("sport_n").size().to_dict()
        out.append(
            F(
                "threshold_audit",
                "alert",
                "Limiar provavelmente baixo demais no TrainingPeaks",
                f"{len(high)} sessões de 45+ min nos últimos 90 dias têm IF > 1,05 segundo o TSS do TP ({by}). Um IF acima de 1,05 durante cerca de uma hora indica limiar desatualizado; o TSS e o CTL ficam inflados até ser corrigido.",
                "coggan_np",
                evidence=high.t_local,
                sessions=len(high),
                max_if=high.if_tp.max(),
            )
        )
    corr = last90[last90.tss_method == "hr-corrected"]
    if len(corr):
        out.append(
            F(
                "threshold_audit",
                "watch",
                "TSS do TP substituído por estimativa",
                f"{len(corr)} sessões dos últimos 90 dias tinham TSS do TP com IF implícito > 1,15 e foram recalculadas por FC. Causa típica: potência de corrida ativada no relógio sem limiar de potência de corrida específico no TP.",
                "epton_runpower",
                evidence=corr.t_local,
                sessions=len(corr),
            )
        )
    big = last90[(last90.tss_final > 450) & (~last90.multisport.fillna(False).astype(bool))]
    if len(big):
        out.append(
            F(
                "threshold_audit",
                "watch",
                "Sessões com TSS acima de 450 fora de prova",
                f"{len(big)} sessões com TSS > 450 que não são provas (máximo {big.tss_final.max():.0f}). Confirmar se são reais; um valor errado distorce o PMC por semanas.",
                "simmons_pmc",
                evidence=big.t_local,
                sessions=len(big),
                max_tss=big.tss_final.max(),
            )
        )
    ftp_used = A.get("ftp")
    bests = A.get("bests", {}).get("bike", [])
    recent = [b for b in bests if b["year"] >= TODAY.year - 1]
    p20 = max([b.get("p1200") or 0 for b in recent], default=0)
    if ftp_used and p20 and p20 * 0.95 > ftp_used * 1.08:
        out.append(
            F(
                "threshold_audit",
                "watch",
                "Melhor 20 min sugere FTP acima do usado",
                f"Melhor 20 min recente {p20:.0f} W × 0,95 = {p20*0.95:.0f} W contra FTP em uso de {ftp_used:.0f} W (diferença > 8%). O fator 0,95 é populacional; confirmar com a curva potência-duração antes de subir.",
                "stephenson_mmp",
                p20=p20,
                ftp=ftp_used,
            )
        )
    if not out:
        out.append(
            F(
                "threshold_audit",
                "ok",
                "Limiares e TSS coerentes",
                "Nenhuma sessão com IF implausível nos últimos 90 dias e nenhum outlier de TSS.",
                "coggan_np",
            )
        )
    return out


def skill_load_monitor(g, A):
    out = []
    daily = A["pmc"]["daily"]
    d = pd.DataFrame(daily)
    d["d"] = pd.to_datetime(d.d)
    d = d.set_index("d")
    # CTL ramp per 7 days over the last 8 weeks
    ramp = d.ctl.diff(7).tail(56)
    if (ramp > 7).any():
        out.append(
            F(
                "load_monitor",
                "alert",
                "Rampa de CTL acima de 7 pontos/semana",
                f"Nos últimos 8 semanas a fitness subiu mais de 7 pontos em 7 dias em {int((ramp > 7).sum())} dias (máximo +{ramp.max():.1f}). Simmons recomenda 5 a 7 pontos por semana como teto.",
                "simmons_total_load",
                max_ramp=ramp.max(),
                days=int((ramp > 7).sum()),
            )
        )
    low = d.tsb.tail(56)
    if (low < -30).any():
        out.append(
            F(
                "load_monitor",
                "alert",
                "Forma (TSB) abaixo de -30",
                f"{int((low < -30).sum())} dias nas últimas 8 semanas com TSB < -30 (mínimo {low.min():.0f}). Recomenda-se descanso consecutivo até voltar acima de -30.",
                "simmons_total_load",
                min_tsb=low.min(),
                days=int((low < -30).sum()),
            )
        )
    # weekly load jumps and lighter week in each 4-week window
    wk = pd.Series({pd.Timestamp(w["w"]): w["tss"] or 0 for w in A["weekly52"]}).sort_index()
    wk_full = wk.iloc[:-1] if week_start(TODAY) == wk.index[-1] else wk  # ignore the current partial week
    last8 = wk_full.tail(8)
    # a jump counts only when the week exceeds the highest of the previous 3 weeks by >10% (a rebound after a light week is expected)
    vals = list(last8.values)
    jumps = []
    for i in range(3, len(vals)):
        ref = max(vals[i - 3 : i])
        if ref > 100 and vals[i] > ref * 1.10:
            jumps.append((ref, vals[i]))
    if jumps:
        ref, cur = jumps[-1]
        out.append(
            F(
                "load_monitor",
                "watch",
                "Semana acima de 10% do máximo das 3 anteriores",
                f"{len(jumps)} das últimas semanas ultrapassaram em mais de 10% a maior das 3 semanas anteriores (última: {cur:.0f} TSS contra máximo anterior {ref:.0f}, +{100*(cur/ref-1):.0f}%). Regra de Simmons: evitar sobrecargas acima de 10%; um regresso ao normal depois de uma semana leve não conta.",
                "simmons_total_load",
                jumps=len(jumps),
                ref=ref,
                last_to=cur,
            )
        )
    last4 = wk_full.tail(4)
    if len(last4) == 4 and last4.min() > 0.85 * last4.mean() and last4.mean() > 150:
        out.append(
            F(
                "load_monitor",
                "watch",
                "Sem semana mais leve nas últimas 4",
                f"As últimas 4 semanas completas ficaram entre {last4.min():.0f} e {last4.max():.0f} TSS sem nenhuma pelo menos 15% abaixo da média. Uma semana de recuperação a cada 3 ou 4 é a regra.",
                "simmons_total_load",
                weeks=[float(x) for x in last4.values],
            )
        )
    tsb_now = d.tsb.iloc[-1]
    ctl_now = d.ctl.iloc[-1]
    flat = d.tsb.tail(14)
    if tsb_now > 25:
        out.append(
            F(
                "load_monitor",
                "watch",
                "Forma muito alta: fitness a cair",
                f"TSB hoje {tsb_now:+.0f}. Acima de +25 fora de um taper significa perda de fitness (CTL {ctl_now:.0f}).",
                "friel_tsb",
                tsb=tsb_now,
                ctl=ctl_now,
            )
        )
    elif 15 <= tsb_now <= 25:
        out.append(
            F(
                "load_monitor",
                "info",
                "Forma de dia de prova",
                f"TSB hoje {tsb_now:+.0f}: janela de +15 a +25 típica de taper. Se não há prova próxima, é semana de perda de fitness.",
                "friel_tsb",
                tsb=tsb_now,
                ctl=ctl_now,
            )
        )
    elif -30 <= tsb_now <= -10:
        out.append(
            F(
                "load_monitor",
                "ok",
                "Zona produtiva de carga",
                f"TSB hoje {tsb_now:+.0f}, dentro da faixa -10 a -30 em que a fitness cresce sem risco excessivo (CTL {ctl_now:.0f}).",
                "friel_tsb",
                tsb=tsb_now,
                ctl=ctl_now,
            )
        )
    elif (flat.between(-10, 10)).all():
        out.append(
            F(
                "load_monitor",
                "watch",
                "Estagnação: TSB entre -10 e +10 há 2 semanas",
                f"TSB manteve-se entre -10 e +10 nos últimos 14 dias (CTL {ctl_now:.0f}). Carga suficiente para manter, não para progredir.",
                "friel_tsb",
                tsb=tsb_now,
                ctl=ctl_now,
            )
        )
    return out


def skill_benchmark(g, A):
    wk = A["weekly52"][-5:-1] if len(A["weekly52"]) > 5 else A["weekly52"]
    tss = np.mean([w["tss"] or 0 for w in wk])
    mins = np.mean([(w["h"] or 0) * 60 for w in wk])
    km = np.mean([sum((w.get(f"km_{s}") or 0) for s in ("swim", "bike", "run")) for w in wk])
    long_ = (574, 615, 171)
    short = (452, 507, 118)
    ref = long_ if tss >= (long_[0] + short[0]) / 2 else short
    label = "longa distância" if ref is long_ else "curta distância"
    return [
        F(
            "benchmark",
            "info",
            f"Carga das últimas 4 semanas vs média age-group ({label})",
            f"Média semanal: {tss:.0f} TSS, {mins:.0f} min, {km:.0f} km, contra {ref[0]} TSS, {ref[1]} min e {ref[2]} km na amostra de 34.731 sessões de Wells ({label}). Pontos de referência amplos, não metas.",
            "wells",
            tss=tss,
            minutes=mins,
            km=km,
            ref_tss=ref[0],
            ref_min=ref[1],
            ref_km=ref[2],
        )
    ]


def skill_ef_trend(g, A):
    out = []
    b = g[(g.sport_n == "bike") & (g.duration_h >= 1.0) & (g["np"] > 0) & (g.avg_power > 0) & (g.avg_hr > 90)].copy()
    b["vi"] = b["np"] / b.avg_power
    b["ifx"] = b["np"] / b.ftp_used
    steady = b[(b.vi <= 1.06) & (b.ifx.between(0.55, 0.9))].copy()
    steady["ef"] = steady["np"] / steady.avg_hr
    cur = steady[steady.date >= TODAY - pd.Timedelta(weeks=12)]
    prev = steady[(steady.date < TODAY - pd.Timedelta(weeks=12)) & (steady.date >= TODAY - pd.Timedelta(weeks=24))]
    if len(cur) >= 4 and len(prev) >= 4:
        ch = 100 * (cur.ef.median() / prev.ef.median() - 1)
        sev = "ok" if ch >= 2 else ("watch" if ch <= -3 else "info")
        out.append(
            F(
                "ef_trend",
                sev,
                f"Eficiência aeróbia no bike {'subiu' if ch > 0 else 'desceu'} {abs(ch):.1f}%",
                f"EF (NP/FC média) em pedais constantes ≥ 1 h (VI ≤ 1,06, IF 0,55 a 0,90): mediana {cur.ef.median():.2f} nas últimas 12 semanas ({len(cur)} sessões) vs {prev.ef.median():.2f} nas 12 anteriores ({len(prev)}). EF deve subir ao longo de um bloco aeróbio; estagnação pede mudança de fase.",
                "friel_decoupling",
                ef_now=cur.ef.median(),
                ef_prev=prev.ef.median(),
                change_pct=ch,
                n_now=len(cur),
                n_prev=len(prev),
            )
        )
    else:
        out.append(
            F(
                "ef_trend",
                "info",
                "Poucos pedais constantes para medir eficiência aeróbia",
                f"Só {len(cur)} pedais constantes de 1 h ou mais nas últimas 12 semanas ({len(prev)} nas 12 anteriores). São precisos 4 por período; sessões de 2 a 4 h abaixo do limiar são o benchmark recomendado.",
                "help_ef",
                n_now=len(cur),
                n_prev=len(prev),
            )
        )
    r = g[
        (g.sport_n == "run")
        & (g.duration_h >= 0.75)
        & (g.avg_speed > 1.5)
        & (g.avg_hr > 90)
        & (~g.multisport.fillna(False).astype(bool))
    ].copy()
    r["ef"] = r.avg_speed * 60 / r.avg_hr  # metres per beat
    cur = r[r.date >= TODAY - pd.Timedelta(weeks=12)]
    prev = r[(r.date < TODAY - pd.Timedelta(weeks=12)) & (r.date >= TODAY - pd.Timedelta(weeks=24))]
    if len(cur) >= 4 and len(prev) >= 4:
        ch = 100 * (cur.ef.median() / prev.ef.median() - 1)
        sev = "ok" if ch >= 2 else ("watch" if ch <= -3 else "info")
        out.append(
            F(
                "ef_trend",
                sev,
                f"Eficiência aeróbia na corrida {'subiu' if ch > 0 else 'desceu'} {abs(ch):.1f}%",
                f"Velocidade por batimento em corridas de 45+ min: {cur.ef.median():.2f} m/bat nas últimas 12 semanas ({len(cur)} corridas) vs {prev.ef.median():.2f} antes ({len(prev)}). Sem dados de ritmo constante por sessão, é uma aproximação; calor e terreno também mexem.",
                "friel_decoupling",
                ef_now=cur.ef.median(),
                ef_prev=prev.ef.median(),
                change_pct=ch,
            )
        )
    return out


def skill_race_analysis(g, A):
    out = []
    for r in [x for x in A.get("races", []) if x.get("kind") == "race"][-4:]:
        b = r["by"].get("bike")
        rn = r["by"].get("run")
        if not b:
            continue
        km = b["km"] or 0
        row = g[(g.multisport.fillna(False).astype(bool)) & (g.sport_n == "bike") & (g.date == pd.Timestamp(r["date"]))]
        if not len(row) or not (row["np"].iloc[0] > 0):
            continue
        rw = row.iloc[0]
        ifx = rw["np"] / rw.ftp_used if rw.ftp_used else None
        vi = rw["np"] / rw.avg_power if rw.avg_power else None
        if km >= 150:
            tgt, lbl, src = (0.65, 0.75), "Ironman age-group 0,65 a 0,75", "johnson_kona"
        elif km >= 70:
            tgt, lbl, src = (0.83, 0.87), "70.3 ótimo 0,83 a 0,87", "thomas_703"
        else:
            tgt, lbl, src = (0.90, 1.04), "olímpico/sprint 0,90 a 1,04", "friel_tsb"
        if ifx is None:
            continue
        if ifx > tgt[1] + 0.03:
            sev, verdict = "watch", "acima do alvo: bike agressivo para a corrida que se segue"
        elif ifx < tgt[0] - 0.03:
            sev, verdict = (
                "info",
                "abaixo do alvo publicado: bike conservador (aceitável se foi para proteger a corrida; caso contrário sobrou bike)",
            )
        else:
            sev, verdict = "ok", "dentro do alvo"
        vi_txt = f", VI {vi:.2f} ({'irregular, alvo ≤ 1,05' if vi and vi > 1.05 else 'regular'})" if vi else ""
        run_txt = (
            f" Corrida: {rn['km']} km a {int(rn['pace'])}:{int(round((rn['pace']%1)*60)):02d}/km."
            if rn and rn.get("pace")
            else ""
        )
        if vi and vi > 1.08 and sev == "ok":
            sev = "watch"
        out.append(
            F(
                "race_analysis",
                sev,
                f"Prova {r['date']} ({r['title']}): IF {ifx:.2f}{vi_txt}",
                f"Bike {km:.0f} km, NP {rw['np']:.0f} W com FTP {rw.ftp_used:.0f} W: IF {ifx:.2f}, {verdict} ({lbl}, fonte fora da categoria Science).{run_txt}",
                src,
                date=r["date"],
                if_=ifx,
                vi=vi,
                np=rw["np"],
                ftp=rw.ftp_used,
                bike_km=km,
            )
        )
    return out


def skill_race_readiness(g, A):
    out = []
    end = TODAY
    w16 = g[(g.date > end - pd.Timedelta(weeks=16))]
    b = w16[w16.sport_n == "bike"]
    r = w16[w16.sport_n == "run"]
    s = w16[w16.sport_n == "swim"]
    peak = A["pmc"]["peak_52w"]["ctl"]
    ctl = A["pmc"]["now"]["ctl"]
    # race TSS estimates from the athlete's own past race durations (or defaults) at the target IFs
    dur_b = max(
        [
            x["by"]["bike"]["h"]
            for x in A.get("races", [])
            if x.get("kind") == "race" and x["by"].get("bike") and x["by"]["bike"]["km"] >= 150
        ]
        or [6.0]
    )
    dur_r = max(
        [
            x["by"]["run"]["h"]
            for x in A.get("races", [])
            if x.get("kind") == "race" and x["by"].get("run") and x["by"]["run"]["km"] >= 35
        ]
        or [4.5]
    )
    est_im = dur_b * 0.70**2 * 100 + dur_r * 0.75**2 * 100 + 1.3 * 0.7**2 * 100
    ratio = est_im / peak if peak else float("nan")
    out.append(
        F(
            "race_readiness",
            "info",
            f"Um full custaria ~{est_im:.0f} TSS; pico de CTL do ano {peak:.0f} ({ratio:.1f}x)",
            f"Estimativa com os teus tempos de prova anteriores a IF 0,70 no bike e 0,75 na corrida. A regra 'TSS de prova ≈ pico de CTL' foi validada em maratona, não em triatlo: num Ironman 4 a 5 vezes o CTL é normal. Usa a razão como tendência entre épocas, não como veredito. CTL hoje {ctl:.0f}.",
            "simmons_running",
            race_tss=est_im,
            peak_ctl=peak,
            ctl_now=ctl,
            ratio=ratio,
        )
    )
    longest_ride = b.duration_h.max() if len(b) else 0
    rides3 = int((b.duration_h >= 3).sum())
    longest_run = r.distance_km.max() if len(r) else 0
    runs18 = int((r.distance_km >= 18).sum())
    ow = int((s.sub_sport == "open_water").sum())
    ladder = f"Últimas 16 semanas: pedal mais longo {longest_ride:.1f} h ({rides3} pedais ≥ 3 h), corrida mais longa {longest_run:.1f} km ({runs18} corridas ≥ 18 km), {ow} natações em águas abertas, {b.ascent_m.sum()/16:.0f} m de subida por semana."
    sev = "alert" if (rides3 == 0 and runs18 == 0) else ("watch" if rides3 < 4 or runs18 < 2 else "ok")
    out.append(
        F(
            "race_readiness",
            sev,
            "Escada de sessões longas",
            ladder
            + " Durabilidade constrói-se com volume abaixo do LT1 e sessões longas repetidas; sem elas não há como medir fade.",
            "rowe_kj",
            longest_ride_h=longest_ride,
            rides_3h=rides3,
            longest_run_km=longest_run,
            runs_18km=runs18,
            open_water=ow,
        )
    )
    return out


def skill_week_structure(g, A):
    out = []
    w4 = g[(g.date > TODAY - pd.Timedelta(weeks=4)) & (g.duration_h > 0.4)].copy()
    w4["hard"] = (w4.if_final >= 0.85) | ((w4.tss_final / w4.duration_h) >= 85)
    hard = w4[w4.hard].sort_values("t_local")
    per_week = hard.groupby(hard.date.map(week_start)).size()
    consecutive = 0
    days = sorted(set(hard.date))
    for a, b_ in zip(days[:-1], days[1:]):
        if (b_ - a).days == 1:
            consecutive += 1
    age = TODAY.year - (A.get("birth_year") or override(A["name"], "birth_year", DEFAULT_BIRTH_YEAR))
    if (per_week > 3).any() or consecutive >= 2:
        out.append(
            F(
                "week_structure",
                "watch",
                "Sessões duras demasiado próximas",
                f"Nas últimas 4 semanas: até {int(per_week.max()) if len(per_week) else 0} sessões duras numa semana e {consecutive} pares em dias consecutivos. Limiar pede 24 a 48 h de intervalo, VO2/HIIT mais de 48 h; dois HIIT seguidos é padrão de risco."
                + (
                    f" Com {age} anos, avaliar em ciclos de 9 a 10 dias com cerca de 2 sessões de qualidade." if age >= 45 else ""
                ),
                "wegerif_stress",
                max_hard_week=int(per_week.max()) if len(per_week) else 0,
                consecutive_pairs=consecutive,
                age=age,
            )
        )
    else:
        out.append(
            F(
                "week_structure",
                "ok",
                "Espaçamento das sessões duras adequado",
                f"Nas últimas 4 semanas, no máximo {int(per_week.max()) if len(per_week) else 0} sessões duras por semana e {consecutive} pares em dias consecutivos.",
                "wegerif_stress",
                max_hard_week=int(per_week.max()) if len(per_week) else 0,
                consecutive_pairs=consecutive,
            )
        )
    share = w4[w4.hard].duration_h.sum() / max(w4.duration_h.sum(), 0.1) * 100
    out.append(
        F(
            "week_structure",
            "watch" if share > 20 else "info",
            f"{share:.0f}% do tempo em sessões duras",
            f"Nas últimas 4 semanas, {share:.0f}% das horas foram em sessões com IF ≥ 0,85 ou ≥ 85 TSS/h. Referências apontam 10 a 15% do tempo em alta intensidade (distribuição 65/25/10; regra RPE 90/9/1).",
            "wegerif_stress",
            hard_share_pct=share,
        )
    )
    return out


def skill_power_profile(g, A):
    out = []
    b = g[(g.sport_n == "bike") & (~g.power_suspect.fillna(False).astype(bool)) & (g.p_best_300s > 0)]
    d90 = b[b.date >= TODAY - pd.Timedelta(days=90)]
    d365 = b[b.date >= TODAY - pd.Timedelta(days=365)]
    if not len(d365):
        return out
    stale = []
    for w, lbl in [(60, "1 min"), (300, "5 min"), (1200, "20 min"), (3600, "60 min")]:
        c = f"p_best_{w}s"
        b90 = d90[c].max() if len(d90) else np.nan
        b365 = d365[c].max()
        if b365 > 0 and (not (b90 > 0) or b90 < 0.9 * b365):
            stale.append(f"{lbl}: 90 d {b90 if b90 == b90 else 0:.0f} W vs 12 m {b365:.0f} W")
    if stale:
        out.append(
            F(
                "power_profile",
                "watch",
                "Melhores de potência com mais de 90 dias",
                "O TP considera 'atual' a janela de 90 dias. Durações abaixo de 90% do melhor de 12 meses: "
                + "; ".join(stale)
                + ". Re-verificar antes de usar para zonas ou previsão.",
                "stephenson_mmp",
                stale=len(stale),
            )
        )
    p5 = d365.p_best_300s.max()
    p20 = d365.p_best_1200s.max()
    p60 = d365.p_best_3600s.max()
    if p5 and p20 and p60:
        ratio = p60 / p5
        pheno = (
            "diesel/time-trialist (curva plana, adequado a longa distância)"
            if ratio >= 0.82
            else ("equilibrado" if ratio >= 0.74 else "explosivo (queda forte com a duração; a base aeróbia é o ganho maior)")
        )
        out.append(
            F(
                "power_profile",
                "info",
                f"Perfil de potência: {pheno.split(' (')[0]}",
                f"Melhores 12 meses: 5 min {p5:.0f} W, 20 min {p20:.0f} W, 60 min {p60:.0f} W (60/5 min = {ratio:.2f}). {pheno}.",
                "stephenson_mmp",
                p5=p5,
                p20=p20,
                p60=p60,
                ratio=ratio,
            )
        )
    return out


KIND_LABEL = {
    "lesao": "Lesão",
    "doenca": "Doença",
    "pausa": "Pausa planeada / férias",
    "familia": "Parentalidade / família",
    "fora_epoca": "Fora de época",
}


def load_context(name):
    """Context periods registered in the app (injury, break...). Empty when the app database does not exist."""
    import sqlite3

    db = Path(os.environ.get("TRIAPP_DB", str(OUT / "app.sqlite")))
    if not db.exists():
        return []
    try:
        con = sqlite3.connect(db)
        con.row_factory = sqlite3.Row
        rows = [
            dict(r)
            for r in con.execute("SELECT kind,start,end,note FROM athlete_context WHERE athlete=? ORDER BY start", (name,))
        ]
        con.close()
        return rows
    except Exception:
        return []


def load_acks(name):
    """Findings the athlete or the coach chose to ignore (app table finding_ack): [{title, evidence, who, at}]."""
    import sqlite3

    db = Path(os.environ.get("TRIAPP_DB", str(OUT / "app.sqlite")))
    if not db.exists():
        return []
    try:
        con = sqlite3.connect(db)
        rows = con.execute("SELECT title, evidence, created_by, created_at FROM finding_ack WHERE athlete=?", (name,)).fetchall()
        con.close()
        return [{"title": t, "evidence": json.loads(e), "who": w, "at": (a or "")[:10]} for t, e, w, a in rows]
    except Exception:
        return []


def apply_acks(findings, acks):
    """An ignored finding becomes info while its sessions are all ones already ignored; a new session brings the alert back."""
    for f in findings:
        if not f.get("evidence") or f["severity"] not in ("alert", "watch"):
            continue
        mine = [a for a in acks if a["title"] == f["title"]]
        seen = set().union(*(a["evidence"] for a in mine)) if mine else set()
        if mine and set(f["evidence"]) <= seen:
            last = max(mine, key=lambda a: a["at"])
            f["ignored"] = {"was": f["severity"], "who": last["who"], "at": last["at"]}
            f["severity"] = "info"
            f[
                "detail"
            ] += f" Ignorado por {last['who']} em {last['at']}: estas sessões já foram vistas; o alerta volta se aparecer outra assim."
    return findings


def apply_context(findings, ctx):
    """Context changes the reading: load increases within 16 weeks after an injury/break are a return, not a red flag."""
    today = TODAY.strftime("%Y-%m-%d")
    out = []
    for c in ctx:
        end = c["end"] or today
        weeks_since = (TODAY - pd.Timestamp(end)).days // 7
        ongoing = (not c["end"] or c["end"] >= today) and c["start"] <= today
        lbl = KIND_LABEL.get(c["kind"], c["kind"])
        out.append(
            {
                "skill": "context",
                "severity": "info",
                "title": f"{lbl}: {c['start']} a {c['end'] or 'em curso'}",
                "detail": (c["note"] or "Sem nota.")
                + (
                    " Em curso: a queda de carga e de CTL é esperada."
                    if ongoing
                    else f" Terminou há {weeks_since} semanas: subidas de carga avaliam-se como retoma."
                ),
                "source": "Registado na app pelo atleta ou treinador",
                "url": "",
                "metrics": {"weeks_since_end": weeks_since},
            }
        )
    recent = [
        c
        for c in ctx
        if c["kind"] in ("lesao", "doenca", "pausa", "familia")
        and (not c["end"] or (TODAY - pd.Timestamp(c["end"])).days <= 16 * 7)
    ]
    if recent:
        for f in findings:
            if (
                f["skill"] == "load_monitor"
                and f["severity"] in ("watch", "alert")
                and ("10%" in f["title"] or "Rampa" in f["title"])
            ):
                f["severity"] = "info"
                f["title"] += " (retoma)"
                f[
                    "detail"
                ] += " Há um período de lesão ou pausa recente registado: esta subida é retoma. Mantém a rampa de CTL até 5 a 7 pontos por semana e evita semanas mais de 10% acima das anteriores."
            if f["skill"] == "load_monitor" and "Estagnação" in f["title"]:
                f["severity"] = "info"
            if f["skill"] == "race_readiness" and f["severity"] == "alert":
                f["severity"] = "watch"
            if f["skill"] == "consistency" and f["severity"] in ("watch", "alert"):
                f["severity"] = "info"
                f["title"] += " (período registado)"
                f[
                    "detail"
                ] += " Há uma lesão, doença ou pausa registada neste período: semanas abaixo do plano são esperadas e não contam como falta de consistência."
    return out + findings


def skill_consistency(g, A):
    """Coach's rule: one complete week at <= 50% of the planned sessions is a yellow flag; 2 of the last 3 weeks at that level,
    or weekly load swinging a lot (coefficient of variation of weekly hours > 0.5 over 6 weeks) is red."""
    out = []
    # only weeks the data fully covers: a week cut by the end of the last export would read as a missed week
    last = pd.Timestamp(A.get("last_date") or TODAY)
    this_week = min(
        week_start(TODAY),
        week_start(last + pd.Timedelta(days=1)) if (last + pd.Timedelta(days=1)).weekday() else last + pd.Timedelta(days=1),
    ).strftime("%Y-%m-%d")
    wk = [w for w in (A.get("weekly52") or []) if w["w"] < this_week][-6:]
    if not wk or sum(w["h"] or 0 for w in wk) == 0:
        return out
    hours = [w["h"] or 0 for w in wk]
    cv = float(np.std(hours) / np.mean(hours)) if len(hours) >= 4 and np.mean(hours) > 0 else None
    C = A.get("compliance") or {}
    # only weeks of the last 6 with a plan, and only when one of the last 3 has one: weeks without a plan (an export that
    # stopped) are skipped, and a window stretched back two months would judge the athlete on an old block
    tw = pd.Timestamp(this_week)
    cw = [
        w
        for w in C.get("weeks", [])
        if (tw - pd.Timedelta(weeks=6)).strftime("%Y-%m-%d") <= w["w"] < this_week and w["planned"] >= 2
    ]
    if not any(w["w"] >= (tw - pd.Timedelta(weeks=3)).strftime("%Y-%m-%d") for w in cw):
        cw = []
    rates = [(w["planned_done"] + w["matched"]) / w["planned"] for w in cw]
    swing = cv is not None and cv > 0.5
    hrs_txt = (
        f"Horas por semana nas últimas {len(hours)}: "
        + ", ".join(f"{h:.1f}" for h in hours)
        + (f" (variação {cv:.0%})." if cv is not None else ".")
    )
    if rates:
        last = rates[-1]
        low3 = sum(r <= 0.5 for r in rates[-3:])
        rtxt = "Cumprimento do plano por semana: " + ", ".join(f"{r:.0%}" for r in rates) + "."
        if low3 >= 2 or (swing and last <= 0.5):
            out.append(
                F(
                    "consistency",
                    "alert",
                    (
                        "Consistência baixa: plano falhado em semanas seguidas"
                        if low3 >= 2
                        else "Consistência baixa: carga muito irregular"
                    ),
                    f"{rtxt} {hrs_txt} Duas ou mais das últimas 3 semanas ficaram em 50% ou menos dos treinos planeados, ou a carga varia muito de semana para semana. A consistência pesa mais na evolução do que qualquer sessão isolada.",
                    "coach_consistency",
                    last_week_rate=last,
                    low_weeks_of_3=low3,
                    weekly_hours_cv=cv,
                )
            )
        elif last <= 0.5 or swing:
            out.append(
                F(
                    "consistency",
                    "watch",
                    "Semana abaixo do plano" if last <= 0.5 else "Carga irregular de semana para semana",
                    f"{rtxt} {hrs_txt} "
                    + (
                        "A última semana completa ficou em 50% ou menos dos treinos planeados: sinal amarelo; se se repetir na próxima, passa a vermelho."
                        if last <= 0.5
                        else "O cumprimento está bem, mas o volume semanal varia muito; semanas regulares adaptam melhor."
                    ),
                    "coach_consistency",
                    last_week_rate=last,
                    low_weeks_of_3=low3,
                    weekly_hours_cv=cv,
                )
            )
        else:
            out.append(
                F(
                    "consistency",
                    "ok",
                    f"Consistência boa: {np.mean(rates):.0%} do plano cumprido",
                    f"{rtxt} {hrs_txt} Nenhuma semana recente abaixo de 50% do planeado.",
                    "coach_consistency",
                    last_week_rate=last,
                    mean_rate=float(np.mean(rates)),
                    weekly_hours_cv=cv,
                )
            )
    elif hours:
        if swing:
            out.append(
                F(
                    "consistency",
                    "watch",
                    "Carga irregular de semana para semana",
                    f"Sem plano registado nas últimas 3 semanas. {hrs_txt} O volume semanal varia muito; semanas regulares adaptam melhor.",
                    "coach_consistency",
                    weekly_hours_cv=cv,
                )
            )
        else:
            out.append(
                F(
                    "consistency",
                    "info",
                    "Volume semanal regular",
                    f"Sem plano registado nas últimas 3 semanas, por isso só se avalia a regularidade. {hrs_txt}",
                    "coach_consistency",
                    weekly_hours_cv=cv,
                )
            )
    return out


SKILLS = [
    skill_threshold_audit,
    skill_load_monitor,
    skill_consistency,
    skill_benchmark,
    skill_ef_trend,
    skill_race_analysis,
    skill_race_readiness,
    skill_week_structure,
    skill_power_profile,
]


# ---------------------------------------------------------------- narrative
def load_skill_docs():
    docs = []
    for p in sorted(SKILLS_DIR.glob("*.md")):
        docs.append(p.read_text(encoding="utf-8"))
    return "\n\n".join(docs)


def narrative(name, findings, tone=""):
    """The reading of the findings; `tone` (voice.tone) adds the athlete's personality to the system prompt."""
    if not llm.available():
        return None, None
    facts = json.dumps(
        [{k: f[k] for k in ("skill", "severity", "title", "detail")} for f in findings], ensure_ascii=False, indent=0
    )
    system = (
        "És um assistente de treinador de triatlo. Escreve em português europeu, tom direto e concreto, sem floreados. "
        "Usa APENAS os factos fornecidos; não inventes números nem sessões. A gravidade segue os rótulos: 'alert' pede ação, 'watch' pede atenção, 'ok' é bom sinal, 'info' é contexto; "
        "se não houver 'alert', o tom é tranquilo e não fales em risco imediato. "
        + "Se o CONTEXTO tiver uma lesão ou doença em curso, ou terminada há menos de 8 semanas: começa a resposta por aí; deduz da nota a disciplina afetada (pé, tornozelo, perna ou joelho afeta sobretudo a corrida; ombro ou braço afeta a natação; costas ou anca podem afetar todas); para essa disciplina, as metas de volume das skills (corridas longas, pedais longos) passam a ser de médio prazo, a progressão é prudente (as outras disciplinas podem carregar primeiro) e diz explicitamente que o regresso à carga nessa disciplina deve ter autorização do médico ou fisioterapeuta. Uma pausa planeada ou de família terminada há pouco é retoma, sem tom de alarme. "
        + "Escolhe os 3 a 5 achados que mais importam agora, diz o que significam em conjunto "
        "e termina com 2 recomendações práticas para a próxima semana. Máximo 180 palavras. Trata o atleta por tu, na segunda pessoa; nunca escrevas na primeira pessoa como se fosses o atleta. Texto simples: sem LaTeX nem fórmulas entre cifrões; escreve ≥ e ≤ como símbolos."
    )
    user = f"Atleta: {name}. Achados das skills (JSON; os de skill 'context' são lesões ou pausas registadas pelo atleta/treinador e mudam a leitura da carga):\n{facts}"
    try:
        text = llm.chat(
            [{"role": "system", "content": system + tone}, {"role": "user", "content": user}], temperature=0.3, think=True
        )
        return text, llm.MODEL
    except Exception as e:
        return f"(narrativa indisponível: {type(e).__name__})", None


def main():
    df = pd.read_csv(OUT / "data" / "activities_all.csv", parse_dates=["t_local", "date"], low_memory=False)
    dash = json.loads((OUT / "data" / "dashboard_data.json").read_text(encoding="utf-8"))
    done = df[df.completed & (df.sport_n != "dayoff") & (df.duration_h > 0)].copy()
    only = [a for a in sys.argv[1:] if a in dash["athletes"]]
    prev_path = OUT / "data" / "analysis.json"
    result = json.loads(prev_path.read_text(encoding="utf-8")) if (only and prev_path.exists()) else {}
    for name, A in dash["athletes"].items():
        if only and name not in only:
            continue
        g = done[done.athlete == name]
        findings = []
        recent = g[g.date >= TODAY - pd.Timedelta(days=90)]
        if len(recent) < 5:
            last = g.date.max().strftime("%Y-%m-%d") if len(g) else "nunca"
            findings = [
                {
                    "skill": "load_monitor",
                    "severity": "info",
                    "title": "Sem treinos suficientes nos últimos 90 dias",
                    "detail": f"Só {len(recent)} sessões registadas nos últimos 90 dias (última em {last}). As skills de carga e forma precisam de dados recentes; o histórico continua visível abaixo.",
                    "source": "",
                    "url": "",
                    "metrics": {"sessions_90d": int(len(recent))},
                }
            ]
            result[name] = {
                "generated": datetime.now().strftime("%Y-%m-%d %H:%M"),
                "findings": findings,
                "narrative": None,
                "narrative_model": None,
                "counts": {"alert": 0, "watch": 0, "ok": 0, "info": 1},
            }
            print(f"{name}: inactive ({len(recent)} sessions in 90 days), skills skipped")
            continue
        for sk in SKILLS:
            try:
                findings.extend(sk(g, A))
            except Exception as e:
                findings.append(
                    {
                        "skill": sk.__name__.replace("skill_", ""),
                        "severity": "info",
                        "title": "Skill não executada",
                        "detail": f"{type(e).__name__}: {e}",
                        "source": "",
                        "url": "",
                        "metrics": {},
                    }
                )
        ctx = load_context(name)
        findings = apply_context(findings, ctx)
        findings = apply_acks(findings, load_acks(name))
        order = {"alert": 0, "watch": 1, "ok": 2, "info": 3}
        findings.sort(key=lambda f: (0 if f["skill"] == "context" else 1, order.get(f["severity"], 9)))
        text, model = narrative(name, findings)
        # the athlete's voice for their own view (the coach reads the neutral text); an ongoing injury or illness keeps it neutral
        v = voice.get(name)
        if voice.health_ongoing(ctx, TODAY.strftime("%Y-%m-%d")):
            v = voice.NEUTRAL
        text_v = narrative(name, findings, voice.tone(v))[0] if (v != voice.NEUTRAL and model) else None
        result[name] = {
            "generated": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "findings": findings,
            "narrative": text,
            "narrative_voice": text_v,
            "voice": v,
            "narrative_model": model,
            "counts": {s: sum(1 for f in findings if f["severity"] == s) for s in order},
        }
        print(f"{name}: {result[name]['counts']}  narrative={'yes' if model else 'no'}")
    (OUT / "data" / "analysis.json").write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    print("written", OUT / "data" / "analysis.json")


if __name__ == "__main__":
    main()

"""App Store screenshots (iPhone 6.9", 1320x2868) from the real pages with the demo athlete (no real names).

Renders each tab with headless Chrome at 440x956 CSS px and 3x scale, inside an iframe (headless Chrome will not make a
window narrower than 500 px), then frames it with a headline. Needs the dashboard data in TRIAPP_OUT (copy the box's
out/data/*.json) and Google Chrome.

Usage: TRIAPP_OUT=<dir with data/> TRIAPP_SECRET=x python native/appstore/make_screenshots.py <output dir>
"""

import copy, json, os, re, subprocess, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from PIL import Image, ImageDraw, ImageFont  # noqa: E402
from app import render, demo  # noqa: E402

CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
W, H, SCALE = 440, 956, 3
OUT_W, OUT_H = 1320, 2868
SHOTS = [
    ("treinos", 0, "O teu treino de hoje,\nexplicado", "plano do treinador e relatório do dia anterior"),
    ("treinos", 1500, "Cada treino com\no que treinou", "tipo de treino pelo tempo em cada zona"),
    ("analise", 0, "A tua semana à luz\ndas regras de treino", "e um assistente que responde sobre os teus dados"),
    ("dados", 1000, "Forma, fadiga\ne carga semanal", "CTL, ATL, TSB e TSS de cada semana"),
    ("prova", 0, "Quanto vais fazer\nna tua prova", "previsão por segmento, com o teu CdA e o percurso"),
]


def page_html():
    user = {"role": "athlete", "athlete": demo.DEMO, "name": demo.NAME, "email": "demo@laloop.ai"}
    html = render.page(user, [demo.DEMO])
    # richer than the plain demo: today's plan and the morning brief of the source athlete (no names or injuries in them)
    d = render.load_data()
    src = d["athletes"][demo.SOURCE]
    m = re.search(r'(<script id="dash-data" type="application/json">)(.*?)(</script>)', html, re.S)
    data = json.loads(m.group(2).replace("<\\/", "</"))
    A = data["athletes"][demo.DEMO]
    A["planned"] = copy.deepcopy(src.get("planned", []))
    b = src.get("brief")
    if b:
        A["brief"] = {k: b[k] for k in ("date", "today", "text", "report") if k in b}
    data["viewer"]["voice"] = "treinador"
    an = (d.get("analysis") or {}).get(demo.SOURCE) or {}
    narr = an.get("narrative") or ""
    if narr and not re.search(r"fratura|lesão|doen", narr, re.I):  # the written reading, without the athlete's name
        data["analysis"][demo.DEMO]["narrative"] = re.sub(rf"^{demo.SOURCE},\s*(\w)", lambda m: m.group(1).upper(), narr).replace(
            demo.SOURCE, demo.NAME
        )
        data["analysis"][demo.DEMO]["narrative_model"] = an.get("narrative_model")
    raw = json.dumps(data, separators=(",", ":")).replace("</", "<\\/")
    return html[: m.start(2)] + raw + html[m.end(2) :]


def shoot(html, sub, scroll, path):
    page = path.with_suffix(".page.html")
    tag = (
        f"<script>try{{localStorage.setItem('tri-sub','{sub}')}}catch(e){{}}"
        f"addEventListener('load',()=>setTimeout(()=>scrollTo(0,{scroll}),800));</script>"
    )
    page.write_text(html.replace('<meta name="viewport"', tag + '<meta name="viewport"', 1), encoding="utf-8")
    frame = path.with_suffix(".frame.html")
    frame.write_text(
        f"<!doctype html><body style='margin:0;background:#111311'><iframe id=f src='{page.name}' "
        f"style='border:0;width:{W}px;height:{H}px'></iframe>",
        encoding="utf-8",
    )
    subprocess.run(
        [
            CHROME,
            "--headless=new",
            "--disable-gpu",
            "--hide-scrollbars",
            f"--force-device-scale-factor={SCALE}",
            "--virtual-time-budget=9000",
            f"--window-size={W + 80},{H}",
            f"--screenshot={path}",
            f"file://{frame}",
        ],
        check=True,
        capture_output=True,
    )
    Image.open(path).crop((0, 0, W * SCALE, H * SCALE)).save(path)
    page.unlink()
    frame.unlink()


def font(size, bold=False):
    for f in ("/System/Library/Fonts/SFNS.ttf", "/System/Library/Fonts/Helvetica.ttc", "/Library/Fonts/Arial.ttf"):
        try:
            return ImageFont.truetype(f, size, index=1 if (bold and f.endswith(".ttc")) else 0)
        except Exception:
            continue
    return ImageFont.load_default()


def framed(shot, title, sub, path):
    bg = Image.new("RGB", (OUT_W, OUT_H), "#111311")
    d = ImageDraw.Draw(bg)
    y = 150
    for line in title.split("\n"):
        f = font(96, True)
        w = d.textlength(line, font=f)
        d.text(((OUT_W - w) / 2, y), line, font=f, fill="#ffffff")
        y += 118
    f = font(48)
    w = d.textlength(sub, font=f)
    d.text(((OUT_W - w) / 2, y + 20), sub, font=f, fill="#c3c2b7")
    top = y + 140
    img = Image.open(shot).convert("RGB")
    target_h = OUT_H - top - 90
    s = target_h / img.height
    img = img.resize((int(img.width * s), target_h), Image.LANCZOS)
    mask = Image.new("L", img.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, img.width - 1, img.height - 1), radius=56, fill=255)
    x = (OUT_W - img.width) // 2
    ImageDraw.Draw(bg).rounded_rectangle((x - 6, top - 6, x + img.width + 5, top + img.height + 5), radius=62, fill="#2c2c2a")
    bg.paste(img, (x, top), mask)
    bg.save(path)


def main(out):
    out = Path(out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    html = page_html()
    for i, (sub, scroll, title, caption) in enumerate(SHOTS, 1):
        raw = out / f"raw-{i}.png"
        shoot(html, sub, scroll, raw)
        framed(raw, title, caption, out / f"iphone69-{i}.png")
        raw.unlink()
        print("written", out / f"iphone69-{i}.png")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "appstore-screens")

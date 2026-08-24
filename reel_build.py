"""reel_build.py — вертикальний рілс 1080x1920 з JSON-специфікації. Порт прототипу під VPS.

Порт креативного build_reel у репо для автономного рендеру рілсів на VPS (прибрати ручний scp).
Відмінності від прототипу (scratchpad):
  * вхід — JSON-файл (не хардкод REELS): {out, endline, endscene, items:[{id,hook,name,spec:[stat,tail],image?}]};
  * шрифти — стратегія репо (Segoe локально → DejaVu на VPS), як у plutus_overlay; НЕ Lato (його нема);
  * сегмент товару генерується INLINE (reel_prep_product → reel_segment), не читається з seg/*.mp4;
  * ендкарта проходить SOURCE-SIDE matte-QA (зелена нитка хромакею → СТОП) — механізм «щоб не повторювалось»;
  * наявність звіряється по локальному feeds/meta_feed.xml.

╔══════════════════════════════════════════════════════════════════════════════╗
║ ПРАВИЛО №0 — ЦІНУ В КАДР НЕ ПИСАТИ. Відео не редагується після публікації,    ║
║ ціна — так; тому в кадрі непсувний маркер-SPEC, ціна друкується в лог для     ║
║ підпису. НАЯВНІСТЬ звіряється жорстко перед кожним товаром і валить збірку.   ║
║ ПРАВИЛО №1 — МАСКОТ ТІЛЬКИ НА СВІТЛУ ПОВЕРХНЮ (mascot_on_light має assert).    ║
╚══════════════════════════════════════════════════════════════════════════════╝

Запуск:  python reel_build.py spec.json        # рендерить один рілс за специфікацією
"""
import json
import os
import re
import sys
import tempfile
import urllib.request
import xml.etree.ElementTree as ET

import imageio
import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from reel_matte import clean_mascot   # noqa: E402  чистий виріз маскота (без плити-«підлоги»)
import reel_prep_product              # noqa: E402  кадрування фото товару в 1080²
import reel_segment                   # noqa: E402  зум-пульсація сегмента

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

FEED = os.path.join(HERE, "feeds", "meta_feed.xml")
OUT = os.path.join(HERE, "reels")
NS = {"g": "http://base.google.com/ns/1.0"}

W, H = 1080, 1920
FPS = 30
CARD = 1000
CARD_X = (W - CARD) // 2
CARD_TOP = 430
CARD_R = 52
SAFE_TOP, SAFE_BOT = 120, H - 220

EC_W, EC_H, EC_TOP = CARD, 620, 370
EC_X = (W - EC_W) // 2
MASCOT_MAX_H = 470
FOOT_INSET = 52

# Стратегія шрифтів як у plutus_overlay: Segoe локально (Windows) → DejaVu на VPS (Linux). Lato нема.
_F_BOLD = ["segoeuib.ttf", "C:/Windows/Fonts/segoeuib.ttf",
           "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", "DejaVuSans-Bold.ttf"]
_F_REG = ["segoeui.ttf", "C:/Windows/Fonts/segoeui.ttf",
          "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "DejaVuSans.ttf"]
_F_SEMI = ["seguisb.ttf", "C:/Windows/Fonts/seguisb.ttf",
           "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", "DejaVuSans-Bold.ttf"]


def _font(paths, size):
    for p in paths:
        try:
            return ImageFont.truetype(p, size)
        except OSError:
            continue
    return ImageFont.load_default()


T = {
    "bg_top": (255, 247, 238), "bg_bot": (255, 224, 196),
    "ink": (46, 34, 28),
    "muted": (122, 98, 84),
    "accent_fill": (232, 122, 42),
    "accent_text": (198, 88, 20),
    "on_accent": (255, 255, 255),
    "card_bg": (255, 255, 255), "shadow": (246, 214, 186),
    "hook_max": 80,
}

# SOURCE-SIDE matte-QA: у теплій палітрі насиченого зеленого нема; будь-яка зелена нитка на вирізі
# маскота = залишок хромакею. Поріг щедрий (чиста scene07 дає ~кількасот px антиаліасу); перевищення
# = погано-кейнута сцена → СТОП (не публікуємо рілс із зеленою бахромою). Число друкується — самодіагностика.
MATTE_GREEN_LIMIT = 4000


# ─────────────────────────── живий фід ───────────────────────────

def live_feed(feed_path=FEED):
    if not os.path.exists(feed_path):
        sys.exit(f"[reel] НЕМА {feed_path} — звірити наявність нема по чому")
    out = {}
    for it in ET.parse(feed_path).getroot().iter("item"):
        gid = it.find("g:id", NS)
        if gid is None or not gid.text:
            continue
        price = it.find("g:price", NS)
        avail = it.find("g:availability", NS)
        img = it.find("g:image_link", NS)
        title = it.find("title")
        out[gid.text] = {
            "price": (price.text if price is not None else "") or "",
            "avail": (avail.text if avail is not None else "") or "",
            "image": (img.text if img is not None else "") or "",
            "title": ((title.text if title is not None else "") or "").strip(),
        }
    return out


def check_live(feed, gid):
    """ПРАВИЛО №0: наявність звіряємо жорстко; ціна — лише в лог для підпису."""
    if gid not in feed:
        sys.exit(f"[reel] СТОП: товару {gid} НЕМА в живому фіді — рекламувати нічого")
    e = feed[gid]
    if e["avail"] != "in stock":
        sys.exit(f"[reel] СТОП: {gid} має availability={e['avail']} — не рендеримо")
    raw = e["price"].split()[0] if e["price"] else "0"
    try:
        price = f"{round(float(raw))} грн"
    except ValueError:
        price = "?"
    return price, e["title"]


# ─────────────────────────── фото товару → сегмент ───────────────────────────

def _fetch_image(url, dst):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 PlutusReel"})
    with urllib.request.urlopen(req, timeout=30) as r, open(dst, "wb") as f:
        f.write(r.read())


def _resolve_photo(item, feed):
    """Локальний шлях item['image'] (пріоритет) або фетч g:image_link з фіду. Повертає (шлях, cleanup)."""
    if item.get("image") and os.path.exists(item["image"]):
        return item["image"], None
    url = feed.get(item["id"], {}).get("image")
    if not url:
        sys.exit(f"[reel] СТОП: для {item['id']} нема ні локального фото, ні g:image_link у фіді")
    fd, tmp = tempfile.mkstemp(suffix=".img")
    os.close(fd)
    _fetch_image(url, tmp)
    return tmp, tmp


def product_segment(item, feed, n_frames):
    """Фото товару → prep (кадрування, фікс порожньої картки) → зум-пульсація. Список кадрів 1080²."""
    photo, cleanup = _resolve_photo(item, feed)
    fd, prepped = tempfile.mkstemp(suffix=".png")
    os.close(fd)
    try:
        reel_prep_product.prep(photo, prepped)
        arr = np.asarray(Image.open(prepped).convert("RGB"))
    finally:
        os.remove(prepped)
        if cleanup:
            os.remove(cleanup)
    return reel_segment.segment_frames(arr, n_frames)


# ─────────────────────────── типографіка (з прототипу) ───────────────────────────

def parse_accent(text):
    toks = []
    for i, chunk in enumerate(re.split(r"\*", text)):
        for w in chunk.split():
            if toks and all(ch in ".,!?:;»)…" for ch in w):
                prev, acc = toks[-1]
                toks[-1] = (prev + w, acc)
            else:
                toks.append((w, i % 2 == 1))
    return toks


def wrap_tokens(draw, toks, font, max_w):
    sp = draw.textlength(" ", font=font)
    lines, cur, cw = [], [], 0.0
    for w, acc in toks:
        ww = draw.textlength(w, font=font)
        add = ww if not cur else sp + ww
        if cur and cw + add > max_w:
            lines.append(cur)
            cur, cw = [(w, acc)], ww
        else:
            cur.append((w, acc))
            cw += add
    if cur:
        lines.append(cur)
    return lines


def fit_hook(draw, text, top, max_w, max_h):
    toks = parse_accent(text)
    for size in (top, top - 8, top - 16, top - 22, top - 28, top - 34):
        f = _font(_F_BOLD, size)
        lines = wrap_tokens(draw, toks, f, max_w)
        lh = int(size * 1.16)
        if len(lines) <= 3 and len(lines) * lh <= max_h:
            return f, lines, lh
    f = _font(_F_BOLD, top - 34)
    return f, wrap_tokens(draw, toks, f, max_w)[:3], int((top - 34) * 1.16)


def draw_line_tokens(d, line, font, y, ink, accent):
    sp = d.textlength(" ", font=font)
    widths = [d.textlength(w, font=font) for w, _ in line]
    total = sum(widths) + sp * (len(line) - 1)
    x = (W - total) / 2
    for (w, acc), ww in zip(line, widths):
        d.text((x, y), w, font=font, fill=accent if acc else ink)
        x += ww + sp


def fit_name(d, name, max_w):
    for size in (54, 50, 46, 43, 40):
        f = _font(_F_BOLD, size)
        if d.textlength(name, font=f) <= max_w:
            return f
    return _font(_F_BOLD, 40)


# ─────────────────────────── композиція (з прототипу) ───────────────────────────

def gradient_bg():
    g = np.zeros((H, W, 3), np.float32)
    k = np.linspace(0, 1, H)[:, None]
    for c in range(3):
        g[..., c] = T["bg_top"][c] * (1 - k) + T["bg_bot"][c] * k
    return g.astype(np.uint8)


def card_mask():
    m = Image.new("L", (CARD, CARD), 0)
    ImageDraw.Draw(m).rounded_rectangle([0, 0, CARD - 1, CARD - 1], CARD_R, fill=255)
    return (np.asarray(m).astype(np.float32) / 255.0)[..., None]


def draw_chrome(bg, hook, name, spec):
    img = Image.fromarray(bg.copy())
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([CARD_X - 14, CARD_TOP + 14, CARD_X + CARD + 14, CARD_TOP + CARD + 14],
                        CARD_R, fill=T["shadow"])
    f_hook, lines, lh = fit_hook(d, hook, T["hook_max"], W - 130, CARD_TOP - 200)
    y = 190
    for ln in lines:
        draw_line_tokens(d, ln, f_hook, y, T["ink"], T["accent_text"])
        y += lh
    f_name = fit_name(d, name, W - 140)
    ny = CARD_TOP + CARD + 46
    d.text(((W - d.textlength(name, font=f_name)) / 2, ny), name, font=f_name, fill=T["ink"])
    stat, tail = spec
    f_stat = _font(_F_BOLD, 46)
    f_tail = _font(_F_SEMI, 40)
    sep = "  ·  "
    ws = d.textlength(stat, font=f_stat)
    wsep = d.textlength(sep, font=f_tail)
    wt = d.textlength(tail, font=f_tail)
    sy = ny + f_name.size + 34
    sx = (W - (ws + wsep + wt)) / 2
    assert sy + 56 < SAFE_BOT, f"спека залазить у нижню UI-зону: {sy + 56} >= {SAFE_BOT}"
    d.text((sx, sy), stat, font=f_stat, fill=T["accent_text"])
    d.text((sx + ws, sy + 4), sep, font=f_tail, fill=T["muted"])
    d.text((sx + ws + wsep, sy + 4), tail, font=f_tail, fill=T["muted"])
    return np.asarray(img)


def mascot_on_light(arr, scene_file, plate):
    x0, y0, x1, y1 = plate
    lum = np.asarray(T["card_bg"], np.float32).mean()
    assert lum > 200, f"МАСКОТ ТІЛЬКИ НА СВІТЛІЙ ПОВЕРХНІ: підкладка яскравість {lum:.0f}"
    rgb, alpha = clean_mascot(scene_file)
    h0, w0 = alpha.shape
    s = min(MASCOT_MAX_H / h0, (x1 - x0) * 0.86 / w0)
    w, h = max(1, int(w0 * s)), max(1, int(h0 * s))
    rgb = np.asarray(Image.fromarray(rgb.clip(0, 255).astype(np.uint8))
                     .resize((w, h), Image.LANCZOS)).astype(np.float32)
    alpha = np.asarray(Image.fromarray((alpha * 255).astype(np.uint8))
                       .resize((w, h), Image.LANCZOS)).astype(np.float32) / 255.0
    x = (W - w) // 2
    y = y1 - FOOT_INSET - h
    sh = Image.new("L", (W, H), 0)
    ImageDraw.Draw(sh).ellipse([x + w * 0.10, y + h - 30, x + w * 0.90, y + h + 26], fill=90)
    sh = np.asarray(sh.filter(ImageFilter.GaussianBlur(18))).astype(np.float32) / 255.0
    sh[:y0, :] = 0
    sh[y1:, :] = 0
    arr *= (1 - 0.55 * sh[..., None])
    a = alpha[..., None]
    arr[y:y + h, x:x + w] = rgb * a + arr[y:y + h, x:x + w] * (1 - a)
    return arr


def matte_quality(scene_file):
    """SOURCE-SIDE QA: виріз маскота не має лишати зеленої нитки хромакею.
    Повертає (ok, green_px). Композитить cutout на біле, рахує зелено-домінантні пікселі."""
    rgb, alpha = clean_mascot(scene_file)
    m = alpha > 0.5
    comp = np.where(m[..., None], rgb, 255).astype(int)
    r, g, b = comp[..., 0], comp[..., 1], comp[..., 2]
    green = int(((g - r > 25) & (g - b > 25) & (g > 60)).sum())
    return green <= MATTE_GREEN_LIMIT, green


def endcard(bg, n_frames, endline, scene_file):
    plate = (EC_X, EC_TOP, EC_X + EC_W, EC_TOP + EC_H)
    img = Image.fromarray(bg.copy())
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([EC_X - 14, EC_TOP + 14, EC_X + EC_W + 14, EC_TOP + EC_H + 14],
                        CARD_R, fill=T["shadow"])
    d.rounded_rectangle([EC_X, EC_TOP, EC_X + EC_W, EC_TOP + EC_H], CARD_R, fill=T["card_bg"])
    f_brand = _font(_F_BOLD, 104)
    f_line = _font(_F_REG, 48)
    d.text(((W - d.textlength("PlutusToys", font=f_brand)) / 2, 1090), "PlutusToys",
           font=f_brand, fill=T["ink"])
    d.text(((W - d.textlength(endline, font=f_line)) / 2, 1235), endline,
           font=f_line, fill=T["muted"])
    cta = "Ціна й замовлення — у підписі"
    f_cta = _font(_F_BOLD, 46)
    cw = d.textlength(cta, font=f_cta)
    cx, cy = (W - cw) / 2, 1360
    assert cy + 76 < SAFE_BOT, "CTA залазить у нижню UI-зону"
    d.rounded_rectangle([cx - 40, cy - 14, cx + cw + 40, cy + 76], 46, fill=T["accent_fill"])
    d.text((cx, cy), cta, font=f_cta, fill=T["on_accent"])
    arr = mascot_on_light(np.asarray(img).astype(np.float32), scene_file, plate)
    return [arr.clip(0, 255).astype(np.uint8)] * n_frames


# ─────────────────────────── збірка ───────────────────────────

def build(spec, feed_path=FEED):
    feed = live_feed(feed_path)
    os.makedirs(OUT, exist_ok=True)
    # SOURCE-SIDE matte-QA ендкарти ДО рендеру: зелена нитка → СТОП (щоб не повторювалось)
    ok, green = matte_quality(spec["endscene"])
    print(f"[reel] matte-QA {spec['endscene']}: зелений залишок={green}px (ліміт {MATTE_GREEN_LIMIT})")
    if not ok:
        sys.exit(f"[reel] СТОП: сцена {spec['endscene']} лишає зелену нитку ({green}px > {MATTE_GREEN_LIMIT}) — "
                 f"погано кейнута, рілс не рендеримо")
    # ПЕРЕДПОЛІТ: наявність УСІХ товарів звіряємо ДО відкриття райтера — інакше OOS на 2-му товарі
    # лишив би недоплетений mp4. ПРАВИЛО №0: ціна лише в лог для підпису.
    validated = []
    for it in spec["items"]:
        price, live_title = check_live(feed, it["id"])
        validated.append((it, price, live_title))
    bg = gradient_bg()
    mask = card_mask()
    dst = os.path.join(OUT, spec["out"] + ".mp4")
    seg_frames = int(round(2.0 * FPS))   # тривалість сегмента товару
    wr = imageio.get_writer(dst, fps=FPS, codec="libx264", quality=8,
                            macro_block_size=1, ffmpeg_params=["-pix_fmt", "yuv420p"])
    total = 0
    try:
        for it, price, live_title in validated:
            chrome = draw_chrome(bg, it["hook"], it["name"], tuple(it["spec"])).astype(np.float32)
            for f in product_segment(it, feed, seg_frames):
                sq = np.asarray(Image.fromarray(f).resize((CARD, CARD), Image.LANCZOS)).astype(np.float32)
                fr = chrome.copy()
                reg = fr[CARD_TOP:CARD_TOP + CARD, CARD_X:CARD_X + CARD]
                fr[CARD_TOP:CARD_TOP + CARD, CARD_X:CARD_X + CARD] = sq * mask + reg * (1 - mask)
                wr.append_data(fr.astype(np.uint8))
            total += seg_frames
            print(f"[reel:{spec['out']}] {it['id']}  у кадрі: {' · '.join(it['spec'])}"
                  f"   | у підпис: {price:>9}  | {live_title[:46]}")
        for fr in endcard(bg, int(2.5 * FPS), spec["endline"], spec["endscene"]):
            wr.append_data(fr)
        total += int(2.5 * FPS)
    finally:
        wr.close()   # закрити райтер навіть на винятку — не лишати битий/незакритий mp4
    print(f"[reel:{spec['out']}] готово: {total} кадрів = {total / FPS:.1f}с -> {dst}")
    return dst


def main():
    if len(sys.argv) < 2:
        sys.exit("Вжиток: python reel_build.py spec.json")
    spec = json.load(open(sys.argv[1], encoding="utf-8"))
    build(spec)


if __name__ == "__main__":
    main()

"""plutus_overlay.py — Плутус НАКЛАДЕНИЙ на картку товару (концепція власника, як Office-помічник).

Відновлено за ПОВНОЮ специфікацією з CODE_LOG.md (2026-08-16). Метод: зелена сценка
(chroma green) → вирізати персонажа (найбільша зв'язна компонента, без водяного знаку) →
поставити в НАЙПОРОЖНІШУ (білу) ділянку реальної картки товару → петля-«дух».

ПЕТЛЯ-«ДУХ» (безшовна, автопрогравання в стрічці): [чистий товар ~2.5с] → [puff-IN:
Плутус матеріалізується з пилу, проявлення+масштаб] → [грає ~2с] → [puff-OUT: тане в пил] →
[чистий товар ~2.5с]. Перший і останній кадр = чистий товар → крутиться без стику.
Пил на ОБОХ кінцях. Глядач долистує у випадковий момент → часто лише товар, іноді
ловить проблиск Плутуса → відчуття «з'явився коли схотів».

Біла зона: find_white_spot() ставить маскота в найбілішу (найпорожнішу) ділянку САМЕ цього
фото (інтегральне зображення по «білості», ухил ДОГОРИ — зверху) — не перекриває товар.
Вирізання: chroma-key + despill + найбільша зв'язна компонента (водяний знак генератора
у куті відкидається). Відео I/O — imageio. Квадрат Feed 1:1.

ЗАПУСК:
    python plutus_overlay.py --scene sceneNN_..._GREEN.mp4 --product товар.jpg --out out.mp4
    (--key green|luma  --size 900  --mascot-frac 0.42  --head 70 --play 55 --tail 70
     --puff 10  --corner auto|br|bl|tr|tl  --seed N)
"""
import argparse
import sys

import numpy as np
import imageio
from PIL import Image
from scipy import ndimage

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def read_frames(path):
    reader = imageio.get_reader(path)
    fps = reader.get_meta_data().get("fps", 30) or 30
    frames = [np.asarray(fr)[..., :3].astype(np.uint8) for fr in reader]
    reader.close()
    return frames, float(fps)


def key_green(frame):
    """chroma-key по зеленому → (rgb float, alpha 0..1) з пером країв і despill."""
    f = frame.astype(np.float32)
    r, g, b = f[..., 0], f[..., 1], f[..., 2]
    green_excess = g - np.maximum(r, b)
    THI, TLO = 55.0, 12.0
    alpha = np.clip((THI - green_excess) / (THI - TLO), 0.0, 1.0)
    alpha = np.where(g < 55, 1.0, alpha)
    g_ds = np.minimum(g, np.maximum(r, b) + 8)   # despill
    return np.dstack([r, g_ds, b]), alpha


def key_luma(frame):
    """Фолбек для світлого (не зеленого) фону: alpha за темнотою відносно білого."""
    f = frame.astype(np.float32)
    lum = f.mean(axis=2)
    return f, np.clip((240.0 - lum) / 60.0, 0.0, 1.0)


def largest_component_mask(union):
    """keep-маска = НАЙБІЛЬША зв'язна компонента (маскот); дрібні (водяний знак) відкидає."""
    binary = union > 0.5
    lbl, n = ndimage.label(binary)
    if n < 1:
        return binary.astype(np.float32)
    sizes = ndimage.sum(binary, lbl, range(1, n + 1))
    keep = ndimage.binary_dilation(lbl == (int(np.argmax(sizes)) + 1), iterations=3)
    return keep.astype(np.float32)


def find_white_spot(card_arr, mw, mh, bias_up=0.30):
    """Найпорожніша (найбіліша) ділянка розміру (mw,mh) — щоб маскот НЕ перекривав товар.
    Інтегральне зображення по «білості», ухил ДОГОРИ (розміщення зверху — рішення власника,
    кут обирається сам за реальною порожнечею фото)."""
    H, W = card_arr.shape[:2]
    white = (card_arr.min(axis=2) > 208).astype(np.float64)
    ii = np.pad(white.cumsum(0).cumsum(1), ((1, 0), (1, 0)))
    if mw >= W or mh >= H:
        return max(0, W - mw), 0
    best, bx, by = -1.0, W - mw, 0
    step = max(6, min(mw, mh) // 8)
    for y in range(0, H - mh + 1, step):
        for x in range(0, W - mw + 1, step):
            frac_white = (ii[y+mh, x+mw] - ii[y, x+mw] - ii[y+mh, x] + ii[y, x]) / (mw * mh)
            s = frac_white + bias_up * (1 - y / max(1, H - mh))   # ухил ДОГОРИ
            if s > best:
                best, bx, by = s, x, y
    return bx, by


def composite(base, over_rgb, over_alpha, x, y):
    """Альфа-накладання over на base у позиції (x,y)."""
    h, w = over_alpha.shape
    H, W = base.shape[:2]
    if x >= W or y >= H:
        return base
    w, h = min(w, W - x), min(h, H - y)
    a = over_alpha[:h, :w, None]
    base[y:y+h, x:x+w] = over_rgb[:h, :w] * a + base[y:y+h, x:x+w] * (1 - a)
    return base


def dust_frame(base, cx, cy, t, rng, spread=100):
    """Кадр фірмового пилу навколо (cx,cy). t∈[0,1] — фаза (0 щільно, 1 розсіявся)."""
    out = base.copy()
    n = 70
    ang = rng.uniform(0, 2 * np.pi, n)
    rad = rng.uniform(0.15, 1.0, n) * (25 + spread * t)
    px = (cx + np.cos(ang) * rad).astype(int)
    py = (cy + np.sin(ang) * rad - 14 * t).astype(int)
    col = np.array([212, 202, 182], np.float32)
    a = max(0.0, 0.65 * (1 - t))
    sz = int(3 + 5 * (1 - t))
    H, W = out.shape[:2]
    for xx, yy in zip(px, py):
        if 0 <= xx < W and 0 <= yy < H:
            x0, x1 = max(0, xx - sz), min(W, xx + sz)
            y0, y1 = max(0, yy - sz), min(H, yy + sz)
            out[y0:y1, x0:x1] = col * a + out[y0:y1, x0:x1] * (1 - a)
    return out


# --- 9:16 вертикальний режим (Reels/Stories) --------------------------------------------
# Спека SMM 2026-08-19: полотно 1080×1920; градієнт #FFF7EE→#FFE0C4; квадрат на всю ширину при
# y=470, скруглення 56 + м'яка тінь #F6D6BA зсув +16; гачок зверху (до 3 рядків), назва+ціна
# знизу (ціна в пілюлі #E87A2A); верх 120 / низ 220 порожні (UI Instagram перекриває). Шрифти —
# з ФОЛБЕКОМ (overlay через автопостер біжить на VPS=Linux, де segoeui немає → DejaVu → дефолт).
V_W, V_H = 1080, 1920
V_BG_TOP, V_BG_BOT = (255, 247, 238), (255, 224, 196)
V_SQUARE_Y, V_RADIUS = 470, 56
# Квадрат ≈на всю ширину, АЛЕ трохи зменшений, щоб низ гарантовано вмістив назву(2р)+ціну-пілюлю
# над полем IG-UI (без накладання, яке дає жорсткий 1080). V_SQ ≈1050 (поля з боків ~15px).
V_SQ = min(V_W, V_H - V_SQUARE_Y - 400)
V_SQ_X = (V_W - V_SQ) // 2
V_SHADOW, V_PILL = (246, 214, 186), (232, 122, 42)
V_TEXT = (60, 40, 25)
_FONT_BOLD = ["segoeuib.ttf", "C:/Windows/Fonts/segoeuib.ttf",
              "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", "DejaVuSans-Bold.ttf"]
_FONT_REG = ["segoeui.ttf", "C:/Windows/Fonts/segoeui.ttf",
             "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "DejaVuSans.ttf"]


def _load_font(size, bold=True):
    from PIL import ImageFont
    for path in (_FONT_BOLD if bold else _FONT_REG):
        try:
            return ImageFont.truetype(path, size)
        except (OSError, IOError):
            continue
    return ImageFont.load_default()


def _wrap_text(draw, text, font, max_w, max_lines):
    lines, cur = [], ""
    for w in (text or "").split():
        trial = (cur + " " + w).strip()
        if not cur or draw.textlength(trial, font=font) <= max_w:
            cur = trial
        else:
            lines.append(cur)
            cur = w
            if len(lines) == max_lines:
                cur = ""
                break
    if cur and len(lines) < max_lines:
        lines.append(cur)
    return lines[:max_lines]


def _vertical_canvas(hook, name, price):
    """Статичне полотно 1080×1920 (градієнт + гачок + назва + ціна-пілюля + тінь квадрата).
    Квадрат НЕ малюємо — вклеюємо покадрово. Повертає (canvas_uint8_array, rounded_mask_L)."""
    from PIL import Image, ImageDraw, ImageFilter
    t = np.linspace(0, 1, V_H)[:, None]
    grad = ((1 - t) * np.array(V_BG_TOP) + t * np.array(V_BG_BOT))
    img = Image.fromarray(np.repeat(grad[:, None, :], V_W, axis=1).clip(0, 255).astype(np.uint8))
    draw = ImageDraw.Draw(img)
    # м'яка тінь квадрата (скруглений прямокутник, зсув +16, розмитий)
    sh = Image.new("L", (V_W, V_H), 0)
    ImageDraw.Draw(sh).rounded_rectangle([V_SQ_X, V_SQUARE_Y + 16, V_SQ_X + V_SQ - 1, V_SQUARE_Y + 16 + V_SQ],
                                         radius=V_RADIUS, fill=120)
    img.paste(Image.new("RGB", (V_W, V_H), V_SHADOW), (0, 0), sh.filter(ImageFilter.GaussianBlur(12)))
    # гачок (до 3 рядків, центрований, y≈190)
    if hook:
        f = _load_font(76, bold=True)
        yy = 190
        for ln in _wrap_text(draw, hook, f, V_W - 120, 3):
            draw.text(((V_W - draw.textlength(ln, font=f)) / 2, yy), ln, font=f, fill=V_TEXT)
            yy += 90
    # назва під квадратом (~44, до 2 рядків)
    yb = V_SQUARE_Y + V_SQ + 24
    if name:
        f = _load_font(44, bold=False)
        for ln in _wrap_text(draw, name, f, V_W - 120, 2):
            draw.text(((V_W - draw.textlength(ln, font=f)) / 2, yb), ln, font=f, fill=V_TEXT)
            yb += 54
    # ціна-пілюля (#E87A2A, білий жирний ~64) — під назвою, без накладання
    if price:
        f = _load_font(64, bold=True)
        tw = draw.textlength(price, font=f)
        pw, ph = int(tw) + 80, 96
        px = (V_W - pw) // 2
        py = min(yb + 14, V_H - 150 - ph)   # низ ≥150px вільний (поле IG-UI)
        draw.rounded_rectangle([px, py, px + pw, py + ph], radius=ph // 2, fill=V_PILL)
        draw.text((px + (pw - tw) / 2, py + (ph - 64) / 2 - 6), price, font=f, fill=(255, 255, 255))
    mask = Image.new("L", (V_SQ, V_SQ), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, V_SQ - 1, V_SQ - 1], radius=V_RADIUS, fill=255)
    return np.asarray(img).astype(np.uint8), mask


def to_vertical(square_frames, hook, name, price):
    """Кожен квадратний кадр → 1080×1920 (на всю ширину при y=470, зі скругленням) на статичне полотно."""
    from PIL import Image
    canvas_arr, mask = _vertical_canvas(hook, name, price)
    out = []
    for fr in square_frames:
        sq = Image.fromarray(fr).resize((V_SQ, V_SQ), Image.LANCZOS)
        base = Image.fromarray(canvas_arr.copy())
        base.paste(sq, (V_SQ_X, V_SQUARE_Y), mask)
        out.append(np.asarray(base).astype(np.uint8))
    return out


def main():
    ap = argparse.ArgumentParser(description="Overlay Плутуса на картку товару (петля-дух).")
    ap.add_argument("--scene", required=True)
    ap.add_argument("--product", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--key", choices=["green", "luma"], default="green")
    ap.add_argument("--size", type=int, default=900)
    ap.add_argument("--mascot-frac", type=float, default=0.30, help="висота маскота (частка кадру); малий, щоб не перекривав товар")
    ap.add_argument("--corner", choices=["auto", "br", "bl", "tr", "tl"], default="auto")
    ap.add_argument("--head", type=int, default=0, help="кадрів чистого товару до появи")
    ap.add_argument("--play", type=int, default=0, help="кадрів гри (0 = ВСЯ сценка — Плутус відіграє весь ролик)")
    ap.add_argument("--tail", type=int, default=14, help="кадрів чистого товару після зникнення")
    ap.add_argument("--puff", type=int, default=10, help="кадрів матеріалізації/розчинення")
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--aspect", choices=["1:1", "9:16"], default="1:1",
                    help="1:1 квадрат (feed) або 9:16 вертикаль 1080×1920 (Reels/Stories)")
    ap.add_argument("--hook", default="", help="гачок-текст зверху (лише 9:16)")
    ap.add_argument("--name", default="", help="назва товару під квадратом (лише 9:16)")
    ap.add_argument("--price", default="", help="ціна в пілюлі (лише 9:16)")
    a = ap.parse_args()
    rng = np.random.default_rng(a.seed)
    S = a.size

    # картка товару — квадрат S×S (contain на білому)
    prod = Image.open(a.product).convert("RGB")
    prod.thumbnail((S, S), Image.LANCZOS)
    card = Image.new("RGB", (S, S), (255, 255, 255))
    card.paste(prod, ((S - prod.width) // 2, (S - prod.height) // 2))
    card_arr = np.asarray(card).astype(np.float32)

    frames, fps = read_frames(a.scene)
    keyer = key_green if a.key == "green" else key_luma
    keyed = [keyer(f) for f in frames]

    # маскот: keep-маска (без знаку) + bbox
    union = np.zeros(frames[0].shape[:2], np.float32)
    for _, al in keyed:
        union = np.maximum(union, al)
    keep_mask = largest_component_mask(union)
    ys, xs = np.where(keep_mask > 0.5)
    if len(xs) == 0:
        print("[overlay] порожня маска — перевір --key/сценку", file=sys.stderr); sys.exit(1)
    x0, y0, x1, y1 = int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1
    bw, bh = x1 - x0, y1 - y0

    target_h = int(a.mascot_frac * S)
    scale = target_h / bh
    target_w = max(1, int(bw * scale))
    margin = int(0.03 * S)

    # позиція: auto → найбіліша ділянка; інакше — заданий кут
    if a.corner == "auto":
        px, py = find_white_spot(card_arr, target_w, target_h)
    else:
        px = (S - target_w - margin) if a.corner in ("br", "tr") else margin
        py = (S - target_h - margin) if a.corner in ("br", "bl") else margin

    def scaled_mascot(rgb, alpha):
        alpha = alpha * keep_mask
        rc, ac = rgb[y0:y1, x0:x1], alpha[y0:y1, x0:x1]
        ri = Image.fromarray(rc.clip(0, 255).astype(np.uint8)).resize((target_w, target_h), Image.LANCZOS)
        ai = Image.fromarray((ac * 255).clip(0, 255).astype(np.uint8)).resize((target_w, target_h), Image.LANCZOS)
        return np.asarray(ri).astype(np.float32), np.asarray(ai).astype(np.float32) / 255.0

    # play-кадри (з циклюванням, якщо сценка коротша за --play)
    play_idx = [i % len(keyed) for i in range(a.play)] if a.play else list(range(len(keyed)))
    cx, cy = px + target_w // 2, py + target_h // 2
    out_frames = []

    def clean():
        return card_arr.astype(np.uint8)

    # HEAD — чистий товар
    out_frames += [clean() for _ in range(a.head)]

    # PUFF-IN — матеріалізація з пилу (проявлення alpha 0→1 + масштаб 0.7→1 + пил t 1→0)
    mr0, ma0 = scaled_mascot(*keyed[0])
    for i in range(a.puff):
        p = (i + 1) / a.puff
        base = card_arr.copy()
        sc = 0.7 + 0.3 * p
        tw, th = max(1, int(target_w * sc)), max(1, int(target_h * sc))
        ox, oy = px + (target_w - tw) // 2, py + (target_h - th) // 2
        mri = np.asarray(Image.fromarray(mr0.clip(0, 255).astype(np.uint8)).resize((tw, th), Image.LANCZOS)).astype(np.float32)
        mai = np.asarray(Image.fromarray((ma0 * 255).clip(0, 255).astype(np.uint8)).resize((tw, th), Image.LANCZOS)).astype(np.float32) / 255.0
        composite(base, mri, mai * p, ox, oy)
        base = dust_frame(base, cx, cy, 1 - p, rng)     # пил густий на старті появи
        out_frames.append(base.clip(0, 255).astype(np.uint8))

    # PLAY — Плутус грає
    for idx in play_idx:
        base = card_arr.copy()
        mr, ma = scaled_mascot(*keyed[idx])
        composite(base, mr, ma, px, py)
        out_frames.append(base.clip(0, 255).astype(np.uint8))

    # PUFF-OUT — тане в пил (alpha 1→0 + пил t 0→1)
    mrL, maL = scaled_mascot(*keyed[play_idx[-1]])
    for i in range(a.puff):
        t = (i + 1) / a.puff
        base = card_arr.copy()
        composite(base, mrL, maL * (1 - t), px, py)
        base = dust_frame(base, cx, cy, t, rng)
        out_frames.append(base.clip(0, 255).astype(np.uint8))

    # TAIL — чистий товар (безшовна петля з HEAD)
    out_frames += [clean() for _ in range(a.tail)]

    # 9:16: обгортаємо квадратні кадри на вертикальне полотно (1:1-шлях лишається незмінним)
    if a.aspect == "9:16":
        out_frames = to_vertical(out_frames, a.hook, a.name, a.price)
        out_fps, size_desc = 30.0, f"{V_W}x{V_H}"
        writer = imageio.get_writer(a.out, fps=out_fps, codec="libx264", quality=8,
                                    macro_block_size=1, pixelformat="yuv420p")
    else:
        out_fps, size_desc = fps, f"{S}x{S}"
        writer = imageio.get_writer(a.out, fps=out_fps, codec="libx264", quality=8, macro_block_size=1)
    for fr in out_frames:
        writer.append_data(fr)
    writer.close()
    dur = len(out_frames) / out_fps
    print(f"[overlay] Готово: {a.out}  ({len(out_frames)} кадрів @ {out_fps:.0f}fps = {dur:.1f}с, {size_desc}, "
          f"поз=({px},{py}) {'auto-біла-зона' if a.corner=='auto' else a.corner})")


if __name__ == "__main__":
    main()

"""Усі 6 кліпів Плутуса для героя (SMM 12.10): перший/останній кадр = hero-poster (змішування K кадрів), кліпи з хвостом ліворуч (grooming, sniff, shakeoff) дзеркалимо ПРИ КОДУВАННІ,
щоб усі дивились в один бік із poster (CSS-дзеркала не потрібне, накладання poster/кліп не ламається). Вихід: файли + друк тривалості мс (для data-ms) і розмірів."""
import json
import os
import sys

import numpy as np
from PIL import Image, ImageOps, ImageSequence

SRC = os.environ.get("MASCOT_SRC", r"C:SERSSMACHCLAUDEPROJECTSPLUTUSTOYS_AVTONOMIYAMASCOT_SITE")   # MANIFEST.JSON + PLUTUS_<ID>.WEBP
OUT = sys.argv[1]
Q = int(sys.argv[2])
W, H = 234, 180
K = 7
CLIPS = [("tailtrip", False), ("ear_scratch", False), ("grooming", True), ("sniff", True), ("shakeoff", True)]   # stretch_yawn: manifest safe_for_hero=false + бюджет → НЕ в героя (додати: ("stretch_yawn", False), ≈286 КБ)


def frames(name, flip):
    im = Image.open(os.path.join(SRC, f"plutus_{name}.webp"))
    fr, du = [], []
    for f in ImageSequence.Iterator(im):
        x = f.convert("RGBA").resize((W, H), Image.LANCZOS)
        if flip:
            x = ImageOps.mirror(x)
        fr.append(x)
        du.append(int(f.info.get("duration", 100)))
    return fr, du


def premul(im):
    a = np.asarray(im).astype(np.float64)
    al = a[..., 3:4] / 255.0
    return np.concatenate([a[..., :3] * al, a[..., 3:4]], axis=-1)


def unpremul(p):
    al = p[..., 3:4] / 255.0
    rgb = np.where(al > 1e-6, p[..., :3] / np.maximum(al, 1e-6), 0)
    return Image.fromarray(np.clip(np.concatenate([rgb, p[..., 3:4]], axis=-1) + 0.5, 0, 255).astype(np.uint8), "RGBA")


def blend(a, b, w):
    return unpremul(premul(a) * (1 - w) + premul(b) * w)


tail, _ = frames("tailtrip", False)
poster = tail[0]
os.makedirs(OUT, exist_ok=True)
poster.save(os.path.join(OUT, "plutus_hero_poster.webp"), quality=80, method=4)
info = {}
total = os.path.getsize(os.path.join(OUT, "plutus_hero_poster.webp"))
for name, flip in CLIPS:
    fr, du = frames(name, flip)
    n = len(fr)
    res = []
    for i, f in enumerate(fr):
        if i < K:
            res.append(poster if i == 0 else blend(poster, f, i / float(K)))
        elif i >= n - K:
            res.append(poster if i == n - 1 else blend(poster, f, (n - 1 - i) / float(K)))
        else:
            res.append(f)
    p = os.path.join(OUT, f"plutus_{name}.webp")
    res[0].save(p, save_all=True, append_images=res[1:], duration=du, loop=1, quality=Q, method=4, lossless=False)
    sz = os.path.getsize(p)
    total += sz
    info[name] = {"bytes": sz, "ms": sum(du), "frames": n, "flip": flip}
print(json.dumps(info))
print("TOTAL", total)

"""prep_product.py — підготовка фото товару під Плутус-оверлей (креативна версія).

Сире фото з фіду — дрібний товар у великих білих полях; plutus_overlay ставить маскота
в найбілішу зону і без підготовки той тулиться до краю й обрізається.

Що робить:
  1. обрізає по контенту (поріг «небілого» ~243);
  2. ДУЖЕ витягнуті товари (співвідношення сторін контенту гірше за ~1:2.5 —
     змія-тягучка й подібне) повертає на 35° по діагоналі ДО вписування в рамку.
     Джерело — 500×500, розтягувати по висоті нема чим (дасть розмиття, не
     заповнення); поворот пікселів не додає, лише переорієнтовує наявні, і
     смужка 5.5:1 стає майже квадратом (~1.3:1), що займає картку по-справжньому;
  3. вписує товар у РАМКУ (0.76 ширини × 0.58 висоти полотна) — а не просто масштабує
     за більшою стороною. Через це пласкі/широкі товари (планки кейкапів, брелоки)
     більше не виходять смужкою на 300 px, а займають кадр по-справжньому;
  4. притискає донизу (низ товару ~0.94 висоти), лишаючи верхні ~36% полотна білими —
     туди find_white_spot кладе маскота цілком.
"""
import os
import sys

import numpy as np
from PIL import Image

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

CANVAS = 1080
MAX_W = 0.76          # рамка товару по ширині
MAX_H = 0.58          # рамка товару по висоті (лишає верхні ~36% під маскота)
MAX_H_SQUARE = 0.74   # ширша межа висоти для квадратних/портретних товарів (aspect 0.75-1.3):
                      # інакше їх обмежує занижена MAX_H, а не ширина, і вони лишаються
                      # дрібними з порожнім білим полем праворуч (кейс "Тигр-ловець")
SQUARE_ASPECT_LO, SQUARE_ASPECT_HI = 0.75, 1.3
BOTTOM_ANCHOR = 0.94  # низ товару на цій висоті полотна
TOP_GUARD = 0.34      # вище цього товар не піднімається
FLAT_CENTER = 0.66    # центр дуже пласких товарів (h < 0.30 полотна)
ELONGATED_RATIO = 2.5  # гірше за 1:2.5 (в будь-яку сторону) -> поворот замість смужки
ROTATE_DEG = 35        # діагональ (30-40°, як домовлено)


def content_bbox(img, thresh=243):
    a = np.asarray(img.convert("RGB")).astype(np.int16)
    nonwhite = (a.min(axis=2) < thresh)
    ys, xs = np.where(nonwhite)
    if len(xs) == 0:
        return (0, 0, img.width, img.height)
    pad = 4
    return (max(xs.min() - pad, 0), max(ys.min() - pad, 0),
            min(xs.max() + pad, img.width), min(ys.max() + pad, img.height))


def prep(src, dst):
    im = Image.open(src).convert("RGB")
    im = im.crop(content_bbox(im))
    aspect = im.width / im.height
    rotated = False
    if aspect > ELONGATED_RATIO or aspect < 1 / ELONGATED_RATIO:
        # дуже витягнутий контент (наприклад змія-тягучка 5.5:1): поворот НЕ
        # масштабує і не додає пікселів, лише переорієнтовує наявні по діагоналі.
        # rotate(expand=True) домальовує білі кути навколо повернутого прямокутника —
        # ті кути прибираємо повторним content_bbox, інакше вони йдуть у рамку
        # як «контент» і товар знову лишається дрібним.
        im = im.rotate(ROTATE_DEG, expand=True, fillcolor=(255, 255, 255),
                       resample=Image.BICUBIC)
        im = im.crop(content_bbox(im))
        aspect = im.width / im.height
        rotated = True
    max_h = MAX_H_SQUARE if SQUARE_ASPECT_LO <= aspect <= SQUARE_ASPECT_HI else MAX_H
    scale = min(CANVAS * MAX_W / im.width, CANVAS * max_h / im.height)
    im = im.resize((max(1, int(im.width * scale)), max(1, int(im.height * scale))),
                   Image.LANCZOS)
    canvas = Image.new("RGB", (CANVAS, CANVAS), (255, 255, 255))
    x = (CANVAS - im.width) // 2
    if im.height < CANVAS * 0.30:
        # дуже пласкі товари (змія-тягучка, планка кейкапів): якщо притиснути їх до низу,
        # у картці лишається величезна порожнеча, яка читається як помилка верстки —
        # тому центруємо їх нижче середини, а не по нижньому краю
        y = int(CANVAS * FLAT_CENTER) - im.height // 2
    else:
        y = max(int(CANVAS * BOTTOM_ANCHOR) - im.height, int(CANVAS * TOP_GUARD))
    canvas.paste(im, (x, y))
    canvas.save(dst, quality=95)
    rot_note = f" (повернуто {ROTATE_DEG}°, aspect->{aspect:.2f})" if rotated else ""
    print(f"[prep] {os.path.basename(src)} -> {im.size} @ ({x},{y}){rot_note}")


if __name__ == "__main__":
    prep(sys.argv[1], sys.argv[2])

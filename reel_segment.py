"""reel_segment.py — сегмент товару для рілса: легка зум-пульсація підготовленого фото.

Сегменти прототипу (seg/*.mp4) виявилися НЕ AI-анімацією, а м'якою пульсацією статичного кадру:
перший кадр ≈ останній, невеликий пік у середині (виміряно: f0↔flast≈0.6, f0↔fmid≈8.8, 0% пікселів
змінюються >30). Відтворюємо алгоритмічно — zoom 1.0→пік→1.0 косинусною пульсацією + центр-кроп назад
у 1080². Жодного AI, жодної мережі — тож придатне для автономного рендеру на VPS.

Вхід — підготовлене фото товару (reel_prep_product.prep, 1080²). Вихід — список кадрів (np.uint8).
"""
import numpy as np
from PIL import Image

SIZE = 1080
ZOOM_PEAK = 1.06   # пік збільшення у середині сегмента (підібрано під м'який профіль прототипу)


def segment_frames(img, n_frames: int) -> list:
    """Кадри сегмента з пульсацією zoom (1.0 на кінцях, ZOOM_PEAK у середині).

    img — np.ndarray (H,W,3) або PIL.Image; приводиться до 1080² RGB. n_frames>=1.
    Косинус `0.5-0.5*cos(2πt)` дає 0→1→0 по t∈[0,1] (пік при t=0.5), тож перший=останній кадр.
    """
    base = img if isinstance(img, Image.Image) else Image.fromarray(np.asarray(img))
    base = base.convert("RGB").resize((SIZE, SIZE), Image.LANCZOS)
    n = max(int(n_frames), 1)
    out = []
    for i in range(n):
        t = i / max(n - 1, 1)
        z = 1.0 + (ZOOM_PEAK - 1.0) * (0.5 - 0.5 * np.cos(2 * np.pi * t))
        zw = max(SIZE, int(round(SIZE * z)))
        big = base.resize((zw, zw), Image.LANCZOS)
        off = (zw - SIZE) // 2
        out.append(np.asarray(big.crop((off, off, off + SIZE, off + SIZE))))
    return out

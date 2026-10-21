#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_reel_scene_first.py — шаблон рілса «сцена першою» (рішення власника 21.10.2026: «тільки сцена повинна бути на першій карці»; reel_build.scene_first_frames).
Інваріанти: (1) сезонні сцени лише у своєму вікні за ДАТОЮ ПУБЛІКАЦІЇ (halloween до 31.10, snowflake з 25.11), несезонні завжди; (2) браковані сцени (префікси scene16/17/18/19/21/24/27) відхиляються;
(3) відсутній файл сцени → чіткий СТОП; (4) перша карта: 24→30 к/с, розмір 1080×1920, маскот у межах і НЕ в нижній UI-зоні, фон під ним світлий, без стрибків (єдиний bbox);
(5) кеш готових кадрів: другий виклик дає ті самі кадри без повторного очищення; (6) сцена довша за ліміт → СТОП."""
import os
import sys
import tempfile
from datetime import date
from pathlib import Path

import imageio
import numpy as np

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
import reel_build as rb  # noqa: E402
import reel_matte  # noqa: E402

F = []


def chk(n, c):
    print(f"[{'OK ' if c else 'FAIL'}] {n}")
    if not c:
        F.append(n)


def exits(fn):
    try:
        fn()
        return None
    except SystemExit as e:
        return str(e.code)


# 1) сезони
ok = rb.scene_season_ok
chk("halloween: 31.10 так, 01.11 ні, 01.10 так, 30.09 ні, січень ні; snowflake: 24.11 ні, 25.11 так, 15.12 так, 31.12 так, 05.01 ні (кінцева дата не названа — консервативно до 31.12)",
    ok("scene_halloween_RENDER_GREEN.mp4", date(2026, 10, 31)) and not ok("scene_halloween_RENDER_GREEN.mp4", date(2026, 11, 1)) and ok("scene_halloween_RENDER_GREEN.mp4", date(2026, 10, 1))
    and not ok("scene_halloween_RENDER_GREEN.mp4", date(2026, 9, 30)) and not ok("scene_halloween_RENDER_GREEN.mp4", date(2027, 1, 15))
    and not ok("scene_snowflake_RENDER_GREEN.mp4", date(2026, 11, 24)) and ok("scene_snowflake_RENDER_GREEN.mp4", date(2026, 11, 25)) and ok("scene_snowflake_RENDER_GREEN.mp4", date(2026, 12, 15))
    and ok("scene_snowflake_RENDER_GREEN.mp4", date(2026, 12, 31)) and not ok("scene_snowflake_RENDER_GREEN.mp4", date(2027, 1, 5)))
chk("несезонна сцена — завжди", all(ok("scene_icecream_RENDER_GREEN.mp4", date(2026, m, 15)) for m in range(1, 13)))
# 2) браковані
for pre in ("scene16_", "scene17_", "scene18_", "scene19_", "scene21_", "scene24_", "scene27_"):
    chk(f"браковану сцену {pre}… відхилено", (exits(lambda p=pre: rb.check_endscene(p + "x_GREEN.mp4")) or "").startswith("[reel] СТОП"))
chk("стара чорна сцена scene04 відхилена (як раніше)", exits(lambda: rb.check_endscene("scene04_sniff_curious_pika_GREEN.mp4")) is not None)
chk("нормальна сцена проходить check_endscene", exits(lambda: rb.check_endscene("scene_icecream_RENDER_GREEN.mp4")) is None)

# 3) відсутній файл + сезон у check_first_scene
td = Path(tempfile.mkdtemp())
rb.SCENES_DIR = str(td)
reel_matte.SCENES = str(td)
rb.OUT = str(td / "reels")
chk("check_first_scene: відсутній файл сцени → СТОП", "нема в plutus_scenes" in (exits(lambda: rb.check_first_scene("scene_nod_RENDER_GREEN.mp4", date(2026, 10, 25))) or ""))
(td / "scene_halloween_RENDER_GREEN.mp4").write_bytes(b"x")
chk("check_first_scene: сезонна сцена поза вікном → СТОП (дата публікації 05.11)", "сезонна сцена" in (exits(lambda: rb.check_first_scene("scene_halloween_RENDER_GREEN.mp4", date(2026, 11, 5))) or ""))
chk("check_first_scene: сезонна сцена у вікні (25.10) проходить", exits(lambda: rb.check_first_scene("scene_halloween_RENDER_GREEN.mp4", date(2026, 10, 25))) is None)


# 4–5) синтетична сцена: рудий «звір» із текстурою на зеленому #00FF00
def make_scene(name, n=12, w=160, h=120, fps=24):
    rng = np.random.default_rng(1)
    frames = []
    for i in range(n):
        im = np.zeros((h, w, 3), np.uint8)
        im[...] = (0, 255, 0)
        cx, cy = w // 2, h // 2 + (i % 3) - 1          # лише вертикальні мікро-рухи: bbox спільний, горизонтально не «стрибає»
        yy, xx = np.ogrid[:h, :w]
        m = ((xx - cx) / 30.0) ** 2 + ((yy - cy) / 40.0) ** 2 <= 1
        tex = rng.integers(-40, 40, (h, w, 1))
        body = np.clip(np.array([210, 120, 50]) + tex, 0, 255).astype(np.uint8)
        im[m] = body[m]
        frames.append(im)
    wr = imageio.get_writer(str(td / name), fps=fps, codec="libx264", quality=9, macro_block_size=1, pixelformat="yuv420p")
    for fr in frames:
        wr.append_data(fr)
    wr.close()


make_scene("scene_test_RENDER_GREEN.mp4")
bg = rb.gradient_bg()
fr = rb.scene_first_frames("scene_test_RENDER_GREEN.mp4", bg)
chk("перша карта: 12 кадрів 24 к/с → 15 кадрів 30 к/с (0,5 с), кожен 1920×1080×3 uint8", len(fr) == 15 and all(f.shape == (1920, 1080, 3) and f.dtype == np.uint8 for f in fr))
diff = np.abs(fr[len(fr) // 2].astype(int) - bg.astype(int)).sum(axis=2) > 60
ys, xs = np.where(diff)
chk("маскот намальований, по центру за x, низ фігури вище нижньої UI-зони (≤1700)", len(ys) > 5000 and abs((xs.min() + xs.max()) / 2 - 540) < 80 and ys.max() <= 1700)
chk("фон поза маскотом = градієнт (верх кадру не змінено), без зеленого (деспіл/ключ)", np.abs(fr[len(fr) // 2][:200].astype(int) - bg[:200].astype(int)).max() == 0 and not ((fr[len(fr) // 2][..., 1].astype(int) - np.maximum(fr[len(fr) // 2][..., 0], fr[len(fr) // 2][..., 2]).astype(int)) > 60).any())
bw = [np.where(np.abs(f.astype(int) - bg.astype(int)).sum(axis=2) > 60)[1] for f in fr]
chk("без «стрибків» маскота між кадрами: лівий край змінюється на ≤ 12 px (єдиний bbox)", max(b.min() for b in bw) - min(b.min() for b in bw) <= 12)
cache_files = list(Path(rb.OUT, "_scene_cache").glob("scene_test_RENDER_GREEN_*.mp4"))
chk("кеш першої карти записано (один файл)", len(cache_files) == 1)
fr2 = rb.scene_first_frames("scene_test_RENDER_GREEN.mp4", bg)
chk("другий виклик береться з кешу: ті самі розміри/кількість і майже ті самі пікселі (mp4 quality 10)", len(fr2) == 15 and fr2[0].shape == fr[0].shape and np.abs(fr2[7].astype(int) - fr[7].astype(int)).mean() < 3.0)
# 6) довга сцена
make_scene("scene_long_RENDER_GREEN.mp4", n=170, w=64, h=48)
chk("сцена довша за ліміт (170 кадрів/24 = 7,1 с > 5 с) → СТОП", "довша за" in (exits(lambda: rb.scene_first_frames("scene_long_RENDER_GREEN.mp4", bg)) or ""))
chk("константи: тривалість рілса ≤ 15 с, перша сцена ≤ 5 с", rb.REEL_SECONDS_MAX == 15.0 and rb.FIRST_SCENE_SECONDS_MAX == 5.0)

print("\nFAIL:" if F else "\nOK: усі перевірки пройдені", *F, sep="\n")
sys.exit(1 if F else 0)

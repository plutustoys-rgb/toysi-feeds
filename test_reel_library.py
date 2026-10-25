#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_reel_library.py — регрес бібліотеки схвалених перших карт (reel_library.py) і режиму бібліотеки в reel_build.py.

Рішення власника 25.10.2026: рілси збираються автоматично лише зі сцен, схвалених ним (перші карти Плутуса). ІНВАРІАНТИ:
  - бібліотека вмикається змінною PLUTUS_SCENE_LIBRARY; нема → library_dir() None і reel_build працює як раніше (ручний потік SMM не ламається);
  - «схвалена» = status == "approved" І first_card-файл існує й непорожній; dropped/відсутній файл не береться;
  - ендсцена мусить мати end_mascot PNG; перша сцена — схвалену; несхвалені → СТОП (sys.exit), без фолбеку на старі сцени;
  - перша карта читається потоком, розмір кадру/кількість кадрів перевіряються (обрізаний файл → помилка);
  - ендкартка без білого квадрата (піксель кута колишньої плити не чисто білий), маскот присутній, форма кадру 1920×1080×3;
  - сезонне вікно діє й у режимі бібліотеки.
Усе в тимчасових теках (мініатюрна бібліотека). `python test_reel_library.py` → exit 0/1.
"""
import json
import os
import sys
import tempfile
from datetime import date
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import imageio
import numpy as np
from PIL import Image

import reel_library as rl

F = []


def chk(name, cond):
    print(f"[{'OK ' if cond else 'FAIL'}] {name}")
    if not cond:
        F.append(name)


def exits(fn, *a, **kw):
    try:
        fn(*a, **kw)
    except SystemExit:
        return True
    return False


def raises(fn, *a, **kw):
    try:
        fn(*a, **kw)
    except RuntimeError:
        return True
    return False


# --- мініатюрна бібліотека
tmp = Path(tempfile.mkdtemp(prefix="reel_lib_test_"))
(tmp / "first_card").mkdir()
(tmp / "end_mascot").mkdir()


def write_clip(path, n, size=(48, 64)):          # size = (висота, ширина)
    w = imageio.get_writer(str(path), fps=30, codec="libx264", macro_block_size=1, pixelformat="yuv420p")
    for i in range(n):
        w.append_data(np.full((size[0], size[1], 3), 40 + i % 100, np.uint8))
    w.close()


write_clip(tmp / "first_card" / "wink.mp4", 12)
write_clip(tmp / "first_card" / "icecream.mp4", 12)
write_clip(tmp / "first_card" / "halloween.mp4", 12)
(tmp / "first_card" / "empty.mp4").write_bytes(b"")
write_clip(tmp / "first_card" / "short.mp4", 3)
png = np.zeros((40, 50, 4), np.uint8)
png[10:30, 15:35] = (200, 120, 60, 255)               # видима частина: рядки 10–29, стовпці 15–34
Image.fromarray(png, "RGBA").save(tmp / "end_mascot" / "wink_f63.png")
Image.fromarray(png, "RGBA").save(tmp / "end_mascot" / "halloween_f94.png")
(tmp / "manifest.json").write_text(json.dumps({"scenes": {
    "wink": {"status": "approved", "file": "first_card/wink.mp4", "end_mascot_png": "end_mascot/wink_f63.png"},
    "icecream": {"status": "approved", "file": "first_card/icecream.mp4"},
    "halloween": {"status": "dropped_by_owner (25.10: «не пускаєм»)", "file": "first_card/halloween.mp4", "end_mascot_png": "end_mascot/halloween_f94.png"},
    "ghost": {"status": "approved", "file": "first_card/ghost_missing.mp4"},
    "empty": {"status": "approved", "file": "first_card/empty.mp4"},
    "short": {"status": "approved", "file": "first_card/short.mp4"},
}}, ensure_ascii=False), encoding="utf-8")

# --- бібліотека вимкнена за замовчуванням
os.environ.pop("PLUTUS_SCENE_LIBRARY", None)
chk("без змінної й без /opt-теки library_dir() = None (старий потік)", rl.library_dir() is None or str(rl.library_dir()) == rl.DEFAULT_VPS_DIR)
os.environ["PLUTUS_SCENE_LIBRARY"] = str(tmp)
chk("зі змінною library_dir() = її шлях", str(rl.library_dir()) == str(tmp))

# --- ключі сцен
chk("scene_key: scene_wink_RENDER_GREEN.mp4 → wink", rl.scene_key("scene_wink_RENDER_GREEN.mp4") == "wink")
chk("scene_key: wink.mp4 / wink → wink", rl.scene_key("wink.mp4") == "wink" and rl.scene_key("wink") == "wink")
chk("scene_key: дивне/порожнє → None", rl.scene_key("") is None and rl.scene_key("../etc/passwd") is None and rl.scene_key(None) is None)

# --- схвалені
ok = rl.approved_scenes()
chk("схвалені: wink, icecream, short (файли є)", {"wink", "icecream", "short"} <= set(ok))
chk("dropped halloween НЕ схвалена", "halloween" not in ok)
chk("approved, але файлу нема (ghost) → не схвалена", "ghost" not in ok)
chk("approved, але файл порожній → не схвалена", "empty" not in ok)
chk("first_card_path(несхвалена) = None", rl.first_card_path("halloween") is None and rl.first_card_path("scene_halloween_RENDER_GREEN.mp4") is None)
chk("first_card_path(схвалена) існує", rl.first_card_path("scene_wink_RENDER_GREEN.mp4").is_file())
chk("end_mascot_path: wink є, icecream (без PNG) None, halloween (dropped) None",
    rl.end_mascot_path("wink") is not None and rl.end_mascot_path("icecream") is None and rl.end_mascot_path("halloween") is None)

# --- кадри потоком
n = sum(1 for _ in rl.iter_first_card_frames("wink", size=(48, 64), frames_range=(10, 20)))
chk("iter_first_card_frames: 12 кадрів потоком", n == 12)
chk("розмір кадру не той → RuntimeError", raises(lambda: list(rl.iter_first_card_frames("wink", size=(1920, 1080), frames_range=(10, 20)))))
chk("кількість кадрів поза діапазоном (обрізаний файл) → RuntimeError", raises(lambda: list(rl.iter_first_card_frames("short", size=(48, 64), frames_range=(10, 20)))))
chk("несхвалена сцена → RuntimeError", raises(lambda: list(rl.iter_first_card_frames("halloween", size=(48, 64), frames_range=(1, 20)))))

# --- вирізка ендкартки
rgb, al = rl.load_end_mascot("wink")
chk("load_end_mascot: обрізка по bbox 20×20", rgb.shape == (20, 20, 3) and al.shape == (20, 20) and al.min() > 0.9)
chk("load_end_mascot без PNG → RuntimeError", raises(rl.load_end_mascot, "icecream"))

# --- reel_build у режимі бібліотеки
import reel_build as rb

chk("перша сцена несхвалена (halloween) → СТОП", exits(rb.check_library_scenes, "scene_halloween_RENDER_GREEN.mp4", "wink"))
chk("перша сцена не з бібліотеки (стара scene02) → СТОП", exits(rb.check_library_scenes, "scene02_sit_smile_ear_scratch_pika_GREEN.mp4", "wink"))
chk("ендсцена без end_mascot PNG (icecream) → СТОП", exits(rb.check_library_scenes, "wink", "icecream"))
chk("ендсцена dropped (halloween) → СТОП", exits(rb.check_library_scenes, "wink", "halloween"))
chk("схвалена перша + ендсцена з PNG → без СТОП", not exits(rb.check_library_scenes, "icecream", "scene_wink_RENDER_GREEN.mp4"))
chk("сезонне вікно діє й у режимі бібліотеки (snowflake у жовтні)", exits(rb.check_first_scene, "scene_snowflake_RENDER_GREEN.mp4", date(2026, 10, 25)))
chk("check_first_scene у режимі бібліотеки не шукає файл у plutus_scenes/", not exits(rb.check_first_scene, "icecream", date(2026, 10, 25)))

# --- ендкартка без плити
bg = rb.gradient_bg()
frames = rb.endcard_library(bg, 3, "Тестовий підпис", "wink")
chk("endcard_library: 3 кадри 1920×1080×3 uint8", len(frames) == 3 and frames[0].shape == (rb.H, rb.W, 3) and frames[0].dtype == np.uint8)
corner = tuple(int(x) for x in frames[0][rb.EC_TOP + 12, rb.EC_X + 12])
chk("ендкартка БЕЗ білого квадрата (кут колишньої плити не чисто білий)", corner != (255, 255, 255))
cx = rb.W // 2
chk("маскот присутній над текстом (піксель маскота не збігається з фоном)",
    any(tuple(frames[0][y, cx]) == (200, 120, 60) for y in range(rb.EC_TOP, rb.EC_TOP + rb.EC_H)))

# --- бібліотеку вимкнено: старий потік не зачеплено
os.environ.pop("PLUTUS_SCENE_LIBRARY", None)
if rl.library_dir() is None:
    chk("без бібліотеки check_first_scene шукає файл у plutus_scenes/ (старий потік)", exits(rb.check_first_scene, "scene_nonexistent_RENDER_GREEN.mp4", date(2026, 10, 25)))

sys.exit(1 if F else 0)

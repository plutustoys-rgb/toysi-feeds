#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_reel_batch.py — підпис і маніфест партії рілсів (reel_batch.py, 21.10.2026). Без мережі/рендера.
Інваріанти: (1) підпис = вступ, підрядок, рядки товарів із ЖИВОЮ ціною сайту, клич + хвіст про сайт, хештеги (без дублів), ≤ 2200; (2) товару нема в наявності на сайті → СТОП (підпис без ціни не пишемо);
(3) маніфест = verify (id, ціна сайту) + not_before = дата публікації T00:00; (4) site_prices бере лише s=1; (5) process --no-render пише .caption.txt і .json, без mp4; (6) усі 6 спек партії №1 валідні й їхні рядки підпису без «довільних знижок»."""
import json
import os
import sys
import tempfile
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
import reel_batch as rb  # noqa: E402

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


SPEC = {"queue_name": "t_01", "publish_date": "2026-11-03", "out": "x", "endline": "e", "first_scene": "scene_nod_RENDER_GREEN.mp4", "endscene": "scene_sunglasses_RENDER_GREEN.mp4",
        "caption": {"intro": "Вступ 🦊", "sub": "Підрядок:", "cta": "Клич", "hashtags": ["пазли", "#plutustoys", "пазли"]},
        "items": [{"id": "1", "hook": "h", "name": "n", "spec": ["a", "b"], "cap_emoji": "🧩", "cap_text": "Пазл А, 60 елементів"}, {"id": "2", "hook": "h", "name": "n", "spec": ["a", "b"], "cap_emoji": "❄️", "cap_text": "Пазл Б"}]}
PR = {"1": 268, "2": 326}
cap = rb.build_caption(SPEC, PR)
lines = cap.split("\n")
chk("підпис: вступ, підрядок, рядки з ціною сайту «— 268 грн», клич із хвостом про сайт", lines[0] == "Вступ 🦊" and lines[1] == "Підрядок:" and lines[2] == "🧩 Пазл А, 60 елементів — 268 грн" and lines[3] == "❄️ Пазл Б — 326 грн"
    and rb.TAIL in cap and "Клич 👉" in cap)
chk("хештеги: базові + теми, без дублів (#plutustoys не повторюється, «пазли» і «#plutustoys» нормалізовано)", lines[-1].split().count("#plutustoys") == 1 and lines[-1].split().count("#пазли") == 1)
chk("товару нема на сайті → СТОП, підпис без ціни не створюється", "нема в наявності на сайті" in (exits(lambda: rb.build_caption(SPEC, {"1": 268})) or ""))
chk("підпис > 2200 символів → СТОП", "2200" in (exits(lambda: rb.build_caption(dict(SPEC, caption=dict(SPEC["caption"], intro="я" * 2300)), PR)) or ""))
m = rb.build_manifest(SPEC, PR)
chk("маніфест: verify (id, ціна сайту) і not_before = 2026-11-03T00:00", m == {"verify": [{"id": "1", "price": 268}, {"id": "2", "price": 326}], "not_before": "2026-11-03T00:00"})

td = Path(tempfile.mkdtemp())
spec_path = td / "t_01.json"
spec_path.write_text(json.dumps(SPEC, ensure_ascii=False), encoding="utf-8")
out = td / "out"
rb.process(str(spec_path), str(out), None, PR, render=False)
chk("process --no-render: .caption.txt і .json створено (LF), mp4 — ні; json = маніфест", sorted(os.listdir(out)) == ["t_01.caption.txt", "t_01.json"] and json.loads((out / "t_01.json").read_text(encoding="utf-8")) == m
    and b"\r" not in (out / "t_01.caption.txt").read_bytes())
(td / "index-t.json").write_text(json.dumps([{"id": "1", "pr": 100, "s": 1}, {"id": "2", "pr": 200, "s": 0}, {"id": "3", "pr": 300.4, "s": 1}]), encoding="utf-8")
chk("site_prices: лише в наявності (s=1), округлення; відсутній файл index-rt/index — пропускається без падіння", rb.site_prices(str(td)) == {"1": 100, "3": 300})

# партія №1: усі спеки валідні, ціни в підписі лише з сайту
specs = sorted((HERE / "reels_specs").glob("*_01.json"))
batch = [p for p in specs if p.stem in ("puzzles_01", "halloween_01", "dough_01", "squish_01", "bubbles_01", "games_01")]
chk("партія №1: 6 спек на місці", len(batch) == 6)
for p in batch:
    sp = json.load(open(p, encoding="utf-8"))
    ok_keys = all(k in sp for k in ("queue_name", "publish_date", "first_scene", "endscene", "endline", "caption", "items")) and len(sp["items"]) == 3 and all(all(k in it for k in ("id", "hook", "name", "spec", "cap_emoji", "cap_text")) for it in sp["items"])
    prices_fake = {it["id"]: 100 for it in sp["items"]}
    try:
        text = rb.build_caption(sp, prices_fake)
    except SystemExit:
        text = ""
    chk(f"{p.stem}: ключі, 3 товари, підпис збирається; немає слів «знижк»/«акці»/«%»/«дешев» (довільних знижок не обіцяємо)", ok_keys and bool(text) and not any(w in text.lower() for w in ("знижк", "акці", "%", "дешев")))

print("\nFAIL:" if F else "\nOK: усі перевірки пройдені", *F, sep="\n")
sys.exit(1 if F else 0)

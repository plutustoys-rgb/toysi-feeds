#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_reel_weekly_vps.py — регрес автозбирача рілсів на VPS (reel_weekly.run_vps / publish_to_queue) і цілісності бібліотеки (SHA256SUMS).

Рішення власника 25.10.2026: рілси збираються автоматично, без людського схвалення, лише з першими сценами, які схвалив він. ІНВАРІАНТИ:
  - без бібліотеки авто-збірка вимкнена (СТОП); без SHA256SUMS або з пошкодженим файлом — СТОП + сигнал (обрізана заливка не доходить до черги);
  - збирає лише недостачу до ENSURE_SLOTS, не більше MAX_PER_RUN; слоти продовжуються ПІСЛЯ останньої дати в черзі (вт/пт);
  - перша сцена — з бібліотеки; ендсцена — схвалена З PNG і не та сама, що перша;
  - у чергу файли кладуться mp4 → caption → json ОСТАННІМ, без залишків *.incoming; стан збирача НЕ в черзі (publisher трактує будь-який *.json як маніфест);
  - збій одного рілса не валить партію й дає сигнал; невдалі товари не «займаються»;
  - сезонне вікно діє на голий ключ сцени.
Усе в тимчасових теках, мережі нема. `python test_reel_weekly_vps.py` → exit 0/1.
"""
import hashlib
import json
import os
import sys
import tempfile
from datetime import date
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).parent))

import numpy as np
from PIL import Image

import reel_library as rl
import reel_weekly as rw
import reel_build
import reel_batch

F = []


def chk(n, c):
    print(f"[{'OK ' if c else 'FAIL'}] {n}")
    if not c:
        F.append(n)


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


# ── бібліотека-фікстура: icecream (без PNG), wink/nod/noselick (з PNG), halloween (dropped)
def make_lib(root, sums=True):
    root = Path(root)
    (root / "first_card").mkdir(parents=True, exist_ok=True)
    (root / "end_mascot").mkdir(parents=True, exist_ok=True)
    png = np.zeros((40, 50, 4), np.uint8)
    png[10:30, 15:35] = (200, 120, 60, 255)
    scenes = {}
    for key, has_png, status in (("icecream", False, "approved"), ("wink", True, "approved"), ("nod", True, "approved"),
                                  ("noselick", True, "approved"), ("halloween", True, "dropped_by_owner")):
        (root / "first_card" / f"{key}.mp4").write_bytes(b"clip-" + key.encode() * 50)
        ent = {"status": status, "file": f"first_card/{key}.mp4"}
        if has_png:
            Image.fromarray(png, "RGBA").save(root / "end_mascot" / f"{key}_f1.png")
            ent["end_mascot_png"] = f"end_mascot/{key}_f1.png"
        scenes[key] = ent
    (root / "manifest.json").write_text(json.dumps({"scenes": scenes}), encoding="utf-8")
    if sums:
        lines = []
        for rel in [e["file"] for e in scenes.values()] + [e["end_mascot_png"] for e in scenes.values() if "end_mascot_png" in e]:
            lines.append(f"{hashlib.sha256((root / rel).read_bytes()).hexdigest()}  {rel}")
        (root / "SHA256SUMS").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return root


tmp = Path(tempfile.mkdtemp(prefix="reel_vps_test_"))
lib = make_lib(tmp / "lib")
os.environ["PLUTUS_SCENE_LIBRARY"] = str(lib)

# ── 1. цілісність бібліотеки
chk("SHA256SUMS збігається → True", rl.check_integrity(lib, require=True) is True)
lib2 = make_lib(tmp / "lib_nosums", sums=False)
chk("нема SHA256SUMS, require=False → False (без помилки)", rl.check_integrity(lib2) is False)
chk("нема SHA256SUMS, require=True → RuntimeError", raises(rl.check_integrity, lib2, True))
lib3 = make_lib(tmp / "lib_tamper")
data = (lib3 / "first_card" / "wink.mp4").read_bytes()
(lib3 / "first_card" / "wink.mp4").write_bytes(data[: len(data) // 2])
chk("обрізаний файл (sha256 не збігається) → RuntimeError", raises(rl.check_integrity, lib3))
lib4 = make_lib(tmp / "lib_missing_entry")
lines = [ln for ln in (lib4 / "SHA256SUMS").read_text(encoding="utf-8").splitlines() if "nod.mp4" not in ln]
(lib4 / "SHA256SUMS").write_text("\n".join(lines) + "\n", encoding="utf-8")
chk("схвалений файл не в SHA256SUMS → RuntimeError", raises(rl.check_integrity, lib4))
chk("dropped (halloween) у SHA256SUMS не вимагається", rl.check_integrity(lib, require=True) is True)
chk("reel_build.check_library_scenes: пошкоджена бібліотека → СТОП", (lambda: (os.environ.__setitem__("PLUTUS_SCENE_LIBRARY", str(lib3)), exits(reel_build.check_library_scenes, "nod", "noselick"))[1])())
os.environ["PLUTUS_SCENE_LIBRARY"] = str(lib)

# ── 2. сезонне вікно на голий ключ
chk("canonical_scene_file: ключ → scene_<ключ>_RENDER_GREEN.mp4", rl.canonical_scene_file("snowflake") == "scene_snowflake_RENDER_GREEN.mp4")
chk("голий ключ snowflake у жовтні → СТОП (сезонне вікно діє)", exits(reel_build.check_first_scene, "snowflake", date(2026, 10, 25)))

# ── 3. ротація й ендсцена з бібліотеки
rot = rw.library_rotation()
chk("ротація лише зі схвалених: noselick, icecream, nod, wink (не halloween/headshake/lookaround)",
    rot == ["scene_noselick_RENDER_GREEN.mp4", "scene_icecream_RENDER_GREEN.mp4", "scene_nod_RENDER_GREEN.mp4", "scene_wink_RENDER_GREEN.mp4"])
ends = rw.library_end_choices()
chk("ендсцени лише схвалені З PNG (icecream без PNG виключено): noselick, wink, nod", ends == ["scene_noselick_RENDER_GREEN.mp4", "scene_wink_RENDER_GREEN.mp4", "scene_nod_RENDER_GREEN.mp4"])
chk("pick_scene у режимі бібліотеки: після noselick → icecream; після wink → noselick (замикання)",
    rw.pick_scene(["scene_noselick_RENDER_GREEN.mp4"], date(2026, 10, 30), reel_build.scene_season_ok, names=rot) == "scene_icecream_RENDER_GREEN.mp4"
    and rw.pick_scene(["scene_wink_RENDER_GREEN.mp4"], date(2026, 10, 30), reel_build.scene_season_ok, names=rot) == "scene_noselick_RENDER_GREEN.mp4")
TH = {"key": "t", "title": "т", "categories": ["К"], "emoji": "🧸", "hook_verb": "Відчуй", "stat_default": "с", "tail": "х", "intro": "i", "sub": "s", "cta": "c", "endline": "e", "hashtags": ["х"]}
sp = rw.make_spec(TH, [{"id": "1", "n": "Товар один"}], date(2026, 10, 30), "scene_noselick_RENDER_GREEN.mp4", "t_01", end_choices=ends)
chk("make_spec: перша noselick → ендсцена wink (інша, з PNG)", sp["first_scene"] == "scene_noselick_RENDER_GREEN.mp4" and sp["endscene"] == "scene_wink_RENDER_GREEN.mp4")
sp = rw.make_spec(TH, [{"id": "1", "n": "Товар один"}], date(2026, 10, 30), "scene_icecream_RENDER_GREEN.mp4", "t_02", end_choices=ends)
chk("make_spec: перша icecream → ендсцена noselick", sp["endscene"] == "scene_noselick_RENDER_GREEN.mp4")

# ── 4. queue_count і publish_to_queue
q = tmp / "queue"
(q / "published").mkdir(parents=True)
for n in ("a_01", "b_01"):
    (q / f"{n}.json").write_text("{}")
(q / "published.json").write_text("[]")
(q / "_junk.json").write_text("{}")
(q / "a_01.caption.txt").write_text("x")
chk("queue_count: лише маніфести (без published.json, без _*, без підтек)", rw.queue_count(str(q)) == 2)
chk("queue_count: нема теки → 0", rw.queue_count(str(tmp / "nope")) == 0)

stg = tmp / "staging"
stg.mkdir()
for ext, body in ((".mp4", b"video"), (".caption.txt", b"caption"), (".json", b"{}")):
    (stg / f"x_01{ext}").write_bytes(body)
order_log = []
real_replace = os.replace


def spy_replace(a, b):
    order_log.append(os.path.basename(b))
    return real_replace(a, b)


os.replace = spy_replace
try:
    rw.publish_to_queue("x_01", str(stg), str(q))
finally:
    os.replace = real_replace
chk("publish_to_queue: порядок mp4 → caption → json ОСТАННІМ", order_log == ["x_01.mp4", "x_01.caption.txt", "x_01.json"])
chk("publish_to_queue: у черзі 3 файли, жодного *.incoming, staging прибрано",
    all((q / f"x_01{e}").is_file() for e in (".mp4", ".caption.txt", ".json")) and not list(q.glob("*.incoming")) and not list(stg.glob("x_01*")))
for ext, body in ((".mp4", b"v"), (".caption.txt", b"c"), (".json", b"{}")):
    (stg / f"x_01{ext}").write_bytes(body)
chk("publish_to_queue: у черзі вже є x_01 → RuntimeError, нічого не перезаписано", raises(rw.publish_to_queue, "x_01", str(stg), str(q)) and (q / "x_01.mp4").read_bytes() == b"video")
for fn in list(stg.glob("*")):
    fn.unlink()
(stg / "y_01.mp4").write_bytes(b"")
(stg / "y_01.caption.txt").write_bytes(b"c")
(stg / "y_01.json").write_bytes(b"{}")
chk("publish_to_queue: порожнє відео у staging → RuntimeError, у чергу не потрапило", raises(rw.publish_to_queue, "y_01", str(stg), str(q)) and not (q / "y_01.json").exists())

# ── 5. run_vps: добір черги з фікстурами
THEMES = json.load(open(rw.THEMES_FILE, encoding="utf-8"))["themes"]
words = ["Альфа", "Браво", "Чарлі", "Дельта", "Ехо", "Фокстрот", "Гольф", "Готель", "Індія", "Джульєт", "Кіло", "Ліма"]
items = []
for t_i, th in enumerate(THEMES[:6]):          # 6 тем × 12 товарів: cooldown тем (3 останні) не вичерпує вибір
    cat = th["categories"][0]
    items += [{"id": str(500 + t_i * 100 + i), "n": f"{w}{chr(1040 + i)}{t_i} виріб {i}", "c": cat, "pr": 150 + i, "s": 1} for i, w in enumerate(words)]
filler = [{"id": str(2000 + i), "n": f"Філер{chr(1040 + i % 30)}{i}", "c": "Інше", "pr": 200, "s": 1} for i in range(1100)]
feed = {x["id"]: {"avail": "in stock", "image": "http://x"} for x in items + filler}

for p in q.glob("*"):
    if p.is_file():
        p.unlink()
(q / "published.json").write_text("[]")
(q / "old_01.json").write_text(json.dumps({"verify": [{"id": "999", "price": 1}], "not_before": "2026-11-10T00:00"}))
(q / "old_02.json").write_text(json.dumps({"verify": [{"id": "998", "price": 1}], "not_before": "2026-11-13T00:00"}))
stg = tmp / "staging2"
EMPTY = str(tmp / "empty_specs")
os.makedirs(EMPTY)
alerts = []
real_process, real_prices = reel_batch.process, reel_batch.site_prices


def fake_process(spec_path, out_dir, feed_path, prices, render=True):
    spec = json.load(open(spec_path, encoding="utf-8"))
    name = spec["queue_name"]
    Path(out_dir, name + ".mp4").write_bytes(b"video-" + name.encode())
    Path(out_dir, name + ".caption.txt").write_text("caption", encoding="utf-8")
    Path(out_dir, name + ".json").write_text(json.dumps({"verify": [{"id": i["id"], "price": 1} for i in spec["items"]], "not_before": spec["publish_date"] + "T00:00"}))
    return name


reel_batch.process = fake_process
reel_batch.site_prices = lambda: {}
try:
    built = rw.run_vps(ensure_slots=4, queue_dir=str(q), staging_dir=str(stg), today=date(2026, 10, 25), index=items + filler, feed=feed, demand=set(),
                       now_forbidden=lambda it: False, specs_dir=EMPTY, alert=lambda k, t: alerts.append((k, t)))
    chk("run_vps: у черзі 2, ціль 4 → зібрано 2", len(built) == 2)
    chk("слоти продовжують ПІСЛЯ останньої дати в черзі (13.11): вт 17.11, пт 20.11", [b["publish_date"] for b in built] == ["2026-11-17", "2026-11-20"])
    chk("перші сцени — з бібліотеки, ендсцени схвалені з PNG і відмінні від першої",
        all(b["first_scene"] in rot and b["endscene"] in ends and b["endscene"] != b["first_scene"] for b in built))
    chk("у черзі тепер 4 маніфести; жодного *.incoming; mp4+caption поруч", rw.queue_count(str(q)) == 4 and not list(q.glob("*.incoming"))
        and all((q / f"{b['queue_name']}.mp4").is_file() and (q / f"{b['queue_name']}.caption.txt").is_file() for b in built))
    chk("стан збирача у staging, НЕ в черзі (publisher не прочитає як маніфест)", (stg / "_weekly_state.json").is_file() and not (q / "_weekly_state.json").exists())
    chk("збирач не лишив у staging mp4/caption/json готових рілсів (лише _specs/ і стан)", sorted(os.listdir(stg)) == ["_specs", "_weekly_state.json"])
    chk("сигналів немає при чистому прогоні", alerts == [])
    again = rw.run_vps(ensure_slots=4, queue_dir=str(q), staging_dir=str(stg), today=date(2026, 10, 25), index=items + filler, feed=feed, demand=set(),
                       now_forbidden=lambda it: False, specs_dir=EMPTY, alert=lambda k, t: alerts.append((k, t)))
    chk("повторний запуск: черга повна (4 з 4) → нічого не збирає", again == [] and rw.queue_count(str(q)) == 4)
    # MAX_PER_RUN
    for p in list(q.glob("*")):
        if p.is_file() and p.name != "published.json":
            p.unlink()
    shutil_stg = tmp / "staging3"
    b3 = rw.run_vps(ensure_slots=10, queue_dir=str(q), staging_dir=str(shutil_stg), today=date(2026, 10, 25), index=items + filler, feed=feed, demand=set(),
                    now_forbidden=lambda it: False, specs_dir=EMPTY, alert=lambda k, t: alerts.append((k, t)))
    chk("MAX_PER_RUN: порожня черга, ціль 10 → за запуск не більше 3", len(b3) == rw.MAX_PER_RUN == 3)

    # збій збірки одного рілса: решта йдуть, сигнал є, невдалі id не «займаються»
    calls = {"n": 0}

    def flaky(spec_path, out_dir, feed_path, prices, render=True):
        calls["n"] += 1
        if calls["n"] == 1:
            sys.exit("[reel_batch] СТОП: тест — товар зник")
        return fake_process(spec_path, out_dir, feed_path, prices, render)

    reel_batch.process = flaky
    for p in list(q.glob("*")):
        if p.is_file() and p.name != "published.json":
            p.unlink()
    stg4 = tmp / "staging4"
    alerts.clear()
    b4 = rw.run_vps(ensure_slots=2, queue_dir=str(q), staging_dir=str(stg4), today=date(2026, 10, 25), index=items + filler, feed=feed, demand=set(),
                    now_forbidden=lambda it: False, specs_dir=EMPTY, alert=lambda k, t: alerts.append((k, t)))
    chk("збій першого рілса: другий зібрано, сигнал reel_weekly_fail надіслано", len(b4) == 1 and [k for k, _ in alerts] == ["reel_weekly_fail"])
    chk("невдала спека не лишилась у _specs/ (не займає id/дату), у черзі лише успішний", sorted(os.listdir(stg4 / "_specs")) == [b4[0]["queue_name"] + ".json"] and rw.queue_count(str(q)) == 1)
finally:
    reel_batch.process, reel_batch.site_prices = real_process, real_prices

# ── 6. вимкнено без бібліотеки / з пошкодженою
os.environ.pop("PLUTUS_SCENE_LIBRARY", None)
if rl.library_dir() is None:
    chk("run_vps без бібліотеки → СТОП", exits(rw.run_vps, 4, str(q), str(tmp / "s5"), alert=lambda k, t: alerts.append((k, t))))
os.environ["PLUTUS_SCENE_LIBRARY"] = str(lib3)
alerts.clear()
chk("run_vps з пошкодженою бібліотекою → СТОП + сигнал reel_weekly_lib", exits(rw.run_vps, 4, str(q), str(tmp / "s6"), alert=lambda k, t: alerts.append((k, t))) and [k for k, _ in alerts] == ["reel_weekly_lib"])

sys.exit(1 if F else 0)

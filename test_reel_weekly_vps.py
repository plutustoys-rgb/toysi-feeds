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

# ── 6. без бібліотеки: тихий вихід; з пошкодженою: СТОП + сигнал
os.environ.pop("PLUTUS_SCENE_LIBRARY", None)
real_default = rl.DEFAULT_VPS_DIR
rl.DEFAULT_VPS_DIR = str(tmp / "no_such_dir")
alerts.clear()
chk("library_dir() без env і без теки = None", rl.library_dir() is None)
chk("run_vps без бібліотеки → тихо [] (exit 0), без сигналу (автозбір вмикає заливка бібліотеки)", rw.run_vps(4, str(q), str(tmp / "s5"), alert=lambda k, t: alerts.append((k, t))) == [] and alerts == [])
os.environ["PLUTUS_SCENE_LIBRARY"] = str(tmp / "no_such_dir2")
chk("PLUTUS_SCENE_LIBRARY на неіснуючу теку → теж тихо []", rw.run_vps(4, str(q), str(tmp / "s5"), alert=lambda k, t: alerts.append((k, t))) == [] and alerts == [])
rl.DEFAULT_VPS_DIR = real_default
os.environ["PLUTUS_SCENE_LIBRARY"] = str(lib3)
alerts.clear()
chk("run_vps з пошкодженою бібліотекою → СТОП + сигнал reel_weekly_lib", exits(rw.run_vps, 4, str(q), str(tmp / "s6"), alert=lambda k, t: alerts.append((k, t))) and [k for k, _ in alerts] == ["reel_weekly_lib"])
os.environ["PLUTUS_SCENE_LIBRARY"] = str(lib)

# ── 7. check_integrity: UTF-16 і відсутній PNG → RuntimeError (не traceback)
lib5 = make_lib(tmp / "lib_utf16")
txt = (lib5 / "SHA256SUMS").read_text(encoding="utf-8")
(lib5 / "SHA256SUMS").write_bytes(txt.encode("utf-16"))
chk("SHA256SUMS у UTF-16 (PowerShell 5.1) → RuntimeError", raises(rl.check_integrity, lib5, True))
lib6 = make_lib(tmp / "lib_nopng")
(lib6 / "end_mascot" / "wink_f1.png").unlink()
chk("у manifest є end_mascot_png, а файла нема → RuntimeError", raises(rl.check_integrity, lib6, True))

# ── 8. імена: чергу й published/ враховано; часткова заливка прибирається
q2 = tmp / "q2"
(q2 / "published").mkdir(parents=True)
(q2 / "puzzles_01.mp4").write_bytes(b"x")                 # сирота без json
(q2 / "published" / "puzzles_02.json").write_text("{}")   # уже опублікований
nm = rw.next_queue_name("puzzles", str(tmp / "out_names"), extra_dirs=(str(q2),))
chk("next_queue_name уникає імен із черги (mp4-сирота) і з published/: → puzzles_03", nm == "puzzles_03")

stg5 = tmp / "stg5"
stg5.mkdir()
for ext, body in ((".mp4", b"video"), (".caption.txt", b"caption"), (".json", b"{}")):
    (stg5 / f"z_01{ext}").write_bytes(body)
q3 = tmp / "q3"
q3.mkdir()
import shutil as _sh
real_copy = _sh.copyfile
n_copy = {"n": 0}


def failing_copy(a, b_):
    n_copy["n"] += 1
    if n_copy["n"] == 2:
        raise OSError(28, "No space left on device")
    return real_copy(a, b_)


_sh.copyfile = failing_copy
try:
    try:
        rw.publish_to_queue("z_01", str(stg5), str(q3))
        raised_os = False
    except OSError:
        raised_os = True
    chk("publish_to_queue: збій диска на 2-му файлі → OSError піднімається далі", raised_os)
finally:
    _sh.copyfile = real_copy
chk("після збою в черзі НІЧОГО від z_01 (ні mp4, ні *.incoming), json не з'явився", os.listdir(q3) == [])
chk("staging після збою не втрачено (повтор можливий)", all((stg5 / f"z_01{e}").is_file() for e in (".mp4", ".caption.txt", ".json")))

# ── 9. слоти за симуляцією черги (поточний реальний стан VPS 25.10)
q4 = tmp / "q4"
q4.mkdir()
for nm_, nb in (("builders_cars_01", "2026-10-23"), ("puzzles_01", "2026-10-28"), ("squish_01", "2026-10-31"), ("bubbles_01", "2026-11-04"), ("games_01", "2026-11-07")):
    (q4 / f"{nm_}.json").write_text(json.dumps({"verify": [], "not_before": nb + "T00:00"}))
last = rw.queue_last_slot(str(q4), date(2026, 10, 25))
chk("queue_last_slot для поточної черги = вт 10.11 (5 рілсів на 27.10, 30.10, 03.11, 06.11, 10.11)", last == date(2026, 11, 10))
chk("наступні слоти: пт 13.11, вт 17.11", rw.next_slots(last, 2) == [date(2026, 11, 13), date(2026, 11, 17)])
chk("порожня черга → None", rw.queue_last_slot(str(tmp / "nope"), date(2026, 10, 25)) is None)

# ── 10. засів стану з черги
st0 = {"used_ids": [], "themes": [], "scenes": []}
q5 = tmp / "q5"
q5.mkdir()
for nm_, nb in (("puzzles_01", "2026-10-30"), ("builders_cars_01", "2026-10-27"), ("squish_01", "2026-11-03")):
    (q5 / f"{nm_}.json").write_text(json.dumps({"verify": [], "not_before": nb + "T00:00"}))
seeded = rw.seed_state_from_queue(st0, str(q5), {"puzzles", "squish", "dough"})
chk("засів: теми з імен черги за not_before, лише відомі теми (builders_cars не тема)", seeded["themes"] == ["puzzles", "squish"])
chk("засів не чіпає вже наявний стан", rw.seed_state_from_queue({"used_ids": [], "themes": ["dough"], "scenes": []}, str(q5), {"puzzles"})["themes"] == ["dough"])

# ── 11. refresh_queue: нові ціни, виселення, SMM-рілс не чіпаємо
q6, s6 = tmp / "q6", tmp / "s6x"
(s6 / "_specs").mkdir(parents=True)
q6.mkdir()
SPEC = {"queue_name": "r_01", "publish_date": "2026-11-03", "first_scene": "scene_wink_RENDER_GREEN.mp4", "endscene": "scene_nod_RENDER_GREEN.mp4",
        "caption": {"intro": "Інтро", "sub": "", "cta": "Клич", "hashtags": ["х"]},
        "items": [{"id": "1", "cap_emoji": "🧸", "cap_text": "Товар А"}, {"id": "2", "cap_emoji": "🧸", "cap_text": "Товар Б"}]}
(s6 / "_specs" / "r_01.json").write_text(json.dumps(SPEC, ensure_ascii=False), encoding="utf-8")
(q6 / "r_01.mp4").write_bytes(b"v")
(q6 / "r_01.caption.txt").write_text("старий", encoding="utf-8")
(q6 / "r_01.json").write_text(json.dumps({"verify": [{"id": "1", "price": 100}, {"id": "2", "price": 200}], "not_before": "2026-11-03T00:00"}))
(q6 / "smm_01.mp4").write_bytes(b"v")
(q6 / "smm_01.json").write_text(json.dumps({"verify": [{"id": "9", "price": 5}], "not_before": "2026-11-06T00:00"}))
al6 = []
r_, e_ = rw.refresh_queue(str(q6), str(s6), {"1": 100, "2": 250, "9": 777}, lambda k, t: al6.append(k))
mf = json.load(open(q6 / "r_01.json", encoding="utf-8"))
cap = (q6 / "r_01.caption.txt").read_text(encoding="utf-8")
chk("refresh: ціна товару 2 змінилась → verify і підпис оновлено, not_before збережено", r_ == ["r_01"] and e_ == [] and mf["verify"][1]["price"] == 250 and "250 грн" in cap and mf["not_before"] == "2026-11-03T00:00")
chk("refresh: SMM-рілс (нема спеки у staging) не чіпаємо навіть із чужою ціною", json.load(open(q6 / "smm_01.json"))["verify"][0]["price"] == 5)
r_, e_ = rw.refresh_queue(str(q6), str(s6), {"1": 100}, lambda k, t: al6.append(k))
chk("refresh: товару 2 нема в наявності → рілс виселено в _evicted/, з черги зник, сигнал reel_weekly_evict",
    e_ == ["r_01"] and not (q6 / "r_01.json").exists() and (s6 / "_evicted" / "r_01.json").is_file() and (s6 / "_evicted" / "r_01.mp4").is_file() and al6[-1] == "reel_weekly_evict")
chk("після виселення queue_count не рахує його (лишається лише SMM-рілс)", rw.queue_count(str(q6)) == 1)

# ── 12. сезонні сцени не в ротації
lib7 = make_lib(tmp / "lib_seasonal")
m7 = json.loads((lib7 / "manifest.json").read_text(encoding="utf-8"))
m7["scenes"]["halloween"]["status"] = "approved"             # власник «схвалив» сезонну: у ротацію авто-режиму вона НЕ входить
(lib7 / "manifest.json").write_text(json.dumps(m7), encoding="utf-8")
os.environ["PLUTUS_SCENE_LIBRARY"] = str(lib7)
chk("сезонна approved-сцена (halloween) не потрапляє в ротацію й ендсцени авто-режиму",
    "scene_halloween_RENDER_GREEN.mp4" not in rw.library_rotation() and "scene_halloween_RENDER_GREEN.mp4" not in rw.library_end_choices())
os.environ["PLUTUS_SCENE_LIBRARY"] = str(lib)

# ── 13. ручний потік не змінився: збій збірки без on_built/failures — виняток як раніше; VPS-режим ловить будь-який виняток
reel_batch.site_prices = lambda: {}


def boom_sysexit(*a, **k):
    sys.exit("[reel_batch] СТОП: тест")


def boom_value(*a, **k):
    raise ValueError("несподіване")


reel_batch.process = boom_sysexit
manual_out = tmp / "manual_out"
chk("ручний потік: СТОП збірки НЕ ковтається (SystemExit як раніше)",
    exits(rw.run_build, 1, str(manual_out), today=date(2026, 10, 25), index=items + filler, feed=feed, demand=set(), now_forbidden=lambda it: False, specs_dir=EMPTY))
reel_batch.process = boom_value
fl = []
b_ = rw.run_build(1, str(tmp / "vps_out"), today=date(2026, 10, 25), index=items + filler, feed=feed, demand=set(), now_forbidden=lambda it: False, specs_dir=EMPTY,
                  failures=fl, on_built=lambda spec: None)
chk("VPS-режим: ValueError збірки не валить партію, потрапляє в failures", b_ == [] and len(fl) == 1 and "ValueError" in fl[0])
reel_batch.process, reel_batch.site_prices = real_process, real_prices


# ── 14. запобіжники refresh_queue (дрібні пропозиції аудиту #1003)
q7, s7 = tmp / "q7", tmp / "s7x"
(s7 / "_specs").mkdir(parents=True)
q7.mkdir()


def mk_reel(qdir, sdir, name, ids, price=100):
    spec = dict(SPEC, queue_name=name, items=[{"id": i, "cap_emoji": "🧸", "cap_text": "Т" + i} for i in ids])
    (sdir / "_specs" / f"{name}.json").write_text(json.dumps(spec, ensure_ascii=False), encoding="utf-8")
    (qdir / f"{name}.mp4").write_bytes(b"v")
    (qdir / f"{name}.caption.txt").write_text("старий", encoding="utf-8")
    (qdir / f"{name}.json").write_text(json.dumps({"verify": [{"id": i, "price": price} for i in ids], "not_before": "2026-11-03T00:00"}))


mk_reel(q7, s7, "p_01", ["1"])
(q7 / "p_01.publishing").write_text("")
al7 = []
r7, e7 = rw.refresh_queue(str(q7), str(s7), {}, lambda k, t: al7.append(k))
chk("refresh: рілс із .publishing не чіпаємо (навіть якщо товару нема)", e7 == [] and r7 == [] and (q7 / "p_01.json").is_file() and al7 == [])

mk_reel(q7, s7, "m_01", ["11"]); mk_reel(q7, s7, "m_02", ["12"]); mk_reel(q7, s7, "m_03", ["13"])
al7.clear()
r7, e7 = rw.refresh_queue(str(q7), str(s7), {"13": 100}, lambda k, t: al7.append(k))
chk("масове виселення (2 з 4 рілсів VPS без публікації) ЗУПИНЕНО: нічого не виселено, сигнал reel_weekly_evict_mass",
    e7 == [] and (q7 / "m_01.json").is_file() and (q7 / "m_02.json").is_file() and al7 == ["reel_weekly_evict_mass"])
(q7 / "p_01.publishing").unlink()
os.remove(q7 / "p_01.json"); os.remove(q7 / "p_01.mp4"); os.remove(q7 / "p_01.caption.txt"); os.remove(s7 / "_specs" / "p_01.json")
al7.clear()
r7, e7 = rw.refresh_queue(str(q7), str(s7), {"11": 100, "13": 100}, lambda k, t: al7.append(k))
chk("1 з 3 рілсів без товару → виселено лише його", e7 == ["m_02"] and (q7 / "m_01.json").is_file() and not (q7 / "m_02.json").exists() and al7 == ["reel_weekly_evict"])

(s7 / "_specs" / "m_03.json").write_text("{не json", encoding="utf-8")
al7.clear()
r7, e7 = rw.refresh_queue(str(q7), str(s7), {"11": 100, "13": 100}, lambda k, t: al7.append(k))
chk("нечитабельна спека НЕ виселяє рілс (тільки сигнал reel_weekly_unreadable)", (q7 / "m_03.json").is_file() and "m_03" not in e7 and "reel_weekly_unreadable" in al7)

# MemoryError не ковтається у VPS-режимі
def boom_mem(*a, **k):
    raise MemoryError()


reel_batch.process = boom_mem
reel_batch.site_prices = lambda: {}
try:
    rw.run_build(1, str(tmp / "vps_out2"), today=date(2026, 10, 25), index=items + filler, feed=feed, demand=set(), now_forbidden=lambda it: False, specs_dir=EMPTY,
                 failures=[], on_built=lambda spec: None)
    mem_swallowed = True
except MemoryError:
    mem_swallowed = False
finally:
    reel_batch.process, reel_batch.site_prices = real_process, real_prices
chk("MemoryError у VPS-режимі піднімається (OOM-каскад зупиняється), не ковтається", not mem_swallowed)

sys.exit(1 if F else 0)

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_reel_guard.py — reel_guard.py (щоденний сторож черги рілсів + автозаміна) і видимість утримання (vps_health.collect_reels, health_check.check_reels).

ІНВАРІАНТИ (23.11.2026, слово власника «перевірте та зробіть автоматизацію»):
  - зник товар у рілсі збирача → заміна збирається на ТОЙ САМИЙ слот і ТУ САМУ тему, проходить перевірку publisher'а ДО запису в чергу, старий виселяється ЛИШЕ після успіху;
  - розбіжність, яку лікує publisher (refresh_prices) або спека (ціни без рендера), заміни НЕ викликає;
  - ліміти: ≤1 спроба/добу на слот, ≤2/добу всього, ≤3 невдалі на рілс; масове утримання → стоп; ручні рілси, .publishing, опубліковані, поза горизонтом — недоторкані;
  - збій збірки → старий лишається, у черзі нічого зайвого; дубль-запуск не робить другої заміни; used_ids не повторюються;
  - брама REEL_GUARD вимкнена → лише спостереження (чергу не чіпає); --dry нічого не пише;
  - сповіщення мають префікс «⚠️ reel_guard:» і категорію warning (telegram_digest._classify); health_check піднімає «слот без рілса» й утримані до 🟠.
Мережі/ffmpeg/Telegram/Instagram нема: рендер — мок, ціни — словник, сповіщення — список. `python test_reel_guard.py` → exit 0/1."""
import hashlib
import json
import os
import sys
import tempfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

os.environ["AUDIT_NO_TELEGRAM"] = "1"
os.environ["TELEGRAM_BOT_TOKEN"] = ""
os.environ["TELEGRAM_CHAT_ID"] = ""
os.environ.pop("REEL_GUARD", None)
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).parent))

import numpy as np
from PIL import Image

import reel_batch
import reel_guard as rg
import reel_library as rl
import reel_weekly as rw
import telegram_digest as td
import vps_health as vh
import health_check as hc

F = []


def chk(n, c):
    print(f"[{'OK ' if c else 'FAIL'}] {n}")
    if not c:
        F.append(n)


# ── фікстури ──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
tmp = Path(tempfile.mkdtemp(prefix="reel_guard_test_"))
rg.LOG_FILE = str(tmp / "reel_guard_log.jsonl")


def make_lib(root):
    root = Path(root)
    (root / "first_card").mkdir(parents=True, exist_ok=True)
    (root / "end_mascot").mkdir(parents=True, exist_ok=True)
    png = np.zeros((40, 50, 4), np.uint8)
    png[10:30, 15:35] = (200, 120, 60, 255)
    scenes = {}
    for key in ("wink", "nod", "noselick", "icecream"):
        (root / "first_card" / f"{key}.mp4").write_bytes(b"clip-" + key.encode() * 50)
        ent = {"status": "approved", "file": f"first_card/{key}.mp4"}
        if key != "icecream":
            Image.fromarray(png, "RGBA").save(root / "end_mascot" / f"{key}_f1.png")
            ent["end_mascot_png"] = f"end_mascot/{key}_f1.png"
        scenes[key] = ent
    (root / "manifest.json").write_text(json.dumps({"scenes": scenes}), encoding="utf-8")
    lines = [f"{hashlib.sha256((root / e['file']).read_bytes()).hexdigest()}  {e['file']}" for e in scenes.values()]
    lines += [f"{hashlib.sha256((root / e['end_mascot_png']).read_bytes()).hexdigest()}  {e['end_mascot_png']}" for e in scenes.values() if "end_mascot_png" in e]
    (root / "SHA256SUMS").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return root


lib = make_lib(tmp / "lib")
os.environ["PLUTUS_SCENE_LIBRARY"] = str(lib)
ENDS = rw.library_end_choices()

THEMES = json.load(open(rw.THEMES_FILE, encoding="utf-8"))["themes"]
words = ["Альфа", "Браво", "Чарлі", "Дельта", "Ехо", "Фокстрот", "Гольф", "Готель", "Індія", "Джульєт", "Кіло", "Ліма"]
ITEMS = []
for t_i, th in enumerate(THEMES[:6]):       # puzzles 500.., dough 600.., squish 700.., bubbles 800.., games 900.., builders 1000..
    cat = th["categories"][0]
    ITEMS += [{"id": str(500 + t_i * 100 + i), "n": f"{w}{chr(1040 + i)}{t_i} виріб {i}", "c": cat, "pr": 150 + i, "s": 1} for i, w in enumerate(words)]
FILLER = [{"id": str(2000 + i), "n": f"Філер{chr(1040 + i % 30)}{i}", "c": "Інше", "pr": 200, "s": 1} for i in range(1100)]
FEED = {x["id"]: {"avail": "in stock", "image": "http://x"} for x in ITEMS + FILLER}
BIG = {x["id"]: int(x["pr"]) for x in ITEMS + FILLER}
assert len(BIG) >= 1000
TH = {t["key"]: t for t in THEMES}
EMPTY_SPECS = str(tmp / "empty_specs")
os.makedirs(EMPTY_SPECS)

NOW = datetime(2026, 11, 23, 12, 0)         # понеділок; найближчий слот — вт 24.11 18:30; горизонт до пт 27.11
SLOT = date(2026, 11, 24)


def ids_of(theme_idx, a, b):
    return [str(500 + theme_idx * 100 + i) for i in range(a, b)]


def new_env(name):
    q, s = tmp / f"{name}_q", tmp / f"{name}_s"
    q.mkdir()
    (s / "_specs").mkdir(parents=True)
    (q / "published.json").write_text("[]", encoding="utf-8")
    return q, s


def mk_built(q, s, name, theme_key, ids, nb, prices=None, publishing=False, mp4=True):
    """Рілс, зібраний автозбирачем: спека в staging/_specs, mp4/підпис/маніфест у черзі з цінами `prices` на момент збірки."""
    prices = prices or BIG
    its = [x for x in ITEMS if x["id"] in ids]
    spec = rw.make_spec(TH[theme_key], its, date.fromisoformat(nb), "scene_wink_RENDER_GREEN.mp4", name, end_choices=ENDS)
    (s / "_specs" / f"{name}.json").write_text(json.dumps(spec, ensure_ascii=False), encoding="utf-8")
    if mp4:
        (q / f"{name}.mp4").write_bytes(b"video-" + name.encode())
    (q / f"{name}.caption.txt").write_text(reel_batch.build_caption(spec, prices) + "\n", encoding="utf-8")
    (q / f"{name}.json").write_text(json.dumps(reel_batch.build_manifest(spec, prices), ensure_ascii=False), encoding="utf-8")
    if publishing:
        (q / f"{name}.publishing").write_text("x", encoding="utf-8")
    return spec


def mk_manual(q, name, ids, nb, prices=None):
    """Ручний (SMM) рілс: у staging спеки нема."""
    prices = prices or BIG
    (q / f"{name}.mp4").write_bytes(b"video")
    (q / f"{name}.caption.txt").write_text("Добірка: " + ", ".join(f"{prices.get(i, 1)} грн" for i in ids), encoding="utf-8")
    (q / f"{name}.json").write_text(json.dumps({"verify": [{"id": i, "price": prices.get(i, 1)} for i in ids], "not_before": nb + "T00:00"}), encoding="utf-8")


def gone(prices, *ids):
    return {k: v for k, v in prices.items() if k not in ids}


# мок рендера: той самий формат, що в reel_batch.process, але без ffmpeg; ціни — з BIG (site_prices підмінено)
real_process, real_prices = reel_batch.process, reel_batch.site_prices
calls = {"n": 0, "specs": []}
fail_mode = {"on": False, "bad_caption": False}


def fake_process(spec_path, out_dir, feed_path, prices, render=True):
    calls["n"] += 1
    if fail_mode["on"]:
        sys.exit("[reel_batch] СТОП: тест — рендер не вдався")
    spec = json.load(open(spec_path, encoding="utf-8"))
    calls["specs"].append(spec)
    name = spec["queue_name"]
    Path(out_dir, name + ".mp4").write_bytes(b"video-" + name.encode())
    cap = "Без цін у підписі" if fail_mode["bad_caption"] else reel_batch.build_caption(spec, prices)
    Path(out_dir, name + ".caption.txt").write_text(cap + "\n", encoding="utf-8")
    Path(out_dir, name + ".json").write_text(json.dumps(reel_batch.build_manifest(spec, prices), ensure_ascii=False), encoding="utf-8")
    return name


reel_batch.process = fake_process
reel_batch.site_prices = lambda: dict(CUR["prices"])
CUR = {"prices": BIG}


def bkw(vanished=()):
    """Дані для run_build: індекс сайту без зниклих товарів (як у житті: їх нема в наявності), фід, без «попиту»."""
    idx = [dict(x, s=0) if x["id"] in vanished else x for x in ITEMS] + FILLER
    return {"index": idx, "feed": FEED, "demand": set(), "now_forbidden": lambda it: False}


def run_guard(q, s, now=NOW, prices=None, enabled=False, dry=False, vanished=(), alerts=None):
    al = alerts if alerts is not None else []
    return rg.run(str(q), str(s), now=now, dry=dry, enabled=enabled, prices=prices if prices is not None else CUR["prices"], alert=lambda k, t: al.append((k, t)), build_kw=bkw(vanished))


def snapshot_dir(d):
    out = {}
    for dp, _dn, fns in os.walk(d):
        for fn in fns:
            p = os.path.join(dp, fn)
            out[os.path.relpath(p, d)] = (os.path.getsize(p), hashlib.sha1(open(p, "rb").read()).hexdigest())
    return out


def queue_names(q):
    return sorted(n for n, _ in rw._queue_manifests(str(q)))


# ── 1. спостереження (брама вимкнена): видимість є, змін у черзі нема ──────────────────────────────────────────────────────────────
q, s = new_env("obs")
vanished_ids = ids_of(0, 0, 3)[:1]                      # 500 зник з наявності
P1 = gone(BIG, *vanished_ids)
mk_built(q, s, "puzzles_01", "puzzles", ids_of(0, 0, 3), "2026-11-24")                 # збирач, зник товар 500
mk_built(q, s, "dough_01", "dough", ids_of(1, 0, 3), "2026-11-27")                      # збирач, усе гаразд
mk_manual(q, "squish_02", ids_of(2, 0, 3), "2026-11-17", prices=P1 | {"700": 100})        # ручний, товар 700 є; ціна збігається
mk_manual(q, "manual_gone", ["500", "501"], "2026-11-17", prices=BIG)                    # ручний: у підписі ціни BIG, а 500 зник
before = snapshot_dir(q)
before_s = snapshot_dir(s)
al = []
calls["n"] = 0
CUR["prices"] = P1
r = run_guard(q, s, enabled=False, alerts=al, vanished=vanished_ids)
chk("спостереження: режим observe, слот 24.11", r["mode"] == "observe" and r["slot"] == "2026-11-24")
chk("спостереження: утримано puzzles_01 (збирач) і manual_gone (ручний); dough_01 і squish_02 придатні", sorted(r["held"]) == ["manual_gone", "puzzles_01"] and sorted(r["ok"]) == ["dough_01", "squish_02"])
chk("спостереження: причина — «товару 500 немає в каталозі»", any("500" in p and "немає в каталозі" in p for p in r["held"]["puzzles_01"]["problems"]))
chk("спостереження: збирач replaceable, ручний — ні (manual=True)", r["held"]["puzzles_01"]["replaceable"] is True and r["held"]["puzzles_01"]["manual"] is False
    and r["held"]["manual_gone"]["manual"] is True and r["held"]["manual_gone"]["replaceable"] is False)
chk("спостереження: ЧЕРГА не змінилась ні на байт, рендер не викликався, нічого не виселено", snapshot_dir(q) == before and calls["n"] == 0 and not (s / "_evicted").exists())
st_ = json.load(open(s / "_guard_state.json", encoding="utf-8"))
chk("спостереження: стан для vps_health записано в staging/_guard_state.json (не в черзі), held із first_seen", set(st_["held"]) == {"manual_gone", "puzzles_01"} and st_["held"]["puzzles_01"]["first_seen"] and not (q / "_guard_state.json").exists())
chk("спостереження: решта staging не змінилась (лише додано _guard_state.json)", {k: v for k, v in snapshot_dir(s).items() if k != "_guard_state.json"} == before_s)
logrows = [json.loads(x) for x in open(rg.LOG_FILE, encoding="utf-8")]
chk("журнал reel_guard_log.jsonl: run + held(2)", [x["event"] for x in logrows].count("run") == 1 and [x["event"] for x in logrows].count("held") == 2)
chk("сповіщення: ключі reel_guard_held:<ім'я> для обох утриманих", sorted(k for k, _ in al) == ["reel_guard_held:manual_gone", "reel_guard_held:puzzles_01"])
chk("сповіщення: префікс «⚠️ reel_guard:» і категорія warning у telegram_digest._classify (потрапляє в алерти знімка VPS)", all(t.startswith("⚠️ reel_guard:") and td._classify(t) == "warning" for _, t in al))
chk("ручний рілс: підказка «SMM», збирач у спостереженні: «після ввімкнення REEL_GUARD=1»", any("SMM" in t for k, t in al if k.endswith("manual_gone")) and any("REEL_GUARD=1" in t for k, t in al if k.endswith("puzzles_01")))

# ── 2. --dry: нічого не пише ───────────────────────────────────────────────────────────────────────────────────────────────────────
q, s = new_env("dry")
mk_built(q, s, "puzzles_01", "puzzles", ids_of(0, 0, 3), "2026-11-24")
b_q, b_s = snapshot_dir(q), snapshot_dir(s)
logsize = os.path.getsize(rg.LOG_FILE)
al = []
r = run_guard(q, s, enabled=True, dry=True, alerts=al, vanished=vanished_ids)
chk("dry: режим dry, утримано puzzles_01, але черга/staging/журнал/сповіщення — без змін", r["mode"] == "dry" and "puzzles_01" in r["held"] and snapshot_dir(q) == b_q and snapshot_dir(s) == b_s
    and os.path.getsize(rg.LOG_FILE) == logsize and al == [] and calls["n"] == 0 and not (s / "_guard.lock").exists())

# ── 3. АКТИВНИЙ режим: зник товар → заміна на той самий слот і тему ──────────────────────────────────────────────────────────────────
q, s = new_env("act")
old_nb = "2026-11-24"
mk_built(q, s, "puzzles_01", "puzzles", ids_of(0, 0, 3), old_nb)                      # зникне 500
mk_built(q, s, "dough_01", "dough", ids_of(1, 0, 3), "2026-11-27")
mk_manual(q, "squish_02", ids_of(2, 0, 3), "2026-11-17")
old_mtime = 1_700_000_000
os.utime(q / "puzzles_01.json", (old_mtime, old_mtime))
al = []
calls["n"], calls["specs"] = 0, []
CUR["prices"] = P1
r = run_guard(q, s, enabled=True, alerts=al, vanished=vanished_ids)
new_name = r["replaced"][0][1] if r["replaced"] else None
chk("заміна: puzzles_01 → нова (та сама тема puzzles), старий виселено", r["replaced"] and r["replaced"][0][0] == "puzzles_01" and new_name.startswith("puzzles_") and new_name != "puzzles_01")
chk("заміна: старий у staging/_evicted/ (json+caption+mp4), у черзі його нема; нова в черзі (mp4+caption+json), без *.incoming",
    all((s / "_evicted" / f"puzzles_01{e}").is_file() for e in (".json", ".caption.txt", ".mp4")) and not (q / "puzzles_01.json").exists()
    and all((q / f"{new_name}{e}").is_file() for e in (".json", ".caption.txt", ".mp4")) and not list(q.glob("*.incoming")))
nm = json.load(open(q / f"{new_name}.json", encoding="utf-8"))
chk("заміна: той самий слот — not_before = 2026-11-24T00:00; 3 товари теми puzzles, без зниклого 500 і без id старого рілса",
    nm["not_before"] == "2026-11-24T00:00" and len(nm["verify"]) == 3 and "500" not in {v["id"] for v in nm["verify"]} and not ({v["id"] for v in nm["verify"]} & set(ids_of(0, 0, 3)))
    and all(int(v["id"]) // 100 == 5 for v in nm["verify"]))
chk("заміна: збирач викликано РІВНО один раз (одна збірка), сцена з бібліотеки, ендсцена з PNG, не та сама", calls["n"] == 1 and calls["specs"][0]["first_scene"] in rw.library_rotation()
    and calls["specs"][0]["endscene"] in ENDS and calls["specs"][0]["endscene"] != calls["specs"][0]["first_scene"])
chk("заміна: успадкувала mtime старого рілса (місце в черзі publisher'а)", int(os.path.getmtime(q / f"{new_name}.json")) == old_mtime)
import reel_autopublish as ra
idx = {k: {"price": float(v), "name": ""} for k, v in P1.items()}
chk("заміна проходить ті самі перевірки publisher'а (check_queue_item → порожньо) у черзі", rw.check_queue_item(str(q), new_name, str(q / f"{new_name}.json"), nm, idx) == [])
chk("ручний і придатний рілси недоторкані; черга: dough_01, нова, squish_02", queue_names(q) == sorted(["dough_01", new_name, "squish_02"]))
chk("після заміни held порожній, слот закрито; сповіщення про заміну з префіксом і категорією warning", r["held"] == {} and r["slot_covered"] is True
    and any(k == "reel_guard_done:puzzles_01" for k, _ in al) and all(td._classify(t) == "warning" for _, t in al))
chk("слот-сповіщення «нема придатного рілса» НЕ надіслано (заміна його закрила)", not any(k.startswith("reel_guard_slot") for k, _ in al))
wst = json.load(open(s / "_weekly_state.json", encoding="utf-8"))
chk("used_ids: ids нового рілса записано; id старого й зниклого лишаються «використаними» (повторно не беруться)", set(v["id"] for v in nm["verify"]) <= set(wst["used_ids"]))
gst = json.load(open(s / "_guard_state.json", encoding="utf-8"))
chk("стан сторожа: спроба {old, new, slot, ok} записана; журнал: replaced", gst["attempts"][-1]["old"] == "puzzles_01" and gst["attempts"][-1]["new"] == new_name and gst["attempts"][-1]["ok"] is True
    and gst["attempts"][-1]["slot"] == "2026-11-24" and any(json.loads(x)["event"] == "replaced" for x in open(rg.LOG_FILE, encoding="utf-8")))

# ── 4. дубль-запуск не створює другої заміни; used_ids не зациклюється ───────────────────────────────────────────────────────────────
calls["n"] = 0
al = []
r2 = run_guard(q, s, now=NOW + timedelta(minutes=5), enabled=True, alerts=al, vanished=vanished_ids)
chk("дубль-запуск: нічого не утримано, нічого не збирається, нічого не виселяється", r2["replaced"] == [] and r2["held"] == {} and calls["n"] == 0 and queue_names(q) == sorted(["dough_01", new_name, "squish_02"]))
used_before = set(wst["used_ids"])
r3 = run_guard(q, s, now=NOW + timedelta(days=1), enabled=True, alerts=al, vanished=vanished_ids)
wst3 = json.load(open(s / "_weekly_state.json", encoding="utf-8"))
chk("наступної доби без нових проблем used_ids не росте (збірка не зациклюється)", set(wst3["used_ids"]) == used_before and calls["n"] == 0)

# ── 5. перерване виселення: нова вже в черзі, старий лишився → лише виселення, друга збірка не робиться ─────────────────────────────────
q, s = new_env("crash")
mk_built(q, s, "puzzles_01", "puzzles", ids_of(0, 0, 3), old_nb)
calls["n"] = 0
real_evict = rw.evict_to_staging


def boom_evict(*a, **k):
    raise OSError(5, "диск")


rw.evict_to_staging = boom_evict
try:
    try:
        run_guard(q, s, enabled=True, vanished=vanished_ids, prices=P1)
        crashed = False
    except OSError:
        crashed = True
finally:
    rw.evict_to_staging = real_evict
chk("збій виселення після запису заміни: виняток піднято, стан спроби вже збережено (ok=True, new=...)", crashed and json.load(open(s / "_guard_state.json", encoding="utf-8"))["attempts"][-1]["ok"] is True
    and not (s / "_guard.lock").exists())
n_after_crash = calls["n"]
r = run_guard(q, s, now=NOW + timedelta(hours=1), enabled=True, vanished=vanished_ids, prices=P1)
chk("повторний запуск: другої збірки НЕМАЄ (calls те саме), старий виселено, у черзі рівно одна нова", calls["n"] == n_after_crash == 1 and r["evict_finished"] == ["puzzles_01"] and not (q / "puzzles_01.json").exists()
    and len(queue_names(q)) == 1 and queue_names(q)[0].startswith("puzzles_") and (s / "_evicted" / "puzzles_01.json").is_file())

# ── 6. refresh_prices-лікована розбіжність і ціна, що лікується зі спеки → заміни нема ────────────────────────────────────────────────
q, s = new_env("heal")
mk_built(q, s, "dough_01", "dough", ids_of(1, 0, 3), "2026-11-24")
P_drift = dict(BIG)
for i in ids_of(1, 0, 3):
    P_drift[i] = BIG[i] + 40                               # ціни на сайті змінились: publisher виправить сам (refresh_prices)
b_q = snapshot_dir(q)
calls["n"] = 0
r = run_guard(q, s, enabled=True, prices=P_drift)
chk("ціна змінилась, refresh_prices лікує → рілс придатний, held порожній, заміни/збірки/змін у черзі нема", r["held"] == {} and r["replaced"] == [] and calls["n"] == 0 and snapshot_dir(q) == b_q)
q, s = new_env("heal2")
a_, b_id, c_ = ids_of(1, 0, 3)
P_build = dict(BIG)
P_build[a_] = P_build[b_id] = 246                           # при збірці дві різні ціни збігались (246/246)...
mk_built(q, s, "dough_01", "dough", [a_, b_id, c_], "2026-11-24", prices=P_build)
P_site = dict(BIG)
P_site[a_], P_site[b_id] = 250, 260                         # ...а тепер сайт розвів їх: refresh_prices неоднозначну заміну НЕ вгадує (як dough_03)
idx_amb = {k: {"price": float(v), "name": ""} for k, v in P_site.items()}
chk("передумова: publisher бачить неоднозначну ціну як проблему (refresh_prices не лікує)", rw.check_queue_item(str(q), "dough_01", str(q / "dough_01.json"), json.load(open(q / "dough_01.json", encoding="utf-8")), idx_amb) != [])
b_q = snapshot_dir(q)
calls["n"] = 0
al = []
r = run_guard(q, s, enabled=False, prices=P_site, alerts=al)
chk("спостереження: неоднозначна ціна збирача — утримано, але НЕ replaceable (лікується зі спеки), чергу не чіпає", "dough_01" in r["held"] and r["held"]["dough_01"]["replaceable"] is False and snapshot_dir(q) == b_q)
r = run_guard(q, s, enabled=True, prices=P_site, alerts=al)
mf = json.load(open(q / "dough_01.json", encoding="utf-8"))
cap = (q / "dough_01.caption.txt").read_text(encoding="utf-8")
chk("активний: ціни переписано зі спеки БЕЗ рендера й без заміни (verify 250/260, підпис оновлено, not_before збережено), рілс придатний",
    r["healed"] == ["dough_01"] and r["replaced"] == [] and calls["n"] == 0 and {v["id"]: v["price"] for v in mf["verify"]}[a_] == 250 and {v["id"]: v["price"] for v in mf["verify"]}[b_id] == 260
    and "250 грн" in cap and "260 грн" in cap and mf["not_before"] == "2026-11-24T00:00" and r["held"] == {} and queue_names(q) == ["dough_01"])

# ── 7. ручні/службові рілси, опубліковані, поза горизонтом, .publishing — недоторкані при ввімкненій брамі ────────────────────────────
q, s = new_env("untouch")
mk_manual(q, "manual_gone", ["500", "501"], "2026-11-17")                                 # ручний, 500 зник
mk_built(q, s, "puzzles_01", "puzzles", ids_of(0, 0, 3), "2026-12-10")                    # збирач, 500 зник, але not_before поза горизонтом (>27.11)
mk_built(q, s, "dough_01", "dough", ids_of(1, 0, 3), "2026-11-24", publishing=True)       # є .publishing: стан невідомий — людина
mk_built(q, s, "squish_01", "squish", ids_of(2, 0, 3), "2026-11-24")                      # у published.json: publisher його пропускає
(q / "published.json").write_text(json.dumps([{"name": "squish_01", "media_id": "1", "at": "2026-11-20T18:31:00"}]), encoding="utf-8")
# збирач із ПІДРОБЛЕНОЮ спекою: спека є, але id не збігаються з verify → вважаємо ручним
mk_built(q, s, "bubbles_01", "bubbles", ids_of(3, 0, 3), "2026-11-24")
sp = json.load(open(s / "_specs" / "bubbles_01.json", encoding="utf-8"))
sp["items"][0]["id"] = "999999"
(s / "_specs" / "bubbles_01.json").write_text(json.dumps(sp, ensure_ascii=False), encoding="utf-8")
P_x = gone(BIG, "500", "800")
b_q = snapshot_dir(q)
calls["n"] = 0
al = []
r = run_guard(q, s, enabled=True, prices=P_x, alerts=al, vanished=("500", "800"))
chk("брама ON: ручний, .publishing, спека з чужими id — утримано, але НЕ replaceable; far/published не перевіряються; збірки нема, черга без змін",
    sorted(r["held"]) == ["bubbles_01", "dough_01", "manual_gone"] and not any(v["replaceable"] for v in r["held"].values()) and calls["n"] == 0 and snapshot_dir(q) == b_q and r["replaced"] == []
    and "puzzles_01" not in r["held"] and "squish_01" not in r["held"] and "squish_01" not in r["ok"])
chk(".publishing: причина — «результат публікації невідомий»; усі сповіщення — warning", any("publishing" in p for p in r["held"]["dough_01"]["problems"]) and all(td._classify(t) == "warning" for _, t in al))

# ── 8. ліміти ───────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
chk("значення лімітів за замовчуванням: ≤2/добу всього, ≤1/добу на слот, ≤3 невдалі, горизонт 3 доби", rg.MAX_PER_DAY == 2 and rg.MAX_PER_SLOT_DAY == 1 and rg.MAX_FAILED_PER_REEL == 3 and rg.HORIZON_DAYS == 3)
q, s = new_env("lim")
mk_built(q, s, "squish_01", "squish", ids_of(2, 0, 3), "2026-11-17")                       # минулий not_before → слот заміни 24.11
mk_built(q, s, "puzzles_01", "puzzles", ids_of(0, 0, 3), "2026-11-24")                     # той самий слот 24.11
for k, (th, ix) in enumerate((("dough", 1), ("bubbles", 3), ("games", 4))):
    mk_built(q, s, f"{th}_01", th, ids_of(ix, 0, 3), "2026-11-27")
P_l = gone(BIG, "500", "700")
al = []
calls["n"] = 0
r = run_guard(q, s, enabled=True, prices=P_l, alerts=al, vanished=("500", "700"))
chk("ліміт на слот: два утримані на ОДИН слот → за добу замінено лише один (старіший за not_before), другому сповіщення reel_guard_limit", len(r["replaced"]) == 1 and r["replaced"][0][0] == "squish_01"
    and any(k == "reel_guard_limit:puzzles_01" for k, _ in al) and "puzzles_01" in r["held"] and (q / "puzzles_01.json").exists() and calls["n"] == 1)
r = run_guard(q, s, now=NOW + timedelta(hours=3), enabled=True, prices=P_l, alerts=al, vanished=("500", "700"))
chk("через 3 год — ще ліміт (доба не минула), збірок більше нема", r["replaced"] == [] and calls["n"] == 1)
r = run_guard(q, s, now=NOW + timedelta(hours=25), enabled=True, prices=P_l, alerts=al, vanished=("500", "700"))
chk("через 25 год — друга заміна дозволена", len(r["replaced"]) == 1 and r["replaced"][0][0] == "puzzles_01" and calls["n"] == 2)
used_ids_all = [v["id"] for n in queue_names(q) for v in json.load(open(q / f"{n}.json", encoding="utf-8"))["verify"]]
chk("used_ids: у черзі жоден товар не повторюється між рілсами (заміни не беруть уже використані)", len(used_ids_all) == len(set(used_ids_all)))
# ліміт на добу ВСЬОГО (патч константи: у межах горизонту лише два слоти)
q, s = new_env("lim2")
mk_built(q, s, "puzzles_01", "puzzles", ids_of(0, 0, 3), "2026-11-24")
mk_built(q, s, "squish_01", "squish", ids_of(2, 0, 3), "2026-11-27")
for th, ix in (("dough", 1), ("bubbles", 3), ("games", 4)):
    mk_built(q, s, f"{th}_01", th, ids_of(ix, 0, 3), "2026-11-27")
real_cap = rg.MAX_PER_DAY
rg.MAX_PER_DAY = 1
try:
    al = []
    r = run_guard(q, s, enabled=True, prices=P_l, alerts=al, vanished=("500", "700"))
finally:
    rg.MAX_PER_DAY = real_cap
chk("ліміт на добу всього (=1 у цьому тесті): два утримані на РІЗНІ слоти → замінено один, другому reel_guard_limit", len(r["replaced"]) == 1 and any(k.startswith("reel_guard_limit:") for k, _ in al))

# ── 9. збій збірки: старий лишається, нічого зайвого; повтори; здаємося після 3 невдалих ────────────────────────────────────────────────
q, s = new_env("fail")
mk_built(q, s, "puzzles_01", "puzzles", ids_of(0, 0, 3), "2026-11-24")
for th, ix in (("dough", 1), ("bubbles", 3), ("games", 4)):
    mk_built(q, s, f"{th}_01", th, ids_of(ix, 0, 3), "2026-11-27")
b_q = snapshot_dir(q)
fail_mode["on"] = True
calls["n"] = 0
al = []
try:
    r = run_guard(q, s, enabled=True, prices=P1, alerts=al, vanished=vanished_ids)
finally:
    fail_mode["on"] = False
chk("збій рендера: старий рілс ЛИШИВСЯ в черзі, черга побайтово та сама, у staging нема недоплетених mp4/caption/json і спеки невдалої заміни",
    r["replaced"] == [] and r["failed"] == ["puzzles_01"] and snapshot_dir(q) == b_q and not (s / "_evicted").exists()
    and sorted(os.listdir(s / "_specs")) == sorted(f"{n}.json" for n in ["puzzles_01", "dough_01", "bubbles_01", "games_01"]) and not [f for f in os.listdir(s) if f.endswith((".mp4", ".caption.txt")) or (f.endswith(".json") and not f.startswith("_"))])
chk("збій: сповіщення reel_guard_fail з причиною (warning), спроба ok=False, старий усе ще held", any(k == "reel_guard_fail:puzzles_01" and "СТОП" in t and td._classify(t) == "warning" for k, t in al)
    and json.load(open(s / "_guard_state.json", encoding="utf-8"))["attempts"][-1]["ok"] is False and "puzzles_01" in r["held"])
calls["n"] = 0
r = run_guard(q, s, now=NOW + timedelta(hours=2), enabled=True, prices=P1, alerts=al, vanished=vanished_ids)
chk("повтор за 2 год — ліміт доби: збірки нема", calls["n"] == 0 and r["failed"] == [])
fail_mode["on"] = True
try:
    for h in (25, 50):
        run_guard(q, s, now=NOW + timedelta(hours=h), enabled=True, prices=P1, alerts=al, vanished=vanished_ids)
    attempts_failed = sum(1 for a in json.load(open(s / "_guard_state.json", encoding="utf-8"))["attempts"] if not a["ok"])
    n3 = calls["n"]
    al.clear()
    run_guard(q, s, now=NOW + timedelta(hours=75), enabled=True, prices=P1, alerts=al, vanished=vanished_ids)
finally:
    fail_mode["on"] = False
chk("3 невдалі спроби (1-ша + через 25 і 50 год) → на 4-й добі НЕ пробуємо (сповіщення reel_guard_giveup, збірок більше нема; зациклення нема)",
    attempts_failed == 3 and calls["n"] == n3 and any(k == "reel_guard_giveup:puzzles_01" for k, _ in al) and (q / "puzzles_01.json").exists())
# заміна не пройшла перевірку publisher'а (підпис без ціни) → у чергу не потрапила
q, s = new_env("badcap")
mk_built(q, s, "puzzles_01", "puzzles", ids_of(0, 0, 3), "2026-11-24")
for th, ix in (("dough", 1), ("bubbles", 3), ("games", 4)):
    mk_built(q, s, f"{th}_01", th, ids_of(ix, 0, 3), "2026-11-27")
b_q = snapshot_dir(q)
fail_mode["bad_caption"] = True
al = []
try:
    r = run_guard(q, s, enabled=True, prices=P1, alerts=al, vanished=vanished_ids)
finally:
    fail_mode["bad_caption"] = False
chk("зібрана заміна не пройшла перевірку publisher'а (у підписі нема ціни) → у чергу НЕ записана, старий лишився, сповіщення з причиною",
    r["replaced"] == [] and snapshot_dir(q) == b_q and any(k == "reel_guard_fail:puzzles_01" and "перевірку publisher" in t for k, t in al))

# ── 10. масове утримання, замок, ціни неповні, бібліотека ──────────────────────────────────────────────────────────────────────────────
q, s = new_env("mass")
for th, ix in (("puzzles", 0), ("dough", 1), ("squish", 2), ("bubbles", 3)):
    mk_built(q, s, f"{th}_01", th, ids_of(ix, 0, 3), "2026-11-24")
P_m = gone(BIG, "500", "600", "700")
b_q = snapshot_dir(q)
calls["n"] = 0
al = []
r = run_guard(q, s, enabled=True, prices=P_m, alerts=al, vanished=("500", "600", "700"))
chk("масове утримання (3 з 4 рілсів збирача) → заміни зупинено (схоже на проблему даних), сповіщення reel_guard_mass, чергу не чіпає",
    r["replaced"] == [] and calls["n"] == 0 and snapshot_dir(q) == b_q and any(k == "reel_guard_mass" for k, _ in al))
q, s = new_env("lock")
mk_built(q, s, "puzzles_01", "puzzles", ids_of(0, 0, 3), "2026-11-24")
(s / "_guard.lock").write_text("x")
r = run_guard(q, s, enabled=True, prices=P1, vanished=vanished_ids)
chk("замок _guard.lock свіжий → «інший запуск ще триває», нічого не робить", r["skipped"] and "триває" in r["skipped"] and (q / "puzzles_01.json").exists() and (s / "_guard.lock").exists())
os.utime(s / "_guard.lock", (1, 1))
r = run_guard(q, s, enabled=False, prices=P1, vanished=vanished_ids)
chk("застарілий замок (>3 год) знімається, запуск іде; після запуску замка нема", r["skipped"] is None and not (s / "_guard.lock").exists())
q, s = new_env("smallprices")
mk_built(q, s, "puzzles_01", "puzzles", ids_of(0, 0, 3), "2026-11-24")
al = []
r = run_guard(q, s, enabled=True, prices={k: v for k, v in list(BIG.items())[:50]}, alerts=al)
chk("ціни сайту неповні (<1000) → перевірку пропущено, сповіщення reel_guard_prices, нічого не змінено", r["skipped"] and (q / "puzzles_01.json").exists() and [k for k, _ in al] == ["reel_guard_prices"] and td._classify(al[0][1]) == "warning")
q, s = new_env("nolib")
mk_built(q, s, "puzzles_01", "puzzles", ids_of(0, 0, 3), "2026-11-24")
for th, ix in (("dough", 1), ("bubbles", 3), ("games", 4)):
    mk_built(q, s, f"{th}_01", th, ids_of(ix, 0, 3), "2026-11-27")
os.environ["PLUTUS_SCENE_LIBRARY"] = str(tmp / "no_such_lib")
b_q = snapshot_dir(q)
al = []
calls["n"] = 0
try:
    r = run_guard(q, s, enabled=True, prices=P1, alerts=al, vanished=vanished_ids)
finally:
    os.environ["PLUTUS_SCENE_LIBRARY"] = str(lib)
chk("бібліотеки схвалених сцен нема → заміну НЕ збираємо (fail-closed), сповіщення reel_guard_lib, чергу не чіпає", r["replaced"] == [] and calls["n"] == 0 and snapshot_dir(q) == b_q and any(k == "reel_guard_lib" for k, _ in al))

# ── 11. слот без придатного рілса → сповіщення; порожня/відсутня черга ─────────────────────────────────────────────────────────────────
q, s = new_env("empty")
al = []
r = run_guard(q, s, enabled=False, prices=BIG, alerts=al)
chk("порожня черга: слот не закрито → сповіщення reel_guard_slot:2026-11-24 (warning)", r["slot_covered"] is False and any(k == "reel_guard_slot:2026-11-24" and td._classify(t) == "warning" for k, t in al))
chk("немає теки черги → skipped, без падіння", rg.run(str(tmp / "nope_q"), str(tmp / "nope_s"), now=NOW, enabled=False, prices=BIG, alert=lambda k, t: None)["skipped"])
chk("next_slot_date: пн 12:00 → вт 24.11; вт 18:29 → вт; вт 18:30 → пт 27.11; пт 19:00 → вт 01.12",
    rg.next_slot_date(datetime(2026, 11, 23, 12, 0)) == date(2026, 11, 24) and rg.next_slot_date(datetime(2026, 11, 24, 18, 29)) == date(2026, 11, 24)
    and rg.next_slot_date(datetime(2026, 11, 24, 18, 30)) == date(2026, 11, 27) and rg.next_slot_date(datetime(2026, 11, 27, 19, 0)) == date(2026, 12, 1))
chk("replacement_slot: not_before у минулому → найближчий слот; 25.11 (серед) → пт 27.11; сьогодні вт після 18:30 → пт",
    rg.replacement_slot(date(2026, 11, 17), NOW) == date(2026, 11, 24) and rg.replacement_slot(date(2026, 11, 25), NOW) == date(2026, 11, 27)
    and rg.replacement_slot(date(2026, 11, 24), datetime(2026, 11, 24, 19, 0)) == date(2026, 11, 27))

# ── 12. reel_weekly поводиться як раніше: publishable_count через спільний check_queue_item; run_build без нових параметрів ────────────────
q, s = new_env("regr")
mk_built(q, s, "dough_01", "dough", ids_of(1, 0, 3), "2026-11-24")
mk_built(q, s, "puzzles_01", "puzzles", ids_of(0, 0, 3), "2026-11-24")
ok_n, held_n = rw.publishable_count(str(q), P1)
chk("reel_weekly.publishable_count (рефакторинг через check_queue_item): 1 придатний, 1 притриманий", ok_n == 1 and held_n == ["puzzles_01"])
out_dir = tmp / "regr_out"
b_plain = rw.run_build(1, str(out_dir), today=date(2026, 11, 23), index=ITEMS + FILLER, feed=FEED, demand=set(), now_forbidden=lambda it: False, specs_dir=EMPTY_SPECS,
                       state_dir=str(out_dir), on_built=lambda spec: None, failures=[])
chk("run_build без slots/prefer_theme: слот = наступний вт/пт ПІСЛЯ today (24.11), тема за звичайним pick_theme (перша: puzzles)", len(b_plain) == 1 and b_plain[0]["publish_date"] == "2026-11-24" and b_plain[0]["queue_name"].startswith("puzzles_"))
b_slots = rw.run_build(1, str(tmp / "regr_out2"), today=date(2026, 11, 23), index=ITEMS + FILLER, feed=FEED, demand=set(), now_forbidden=lambda it: False, specs_dir=EMPTY_SPECS,
                       state_dir=str(tmp / "regr_out2"), on_built=lambda spec: None, failures=[], slots=[date(2026, 11, 27)], prefer_theme="squish")
chk("run_build(slots=[27.11], prefer_theme=squish): слот 27.11, тема squish (а не перша за списком), журнал тем не дублюється", b_slots[0]["publish_date"] == "2026-11-27" and b_slots[0]["queue_name"].startswith("squish_")
    and json.load(open(tmp / "regr_out2" / "_weekly_state.json", encoding="utf-8"))["themes"] == [])
# prefer_theme без достатку товарів → звичайний вибір теми
few = [x for x in ITEMS if not x["id"].startswith("7")] + [x for x in ITEMS if x["id"] in ("700", "701")] + FILLER
b_fb = rw.run_build(1, str(tmp / "regr_out3"), today=date(2026, 11, 23), index=few, feed=FEED, demand=set(), now_forbidden=lambda it: False, specs_dir=EMPTY_SPECS,
                    state_dir=str(tmp / "regr_out3"), on_built=lambda spec: None, failures=[], slots=[date(2026, 11, 24)], prefer_theme="squish")
chk("prefer_theme: у темі лише 2 придатні → фолбек на звичайний pick_theme (слот не пропадає)", len(b_fb) == 1 and not b_fb[0]["queue_name"].startswith("squish_"))

# ── 13. видимість: vps_health.collect_reels + health_check.check_reels ──────────────────────────────────────────────────────────────────────
app = tmp / "vpsapp"
(app / "reels_queue").mkdir(parents=True)
(app / "reels_pending").mkdir()
rq = app / "reels_queue"
for n, nb in (("a_01", "2026-11-24T00:00"), ("b_01", "2026-11-27T00:00")):
    (rq / f"{n}.json").write_text(json.dumps({"verify": [], "not_before": nb}), encoding="utf-8")
    (rq / f"{n}.mp4").write_bytes(b"v")
    (rq / f"{n}.caption.txt").write_text("c", encoding="utf-8")
(rq / "c_01.json").write_text("{не json", encoding="utf-8")
(rq / "_junk.json").write_text("{}", encoding="utf-8")
(rq / "published.json").write_text(json.dumps([{"name": "z", "at": "2026-11-20T18:31:00"}, {"name": "y", "at": "2026-11-17T18:31:00"}]), encoding="utf-8")
(app / "reels_pending" / "_guard_state.json").write_text(json.dumps({"at": "2026-11-23T12:00:05", "mode": "observe", "slot": "2026-11-24", "checked": 2, "ok": ["b_01"], "attempts": [{"x": 1}],
                                                                       "held": {"a_01": {"first_seen": "2026-11-20T12:00:00", "problems": ["товару 22118 немає в каталозі сайту"], "manual": False, "replaceable": True}}, "slot_covered": False}), encoding="utf-8")
os.environ.pop("REEL_QUEUE_DIR", None)
os.environ.pop("REEL_STAGING_DIR", None)
cr = vh.collect_reels(str(app))
chk("collect_reels: черга без _junk і published.json (a_01, b_01, c_01), нечитабельний c_01 позначено, останній published = 2026-11-20", [q_["name"] for q_ in cr["queue"]] == ["a_01", "b_01", "c_01"]
    and cr["queue"][2]["readable"] is False and cr["queue"][0]["has_mp4"] and cr["queue"][0]["not_before"] == "2026-11-24T00:00" and cr["last_published_at"] == "2026-11-20T18:31:00")
chk("collect_reels: стан сторожа (без attempts — лише потрібні поля); нема теки черги → queue_present False; у collect() ключ reels є", cr["guard"]["held"]["a_01"]["problems"] and "attempts" not in cr["guard"]
    and vh.collect_reels(str(tmp / "no_app"))["queue_present"] is False and "reels" in vh.collect(app_dir=str(app), run=lambda *a, **k: ""))

H_NOW = datetime(2026, 11, 23, 12, 0, tzinfo=timezone.utc)


def hiso(h_ago):
    return (H_NOW - timedelta(hours=h_ago)).isoformat()


def hq(name, nb="2026-11-24T00:00", **kw):
    d = {"name": name, "not_before": nb, "readable": True, "name_ok": True, "has_mp4": True, "has_caption": True, "publishing": False}
    d.update(kw)
    return d


def hsnap(queue, guard=None, alerts=None, timers=None, present=True):
    return {"generated_at": hiso(0.5), "units": {"services": {"ok.service": {"active": "inactive", "result": "success"}}, "timers": timers or {}, "failed": []}, "host_stats": {"disk_used_pct": 40.0},
            "state_files": {}, "alerts": alerts or [], "reels": {"queue_present": present, "queue": queue, "last_published_at": "2026-11-20T18:31:00", "guard": guard}}


def hctx(snapshot, now=None):
    return hc.Ctx(now=now or H_NOW, vps_snapshot=lambda: (snapshot, None), master=lambda: ("", ""), feed_meta=lambda: {}, eva_date=lambda: "", tasks=lambda: [], local_alerts=lambda: [], critical=lambda: {})


iss = hc.check_vps(hctx(hsnap([])))
chk("health_check: порожня черга за ≤48 год до слота (пн 12:00 → вт 18:30) → 🟠 vps:reel_slot:2026-11-24", any(i["id"] == "vps:reel_slot:2026-11-24" and i["sev"] == "warn" and not i["owner"] for i in iss))
chk("health_check: рядок звіту (brief) показує 🟠 «нема придатного рілса» (раніше це ℹ️ і не друкувалось)", "нема придатного рілса" in hc.brief(iss, H_NOW))
iss = hc.check_vps(hctx(hsnap([hq("a_01")])))
chk("health_check: є придатний рілс (файли на місці, not_before ≤ слота) → без reel_slot", not any(i["id"].startswith("vps:reel_slot") for i in iss))
iss = hc.check_vps(hctx(hsnap([hq("a_01", nb="2026-11-30T00:00")])))
chk("health_check: єдиний рілс із not_before ПІСЛЯ слота — не кандидат → 🟠", any(i["id"].startswith("vps:reel_slot") for i in iss))
iss = hc.check_vps(hctx(hsnap([hq("a_01", has_mp4=False), hq("b_01", publishing=True), hq("c_01", readable=False), hq("d_01", name_ok=False)])))
chk("health_check: рілси без mp4 / з .publishing / нечитабельні / з поганим іменем — не кандидати → 🟠", any(i["id"].startswith("vps:reel_slot") for i in iss))
H_W = datetime(2026, 11, 25, 12, 0, tzinfo=timezone.utc)      # середа: до пт 18:30 — 54,5 год > 48
w_snap = hsnap([])
w_snap["generated_at"] = (H_W - timedelta(hours=1)).isoformat()
iss = hc.check_vps(hctx(w_snap, now=H_W))
chk("health_check: слот далі ніж за 48 год (ср 12:00 → пт 18:30) → ще не тривога", not any(i["id"].startswith("vps:reel_slot") for i in iss))
g_ok = {"at": hiso(1), "mode": "observe", "slot": "2026-11-24", "checked": 1, "ok": [], "slot_covered": False,
        "held": {"a_01": {"first_seen": hiso(24 * 11), "problems": ["товару 22118 немає в каталозі сайту (нема в наявності)"], "manual": False, "replaceable": True}}}
iss = hc.check_vps(hctx(hsnap([hq("a_01")], guard=g_ok)))
chk("health_check: рілс утримано сторожем → 🟠 vps:reel_held:a_01 («11 дн», причина) і слот без придатного (a_01 виключено)", any(i["id"] == "vps:reel_held:a_01" and "11 дн" in i["title"] and "22118" in i["title"] for i in iss)
    and any(i["id"].startswith("vps:reel_slot") for i in iss))
iss = hc.check_vps(hctx(hsnap([hq("a_01")], guard=dict(g_ok, at=hiso(60)))))
chk("health_check: стан сторожа застарів (60 год) — утримані з нього не показуємо як факт; без reel-guard.timer це не помилка сторожа", not any(i["id"].startswith("vps:reel_held") for i in iss)
    and not any(i["id"] == "vps:reel_guard_stale" for i in iss))
iss = hc.check_vps(hctx(hsnap([hq("a_01")], guard=dict(g_ok, at=hiso(60), held={}), timers={"reel-guard.timer": {"enabled": "enabled", "active": "active"}})))
chk("health_check: reel-guard.timer увімкнено, стан старший за 36 год → 🟠 vps:reel_guard_stale", any(i["id"] == "vps:reel_guard_stale" and i["sev"] == "warn" for i in iss))
al_vps = [{"at": "2026-11-23T03:10:00", "source": "reel_autopublish.py", "category": "warning", "muted_in_telegram": True,
           "text": "⚠️ Reel dough_02 НЕ опубліковано, поки: товару 22118 немає в каталозі сайту (нема в наявності). SMM: онови підпис/ціни й маніфест, власник: перезалий файли."}]
iss = hc.check_vps(hctx(hsnap([hq("a_01")], alerts=al_vps)))
chk("health_check: «⚠️ Reel … НЕ опубліковано» з журналу VPS піднято з ℹ️ до 🟠 (vps:reel_hold_alert:dough_02); група ℹ️ лишається", any(i["id"] == "vps:reel_hold_alert:dough_02" and i["sev"] == "warn" for i in iss)
    and any(i["id"].startswith("vps:alert:") and i["sev"] == "info" for i in iss))
chk("справжній текст алерту publisher'а має категорію warning (її бере vps_health.collect_alerts: error/warning/money)", td._classify(al_vps[0]["text"]) == "warning")
snap_old = hsnap([])
del snap_old["reels"]
chk("знімок без ключа reels (VPS на старому коді) → нічого про рілси, без падіння", not any("reel" in i["id"] for i in hc.check_vps(hctx(snap_old))))
chk("queue_present=False → мовчить (нема теки черги)", hc.check_reels(hsnap([], present=False), H_NOW) == [])
chk("next_reel_slot: пн 12:00 → вт 24.11 18:30; вт 19:00 → пт 27.11", hc.next_reel_slot(H_NOW).date() == date(2026, 11, 24) and hc.next_reel_slot(datetime(2026, 11, 24, 19, 0, tzinfo=timezone.utc)).date() == date(2026, 11, 27))

# ── 14. юніти й реєстр ──────────────────────────────────────────────────────────────────────────────────────────────────────────────────
root = Path(__file__).parent
svc = (root / "reel-guard.service").read_text(encoding="utf-8")
tim = (root / "reel-guard.timer").read_text(encoding="utf-8")
chk("reel-guard.service: venv-python (CLAUDE.md), reel_guard.py, Nice/MemoryMax як у reel-weekly; timer щодня 12:00", "/opt/plutustoys/venv/bin/python3 /opt/plutustoys/reel_guard.py" in svc and "MemoryMax=1800M" in svc and "Nice=15" in svc
    and "OnCalendar=*-*-* 12:00:00" in tim and "Persistent=true" in tim)
chk("SYSTEM_MAP: reel-guard у реєстрі vps_units", '"reel-guard"' in (root / "SYSTEM_MAP.md").read_text(encoding="utf-8"))
ps1 = (root / "deploy" / "reel_guard_switch.ps1").read_bytes()
chk("deploy/reel_guard_switch.ps1: лише ASCII", all(b < 128 for b in ps1))

reel_batch.process, reel_batch.site_prices = real_process, real_prices
print(f"\nпровалів: {len(F)}")
sys.exit(1 if F else 0)

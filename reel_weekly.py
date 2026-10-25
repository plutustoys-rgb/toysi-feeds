# -*- coding: utf-8 -*-
"""reel_weekly.py — щотижнева партія рілсів «сцена першою» у чергу «НА СХВАЛЕННЯ» (SMM за словами власника 21.10.2026: «автоматично щотижня збирає наступну партію; публікація лише після «ок» власника»).

Що робить (запуск на ПК власника, де сцени/шрифти/ffmpeg і де SMM робить QA; нічого не пише на VPS і нічого не публікує):
  1. Читає ЖИВІ дані: опублікований сайт (index-t.json/index-rt.json: наявність і ціна), фід наявності (meta_feed.xml з feed-data), «попит» (demand_featured_auto.json).
  2. Слоти публікації: наступні вівторки й п'ятниці після останнього запланованого рілса (not_before у теці черги) — по `--count` (за замовч. 2 = тиждень).
  3. Тема: за списком reel_themes.json, що НЕ повторювалась у останніх рілсах і має ≥3 придатних товари. Товари: у наявності на сайті І у фіді, є фото, не заборонені (is_forbidden + FEED_EXCLUDED + ключові слова зброї/страшного), без дублю з УСІМА раніше використаними (маніфести в теці + журнал), ціна 80–1500 ₴; пріоритет — «попит» із Search Console, далі помірна ціна; різні вироби (без однакових назв).
  4. Сцена: ротація 6 затверджених несезонних (SCENE_ROTATION; не та сама підряд; сезонні — лише у своєму вікні за ДАТОЮ ПУБЛІКАЦІЇ; браковані — ніколи).
  5. Рендер reel_build (перша карта = лише сцена, 3 товари, картка бренду) + підпис і маніфест reel_batch (ЖИВІ ціни з сайту) → <out>/<name>.{mp4,caption.txt,json} + список «НА_СХВАЛЕННЯ_<дата>.md» (гачки й підписи — ЧЕРНЕТКА за шаблоном: SMM перечитує).
Публікація = окремий явний крок власника (scp у /opt/plutustoys/reels_queue/) після «ок». Журнал: <out>/_weekly_state.json.

Запуск:  python reel_weekly.py build [--count 2] [--out теку] [--dry-run]   (dry-run: лише план і підписи, без рендера)"""
import argparse
import json
import os
import re
import sys
import urllib.request
from datetime import date, datetime, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from reel_seasons import season_not_after  # noqa: E402
import reel_library  # noqa: E402  (легкий: numpy/PIL лише в функціях)
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

SITE = "https://plutustoys.com.ua"
FEED_URL = "https://raw.githubusercontent.com/plutustoys-rgb/toysi-feeds/feed-data/feeds/meta_feed.xml"
THEMES_FILE = os.path.join(HERE, "reel_themes.json")
DEFAULT_OUT = r"C:\Users\smach\Claude\Projects\PlutusToys_avtonomiya\reels_queue_upload"
PRICE_MIN, PRICE_MAX = 80, 1500
ITEMS_PER_REEL = 3
THEME_COOLDOWN = 3          # скільки останніх рілсів тема не повторюється
# ЗАТВЕРДЖЕНІ сцени для ротації (SMM 22.10.2026, QA кадрами за вердиктом по 14 сценах): ЧОРНИЙ СПИСОК — sunglasses, laugh, bubblegum, dustbook, flychase (дефект джерела: водяний знак/вибиті
# ключем кадри/зрізи); tailwag — після виправлення хвоста (поки не в ротації); halloween/snowflake — сезонні, лише вручну під тему/дату.
SCENE_ROTATION = ["noselick", "headshake", "icecream", "nod", "wink", "lookaround"]
SCENE_BLACKLIST = ("sunglasses", "laugh", "bubblegum", "dustbook", "flychase")
END_SCENE = "scene_noselick_RENDER_GREEN.mp4"          # кінцева сцена за замовчуванням — чиста (не sunglasses)
END_SCENE_ALT = "scene_wink_RENDER_GREEN.mp4"          # коли перша сцена — noselick
# слова, через які товар НЕ береться в рілс (зброя, імітації, страшне; доповнення до forbidden_products)
BAD_NAME_RE = re.compile(r"пістолет|пистолет|рушниц|автомат(?!ичн)|меч\b|мечі|\bніж\b|ножі|арбалет|\bлук\b|лук зі|\bграната\b|гранатомет|бомб|кинджал|катан|сокир|\bкров|\bчереп(?!ах|аш)|скелет|джейсон|чакі|вбив|монстр-?трак|страш|сигарет|вейп|алкогол|пиво|горілк|зброя|зброї|бластер|нунчак|кастет|топор|\bшабл(?!он)|\bспис\b|стріл[яь]|снайпер|військов|воєнн|\bтанк(?!ер)|зомбі|вампір|дракул|привид|демон(?!стр)|диявол|\bчорт|відьм|\bтруп|могил|кладовищ|смерт|убивц|вбивц|мафі|гангстер|squid|кальмар|перестріл|\bармі|\bгуль\b|18\+", re.I)
UNIT_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s*(елемент\w*|ел\b|елм|шт\w*|кольор\w*|мл|г\b|см|деталей|деталі|карток|картки|кульок)", re.I)
UNIT_FIX = {"ел": "елементів", "елм": "елементів"}


def fetch_json(name):
    with urllib.request.urlopen(f"{SITE}/{name}", timeout=60) as r:
        return json.loads(r.read().decode("utf-8"))


def norm_stem(name):
    """Основа назви для дедуплікації «однакових виробів» (перші 2 слова без цифр/лапок/кольору в дужках)."""
    n = re.sub(r"\(.*?\)|[«»\"'\d,.\-–/]", " ", (name or "").lower())
    w = [x for x in n.split() if len(x) > 2]
    return " ".join(w[:2])


def short_name(name, limit=26):
    """Коротка назва для гачка: без дужок/розмірів/«укр», до `limit` символів по межі слова."""
    n = re.sub(r"\(.*?\)", " ", (name or "").replace("*", " "))
    n = re.sub(r",\s*(укр|\d).*$", "", n)
    n = re.sub(r"\s+", " ", n).strip(" ,.-")
    if len(n) <= limit:
        return n
    cut = n[:limit].rsplit(" ", 1)[0]
    return cut.strip(" ,.-«»\"") or n[:limit]


def stat_for(item_name, theme):
    m = UNIT_RE.search(item_name or "")
    if m:
        num, unit = m.group(1), m.group(2).lower()
        return f"{num} {UNIT_FIX.get(unit, unit)}"
    return theme["stat_default"]


def next_slots(after, n):
    """n наступних вівторків/п'ятниць СТРОГО після дати `after`."""
    out, d = [], after
    while len(out) < n:
        d += timedelta(days=1)
        if d.weekday() in (1, 4):
            out.append(d)
    return out


def load_state(out_dir):
    p = os.path.join(out_dir, "_weekly_state.json")
    try:
        st = json.load(open(p, encoding="utf-8"))
        if isinstance(st, dict):
            st.setdefault("used_ids", [])
            st.setdefault("themes", [])
            st.setdefault("scenes", [])
            return st
    except (OSError, ValueError):
        pass
    return {"used_ids": [], "themes": [], "scenes": []}


def save_state(out_dir, st):
    p = os.path.join(out_dir, "_weekly_state.json")
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        json.dump(st, f, ensure_ascii=False, indent=1)
    os.replace(tmp, p)


def folder_used_ids_and_last_date(out_dir, specs_dir=None, extra=()):
    """(множина id, остання дата) з УСІХ маніфестів у теці черги (разом з підтеками: published/ тощо) і з усіх спек репо — джерело правди «що вже було/заплановано».
    Маніфест: verify[].id + not_before; спека: items[].id + publish_date. Нечитабельний файл пропускається."""
    ids, last = set(), None
    roots = [r for r in (out_dir, specs_dir if specs_dir is not None else os.path.join(HERE, "reels_specs"), *extra) if r and os.path.isdir(r)]
    for root in roots:
        for dp, _dn, fns in os.walk(root):
            for fn in fns:
                if not fn.endswith(".json") or fn.startswith("_"):
                    continue
                try:
                    m = json.load(open(os.path.join(dp, fn), encoding="utf-8"))
                except (OSError, ValueError):
                    continue
                if not isinstance(m, dict):
                    continue
                ids |= {str(v.get("id")) for v in m.get("verify", []) if isinstance(v, dict)}
                ids |= {str(v.get("id")) for v in m.get("items", []) if isinstance(v, dict)}
                try:
                    d = date.fromisoformat(str(m.get("not_before") or m.get("publish_date") or "")[:10])
                    last = d if last is None or d > last else last
                except ValueError:
                    pass
    return ids, last


def candidates(theme, index, feed, used, demand, excluded=frozenset(), forbidden=None):
    """Придатні товари теми: у наявності на сайті в категоріях теми, ціна в межах, у фіді (in stock + фото), не заборонені/використані, назва без «поганих» слів. -> [(score, item)] відсортовано."""
    cats = set(theme["categories"])
    out = []
    for x in index:
        if x.get("s") != 1 or x.get("c") not in cats:
            continue
        i = str(x["id"])
        pr = x.get("pr")
        if i in used or i in excluded or pr is None or not (PRICE_MIN <= float(pr) <= PRICE_MAX):
            continue
        f = feed.get(i)
        if not f or f.get("avail") != "in stock" or not f.get("image"):
            continue
        if BAD_NAME_RE.search(x.get("n", "")):
            continue
        if forbidden and forbidden({"id": i, "category_name": x.get("c"), "name": x.get("n")}):
            continue
        score = (i in demand, 100 <= float(pr) <= 600, -abs(float(pr) - 250))
        out.append((score, x))
    out.sort(key=lambda t: (t[0], -int(t[1]["id"]) if str(t[1]["id"]).isdigit() else 0), reverse=True)
    return out


def pick_items(cands, n=ITEMS_PER_REEL):
    """n виробів: за рейтингом, без однакових основ назви (різні вироби)."""
    seen, picked = set(), []
    for _, x in cands:
        st = norm_stem(x["n"])
        if st in seen:
            continue
        seen.add(st)
        picked.append(x)
        if len(picked) == n:
            break
    return picked


def pick_theme(themes, recent, index, feed, used, demand, excluded, forbidden=None):
    """Перша тема зі списку, що не в останніх THEME_COOLDOWN і має ≥ ITEMS_PER_REEL придатних РІЗНИХ виробів. -> (theme, picked items) або (None, [])."""
    banned = set(recent[-THEME_COOLDOWN:])
    for th in themes:
        if th["key"] in banned:
            continue
        picked = pick_items(candidates(th, index, feed, used, demand, excluded, forbidden))
        if len(picked) >= ITEMS_PER_REEL:
            return th, picked
    return None, []


def pick_scene(prev_scenes, publish_date, season_ok, names=None):
    """Наступна сцена за ротацією після останньої використаної, не та сама підряд, сезонні сюди не входять (тільки затверджені несезонні, SCENE_ROTATION)."""
    if names is None:
        names = [f"scene_{n}_RENDER_GREEN.mp4" for n in SCENE_ROTATION]
    names = [n for n in names if season_ok(n, publish_date)]
    last = prev_scenes[-1] if prev_scenes else None
    start = (names.index(last) + 1) % len(names) if last in names else 0
    for k in range(len(names)):
        c = names[(start + k) % len(names)]
        if c != last:
            return c
    return names[0]


def make_spec(theme, items, publish_date, scene, queue_name, end_choices=None):
    end = END_SCENE_ALT if scene == END_SCENE else END_SCENE
    if end_choices:      # режим бібліотеки: ендсцена — схвалена З end_mascot PNG і не та сама, що перша
        end = next((c for c in end_choices if c != scene), end_choices[0])
    its = []
    for x in items:
        sn = short_name(x["n"])
        its.append({"id": str(x["id"]), "hook": f"{theme['hook_verb']} *{sn}*", "name": short_name(x["n"], 44), "spec": [stat_for(x["n"], theme), theme["tail"]],
                    "cap_emoji": theme["emoji"], "cap_text": re.sub(r"\s+", " ", x["n"].replace("*", " ")).strip()[:90]})
    return {"queue_name": queue_name, "publish_date": publish_date.isoformat(), "out": "PlutusToys_reel_" + queue_name, "endline": theme["endline"], "first_scene": scene, "endscene": end,
            "caption": {"intro": theme["intro"], "sub": theme["sub"], "cta": theme["cta"], "hashtags": theme["hashtags"]}, "items": its}


def next_queue_name(theme_key, out_dir):
    k = 1
    while any(os.path.exists(os.path.join(d, f"{theme_key}_{k:02d}.json")) for d in (out_dir, os.path.join(out_dir, "_specs"), os.path.join(HERE, "reels_specs"))):
        k += 1
    return f"{theme_key}_{k:02d}"


def approval_note(out_dir, built, today):
    lines = [f"# Рілси НА СХВАЛЕННЯ — зібрано {today.isoformat()} (reel_weekly.py)", "",
             "Публікація — лише після «ок» власника: один scp mp4+caption+json у /opt/plutustoys/reels_queue/. Гачки й підписи — чернетка за шаблоном, SMM перечитує.", ""]
    for b in built:
        na = season_not_after([b.get("first_scene"), b.get("endscene")], date.fromisoformat(b["publish_date"]))
        lines += [f"## {b['queue_name']} — публікація не раніше {b['publish_date']}" + (f", не пізніше {na.isoformat()} (сезонна сцена)" if na else "") + f" (сцена {b['first_scene']}, ендсцена {b['endscene']})",
                  f"Файли: `{b['queue_name']}.mp4`, `{b['queue_name']}.caption.txt`, `{b['queue_name']}.json`", ""]
        for it in b["items"]:
            lines.append(f"- {it['id']}: гачок «{it['hook']}» · {it['name']} · {' / '.join(it['spec'])} · підпис: {it['cap_emoji']} {it['cap_text']}")
        lines.append("")
    p = os.path.join(out_dir, f"НА_СХВАЛЕННЯ_{today.isoformat()}.md")
    with open(p, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines))
    return p


# ───────────────────────── режим VPS (етап 2 автозбирача, рішення власника 25.10.2026) ─────────────────────────
# «Самі рілси збирайте вже з [схваленими першими сторінками] без схвалення»: тижневий таймер добирає чергу автопублікації до ENSURE_SLOTS майбутніх слотів.
# Збірка — у staging (reels_pending/, її НЕ читає автопублікатор), у чергу файли кладуться атомарно: mp4 → caption → json ОСТАННІМ (publisher бере *.json).
# Стан (_weekly_state.json) — НЕ в черзі: publisher трактує будь-який *.json у reels_queue як маніфест.
VPS_QUEUE = "/opt/plutustoys/reels_queue"
VPS_STAGING = "/opt/plutustoys/reels_pending"
VPS_FEED = "/opt/plutustoys/feeds/meta_feed.xml"
ENSURE_SLOTS = 6            # скільки майбутніх слотів (вт/пт) тримаємо заповненими: ≈3 тижні
MAX_PER_RUN = 3             # не більше рілсів за запуск (навантаження на 2 ядра VPS)
FEED_MAX_AGE_H = 36


def _alert(key, text):
    try:
        from telegram_notify import send_throttled_alert
        send_throttled_alert(key, text, cooldown_sec=24 * 60 * 60)
    except Exception as e:  # noqa: BLE001
        print(f"[reel_weekly] сигнал не надіслано: {type(e).__name__}", file=sys.stderr)


def library_rotation():
    """Ротація першої сцени лише зі СХВАЛЕНИХ власником (порядок SCENE_ROTATION, далі решта схвалених за алфавітом)."""
    ok = reel_library.approved_scenes()
    keys = [k for k in SCENE_ROTATION if k in ok] + sorted(k for k in ok if k not in SCENE_ROTATION)
    return [f"scene_{k}_RENDER_GREEN.mp4" for k in keys]


def library_end_choices():
    """Ендсцени: схвалені, з end_mascot PNG; чистіші першими (як END_SCENE/END_SCENE_ALT), далі решта."""
    ok = [k for k in reel_library.approved_scenes() if reel_library.end_mascot_path(k)]
    pref = [k for k in ("noselick", "wink", "nod") if k in ok]
    return [f"scene_{k}_RENDER_GREEN.mp4" for k in pref + sorted(k for k in ok if k not in pref)]


def queue_count(queue_dir):
    """Скільки рілсів чекає в черзі (маніфести *.json у теці черги без published.json та службових `_*`)."""
    try:
        return sum(1 for f in os.listdir(queue_dir) if f.endswith(".json") and f != "published.json" and not f.startswith("_"))
    except OSError:
        return 0


def publish_to_queue(name, staging, queue):
    """staging → черга: кожен файл копіюється під тимчасовим іменем (НЕ *.json) і атомарно перейменовується; json — ОСТАННІМ, тож publisher не побачить
    маніфест без відео/підпису. Лише після успіху staging-копії видаляються."""
    import shutil
    os.makedirs(queue, exist_ok=True)
    order = [name + ".mp4", name + ".caption.txt", name + ".json"]
    for fn in order:
        if not os.path.isfile(os.path.join(staging, fn)) or os.path.getsize(os.path.join(staging, fn)) == 0:
            raise RuntimeError(f"[reel_weekly] у staging нема/порожній {fn} — в чергу не кладу")
    for fn in order:
        if os.path.exists(os.path.join(queue, fn)):
            raise RuntimeError(f"[reel_weekly] у черзі вже є {fn} — нічого не перезаписую")
    for fn in order:
        tmp = os.path.join(queue, fn + ".incoming")
        shutil.copyfile(os.path.join(staging, fn), tmp)
        os.replace(tmp, os.path.join(queue, fn))
    for fn in order:
        try:
            os.remove(os.path.join(staging, fn))
        except OSError:
            pass


def run_build(count, out_dir, dry_run=False, today=None, index=None, feed=None, demand=None, now_forbidden=None, feed_path=None, specs_dir=None,
              state_dir=None, scan_dirs=(), feed_file=None, on_built=None, failures=None):
    """Збирає `count` рілсів. Дані можна підмінити (тести): index/feed/demand. VPS-режим: state_dir (стан поза чергою), scan_dirs (черга — звідки беруться
    використані id і остання дата), feed_file (локальний meta_feed.xml), on_built(spec) (кладе готовий рілс у чергу), failures (список збоїв збірки)."""
    import reel_build
    import reel_batch
    from forbidden_products import is_forbidden
    from generate_google_feed import FEED_EXCLUDED_PRODUCT_IDS
    today = today or date.today()
    os.makedirs(out_dir, exist_ok=True)
    if index is None:
        index = []
        for name in ("index-t.json", "index-rt.json"):
            try:
                index += fetch_json(name)
            except Exception as e:  # noqa: BLE001
                print(f"[reel_weekly] ⚠ {name}: {type(e).__name__}", file=sys.stderr)
    if len([x for x in index if x.get("s") == 1]) < 1000:
        sys.exit("[reel_weekly] СТОП: опублікований каталог сайту недоступний/малий — партію не збираємо")
    if feed is None:
        if feed_file and os.path.isfile(feed_file):
            feed_path = feed_file
        else:
            import tempfile
            feed_path = os.path.join(tempfile.mkdtemp(), "meta_feed.xml")
            urllib.request.urlretrieve(FEED_URL, feed_path)
        feed = reel_build.live_feed(feed_path)
    if demand is None:
        try:
            demand = set(str(i) for i in fetch_json("demand_featured_auto.json").get("ids", []))
        except Exception:  # noqa: BLE001
            demand = set()
    lib = reel_library.library_dir() is not None
    st = load_state(state_dir or out_dir)
    folder_ids, last_date = folder_used_ids_and_last_date(out_dir, specs_dir, extra=tuple(scan_dirs))
    used = set(st["used_ids"]) | folder_ids
    themes = json.load(open(THEMES_FILE, encoding="utf-8"))["themes"]
    slots = next_slots(max([today] + ([last_date] if last_date else [])), count)
    prices = None if dry_run else reel_batch.site_prices()
    built = []
    bad_ids = set()             # товари з невдалої збірки: у цьому запуску їх більше не беремо
    for sd in slots:
        th, items = pick_theme(themes, st["themes"], index, feed, used, demand, set(FEED_EXCLUDED_PRODUCT_IDS) | bad_ids, now_forbidden or is_forbidden)
        if not th:
            print(f"[reel_weekly] ⚠ для слоту {sd} немає теми з ≥{ITEMS_PER_REEL} придатними товарами — пропускаю", file=sys.stderr)
            if failures is not None:
                failures.append(f"слот {sd}: нема теми з ≥{ITEMS_PER_REEL} придатними товарами")
            continue
        scene = pick_scene(st["scenes"], sd, reel_build.scene_season_ok, names=library_rotation() if lib else None)
        spec = make_spec(th, items, sd, scene, next_queue_name(th["key"], out_dir), end_choices=library_end_choices() if lib else None)
        if dry_run:
            print(f"[reel_weekly] ПЛАН {spec['queue_name']} на {sd}: {th['key']} · сцена {scene} · " + ", ".join(i["id"] for i in spec["items"]))
        else:
            # спека — у теці виводу (_specs/), НЕ в репо: щотижневі спеки не засмічують git; id з неї враховує folder_used_ids_and_last_date
            sp_dir = os.path.join(out_dir, "_specs")
            os.makedirs(sp_dir, exist_ok=True)
            sp_path = os.path.join(sp_dir, spec["queue_name"] + ".json")
            with open(sp_path, "w", encoding="utf-8", newline="\n") as f:
                json.dump(spec, f, ensure_ascii=False, indent=2)
                f.write("\n")
            try:
                reel_batch.process(sp_path, out_dir, feed_path or os.path.join(HERE, "feeds", "meta_feed.xml"), prices, render=True)
                if on_built:
                    on_built(spec)
            except (SystemExit, RuntimeError, OSError) as e:      # СТОП збірки (товар зник, підпис задовгий, сцена/бібліотека) не валить усю партію
                msg = f"{spec['queue_name']}: {type(e).__name__}: {e}"
                print(f"[reel_weekly] ⚠ збірка не вдалась — {msg}", file=sys.stderr)
                if failures is not None:
                    failures.append(msg)
                bad_ids |= {i["id"] for i in spec["items"]}
                for fn in (spec["queue_name"] + ".mp4", spec["queue_name"] + ".caption.txt", spec["queue_name"] + ".json"):
                    try:
                        os.remove(os.path.join(out_dir, fn))      # недоплетене в staging не лишаємо (у чергу воно не потрапило)
                    except OSError:
                        pass
                try:
                    os.remove(sp_path)                             # спека невдалого рілса не має «займати» id/дату
                except OSError:
                    pass
                continue
        used |= {i["id"] for i in spec["items"]}
        st["used_ids"] = sorted(used)
        st["themes"].append(th["key"])
        st["scenes"].append(scene)
        built.append(spec)
        if not dry_run:
            save_state(state_dir or out_dir, st)          # після КОЖНОГО рілса: збій на наступному не губить уже зібрані (інакше повторний запуск знову візьме ті самі id)
    if built and not dry_run and on_built is None:
        print("[reel_weekly] список на схвалення:", approval_note(out_dir, built, today))
    return built


def run_vps(ensure_slots=ENSURE_SLOTS, queue_dir=VPS_QUEUE, staging_dir=VPS_STAGING, today=None, dry_run=False, feed_file=None, alert=None, **kw):
    """Тижневий автозбирач на VPS без людського схвалення (лише схвалені власником першi сцени). Добирає чергу до `ensure_slots` майбутніх слотів."""
    alert = alert or _alert
    if not reel_library.library_dir():
        sys.exit("[reel_weekly] СТОП: бібліотека схвалених сцен не налаштована (PLUTUS_SCENE_LIBRARY або /opt/plutustoys/plutus_scene_library) — авто-збірка вимкнена")
    try:
        reel_library.check_integrity(require=True)
    except RuntimeError as e:
        alert("reel_weekly_lib", f"🔴 reel-weekly: {e}")
        sys.exit(f"[reel_weekly] СТОП: {e}")
    have = queue_count(queue_dir)
    need = min(MAX_PER_RUN, ensure_slots - have)
    print(f"[reel_weekly] у черзі {have} рілсів, ціль {ensure_slots}, добираю {max(0, need)}")
    if need <= 0:
        return []
    if feed_file is None and os.path.isfile(VPS_FEED) and (datetime.now().timestamp() - os.path.getmtime(VPS_FEED)) / 3600 <= FEED_MAX_AGE_H:
        feed_file = VPS_FEED
    failures = []
    built = run_build(need, staging_dir, dry_run=dry_run, today=today, state_dir=staging_dir, scan_dirs=(queue_dir,), feed_file=feed_file,
                      on_built=None if dry_run else (lambda spec: publish_to_queue(spec["queue_name"], staging_dir, queue_dir)), failures=failures, **kw)
    if failures and not dry_run:
        alert("reel_weekly_fail", "⚠️ reel-weekly: частина рілсів не зібрана — " + "; ".join(failures)[:600])
    if built and not dry_run:
        print("[reel_weekly] у чергу додано: " + ", ".join(b["queue_name"] for b in built) + " (публікація — автоматично за слотами вт/пт)")
    return built


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["build"])
    ap.add_argument("--count", type=int, default=2)
    ap.add_argument("--out", default=DEFAULT_OUT)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--vps", action="store_true", help="автозбирач на VPS: лише схвалені сцени бібліотеки, без людського схвалення, добирає чергу reels_queue")
    ap.add_argument("--ensure-slots", type=int, default=ENSURE_SLOTS)
    ap.add_argument("--queue-dir", default=VPS_QUEUE)
    ap.add_argument("--staging-dir", default=VPS_STAGING)
    a = ap.parse_args(argv)
    if a.vps:
        run_vps(a.ensure_slots, a.queue_dir, a.staging_dir, dry_run=a.dry_run)
    else:
        run_build(a.count, a.out, dry_run=a.dry_run)


if __name__ == "__main__":
    main()

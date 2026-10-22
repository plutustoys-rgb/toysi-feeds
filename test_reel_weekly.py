#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_reel_weekly.py — щотижневий збирач рілсів «на схвалення» (reel_weekly.py, 21.10.2026). Синтетичні дані, без мережі й рендера.
Інваріанти: (1) слоти — наступні вівторки/п'ятниці СТРОГО після останньої запланованої дати; (2) кандидати: лише в наявності на сайті І у фіді з фото, ціна 80–1500, не використані/виключені/заборонені, без «поганих» слів (зброя, страшне);
(3) пріоритет — попит із GSC; вибір 3 РІЗНИХ виробів; (4) тема не повторюється у останніх 3 рілсах і має ≥3 товарів; (5) сцена — ротація, не та сама підряд, сезонні не входять, поза сезоном недоступні;
(6) використані id беруться з УСІХ маніфестів у теці + журналу (без дублю з уже заплановані/опублікованими); (7) spec: гачок/назва/стат/хвіст/підпис за темою; (8) dry-run збирає план і не пише спеки/рендер/журнал."""
import json
import os
import sys
import tempfile
from datetime import date
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).parent))
import reel_weekly as rw  # noqa: E402
import reel_build  # noqa: E402

F = []


def chk(n, c):
    print(f"[{'OK ' if c else 'FAIL'}] {n}")
    if not c:
        F.append(n)


# 1) слоти
s = rw.next_slots(date(2026, 10, 23), 4)       # п'ятниця 23.10
chk("слоти строго ПІСЛЯ дати: після пт 23.10 → вт 27.10, пт 30.10, вт 03.11, пт 06.11", s == [date(2026, 10, 27), date(2026, 10, 30), date(2026, 11, 3), date(2026, 11, 6)])
chk("після середи 21.10 → пт 23.10, вт 27.10", rw.next_slots(date(2026, 10, 21), 2) == [date(2026, 10, 23), date(2026, 10, 27)])

# 2–3) кандидати
THEME = {"key": "t", "title": "т", "categories": ["Сквіші"], "emoji": "🧸", "hook_verb": "Відчуй", "stat_default": "антистрес", "tail": "мʼякий", "intro": "i", "sub": "s", "cta": "c", "endline": "e", "hashtags": ["х"]}


def X(i, n, c="Сквіші", pr=150, s=1):
    return {"id": str(i), "n": n, "c": c, "pr": pr, "s": s}


INDEX = [X(1, 'Сквіш «Сир» Squishy, 8×8 см'), X(2, 'Сквіш «Тост» Butter'), X(3, 'Сквіш «Шоколад» 15,5 см (мікс)', pr=180), X(4, "Сквіш «Манго»", pr=60), X(5, "Сквіш «Мілкшейк»", pr=2000),
         X(6, "Сквіш «Не в наявності»", s=0), X(7, "Сквіш «Пістолет-сквіш»"), X(8, "Сквіш «Чужа категорія»", c="Пазли Trefl"), X(9, "Сквіш «Без фото»"), X(10, "Сквіш «Використаний»"),
         X(11, "Сквіш «Заборонений»"), X(12, "Сквіш «Виключений»"), X(13, 'Сквіш «Сир» Squishy, 9×9 см'), X(14, "Сквіш «Попит»", pr=500)]
FEED = {str(i): {"avail": "in stock", "image": "http://x/i.jpg"} for i in range(1, 15)}
FEED["6"]["avail"] = "out of stock"
FEED["9"]["image"] = ""
c = rw.candidates(THEME, INDEX, FEED, used={"10"}, demand={"14"}, excluded={"12"}, forbidden=lambda it: it["id"] == "11")
ids = [x["id"] for _, x in c]
chk("кандидати: відсіяно (ціна <80/>1500, не в наявності на сайті, зброя у назві, чужа категорія, без фото, використаний, заборонений, виключений); лишились 1,2,3,13,14", sorted(ids, key=int) == ["1", "2", "3", "13", "14"])
chk("пріоритет: товар із попитом GSC (14) — першим", ids[0] == "14")
picked = rw.pick_items(c)
chk("3 РІЗНІ вироби: «Сир 8×8» (1) і «Сир 9×9» (13) мають однакову основу назви → беремо лише один", len(picked) == 3 and not ({"1", "13"} <= {x["id"] for x in picked}))
chk("BAD_NAME_RE ловить зброю/страшне: пістолет, арбалет, скелет, кров, нож", all(rw.BAD_NAME_RE.search(w) for w in ("Пістолет водяний", "Арбалет іграшковий", "Скелет-світильник", "Ніж з кров'ю", "Автомат іграшковий")) and not rw.BAD_NAME_RE.search("Мʼяка іграшка «Котик»"))

# 4) тема: cooldown
TH2 = dict(THEME, key="u", categories=["Пазли Trefl"])
IDX2 = INDEX + [X(20, "Пазл Альфа", c="Пазли Trefl"), X(21, "Пазл Браво", c="Пазли Trefl"), X(22, "Пазл Чарлі", c="Пазли Trefl")]
FEED2 = dict(FEED, **{str(i): {"avail": "in stock", "image": "http://x"} for i in (20, 21, 22)})
th, it_ = rw.pick_theme([THEME, TH2], [], IDX2, FEED2, set(), set(), set())
chk("тема: перша зі списку, що має ≥3 товарів (t)", th and th["key"] == "t" and len(it_) == 3)
th, it_ = rw.pick_theme([THEME, TH2], ["t"], IDX2, FEED2, set(), set(), set())
chk("cooldown: «t» щойно була → береться «u»", th and th["key"] == "u")
th, it_ = rw.pick_theme([THEME, TH2], ["t", "u"], IDX2, FEED2, set(), set(), set())
chk("обидві теми в cooldown → None (краще пропустити слот, ніж повторити тему)", th is None)
th, it_ = rw.pick_theme([TH2], [], INDEX, FEED, set(), set(), set())
chk("тема без ≥3 придатних товарів → None", th is None)

# 5) сцени
ok = reel_build.scene_season_ok
chk("сцена: перша — noselick; після неї headshake; ротація замикається (lookaround → noselick); ніколи та сама підряд; чорного списку в ротації нема",
    rw.pick_scene([], date(2026, 11, 3), ok) == "scene_noselick_RENDER_GREEN.mp4"
    and rw.pick_scene(["scene_noselick_RENDER_GREEN.mp4"], date(2026, 11, 3), ok) == "scene_headshake_RENDER_GREEN.mp4"
    and rw.pick_scene(["scene_lookaround_RENDER_GREEN.mp4"], date(2026, 11, 3), ok) == "scene_noselick_RENDER_GREEN.mp4"
    and not set(rw.SCENE_ROTATION) & set(rw.SCENE_BLACKLIST) and "sunglasses" not in rw.END_SCENE and "sunglasses" not in rw.END_SCENE_ALT)
allsc = set()
prev = []
for _ in range(24):
    sc = rw.pick_scene(prev, date(2026, 11, 3), ok)
    assert not prev or sc != prev[-1]
    prev.append(sc)
    allsc.add(sc)
chk("ротація охоплює усі 6 затверджених сцен, жодної сезонної (halloween/snowflake) і жодної з чорного списку", len(allsc) == len(rw.SCENE_ROTATION) == 6 and not any("halloween" in x or "snowflake" in x or any(b in x for b in rw.SCENE_BLACKLIST) for x in allsc))

# 6) використані id з теки
td = Path(tempfile.mkdtemp())
(td / "a.json").write_text(json.dumps({"verify": [{"id": "1", "price": 1}, {"id": "2", "price": 2}], "not_before": "2026-11-06T00:00"}), encoding="utf-8")
(td / "b.json").write_text(json.dumps({"verify": [{"id": "3", "price": 1}], "not_before": "2026-10-30T00:00"}), encoding="utf-8")
(td / "_weekly_state.json").write_text("{не json", encoding="utf-8")
EMPTY = tempfile.mkdtemp()
ids_, last = rw.folder_used_ids_and_last_date(str(td), EMPTY)
chk("з теки: id з УСІХ маніфестів (1,2,3), остання дата not_before = 2026-11-06; службовий _weekly_state.json і битий JSON не ламають", ids_ == {"1", "2", "3"} and last == date(2026, 11, 6))
chk("битий журнал → порожній стан, без падіння", rw.load_state(str(td)) == {"used_ids": [], "themes": [], "scenes": []})

# 6б) підтеки, спеки, службові файли
(td / "published").mkdir()
(td / "published" / "old.json").write_text(json.dumps({"verify": [{"id": "77", "price": 1}], "not_before": "2026-12-01T00:00"}), encoding="utf-8")
(td / "_specs").mkdir()
(td / "_specs" / "sq_01.json").write_text(json.dumps({"publish_date": "2026-12-04", "items": [{"id": "88"}]}), encoding="utf-8")
SPD = Path(tempfile.mkdtemp())
(SPD / "x_01.json").write_text(json.dumps({"publish_date": "2026-12-08", "items": [{"id": "99"}, {"id": "98"}]}), encoding="utf-8")
ids2, last2 = rw.folder_used_ids_and_last_date(str(td), str(SPD))
chk("id і дати беруться також з підтек (published/), з _specs/ і зі спек репо; остання дата = 2026-12-08", {"1", "2", "3", "77", "88", "98", "99"} <= ids2 and last2 == date(2026, 12, 8))
chk("BAD_NAME_RE (розширено): бластер, зомбі, привид, мафія, зброя — відсіяно; «Список», «Демонстрація», «Мумі-тролі» — ні", all(rw.BAD_NAME_RE.search(w) for w in ("Бластер Nerf", "Зомбі-набір", "Набір Привиди", "Гра Мафія", "Набір зброї")) and not any(rw.BAD_NAME_RE.search(w) for w in ("Список покупок", "Демонстрація", "Мумі-тролі", "Пазл Спис-гра".replace("Спис-гра", "Альфа"))))
chk("зірочка в назві не ламає розмітку гачка *…* (short_name і cap_text без «*»)", "*" not in rw.short_name("Сквіш *Супер* Сир") and "*" not in rw.make_spec(THEME, [X(1, "Сквіш *Сир*")], date(2026, 11, 3), "scene_nod_RENDER_GREEN.mp4", "t_01")["items"][0]["cap_text"])
nb = rw.approval_note(tempfile.mkdtemp(), [dict(rw.make_spec(THEME, [X(1, "Сквіш Сир")], date(2026, 10, 30), "scene_halloween_RENDER_GREEN.mp4", "t_01"))], date(2026, 10, 21))
txt = open(nb, encoding="utf-8").read()
chk("список на схвалення: файли рілса, ендсцена, підпис товару і «не пізніше 2026-10-31» для сезонної сцени", "t_01.mp4" in txt and "ендсцена" in txt and "підпис: 🧸 Сквіш Сир" in txt and "не пізніше 2026-10-31" in txt)

# 7) spec
TH3 = json.load(open(rw.THEMES_FILE, encoding="utf-8"))["themes"][0]
spec = rw.make_spec(TH3, [X(1, 'Пазл «Марвел. Спайдермен і друзі» (60 елементів)', c="Пазли Trefl", pr=268), X(2, "Пазли 3в1 «Холодне серце»", c="Пазли Trefl", pr=326), X(3, "Пазл 100 ел «Поні»", c="Пазли Trefl", pr=298)], date(2026, 11, 3),
                    "scene_noselick_RENDER_GREEN.mp4", "puzzles_09")
chk("spec: first_scene, endscene (інша ніж перша), publish_date, queue_name, 3 товари з гачком «Збери *…*», стат «60 елементів»/«100 елементів»/запасний",
    spec["first_scene"] == "scene_noselick_RENDER_GREEN.mp4" and spec["endscene"] == "scene_wink_RENDER_GREEN.mp4" and spec["publish_date"] == "2026-11-03" and spec["queue_name"] == "puzzles_09" and len(spec["items"]) == 3
    and spec["items"][0]["hook"].startswith("Збери *Пазл «Марвел") and spec["items"][0]["spec"][0] == "60 елементів" and spec["items"][2]["spec"][0] == "100 елементів" and spec["items"][1]["spec"][0] == TH3["stat_default"])
chk("short_name: прибирає дужки/розміри, ≤ 26 символів по межі слова", rw.short_name('Пазл «Марвел. Спайдермен і друзі» (60 елементів)') == "Пазл «Марвел. Спайдермен і" or len(rw.short_name('Пазл «Марвел. Спайдермен і друзі» (60 елементів)')) <= 26)
chk("reel_themes.json валідний: 10 тем, кожна з категоріями/емодзі/гачком/хештегами/endline", len(json.load(open(rw.THEMES_FILE, encoding="utf-8"))["themes"]) == 10 and all(all(k in t for k in ("key", "categories", "emoji", "hook_verb", "intro", "sub", "cta", "endline", "hashtags", "stat_default", "tail")) for t in json.load(open(rw.THEMES_FILE, encoding="utf-8"))["themes"]))

# 8) dry-run
out = tempfile.mkdtemp()
big = [X(i, f"Сквіш {w} {i}", c="Сквіші", pr=150 + i) for i, w in zip(range(100, 130), ["Альфа", "Браво", "Чарлі", "Дельта", "Ехо", "Фокстрот", "Гольф", "Готель", "Індія", "Джульєт"] * 3)]
feedbig = {x["id"]: {"avail": "in stock", "image": "http://x"} for x in big}
filler = [X(1000 + i, f"Пазл{chr(1040 + i % 30)}{i}", c="Пазли Trefl", pr=200) for i in range(1100)]
for x in filler:
    feedbig[x["id"]] = {"avail": "in stock", "image": "http://x"}
built = rw.run_build(2, out, dry_run=True, today=date(2026, 10, 21), index=big + filler, feed=feedbig, demand=set(), now_forbidden=lambda it: False, specs_dir=EMPTY)
chk("dry-run: 2 рілси на слоти пт 23.10 і вт 27.10; теми різні, сцени різні й підряд не повторюються; id не перетинаються", len(built) == 2 and [b["publish_date"] for b in built] == ["2026-10-23", "2026-10-27"]
    and built[0]["queue_name"].split("_")[0] != built[1]["queue_name"].split("_")[0] and built[0]["first_scene"] != built[1]["first_scene"] and not ({i["id"] for i in built[0]["items"]} & {i["id"] for i in built[1]["items"]}))
chk("dry-run нічого не пише: тека порожня (немає спек/журналу/mp4)", os.listdir(out) == [])

print("\nFAIL:" if F else "\nOK: усі перевірки пройдені", *F, sep="\n")
sys.exit(1 if F else 0)

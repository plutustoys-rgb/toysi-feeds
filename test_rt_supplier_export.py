#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_rt_supplier_export.py — пілот RoyalToys (19.10.2026): експорт даних для сайту. Інваріанти: (1) ціна за закритою формулою max(опт×1,5;(опт+15)×1,03) вгору до гривні; (2) лише укр. назва (нема name_ua → не публікуємо), без фото → не публікуємо, ціна ≤0/нечислова → ні; (3) stock=0 якщо не available;
(4) відбір лише за rt_pilot_ids.json (209 id, унікальні, префікс rt-); (5) порожній YML → попередній файл НЕ чіпаємо; (6) файл приватний (не в site/), атомарний, "updated" ISO UTC; (7) stock_snapshot додає rt-<id> з файлу, а старий файл (>3 діб)/відсутній → ключів rt- нема."""
import json
import re
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).parent
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, str(HERE))
import rt_supplier_export as ex  # noqa: E402
import stock_snapshot as ss  # noqa: E402

F = []


def chk(n, c):
    print(f"[{'OK ' if c else 'FAIL'}] {n}")
    if not c:
        F.append(n)


chk("ціна: 100 → max(150; 118,45) = 150; 20 → max(30; 36,05) = 37; 10,5 → max(15,75; 26,3) = 27", ex.site_price(100) == 150 and ex.site_price(20) == 37 and ex.site_price(10.5) == 27)
ids = ex.load_pilot_ids()
chk("rt_pilot_ids.json: 234 унікальних id з префіксом rt-", len(ids) == 234 == len(set(ids)) and all(i.startswith("rt-") for i in ids))
chk("офіс. каталог не публікує гроші в site/: OUT_FILE поза site/", "site" not in ex.OUT_FILE.parts)
V = {"name_ua": "Іграшка", "description_ua": " Опис ", "price": "200", "stock": 7, "available": True, "vendor": "V", "vendor_code": "C1", "category_name": "Конструкторы", "dims_cm": [28.0, 4.0, 20.0], "weight_kg": 0.35}
it = ex.build_item("rt-1", V, ["https://x/rt-1-1.jpg"])
chk("елемент: ціна 300, cost 200, stock 7, категорія рос., опис обрізано", it["price"] == 300 and it["cost"] == 200.0 and it["stock"] == 7 and it["category"] == "Конструкторы" and it["description"] == "Опис")
chk("опис: HTML-теги прибрано, сутності розкодовано, абзаци лишились", ex.plain_text("<p>Один &amp; два</p><p>Три<br>чотири</p>") == "Один & два\nТри\nчотири" and ex.plain_text(None) == "")
chk("габарити й вага в елементі; без ваги → None", it["dims_cm"] == [28.0, 4.0, 20.0] and it["weight_kg"] == 0.35 and ex.build_item("rt-1", dict(V, weight_kg=None), ["u"])["weight_kg"] is None)
chk("без габаритів (None / неповні / нуль) → None, картку не публікуємо", all(ex.build_item("rt-1", dict(V, dims_cm=d), ["u"]) is None for d in (None, [1, 2], [1, 0, 3], ["a", 1, 1])))
chk("не available → stock 0", ex.build_item("rt-1", dict(V, available=False), ["u"])["stock"] == 0)
chk("нема name_ua → None", ex.build_item("rt-1", dict(V, name_ua=""), ["u"]) is None)
chk("без фото → None", ex.build_item("rt-1", V, []) is None)
for bad in ("0", "-5", "abc", "", "nan"):
    chk(f"ціна {bad!r} → None", ex.build_item("rt-1", dict(V, price=bad), ["u"]) is None)
chk("залишок нечисловий → 0, не падіння", ex.build_item("rt-1", dict(V, stock="x"), ["u"])["stock"] == 0)

with tempfile.TemporaryDirectory() as td:
    td = Path(td)
    out = td / "supplier_rt.json"
    cat = {"1": dict(V, pictures=["http://a/1.png", "http://a/2.png"]), "2": dict(V, name_ua="", pictures=["http://a/3.png"])}
    got = []
    fake = lambda url, dest: (got.append(dest.name), True)[1]  # noqa: E731
    ok = ex.run_once(cat, ["rt-1", "rt-2", "rt-3"], out, fake, td / "img")
    d = json.loads(out.read_text(encoding="utf-8"))
    chk("run_once: записано лише валідну (rt-1); rt-2 без укр. назви і rt-3 (нема в YML) пропущено", ok and list(d["items"]) == ["rt-1"])
    chk("фото: 2 шт., URL на наш сервер /img/rt/rt-1-N.jpg", d["items"]["rt-1"]["images"] == ["https://plutustoys.com.ua/img/rt/rt-1-1.jpg", "https://plutustoys.com.ua/img/rt/rt-1-2.jpg"] and got == ["rt-1-1.jpg", "rt-1-2.jpg"])
    upd = datetime.fromisoformat(d["updated"])
    chk('"updated" ISO із поясом UTC, свіжий', upd.tzinfo is not None and abs((datetime.now(timezone.utc) - upd).total_seconds()) < 60)
    before = out.read_text(encoding="utf-8")
    chk("порожній YML → False, файл не змінено", ex.run_once({}, ["rt-1"], out, fake, td / "img") is False and out.read_text(encoding="utf-8") == before)
    chk("жодної валідної картки → False, файл не змінено", ex.run_once({"9": V}, ["rt-1"], out, fake, td / "img") is False and out.read_text(encoding="utf-8") == before)
    chk("фото не отримано (fetch=False) → картка не публікується", ex.run_once(cat, ["rt-1"], td / "o2.json", lambda u, d_: False, td / "img") is False and not (td / "o2.json").exists())
    # stock_snapshot
    chk("stock_snapshot: rt-1 → 7 з файлу", ss.rt_stock_entries(out) == {"rt-1": 7})
    old = {"updated": (datetime.now(timezone.utc) - timedelta(hours=3, minutes=5)).isoformat(), "items": {"rt-1": {"stock": 5}}}
    (td / "old.json").write_text(json.dumps(old), encoding="utf-8")
    chk("stock_snapshot: файл старший за 3 год → {} (fail-closed)", ss.rt_stock_entries(td / "old.json") == {})
    chk("stock_snapshot: нема файлу / сміття → {}", ss.rt_stock_entries(td / "none.json") == {} and ss.rt_stock_entries(td / "o2.json") == {})
    (td / "bad.json").write_text("{не json", encoding="utf-8")
    chk("stock_snapshot: битий JSON → {}", ss.rt_stock_entries(td / "bad.json") == {})
    stk = td / "stock.json"
    sup = td / "sup.json"
    sup.write_text(json.dumps({"updated": datetime.now(timezone.utc).isoformat(), "items": {"rt-1": {"stock": 4}}}), encoding="utf-8")
    ex.OUT_FILE = sup
    import parser as _tp  # noqa: E402
    _tp.assert_catalog_size_sane = lambda c: None
    ss.run_once(fetch=lambda: {"100": {"stock": 5}}, out=stk)
    snap = json.loads(stk.read_text(encoding="utf-8")) if stk.exists() else {}
    chk("run_once stock_snapshot: у stock.json є Toysi-ключ і rt-1", snap.get("s", {}).get("100") == 5 and snap.get("s", {}).get("rt-1") == 4)

# --- режим «весь каталог» (19.10, слово власника) ---
import json as _j2, tempfile as _tf2
def mk(price="200", stock=7, cat="Конструкторы", name="Іграшка", dims=(10, 10, 10), avail=True, pics=("http://a/x.jpg",)):
    return {"id": "", "name_ua": name, "description_ua": "Опис", "price": price, "stock": stock, "available": avail, "vendor": "V", "vendor_code": "C", "category_name": cat, "dims_cm": list(dims) if dims else None, "weight_kg": None, "pictures": list(pics)}
cat2 = {"1": mk(), "2": mk(cat="Оружие игрушечное"), "3": mk(cat="Книги"), "4": mk(price="100"), "5": mk(stock=2), "6": mk(price="900", stock=10), "7": mk(name="Ніж «Катана»"), "8": mk(avail=False), "9": mk(price="0")}
for k, v in cat2.items():
    v["id"] = k
chk("policy_reason: зброя/ніж → policy, книги → not_toy, звичайна іграшка → None", ex.policy_reason(cat2["2"]) == "policy" and ex.policy_reason(cat2["7"]) == "policy" and ex.policy_reason(cat2["3"]) == "not_toy" and ex.policy_reason(cat2["1"]) is None)
chk("режим pilot → лише пілотні id", ex.select_ids(cat2, ["rt-1"], {"mode": "pilot"}) == ["rt-1"])
full = {"mode": "full", "min_cost": 150, "min_stock": 5, "exclude_not_toys": True}
sel = ex.select_ids(cat2, ["rt-5"], full)
chk("full: пілот завжди (rt-5 з залишком 2), політика/не-іграшки/опт<150/залишок<5/не available/ціна 0 відсіяно; решта за спаданням внеску (rt-6 раніше за rt-1)", sel == ["rt-5", "rt-6", "rt-1"])
chk("full без фільтрів, exclude_not_toys=False: книги лишаються; зброя, ніж, не available (rt-8), ціна 0 (rt-9) — ні", set(ex.select_ids(cat2, [], {"mode": "full", "exclude_not_toys": False})) == {"rt-1", "rt-3", "rt-4", "rt-5", "rt-6"})
with _tf2.TemporaryDirectory() as td2:
    td2 = Path(td2)
    chk("load_selection: нема файлу/сміття/невідомий режим → pilot", ex.load_selection(td2 / "none.json") == {"mode": "pilot"} and (td2 / "b.json").write_text("{x", encoding="utf-8") is not None and ex.load_selection(td2 / "b.json") == {"mode": "pilot"} and (td2 / "c.json").write_text('{"mode":"all"}', encoding="utf-8") is not None and ex.load_selection(td2 / "c.json") == {"mode": "pilot"})
    (td2 / "d.json").write_text(_j2.dumps({"rt-1": "555", "bad": "x"}), encoding="utf-8")
    chk("load_dup_map: лише коректні rt- ключі", ex.load_dup_map(td2 / "d.json") == {"rt-1": "555"} and ex.load_dup_map(td2 / "none.json") == {})
    import threading as _th
    seen = []
    def slow(url, dest):
        seen.append(_th.get_ident())
        return True
    r = ex.prefetch([("u", td2 / f"{n}.jpg") for n in range(20)], slow, workers=4)
    chk("prefetch: усі 20 виконано паралельно (кілька потоків), результат по dest", len(r) == 20 and all(r.values()))
    r0 = ex.prefetch([("u", td2 / "a.jpg"), ("u", td2 / "b.jpg")], lambda u, d: True, workers=1, budget_s=-1)
    chk("prefetch: бюджет часу вичерпано → недокачане False (не падає)", r0 == {td2 / "a.jpg": False, td2 / "b.jpg": False})
    out3 = td2 / "o.json"
    ex.run_once({"1": mk(), "2": mk(cat="Оружие")}, None, out3, lambda u, d: True, td2 / "img", selection={"mode": "full", "min_cost": 150, "min_stock": 5, "exclude_not_toys": True}, dup_map={"rt-1": "555"})
    # ids=None → select_ids з load_pilot_ids() (234 id із файлу) + режим full: у каталозі лише 1 і 2 → rt-1 з full, пілотні відсутні в YML пропущено
    d3 = _j2.loads(out3.read_text(encoding="utf-8"))["items"]
    chk("run_once full: rt-1 опубліковано з dup_of, зброя (rt-2) — ні", list(d3) == ["rt-1"] and d3["rt-1"]["dup_of"] == "555")
    out4 = td2 / "o4.json"
    cnt = {"n": 0}
    def fl(u, d):
        cnt["n"] += 1
        return cnt["n"] % 2 == 1
    ex.run_once({"1": mk(pics=("a", "b", "c", "d")), "2": mk(pics=("a",))}, ["rt-1", "rt-2"], out4, fl, td2 / "img", workers=1)
    d4 = _j2.loads(out4.read_text(encoding="utf-8"))["items"]
    chk("не більше 3 фото на картку (MAX_IMAGES); картка без жодного фото цього разу не публікується (pending)", len(d4["rt-1"]["images"]) <= 3 and ex.MAX_IMAGES == 3)

# --- зауваження аудиту #866 ---
def pv(name, cat="Игровые наборы", ru=None):
    d = {"id": "1", "name_ua": name, "category_name": cat}
    if ru:
        d["name"] = ru
    return ex.policy_reason(d)
chk("політика: «зброєю», «шпаги», «рогатка», «бластер», «Покерный набір», «для дорослих» → policy", all(pv(n) == "policy" for n in ("Ігровий набір зі зброєю М18-3", "Мушкетерський набір (2 шпаги)", "Рогатка дитяча", "Бластер водяний", "Покер набір", "Гра для дорослих")))
chk("політика: «Ножка/подножка» (меблі, мольберт, самокат) і «шпагат» НЕ policy; «нож», «ножи», «шпага» — policy", all(pv(n) is None for n in ("Крісло з підніжкою", "Мольберт на ніжках", "Шпагат декоративний")) and pv("Набір", ru="Подножка для кресла") is None and pv("Набір", ru="Игрушечный нож") == "policy" and pv("Набір", ru="Набор ножи") == "policy" and pv("Шпага дитяча") == "policy")
chk("політика: перевіряється й рос. назва (укр. безпечна, рос. — «пистолет») → policy", pv("Набір", ru="Пистолет игрушечный") == "policy")
chk("«не іграшки» за назвою лише явні слова: «Кошик з ручками» і «Круг з ручками» — НЕ not_toy; «Зошит» — not_toy; за категорією «Книги» — not_toy", pv("Кошик з ручками") is None and pv("Круг з ручками") is None and pv("Зошит у клітинку") == "not_toy" and pv("Казка", cat="Книги") == "not_toy")
chk("звичайна іграшка («Конструктор», «Лялька») → None", pv("Конструктор 100 деталей", cat="Конструкторы") is None and pv("Лялька Маша", cat="Куклы") is None)
with _tf2.TemporaryDirectory() as td5:
    td5 = Path(td5)
    # prefetch: фото на диску — True навіть при вичерпаному бюджеті; виняток у fetch → False
    (td5 / "x.jpg").write_bytes(b"1")
    def boom(u, d):
        raise RuntimeError("мережа")
    r5 = ex.prefetch([("u", td5 / "x.jpg"), ("u", td5 / "y.jpg")], boom, workers=2, budget_s=-1)
    chk("prefetch: наявне фото True при бюджеті вичерпаному; нове — False", r5 == {td5 / "x.jpg": True, td5 / "y.jpg": False})
    r6 = ex.prefetch([("u", td5 / "z.jpg")], boom, workers=1)
    chk("prefetch: виняток у fetch не валить прогін → False", r6 == {td5 / "z.jpg": False})
    # гард усадки
    big = td5 / "big.json"
    big.write_text(_j2.dumps({"catalog_n": 10, "items": {}}), encoding="utf-8")
    chk("гард усадки каталогу: YML 4 з 10 (<50%) → не перезаписувати; 6 з 10 → можна; нема файлу / нема catalog_n → можна", ex.out_guard_ok(big, 4) is False and ex.out_guard_ok(big, 6) is True and ex.out_guard_ok(td5 / "none.json", 0) is True)
    (td5 / "old.json").write_text(_j2.dumps({"items": {f"rt-{n}": {} for n in range(3000)}}), encoding="utf-8")
    chk("звуження ВИХОДУ (3000 → 197 карток при повному YML) гардом не блокується: порівнюється розмір каталогу, а не виходу", ex.out_guard_ok(td5 / "old.json", 18000) is True)
    chk("catalog_n записується в supplier_rt.json", (lambda f: (ex.run_once({"1": mk()}, ["rt-1"], f, lambda u, d: True, td5 / "imgc"), _j2.loads(f.read_text(encoding="utf-8")).get("catalog_n"))[1])(td5 / "oc.json") == 1)
    # мапа категорій: картка з невідомою категорією — фото не качаємо
    calls5 = []
    def f5(u, d):
        calls5.append(d.name)
        return True
    cat5 = {"1": mk(cat="Известная"), "2": mk(cat="Невідома")}
    for k, v in cat5.items():
        v["id"] = k
    o5 = td5 / "o5.json"
    ex.run_once(cat5, ["rt-1", "rt-2"], o5, f5, td5 / "img", category_keys={"Известная"})
    d5 = _j2.loads(o5.read_text(encoding="utf-8"))["items"]
    chk("category_keys: rt-2 (категорія без мапи) не публікується і фото для неї не качаємо", list(d5) == ["rt-1"] and all(n.startswith("rt-1-") for n in calls5))
    # проміжний запис: фото вже на диску → файл пишеться ДО докачування
    img6 = td5 / "img6"
    img6.mkdir()
    (img6 / "rt-1-1.jpg").write_bytes(b"x")
    o6 = td5 / "o6.json"
    seen6 = {}
    def f6(u, d):
        seen6["file_exists_before_download"] = o6.exists()
        return True
    ex.run_once({"1": mk(pics=("a",)), "2": mk(pics=("b",))}, ["rt-1", "rt-2"], o6, f6, img6)
    chk("проміжний запис: файл з уже готовими картками з'являється ДО докачування решти", seen6.get("file_exists_before_download") is True and set(_j2.loads(o6.read_text(encoding="utf-8"))["items"]) == {"rt-1", "rt-2"})
    svc = (Path(__file__).parent / "rt-supplier-export.service").read_text(encoding="utf-8")
    m = re.search(r"TimeoutStartSec=(\d+)", svc)
    chk("юніт: TimeoutStartSec > бюджет фото + 30 с запити + запас (аудит #866)", m and int(m.group(1)) >= ex.PREFETCH_BUDGET_S * 2)
chk("російські літери: опис (ы э ъ ё) → порожній опис, картка лишається; російська «укр.» назва → картку не публікуємо", ex.build_item("rt-1", dict(V, description_ua="Набор посуды из пластика"), ["u"])["description"] == "" and ex.build_item("rt-1", dict(V, name_ua="Набор посуды"), ["u"]) is None and ex.build_item("rt-1", dict(V, name_ua="Набір посуду", description_ua="Опис українською"), ["u"])["description"] == "Опис українською")
# --- поле index (SEO: нові rt-картки index:false, пілот true) ---
with _tf2.TemporaryDirectory() as td7:
    td7 = Path(td7)
    ids_f = td7 / "ix.json"
    ids_f.write_text(_j2.dumps({"ids": ["rt-2", "bad", "rt-../x"]}), encoding="utf-8")
    chk("load_index_ids: лише коректні rt- id; нема файлу → порожньо", ex.load_index_ids(ids_f) == {"rt-2"} and ex.load_index_ids(td7 / "none.json") == set())
    cat7 = {"1": mk(), "2": mk(), "3": mk()}
    for k, v in cat7.items():
        v["id"] = k
    real_pilot, real_idx = ex.load_pilot_ids, ex.load_index_ids
    ex.load_pilot_ids = lambda: ["rt-1"]
    ex.load_index_ids = lambda: {"rt-2"}
    o7 = td7 / "o7.json"
    ex.run_once(cat7, None, o7, lambda u, d: True, td7 / "img", selection={"mode": "full", "min_cost": None, "min_stock": None, "exclude_not_toys": False})
    ex.load_pilot_ids, ex.load_index_ids = real_pilot, real_idx
    d7 = _j2.loads(o7.read_text(encoding="utf-8"))["items"]
    chk("index: пілот (rt-1) true, id із rt_index_ids.json (rt-2) true, решта (rt-3) false", d7["rt-1"]["index"] is True and d7["rt-2"]["index"] is True and d7["rt-3"]["index"] is False)
    o8 = td7 / "o8.json"
    ex.run_once(cat7, ["rt-3"], o8, lambda u, d: True, td7 / "img")
    chk("явний список id (ручний запуск/тести) → index true (як було)", _j2.loads(o8.read_text(encoding="utf-8"))["items"]["rt-3"]["index"] is True)
chk("rt_selection.json = рішення власника 19.10: mode full, без мінімальної опт-ціни (дешеві теж), залишок ≥3, «не іграшки» включено", (lambda d: d == {"mode": "full", "min_cost": None, "min_stock": 3, "exclude_not_toys": False})(_j2.loads((Path(__file__).parent / "rt_selection.json").read_text(encoding="utf-8"))))
chk("ціна сайту не нижча за опт+еквайринг+15: мінімум внеску ≥ 15 ₴ для будь-якого опту (формула власника, зворотна ТТН не в ціні)", all(ex.site_price(c) - c - ex.site_price(c) * 0.028 >= 15.0 for c in (0.49, 1, 5, 9.9, 10, 20, 33.3, 33.4, 50, 99.99, 150, 1000)))
chk("російські СЛОВА в name_ua (без літер ы/э): «Набор для проведения раскопок», «Ламинированное расписание», «размер» → картку не публікуємо", all(ex.build_item("rt-1", dict(V, name_ua=n), ["u"]) is None for n in ("Набор для проведения раскопок 7576DT", "Ламинированное расписание уроков", "Игрушка Мишка", "Детский набор", "Футболка размер 40")))
chk("українське НЕ ловиться: «в наборі», «набором», «Дитячі розвиваючі кубики, 9 шт. в наборі», «Комплект креативної творчості», «Зелений», «Коричневий»", all(ex.build_item("rt-1", dict(V, name_ua=n), ["u"]) is not None for n in ("Кубики 9 шт. в наборі", "Дитячі розвиваючі кубики, 9 шт. в наборі", "Комплект креативної творчості", "Каталка Зелений", "Іграшка коричневий")))
chk("опис з російськими словами → порожній (картка лишається)", ex.build_item("rt-1", dict(V, description_ua="Набор для проведения раскопок"), ["u"])["description"] == "")
chk("мовний маркер (власник 20.10): «рус.», «рос.», «російською мовою», «російськомовна», «рос. звучення» → картку не публікуємо; двомовні «рос. і укр.» лишаються; «Росомаха/Ростов» не ловляться", all(ex.build_item("rt-1", dict(V, name_ua=n), ["u"]) is None for n in ("Книга рус.", "Сортер Bambi рос. звучення", "Гра російською мовою", "Російськомовна книга", "Пазл рос")) and all(ex.build_item("rt-1", dict(V, name_ua=n), ["u"]) is not None for n in ("Гра рос. і укр. мовами", "Книга рус. та українська", "Росомаха м'яка іграшка", "Конструктор Ростов")))
print("\nFAIL:" if F else "\nOK: усі перевірки пройдені", *F, sep="\n")
sys.exit(1 if F else 0)

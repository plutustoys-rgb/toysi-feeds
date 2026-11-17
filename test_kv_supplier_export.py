#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_kv_supplier_export.py — експорт даних KIDDISVIT для сайту (17.11.2026). Інваріанти: (1) ціна сайту за ЗАКРИТОЮ формулою (rt_supplier_export.site_price), cost = clientPrice із API; (2) залишок = лише склад Dnepr, «101» → кап 100, NTV20/NTV30 не рахуються;
(3) без фото / назви (рос.) / ціни / габаритів → картки нема; габарити з properties «в упаковці»; (4) відбір: мін. залишок, бренди-не-іграшки (kv_selection.json), політика (зброя); (5) порожні дані API / збій → попередній файл НЕ чіпаємо; гард усадки каталогу;
(6) файл приватний, атомарний, ключі kv-<id>, index=false без kv_index_ids; (7) без ключа нічого не робимо; (8) stock_snapshot-контракт: «updated» ISO UTC."""
import json
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).parent
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, str(HERE))
import kv_supplier_export as ex  # noqa: E402
import kiddisvit_api as api  # noqa: E402
import stock_snapshot as ss  # noqa: E402
from datetime import timedelta  # noqa: E402

F = []


def chk(n, c):
    print(f"[{'OK ' if c else 'FAIL'}] {n}")
    if not c:
        F.append(n)


PROPS = [{"name": "Розмір іграшки в упаковці довжина", "value": "15,9"}, {"name": "Розмір іграшки в упаковці ширина", "value": "11.5"}, {"name": "Розмір іграшки в упаковці висота", "value": "8,8"},
         {"name": "Розмір іграшки без упаковки висота", "value": "99"}]
CATS = {"root": {"id": "root", "parentID": None, "name": "Ігри"}, "leaf": {"id": "leaf", "parentID": "root", "name": "Фігурки"}}


def P(pid="100", **kw):
    d = {"productID": pid, "name": "Ігрова фігурка Funko POP! - Гаві", "description": "<p>Опис &amp; текст</p>", "brand_name": "Funko", "brand_id": "XY1", "barcode": "89 300-07", "sku": "72235", "price": "945", "url": "u", "category_id": "leaf", "images": ["https://i/1.jpg", "https://i/2.jpg"], "properties": PROPS}
    d.update(kw)
    return d


def R(pid="100", cost=700.02, rest=None, **kw):
    d = {"ProductID": pid, "clientPrice": cost, "retailPrice": 945, "rest": rest if rest is not None else [{"WarehouseID": "Dnepr", "Rest": 7}]}
    d.update(kw)
    return d


chk("габарити з properties «в упаковці» (кома/крапка), «без упаковки» ігноруються → [15.9, 11.5, 8.8]", ex.dims_from_properties(PROPS) == [15.9, 11.5, 8.8])
chk("габарити: бракує виміру / нуль / >200 см / сміття → None", all(ex.dims_from_properties(p) is None for p in (PROPS[:2], [dict(PROPS[0], value="0")] + PROPS[1:3], [dict(PROPS[0], value="500")] + PROPS[1:3], [dict(PROPS[0], value="abc")] + PROPS[1:3], None, [])))
chk("залишок: лише Dnepr; 101 → кап 100; NTV20/NTV30 не рахуються; сміття ігнорується", ex.stock_of([{"WarehouseID": "Dnepr", "Rest": 7}, {"WarehouseID": "NTV20", "Rest": 50}, {"WarehouseID": "NTV30", "Rest": 9}]) == 7 and ex.stock_of([{"WarehouseID": "Dnepr", "Rest": 101}]) == 100 and ex.stock_of([{"WarehouseID": "NTV20", "Rest": 50}]) == 0 and ex.stock_of(None) == 0 and ex.stock_of([{"WarehouseID": "Dnepr", "Rest": "x"}, "bad"]) == 0)
chk("шлях категорії від кореня до листка; цикл не вішає", ex.category_path("leaf", CATS) == ["Ігри", "Фігурки"] and ex.category_path("x", CATS) == [] and ex.category_path("a", {"a": {"id": "a", "parentID": "b", "name": "A"}, "b": {"id": "b", "parentID": "a", "name": "B"}}) == ["B", "A"])
it = ex.build_item("100", P(), R(), CATS, ["https://plutustoys.com.ua/img/kv/kv-100-1.jpg"])
chk("елемент: cost = clientPrice, ціна за закритою формулою 700,02 → ceil(1050,03)=1051, rrp, штрихкод лише цифри, категорія-листок", it["cost"] == 700.02 and it["price"] == ex.rt.site_price(700.02) == 1051 and it["rrp"] == 945.0 and it["barcode"] == "8930007" and it["category"] == "Фігурки" and it["category_path"] == ["Ігри", "Фігурки"])
chk("опис: теги прибрано; стоки/габарити/вага/продавець у елементі; vendor_code = sku", it["description"] == "Опис & текст" and it["stock"] == 7 and it["dims_cm"] == [15.9, 11.5, 8.8] and it["weight_kg"] is None and it["vendor"] == "Funko" and it["vendor_code"] == "72235")
chk("без фото → None", ex.build_item("100", P(), R(), CATS, []) is None)
chk("без ціни клієнта (None / 0 / текст) → None", all(ex.build_item("100", P(), R(cost=c), CATS, ["u"]) is None for c in (None, 0, "x", -5)))
chk("без габаритів → None (ТТН потребує розмірів)", ex.build_item("100", P(properties=[]), R(), CATS, ["u"]) is None)
chk("російська назва (ы/э або слова) → None; опис із російськими словами → порожній, картка лишається", all(ex.build_item("100", P(name=n), R(), CATS, ["u"]) is None for n in ("Игрушка Мишка", "Детский набор", "Фигурка Эльза")) and ex.build_item("100", P(description="Набор для проведения раскопок"), R(), CATS, ["u"])["description"] == "")
chk("українське не ловиться: «в наборі», «Зелений»", all(ex.build_item("100", P(name=n), R(), CATS, ["u"]) is not None for n in ("Кубики 9 шт. в наборі", "Каталка Зелений")))

prods = {"1": P("1"), "2": P("2", brand_name="Goo.N Plus"), "3": P("3"), "4": P("4"), "5": P("5", name="Іграшковий пістолет з пістонами")}
prices = {"1": R("1"), "2": R("2"), "3": R("3", rest=[{"WarehouseID": "Dnepr", "Rest": 2}]), "4": R("4", rest=[{"WarehouseID": "NTV20", "Rest": 80}]), "5": R("5")}
sel = ex.select_ids(prods, prices, CATS, ex.DEFAULT_SELECTION)
chk("відбір: пройшов лише kv-1 (kv-2 підгузки-бренд; kv-3 залишок 2 < 3; kv-4 лише NTV; kv-5 зброя/політика)", sel == ["kv-1"])
chk("відбір: без вимоги залишку/мін. ціни kv_selection → kv-3 теж", "kv-3" in ex.select_ids(prods, prices, CATS, {"min_stock": 0, "exclude_brands": []}) and "kv-2" in ex.select_ids(prods, prices, CATS, {"min_stock": 0, "exclude_brands": []}))
chk("відбір: min_cost відсікає дешеве", ex.select_ids(prods, prices, CATS, {"min_stock": 1, "min_cost": 800, "exclude_brands": []}) == [])
chk("kv_selection.json: нема/битий файл → типові (мін. залишок 3, підгузки виключені)", ex.load_selection(Path("/nonexistent.json")) == ex.DEFAULT_SELECTION)

fetched = []


def fetch_ok(url, dest):
    fetched.append(str(dest))
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(b"jpg")
    return True


NOW = datetime(2026, 11, 17, 12, 0, tzinfo=timezone.utc)
with tempfile.TemporaryDirectory() as td:
    td = Path(td)
    out = td / "supplier_kv.json"
    ok = ex.run_once(collected=(prods, prices, CATS), out=out, fetch=fetch_ok, img_dir=td / "img", now=NOW, selection=ex.DEFAULT_SELECTION, index_ids=set())
    d = json.loads(out.read_text(encoding="utf-8"))
    chk("run_once: файл записано; ключ kv-1; фото kv-1-1/-2 на диску; index=false за замовчуванням; updated ISO UTC; catalog_n = розмір каталогу", ok and list(d["items"]) == ["kv-1"] and (td / "img" / "kv-1-1.jpg").exists() and (td / "img" / "kv-1-2.jpg").exists() and d["items"]["kv-1"]["index"] is False and d["updated"].startswith("2026-11-17T12:00:00") and d["catalog_n"] == 5)
    chk("фото ≤ MAX_IMAGES; URL фото на нашому сервері, не KIDDISVIT", len(d["items"]["kv-1"]["images"]) == 2 and all(u.startswith("https://plutustoys.com.ua/img/kv/") for u in d["items"]["kv-1"]["images"]))
    chk("файл приватний: поза site/", "site" not in ex.OUT_FILE.parts)
    ok2 = ex.run_once(collected=(prods, prices, CATS), out=out, fetch=fetch_ok, img_dir=td / "img", now=NOW, selection=ex.DEFAULT_SELECTION, index_ids={"kv-1"})
    chk("index_ids → index=true", ok2 and json.loads(out.read_text(encoding="utf-8"))["items"]["kv-1"]["index"] is True)
    before = out.read_bytes()
    chk("порожні товари/ціни API → False, попередній файл не змінено", not ex.run_once(collected=({}, {}, CATS), out=out, fetch=fetch_ok, img_dir=td / "img") and out.read_bytes() == before)
    chk("гард усадки: каталог 2 позиції проти 5 раніше (<50 %) → False, файл не змінено", not ex.run_once(collected=({"1": P("1"), "9": P("9")}, {"1": R("1"), "9": R("9")}, CATS), out=out, fetch=fetch_ok, img_dir=td / "img") and out.read_bytes() == before)
    out3 = td / "o3.json"
    chk("фото не завантажились → жодної картки → False, файл не створено", not ex.run_once(collected=(prods, prices, CATS), out=out3, fetch=lambda u, d: False, img_dir=td / "img3", now=NOW, selection=ex.DEFAULT_SELECTION, index_ids=set()) and not out3.exists())

    class NoKey(api.KiddisvitClient):
        def __init__(self):
            super().__init__(key="")

    chk("без ключа API → False, нічого не пишемо", not ex.run_once(client=NoKey(), out=td / "o4.json", img_dir=td / "img4") and not (td / "o4.json").exists())

    class Boom(api.KiddisvitClient):
        def __init__(self):
            super().__init__(key="K")

        def clients_list(self):
            raise api.KiddisvitError("GET clients/list: HTTP 403")

    chk("збій API → False, файл не чіпаємо", not ex.run_once(client=Boom(), out=out, img_dir=td / "img5") and out.read_bytes() == before)

    class Fake(api.KiddisvitClient):
        def __init__(self):
            super().__init__(key="K")
            self.price_calls = []

        def clients_list(self):
            return {"clients": [{"ClientID": "CL", "ContractID": "CT"}]}

        def products(self, *a, **k):
            return [P("1", brand_id="B1"), P("2", brand_id="B2"), P("3", brand_id="NOPE")]

        def categories(self, *a, **k):
            return list(CATS.values())

        def brands(self):
            return [{"ID": "g1", "Name": "Funko", "brand_id": "B1"}, {"ID": "g2", "Name": "Other", "brand_id": "B2"}]

        def prices_by_brand(self, c, k, b):
            self.price_calls.append((c, k, b))
            return {"g1": [R("1")], "g2": [R("2", cost=50)]}[b]

    fk = Fake()
    products, pr, cs = ex.collect(fk)
    chk("collect: ціни по брендах через catalog/brands (за brand_id-кодом, не за назвою); бренд без відповідника пропущено; по одному запиту на бренд", sorted(pr) == ["1", "2"] and sorted(fk.price_calls) == [("CL", "CT", "g1"), ("CL", "CT", "g2")] and set(products) == {"1", "2", "3"} and "leaf" in cs)

with tempfile.TemporaryDirectory() as td2:
    td2 = Path(td2)
    ok_f = td2 / "kv.json"
    ok_f.write_text(json.dumps({"updated": NOW.isoformat(), "items": {"kv-1": {"stock": 7}, "kv-2": {"stock": "x"}, "kv-3": {"stock": -4}}}), encoding="utf-8")
    chk("stock_snapshot: kv_stock_entries → {kv-1: 7, kv-3: 0}; нечислове пропущено", ss.kv_stock_entries(ok_f, NOW) == {"kv-1": 7, "kv-3": 0})
    chk("stock_snapshot: файл старший за 3 год → {} (fail-closed)", ss.kv_stock_entries(ok_f, NOW + timedelta(hours=3, minutes=1)) == {})
    chk("stock_snapshot: нема файлу / сміття → {}", ss.kv_stock_entries(td2 / "none.json", NOW) == {} and ss.kv_stock_entries(__file__, NOW) == {})
    chk("stock_snapshot: rt_stock_entries поводиться як раніше (спільний хелпер)", ss.rt_stock_entries(ok_f, NOW) == {"kv-1": 7, "kv-3": 0})

# аудит #1691: покриття цінами, не-іграшки, регістр складу, нестандартні числа, збій генерує сигнал
alerts = []
_real_alert = ex.rt._alert
ex.rt._alert = lambda k, t: alerts.append(k)
with tempfile.TemporaryDirectory() as td3:
    td3 = Path(td3)
    prods200 = {str(i): P(str(i)) for i in range(1, 201)}
    full = {str(i): R(str(i)) for i in range(1, 201)}
    o9 = td3 / "o9.json"
    chk("повне покриття цінами → файл записано, priced_n = 200 і catalog_n = 200", ex.run_once(collected=(prods200, full, CATS), out=o9, fetch=fetch_ok, img_dir=td3 / "img", now=NOW, selection=ex.DEFAULT_SELECTION, index_ids=set()) and json.loads(o9.read_text(encoding="utf-8"))["priced_n"] == 200)
    before9 = o9.read_bytes()
    alerts.clear()
    part = {str(i): R(str(i)) for i in range(1, 21)}          # ціни прийшли лише для 10 % (збій решти запитів по брендах)
    chk("ціни лише для 10 % каталогу → False, файл не змінено, сигнал kv_export_coverage", not ex.run_once(collected=(prods200, part, CATS), out=o9, fetch=fetch_ok, img_dir=td3 / "img", now=NOW, selection=ex.DEFAULT_SELECTION, index_ids=set()) and o9.read_bytes() == before9 and "kv_export_coverage" in alerts)
    chk("покриття 85 % (понад поріг 80 %) → законно, записує", ex.run_once(collected=(prods200, {str(i): R(str(i)) for i in range(1, 171)}, CATS), out=td3 / "o10.json", fetch=fetch_ok, img_dir=td3 / "img", now=NOW, selection=ex.DEFAULT_SELECTION, index_ids=set()))
    alerts.clear()
    half = {str(i): R(str(i)) for i in range(1, 201)}
    for i in range(1, 201):
        if i > 90:
            half[str(i)] = R(str(i), cost=None)                 # каталог цілий, але цін клієнта лише 90 з 200 (45 %) < 80 %
    chk("45 % покриття → False (поріг покриття)", not ex.run_once(collected=(prods200, half, CATS), out=td3 / "o11.json", fetch=fetch_ok, img_dir=td3 / "img", now=NOW, selection=ex.DEFAULT_SELECTION, index_ids=set()) and "kv_export_coverage" in alerts)
    alerts.clear()

    class Crash(api.KiddisvitClient):
        def __init__(self):
            super().__init__(key="K")

        def clients_list(self):
            raise ValueError("несподіване")

    chk("неочікуваний збій коду → False + сигнал kv_export_crash", not ex.run_once(client=Crash(), out=td3 / "o12.json", img_dir=td3 / "img") and "kv_export_crash" in alerts)
ex.rt._alert = _real_alert

chk("залишок: «7.0» і склад у іншому регістрі («dnepr») рахуються; «101» = 100", ex.stock_of([{"WarehouseID": "dnepr", "Rest": "7.0"}]) == 7 and ex.stock_of([{"WarehouseID": "Dnepr", "Rest": 101}]) == 100)
chk("не-іграшки (зошит/гантелі за політикою rt.policy_reason) виключаються за замовчуванням і повертаються при exclude_not_toys=false", (lambda a, b: ("kv-9" not in a) and ("kv-9" in b))(
    ex.select_ids({"9": P("9", name="Зошит у клітинку")}, {"9": R("9")}, CATS, ex.DEFAULT_SELECTION),
    ex.select_ids({"9": P("9", name="Зошит у клітинку")}, {"9": R("9")}, CATS, dict(ex.DEFAULT_SELECTION, exclude_not_toys=False))))
chk("сміття в min_stock (рядок) не валить відбір, діє типове", ex.select_ids({"1": P("1")}, {"1": R("1")}, CATS, {"min_stock": "abc", "exclude_brands": []}) == ["kv-1"])
import subprocess  # noqa: E402
env0 = {k: v for k, v in __import__("os").environ.items() if k != "KIDDISVIT_API_KEY"}
r0 = subprocess.run([sys.executable, str(HERE / "kv_supplier_export.py")], capture_output=True, env=dict(env0, KIDDISVIT_API_KEY="", SUPPLIER_KV_FILE=str(Path(tempfile.gettempdir()) / "nokey_kv_test.json")), cwd=str(HERE), timeout=60)
chk("запуск без ключа → код 0 («не налаштовано»), а не щогодинний збій юніта", r0.returncode == 0)


print("\nFAIL:" if F else "\nOK: усі перевірки пройдені", *F, sep="\n")
sys.exit(1 if F else 0)

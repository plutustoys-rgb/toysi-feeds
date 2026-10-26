#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_feed_licensed_gate.py — тимчасовий гейт ліцензійних марок у рекламних фідах Google/Meta/Bing (generate_google_feed.build_feed_items).

Рішення власника 26.10.2026 (через Консультанта): «поки прибрати бренди з фіда». ІНВАРІАНТИ:
  - гейт типово УВІМКНЕНО; FEED_EXCLUDE_LICENSED=0 вимикає (оборотність без релізу), решта фільтрів не міняється;
  - перелік читається ФАЙЛОМ (групи → регулярки; ключі з «_» і не-списки ігноруються, напр. `_автомарки_окремо`); нечитабельний/порожній файл при ввімкненому гейті → RuntimeError (fail-closed);
  - межі слів у переліку працюють: «гімнастична» не збігається зі «стич»;
  - лічильник stats["licensed_excluded"] веде облік; сусідній звичайний товар лишається; Meta/Bing використовують той самий build_feed_items;
  - файл у репо (feed_licensed_marks.json) валідний і ловить відомі приклади;
  - `trademark_filter.py` НЕ змінено (його імпортують сайт і акція — вилучення з сайту недопустиме).
`python test_feed_licensed_gate.py` → exit 0/1.
"""
import json
import os
import sys
import tempfile
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
os.environ["AUDIT_NO_TELEGRAM"] = "1"
sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent / "site"))

import generate_google_feed as gg  # noqa: E402

F = []


def chk(n, c):
    print(f"[{'OK ' if c else 'FAIL'}] {n}")
    if not c:
        F.append(n)


def item(pid, name):
    return {"id": pid, "name": name, "vendor": "Test", "price": 200.0, "stock": 5, "category_name": "Різне", "category_id": "1",
            "pictures": ["https://toysi.ua/p/enl_%s.jpg" % pid], "description": "Опис товару достатньої довжини для фіда.", "barcode": ""}


def ids(cat):
    items, st = gg.build_feed_items(cat, {}, {}, {}, None, None)
    return {i["pid"] for i in items}, st


tmp = Path(tempfile.mkdtemp(prefix="lic_gate_"))
marks = tmp / "marks.json"
marks.write_text(json.dumps({
    "_що це": "службове", "_автомарки_окремо": {"форми": [r"\bbmw\b"]}, "_не_включено_свідомо": {"трансформер": "родове"},
    "Disney": [r"\bхолодне серце\b", r"\bстич\b"], "Marvel": [r"\bлюдина[- ]павук\w*"],
}, ensure_ascii=False), encoding="utf-8")
os.environ["FEED_LICENSED_MARKS_FILE"] = str(marks)
gg.FEED_LICENSED_MARKS_FILE = str(marks)
os.environ.pop("FEED_EXCLUDE_LICENSED", None)

cat = {"1": item("1", 'Пазли 3в1 "Холодне серце"'), "2": item("2", "Фігурка Людина-павук 15 см"), "3": item("3", "Гімнастична палиця"),
       "4": item("4", "Машинка BMW X6"), "5": item("5", "Конструктор «Дитяче місто»")}

chk("гейт типово УВІМКНЕНО", gg.licensed_gate_on() is True)
got, st = ids(cat)
chk("ліцензійні (Холодне серце, Людина-павук) прибрано; звичайні, «Гімнастична» (межа слова) і автомарка BMW (службовий ключ ігнорується) лишились", got == {"3", "4", "5"})
chk("лічильник licensed_excluded = 2", st.get("licensed_excluded") == 2)

os.environ["FEED_EXCLUDE_LICENSED"] = "0"
got0, st0 = ids(cat)
chk("FEED_EXCLUDE_LICENSED=0 → гейт вимкнено, у фіді всі 5, лічильник 0", got0 == {"1", "2", "3", "4", "5"} and st0.get("licensed_excluded") == 0)
os.environ["FEED_EXCLUDE_LICENSED"] = "1"
chk("FEED_EXCLUDE_LICENSED=1 → гейт увімкнено", gg.licensed_gate_on() is True and ids(cat)[0] == {"3", "4", "5"})
os.environ.pop("FEED_EXCLUDE_LICENSED", None)

# fail-closed
for label, content in (("порожній перелік", json.dumps({"_x": "y"})), ("зламаний JSON", "{не json"), ("неправильна регулярка", json.dumps({"G": ["(непарна"]}))):
    bad = tmp / "bad.json"
    bad.write_text(content, encoding="utf-8")
    gg.FEED_LICENSED_MARKS_FILE = str(bad)
    try:
        gg.build_feed_items(cat, {}, {}, {}, None, None)
        failed_closed = False
    except RuntimeError:
        failed_closed = True
    chk(f"{label} при ввімкненому гейті → RuntimeError (fail-closed)", failed_closed)
gg.FEED_LICENSED_MARKS_FILE = str(tmp / "no_such.json")
try:
    gg.build_feed_items(cat, {}, {}, {}, None, None)
    nf = False
except RuntimeError:
    nf = True
chk("файлу нема при ввімкненому гейті → RuntimeError", nf)
os.environ["FEED_EXCLUDE_LICENSED"] = "0"
chk("при вимкненому гейті зламаний/відсутній файл не заважає збірці фіду", len(ids(cat)[0]) == 5)
os.environ.pop("FEED_EXCLUDE_LICENSED", None)

# контракт SEO (в.4): групи СТРОГО за `_контракт_для_коду.застосовувати`; вкладені робочі регекси автомарок і службові ключі не збираються
contract_file = tmp / "contract.json"
contract_file.write_text(json.dumps({
    "_контракт_для_коду": {"застосовувати": ["Disney"], "НЕ застосовувати": ["Автомарки"]},
    "Disney": [r"\bхолодне серце\b"], "Marvel": [r"\bлюдина[- ]павук\w*"],
    "_автомарки_окремо": {"форми": [r"\bbmw\b"]},
}, ensure_ascii=False), encoding="utf-8")
rxc = gg.load_licensed_marks_regex(str(contract_file))
chk("контракт: збирається лише перелічена група (Disney), а Marvel (поза контрактом) і вкладені автомарки — ні",
    bool(rxc.search("Холодне серце")) and not rxc.search("Людина-павук") and not rxc.search("BMW X6"))
bad_contract = tmp / "bad_contract.json"
bad_contract.write_text(json.dumps({"_контракт_для_коду": {"застосовувати": ["Нема такої"]}, "Disney": [r"\bx\b"]}, ensure_ascii=False), encoding="utf-8")
try:
    gg.load_licensed_marks_regex(str(bad_contract))
    bc = False
except RuntimeError:
    bc = True
chk("контракт посилається на відсутню групу → RuntimeError (fail-closed)", bc)

# файл у репо
gg.FEED_LICENSED_MARKS_FILE = str(Path(__file__).parent / "feed_licensed_marks.json")
repo_doc = json.loads((Path(__file__).parent / "feed_licensed_marks.json").read_text(encoding="utf-8"))
chk("файл у репо — в.6+ з контрактом, 8 груп, 132 форми", int(str(repo_doc.get("_версія", "0")).split()[0]) >= 6 and len(repo_doc["_контракт_для_коду"]["застосовувати"]) == 8
    and sum(len(repo_doc[g]) for g in repo_doc["_контракт_для_коду"]["застосовувати"]) == 132)
rx = gg.load_licensed_marks_regex()
chk("regex із файла в репо не ловить автомарки (діекаст не вилучається): BMW, Ferrari, Toyota, Mustang, Mercedes",
    not any(rx.search(s) for s in ("Машинка BMW X6", "Металева Ferrari", "Toyota Land Cruiser", "Ford Mustang", "Mercedes-Benz G-Class")))
chk("feed_licensed_marks.json у репо валідний; ловить «Людина-павук», «Холодне серце», «Стіч», «Barbie»",
    all(rx.search(s) for s in ("Фігурка Людина-павук", 'Пазли "Холодне серце"', "Бомбочка для ванни «Стіч»", "Лялька Barbie")))
chk("не ловить звичайні: «Гімнастична палиця», «Реалістична собака», «Еластична стрічка», «Мелодії для малюків»",
    not any(rx.search(s) for s in ("Гімнастична палиця", "Реалістична собака", "Еластична стрічка", "Мелодії для малюків")))
chk("не ловить службовий блок автомарок (BMW/Mustang лишаються у фідах: окреме рішення)", not rx.search("Машинка металева BMW X6") and not rx.search("Ford Mustang"))

# Meta/Bing — той самий build_feed_items
import generate_meta_feed as gm  # noqa: E402
import generate_bing_feed as gb  # noqa: E402
chk("Meta і Bing викликають gg.build_feed_items (успадковують гейт)", "build_feed_items" in Path(gm.__file__).read_text(encoding="utf-8") and "build_feed_items" in Path(gb.__file__).read_text(encoding="utf-8"))
chk("trademark_filter.py не знає про ліцензійний перелік (сайт/акція не зачеплені)", "licensed" not in Path(__file__).parent.joinpath("trademark_filter.py").read_text(encoding="utf-8").lower())

sys.exit(1 if F else 0)

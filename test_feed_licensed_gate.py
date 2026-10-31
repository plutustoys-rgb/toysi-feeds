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
os.environ["FEED_EXCLUDE_LICENSED"] = "1"   # з 30.10 типово вимкнено — тести механіки гейта вмикають явно
os.environ["FEED_LICENSED_VENDOR_BLOCKLIST"] = "0"   # з 31.10 чорний список вендорів типово ввімкнено — механіка гейта перевіряється без нього; його тести — в кінці

cat = {"1": item("1", 'Пазли 3в1 "Холодне серце"'), "2": item("2", "Фігурка Людина-павук 15 см"), "3": item("3", "Гімнастична палиця"),
       "4": item("4", "Машинка BMW X6"), "5": item("5", "Конструктор «Дитяче місто»")}

os.environ.pop("FEED_EXCLUDE_LICENSED", None)
chk("гейт ТИПОВО ВИМКНЕНО (30.10, рішення власника «повертай усі бренди у фід»): змінної нема → вимкнено; «1» вмикає; «0»/порожнє — вимкнено", gg.licensed_gate_on() is False)
os.environ["FEED_EXCLUDE_LICENSED"] = "1"
chk("FEED_EXCLUDE_LICENSED=1 вмикає гейт", gg.licensed_gate_on() is True)
os.environ["FEED_EXCLUDE_LICENSED"] = ""
chk("порожнє значення — вимкнено", gg.licensed_gate_on() is False)
os.environ["FEED_EXCLUDE_LICENSED"] = "1"
got, st = ids(cat)
chk("ліцензійні (Холодне серце, Людина-павук) прибрано; звичайні, «Гімнастична» (межа слова) і автомарка BMW (службовий ключ ігнорується) лишились", got == {"3", "4", "5"})
chk("лічильник licensed_excluded = 2", st.get("licensed_excluded") == 2)

os.environ["FEED_EXCLUDE_LICENSED"] = "0"
got0, st0 = ids(cat)
chk("FEED_EXCLUDE_LICENSED=0 → гейт вимкнено, у фіді всі 5, лічильник 0", got0 == {"1", "2", "3", "4", "5"} and st0.get("licensed_excluded") == 0)
os.environ["FEED_EXCLUDE_LICENSED"] = "1"
del os.environ["FEED_EXCLUDE_LICENSED"]
got_def, st_def = ids(cat)
chk("БЕЗ змінної (типовий стан з 30.10): у фіді всі 5 позицій, жодна марка не виключена, лічильник 0", got_def == {"1", "2", "3", "4", "5"} and st_def.get("licensed_excluded") == 0)
os.environ["FEED_EXCLUDE_LICENSED"] = "1"
chk("FEED_EXCLUDE_LICENSED=1 → гейт увімкнено", gg.licensed_gate_on() is True and ids(cat)[0] == {"3", "4", "5"})
os.environ["FEED_EXCLUDE_LICENSED"] = "1"   # з 30.10 типово вимкнено — тести механіки гейта вмикають явно

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
os.environ["FEED_EXCLUDE_LICENSED"] = "1"   # з 30.10 типово вимкнено — тести механіки гейта вмикають явно

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
chk("файл у репо — в.9+ з контрактом, 10 груп, 214 форм", int(str(repo_doc.get("_версія", "0")).split()[0]) >= 9 and len(repo_doc["_контракт_для_коду"]["застосовувати"]) == 10
    and sum(len(repo_doc[g]) for g in repo_doc["_контракт_для_коду"]["застосовувати"]) == 214)
rx = gg.load_licensed_marks_regex()
chk("regex із файла в репо не ловить автомарки (діекаст не вилучається): BMW, Ferrari, Toyota, Mustang, Mercedes",
    not any(rx.search(s) for s in ("Машинка BMW X6", "Металева Ferrari", "Toyota Land Cruiser", "Ford Mustang", "Mercedes-Benz G-Class")))
chk("feed_licensed_marks.json у репо валідний; ловить «Людина-павук», «Холодне серце», «Стіч», «Barbie»",
    all(rx.search(s) for s in ("Фігурка Людина-павук", 'Пазли "Холодне серце"', "Бомбочка для ванни «Стіч»", "Лялька Barbie")))
chk("не ловить звичайні: «Гімнастична палиця», «Реалістична собака», «Еластична стрічка», «Мелодії для малюків»",
    not any(rx.search(s) for s in ("Гімнастична палиця", "Реалістична собака", "Еластична стрічка", "Мелодії для малюків")))
chk("не ловить службовий блок автомарок (BMW/Mustang лишаються у фідах: окреме рішення)", not rx.search("Машинка металева BMW X6") and not rx.search("Ford Mustang"))

# в.9 (27.10, SEO): сім франшиз, що просиділи у фіді (46 позицій), + дрібні виправлення пробілів; хибних на каталозі 0
for _s in ('Конструктор "Pixel Heroes: Squid Game", 431 дет.', 'Пазл "Хот віллс. Швидкісні машинки"', 'Пазли на 1000 елм "Преміум: Рік і Морті"',
           'Подушка м\'яка "Аніме. Наруто" 28 см', "М'яка іграшка Леон (Бравл Старс) 25см", 'Лялька "Крижане серце: Анна"',
           'Пазли - 100 ел, "Трансформери. Команда роботів-трансформерів"', 'Картина за номерами "L. O. L. Surprise! Tweens Skate Dance Ali Dance 2"',
           'Пазли Махі "Спайдер мен та його друзі" (24 ел)', 'Брелок 3d друк "ГубкаБоб (flexy)"', 'Брелок 3d друк "Вендздей"',
           'Картина по номерах "Аніме. Ророноа Зоро. Ван Піс"', 'Книжка-панорамка "Маша і ведмідь" укр', 'Бульбашки мильні «Sponge Bob»'):
    chk(f"в.9: ловить «{_s[:60]}»", bool(rx.search(_s)))
for _s in ('Іграшка антистрес "Кальмар" (РОЖЕВИЙ)', 'Танцюючий кальмар (восьминіг) "КАЛЬМАРІНІ ДЕНСЕРІНІ"', "Пазл «Увесь рік» укр, 100 елементів",
           'Книга "Еґґі Мортон. Небіжчик у садку"', "Іграшка-трансформер Дихаючий заєць", "Новий рік. Набір для творчості"):
    chk(f"в.9: НЕ чіпає родове «{_s[:60]}»", not rx.search(_s))

# --- білий список вендорів з ліцензією (29.10, «коригуй фіди»): марка І вендор не в списку → виключити
import io as _io  # noqa: E402
from contextlib import redirect_stderr  # noqa: E402

def vitem(pid, name, vendor, pictures=True):
    d = item(pid, name)
    d["vendor"] = vendor
    if not pictures:
        d["pictures"] = []
    return d

gg.FEED_LICENSED_MARKS_FILE = str(marks)
wl_repo = Path(__file__).parent / "feed_licensed_vendor_whitelist.json"
gg.FEED_LICENSED_WHITELIST_FILE = str(wl_repo)
os.environ["FEED_EXCLUDE_LICENSED"] = "1"   # з 30.10 типово вимкнено — тести механіки гейта вмикають явно
os.environ.pop("FEED_LICENSED_VENDOR_WHITELIST", None)
chk("вендор-вайтліст типово УВІМКНЕНО", gg.licensed_whitelist_on() is True)
wl_doc = json.loads(wl_repo.read_text(encoding="utf-8-sig"))
chk("файл білого списку в репо: рівно Trefl, GToys, Dodo; у кожного джерело (цитата) і дата", sorted(gg.vendor_key(r["вендор"]) for r in wl_doc["вендори"]) == ["dodo", "gtoys", "trefl"]
    and all(r["джерело"].strip() and r["дата"].strip() for r in wl_doc["вендори"]))
chk("завантажений набір = {trefl, gtoys, dodo}", gg.load_licensed_vendor_whitelist() == frozenset({"trefl", "gtoys", "dodo"}))
chk("vendor_key: «G-Toys» = «Gtoys» = «GTOYS »; «Додо» (кирилиця) ≠ «Dodo»; «YG Toys» ≠ «GToys»",
    gg.vendor_key("G-Toys") == gg.vendor_key("Gtoys") == gg.vendor_key(" GTOYS ") == "gtoys" and gg.vendor_key("Додо") != gg.vendor_key("Dodo") and gg.vendor_key("YG Toys") != gg.vendor_key("GToys"))

wcat = {"1": vitem("1", 'Пазл "Холодне серце"', "Trefl"), "2": vitem("2", 'Пазл "Холодне серце"', "Gtoys"), "3": vitem("3", 'Пазл "Холодне серце"', "Dodo"),
        "4": vitem("4", 'Пазл "Холодне серце"', "MIC"), "5": vitem("5", 'Пазл "Холодне серце"', "YG Toys"), "6": vitem("6", 'Пазл "Холодне серце"', "Додо"),
        "7": vitem("7", 'Пазл "Холодне серце"', "G-Toys"), "8": vitem("8", 'Звичайний пазл', "MIC"), "9": vitem("9", 'Пазл "Холодне серце"', ""),
        "10": vitem("10", 'Лялька Стич', "Trefl", pictures=False)}
got, st = ids(wcat)
chk("білий список: Trefl/Gtoys/Dodo/G-Toys з маркою ПОВЕРНУТО (1,2,3,7); MIC, YG Toys, кирилична «Додо», порожній вендор — лишились виключені", got == {"1", "2", "3", "7", "8"})
chk("лічильники: виключено 4 (MIC, YG Toys, Додо, порожній), пройшло гейт за списком 5, у фіді з них 4 (10 відпав на «без фото»)",
    st["licensed_excluded"] == 4 and st["licensed_whitelisted"] == 5 and st["licensed_returned"] == 4 and st["no_image"] == 1)
chk("звичайна позиція MIC без марки не зачеплена (8 у фіді)", "8" in got)
os.environ["FEED_LICENSED_VENDOR_WHITELIST"] = "0"
got_off, st_off = ids(wcat)
chk("FEED_LICENSED_VENDOR_WHITELIST=0 → як до 29.10: усі марки поза фідом, повернено 0", got_off == {"8"} and st_off["licensed_returned"] == 0 and st_off["licensed_whitelisted"] == 0 and st_off["licensed_excluded"] == 9)
os.environ.pop("FEED_LICENSED_VENDOR_WHITELIST", None)
os.environ["FEED_EXCLUDE_LICENSED"] = "0"
got_g0, st_g0 = ids(wcat)
chk("гейт марок вимкнено (FEED_EXCLUDE_LICENSED=0) → у фіді все з фото, лічильники білого списку 0", len(got_g0) == 9 and st_g0["licensed_whitelisted"] == 0 and st_g0["licensed_returned"] == 0)
os.environ["FEED_EXCLUDE_LICENSED"] = "1"   # з 30.10 типово вимкнено — тести механіки гейта вмикають явно

# збійний / недійсний файл списку → СУВОРО (виключається більше), без винятку
for label, content in (("зламаний JSON", "{не json"), ("запис без джерела", json.dumps({"вендори": [{"вендор": "Trefl", "дата": "2026-10-28"}]})),
                       ("запис без дати", json.dumps({"вендори": [{"вендор": "Trefl", "джерело": "цитата"}]})), ("порожній", json.dumps({"вендори": []}))):
    badwl = tmp / "badwl.json"
    badwl.write_text(content, encoding="utf-8")
    gg.FEED_LICENSED_WHITELIST_FILE = str(badwl)
    buf = _io.StringIO()
    with redirect_stderr(buf):
        g_, s_ = ids(wcat)
    chk(f"{label}: суворий режим — Trefl/Gtoys/Dodo знову виключені, повернено 0, збірка не падає", g_ == {"8"} and s_["licensed_returned"] == 0)
gg.FEED_LICENSED_WHITELIST_FILE = str(tmp / "no_wl.json")
buf = _io.StringIO()
with redirect_stderr(buf):
    g_, s_ = ids(wcat)
chk("файлу списку нема → суворий режим + попередження в stderr", g_ == {"8"} and "СУВОРИЙ режим" in buf.getvalue())
gg.FEED_LICENSED_WHITELIST_FILE = str(wl_repo)

# Meta/Bing — той самий build_feed_items
import generate_meta_feed as gm  # noqa: E402
import generate_bing_feed as gb  # noqa: E402
chk("Meta і Bing викликають gg.build_feed_items (успадковують гейт)", "build_feed_items" in Path(gm.__file__).read_text(encoding="utf-8") and "build_feed_items" in Path(gb.__file__).read_text(encoding="utf-8"))
chk("trademark_filter.py не знає про ліцензійний перелік (сайт/акція не зачеплені)", "licensed" not in Path(__file__).parent.joinpath("trademark_filter.py").read_text(encoding="utf-8").lower())

# --- 31.10: ЧОРНИЙ СПИСОК ВЕНДОРІВ (рішення власника «без мік та бамбі»): гейт марок вимкнено, але MIC/МІС/Bambi з маркою у фід не йдуть
bl_repo = Path(__file__).parent / "feed_licensed_vendor_blocklist.json"
gg.FEED_LICENSED_BLOCKLIST_FILE = str(bl_repo)
os.environ.pop("FEED_LICENSED_VENDOR_BLOCKLIST", None)
os.environ.pop("FEED_EXCLUDE_LICENSED", None)
chk("чорний список типово УВІМКНЕНО; гейт марок типово вимкнено", gg.licensed_blocklist_on() is True and gg.licensed_gate_on() is False)
bl_doc = json.loads(bl_repo.read_text(encoding="utf-8-sig"))
chk("файл чорного списку в репо: рівно MIC, Bambi (кириличне «МІС» — окремий бренд, не внесено); у кожного джерело й дата", sorted(gg.vendor_key(r["вендор"]) for r in bl_doc["вендори"]) == ["bambi", "mic"]
    and all(r["джерело"].strip() and r["дата"].strip() for r in bl_doc["вендори"]))
chk("завантажений набір = {mic, bambi}; «MiC» = «MIC»; кириличне «МІС» ≠ латинське «MIC» і в набір не входить", gg.load_licensed_vendor_blocklist() == frozenset({"mic", "bambi"})
    and gg.vendor_key("MiC") == "mic" and gg.vendor_key("МІС") == "міс" and gg.vendor_key("МІС") != gg.vendor_key("MIC"))
bcat = {"1": vitem("1", 'Пазл "Холодне серце"', "MIC"), "2": vitem("2", 'Пазл "Холодне серце"', "MiC"), "3": vitem("3", 'Пазл "Холодне серце"', "МІС"),
        "4": vitem("4", 'Пазл "Холодне серце"', "Bambi"), "5": vitem("5", 'Пазл "Холодне серце"', "Funko"), "6": vitem("6", 'Пазл "Холодне серце"', "Trefl"),
        "7": vitem("7", 'Звичайний пазл', "MIC"), "8": vitem("8", 'Лялька Стич', "Невідомий"), "9": vitem("9", 'Лялька Стич', "")}
got_b, st_b = ids(bcat)
chk("гейт вимкнено + чорний список: Холодне серце від MIC/MiC/Bambi (1, 2, 4) прибрано; МІС (окремий бренд), Funko, Trefl, невідомий і порожній вендор З маркою лишились; MIC без марки лишився",
    got_b == {"3", "5", "6", "7", "8", "9"} and st_b["licensed_vendor_blocked"] == 3 and st_b["licensed_excluded"] == 0)
os.environ["FEED_LICENSED_VENDOR_BLOCKLIST"] = "0"
got_b0, st_b0 = ids(bcat)
chk("FEED_LICENSED_VENDOR_BLOCKLIST=0 → чорний список знято: у фіді все (9), лічильник 0", len(got_b0) == 9 and st_b0["licensed_vendor_blocked"] == 0)
os.environ.pop("FEED_LICENSED_VENDOR_BLOCKLIST", None)
os.environ["FEED_EXCLUDE_LICENSED"] = "1"
got_b1, st_b1 = ids(bcat)
chk("гейт УВІМКНЕНО + чорний список: MIC/MiC/Bambi рахує чорний список (3), решта марок — гейт (МІС, Funko, Невідомий, порожній = 4; Trefl повернено білим списком)",
    got_b1 == {"6", "7"} and st_b1["licensed_vendor_blocked"] == 3 and st_b1["licensed_excluded"] == 4)
os.environ.pop("FEED_EXCLUDE_LICENSED", None)
wl_and_bl = tmp / "wl_bl.json"
wl_and_bl.write_text(json.dumps({"вендори": [{"вендор": "MIC", "джерело": "цитата", "дата": "2026-10-31"}]}, ensure_ascii=False), encoding="utf-8")
gg.FEED_LICENSED_WHITELIST_FILE = str(wl_and_bl)
os.environ["FEED_EXCLUDE_LICENSED"] = "1"
got_x, _ = ids({"1": vitem("1", 'Пазл "Холодне серце"', "MIC")})
chk("чорний список сильніший за білий: вендор в обох → виключено", got_x == set())
gg.FEED_LICENSED_WHITELIST_FILE = str(wl_repo)
os.environ.pop("FEED_EXCLUDE_LICENSED", None)
# fail-closed: чорний список нечитабельний → RuntimeError (фід не перезаписується)
badbl = tmp / "badbl.json"
badbl.write_text("{не json", encoding="utf-8")
gg.FEED_LICENSED_BLOCKLIST_FILE = str(badbl)
try:
    ids(bcat)
    fc = False
except RuntimeError:
    fc = True
chk("чорний список: зламаний JSON → RuntimeError (fail-closed)", fc)
gg.FEED_LICENSED_BLOCKLIST_FILE = str(tmp / "no_bl.json")
try:
    ids(bcat)
    fc = False
except RuntimeError:
    fc = True
chk("файлу чорного списку нема → RuntimeError", fc)
for label, content in (("валідний JSON без списку «вендори» ({})", "{}"), ("«вендори» не список", json.dumps({"вендори": {"MIC": 1}})), ("корінь — список", "[]")):
    badbl2 = tmp / "badbl2.json"
    badbl2.write_text(content, encoding="utf-8")
    gg.FEED_LICENSED_BLOCKLIST_FILE = str(badbl2)
    try:
        ids(bcat)
        fc = False
    except RuntimeError:
        fc = True
    chk(f"чорний список: {label} → RuntimeError (fail-closed на схему, не тихо порожній)", fc)
os.environ["FEED_LICENSED_VENDOR_BLOCKLIST"] = "0"
chk("FEED_LICENSED_VENDOR_BLOCKLIST=0 і файлу нема → збірка не падає (список не читається)", len(ids(bcat)[0]) == 9)
os.environ.pop("FEED_LICENSED_VENDOR_BLOCKLIST", None)
gg.FEED_LICENSED_BLOCKLIST_FILE = str(bl_repo)

sys.exit(1 if F else 0)

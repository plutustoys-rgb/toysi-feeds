#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_dropt_supplier_export.py — Dropt (рішення власника 04.11.2026): читач публічного каталогу (sitemap → сторінки товарів; robots.txt забороняє списки ?limit/?page) + експорт supplier_dropt.json.
Мережі нема: синтетичний HTML. Інваріанти: (1) закупівля = ціна «Дроп» (JSON-LD offers.price), НЕ «Опт»; ціна сайту — закрита формула; (2) наявність = лише InStock, інакше fail-closed; (3) назва/опис українські,
зброя/18+ не публікуються, без хибних спрацювань на «автомат/катання/феєрверк-лампа/кухонний ніж»; (4) збій мережі, усадка файлу/розділу, порожній результат → попередній файл НЕ чіпаємо;
(5) id dropt-<артикул>; (6) порядок читання: нові → найстаріші обрані; часовий бюджет; кеш зберігається частинами; (7) приватність: файл 0600, юніт через venv-python."""
import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).parent
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, str(HERE))
import dropt_parser as dp  # noqa: E402
import dropt_supplier_export as ex  # noqa: E402
import rt_supplier_export as rt  # noqa: E402

F = []


def chk(n, c):
    print(f"[{'OK ' if c else 'FAIL'}] {n}")
    if not c:
        F.append(n)


def product(sku, name="Іграшка Тест • Підзаголовок", price="100.00", avail="InStock", top="Товари для дітей", sub="Ігрові набори", brand="Asson", desc="Корисний опис.", images=None, ld_image=None):
    crumbs = [{"@type": "ListItem", "position": 1, "name": '<i class="fa fa-home"></i>'}]
    for n in (top, sub):
        if n:
            crumbs.append({"@type": "ListItem", "position": len(crumbs) + 1, "name": n})
    crumbs.append({"@type": "ListItem", "position": len(crumbs) + 1, "name": name})
    ld = {"@type": "Product", "name": name, "sku": sku, "brand": {"@type": "Brand", "name": brand}, "offers": {"@type": "Offer", "price": price, "availability": f"https://schema.org/{avail}"}}
    if ld_image:
        ld["image"] = ld_image
    anchors = "".join(f'<a href="{u}"></a>' for u in (images if images is not None else [f"/image/cache/catalog/x-{sku}_2-1000x1000.webp", f"/image/cache/catalog/x-{sku}-1000x1000.webp"]))
    return (f'<html><script type="application/ld+json">{json.dumps(ld, ensure_ascii=False)}</script><script type="application/ld+json">{json.dumps({"@type": "BreadcrumbList", "itemListElement": crumbs}, ensure_ascii=False)}</script>'
            f'<div id="tab-description"><p>{desc}</p></div>{anchors}</html>')


# ---- sitemap ----
SM = ("<urlset><url><loc>https://dropt.in.ua/ua/</loc></url><url><loc>https://dropt.in.ua/ru/</loc></url><url><loc>https://dropt.in.ua/ua/akustika</loc></url>"
      "<url><loc>https://dropt.in.ua/ru/akustika</loc></url><url><loc>https://dropt.in.ua/ua/tovar-1</loc></url><url><loc>https://dropt.in.ua/ua/tovar-1</loc></url><url><loc>https://dropt.in.ua/ua/a/b</loc></url></urlset>")
chk("sitemap: лише /ua/<slug> (без кореня, /ru/, вкладених, дублів)", dp.parse_sitemap(SM) == ["https://dropt.in.ua/ua/akustika", "https://dropt.in.ua/ua/tovar-1"])

# ---- сторінка товару ----
D = dp.parse_detail(product("59298", price="502.20", name="Холодильник • Набір", ld_image="https://dropt.in.ua/image/catalog/x-59298.webp"))
chk("«Дроп» = offers.price 502.2; sku; бренд; категорія «Розділ / Підрозділ»; top", D["price"] == 502.2 and D["sku"] == "59298" and D["brand"] == "Asson" and D["category"] == "Товари для дітей / Ігрові набори" and D["top"] == "Товари для дітей")
chk("наявність: InStock → True; OutOfStock → False; відсутнє поле/дивне значення → False (fail-closed)",
    D["in_stock"] is True and dp.parse_detail(product("1", avail="OutOfStock"))["in_stock"] is False and dp.parse_detail(product("1", avail="PreOrder"))["in_stock"] is False
    and dp.parse_detail(product("1").replace('"availability"', '"x"'))["in_stock"] is False)
chk("головне фото (збігається з ld.image) першим, далі за порядком DOM", D["images"][0].endswith("x-59298-1000x1000.webp") and len(D["images"]) == 2)
old = dp.parse_detail(product("57445", images=["/image/products/57/57445_ab12-1000x1000.jpg", "/image/products/57/57445_cd34-1000x1000.JPG", "/image/x-1000x1000.gif"], ld_image="https://dropt.in.ua/image/products/57/57445_cd34.jpg"))
chk("старі товари: .jpg/.jpeg/.png у /products/ читаються, gif ні, головне першим (аудит #1099 п.3)", old["images"] == ["https://dropt.in.ua/image/products/57/57445_cd34-1000x1000.JPG", "https://dropt.in.ua/image/products/57/57445_ab12-1000x1000.jpg"])
chk("без фото-посилань → фолбек на ld.image", dp.parse_detail(product("2", images=[], ld_image="https://dropt.in.ua/image/catalog/m.webp"))["images"] == ["https://dropt.in.ua/image/catalog/m.webp"])
chk("не товар / нема числового sku → None", dp.parse_detail("<html></html>") is None and dp.parse_detail(product("abc")) is None)
Z = dp.parse_detail(product("1", price="0"))
chk("ціна 0/нечислова на сторінці ТОВАРУ → запис товару з price 0 і in_stock False (не «не-товар»: аудит #1099 п.A)", Z and Z["price"] == 0.0 and Z["in_stock"] is False and dp.parse_detail(product("1", price="x"))["in_stock"] is False)
chk("ціна формату «1 234,50» читається", dp.parse_detail(product("3", price="1 234,50"))["price"] == 1234.5)

# ---- назва, опис, політика ----
E = dict(dp.parse_detail(product("59298", price="502.20", name="Ігровий холодильник Mini ∙ Підзаголовок", desc="Ігровий холодильник Mini ∙ Підзаголовок\nДуже цікавий набір.\nДоставка\nНова Пошта, оплата при отриманні")), url="https://dropt.in.ua/ua/p")
E["fetched_at"] = 1.0
IMGS = ["https://plutustoys.com.ua/img/dropt/dropt-59298-1.jpg"]
it = ex.build_item("dropt-59298", E, IMGS)
chk("ціна: cost 502,20 («Дроп») → 754 = site_price; cost = 502.2", it and it["price"] == 754 == rt.site_price(502.2) and it["cost"] == 502.2)
chk("роздільник ∙ (і • і ·): назва скорочена до «Ігровий холодильник Mini»", it["name"] == "Ігровий холодильник Mini" and ex.build_item("dropt-1", dict(E, name="Чайник · Підзаголовок"), IMGS)["name"] == "Чайник" and ex.build_item("dropt-1", dict(E, name="Чайник • Підзаголовок"), IMGS)["name"] == "Чайник")
chk("опис: дубль заголовка прибрано, шаблонний хвіст «Доставка…оплата при отриманні» обрізано (аудит #1099 п.5)", it["description"] == "Дуже цікавий набір.")
chk("опис: рядки про «на нашому сайті»/Dropt прибрано", ex.clean_description("Гарний рюкзак.\nЗараз на нашому сайті!\nПишіть у Dropt") == "Гарний рюкзак.")
chk("vendor_code = артикул, stock = STOCK_IN, dims None", it["vendor_code"] == "59298" and it["stock"] == ex.STOCK_IN and it["dims_cm"] is None and it["weight_kg"] is None)
for why, bad in (("нема в наявності", dict(E, in_stock=False)), ("ціна 0", dict(E, price=0)), ("ціна x", dict(E, price="x")), ("назва лише з роздільника", dict(E, name="• Х")), ("порожня назва", dict(E, name=""))):
    chk(f"build_item None: {why}", ex.build_item("dropt-59298", bad, IMGS) is None)
chk("без фото / неправильний id → None", ex.build_item("dropt-1", E, []) is None and ex.build_item("rt-1", E, IMGS) is None and ex.build_item("dropt-x", E, IMGS) is None)
for n in ("Набор игрушек Ыгра", "Бездротові Bluetooth-наушники для телефону", "Щетка фурминатор для груминга собак", "Чехол для телефона и планшета", "Ремінець рос. звучення"):
    chk(f"російська назва відсіюється: {n}", ex.build_item("dropt-1", dict(E, name=n), IMGS) is None)
chk("українські назви з «при/без/для» НЕ відсіюються", all(ex.build_item("dropt-1", dict(E, name=n), IMGS) for n in ("Намет для туризму при дощі", "Набір без батарейок для дітей", "Іграшка для собак і котів")))
for n in ("Іграшковий пістолет Бластер", "Арбалет дитячий", "Мультиніж подарунковий XL-5508", "Вейп одноразовий", "Меч самурайський", "Іграшковий автомат з м'якими кулями", "Автомат іграшковий звуковий", "Травматичний револьвер", "Пневматична гвинтівка", "Іграшковий кулемет", "Електрошокер", "Кастет-ліхтар тактичний", "Газовий балончик", "Сигнальна ракетниця", "Тесак туристичний", "Нунчаки"):
    chk(f"політика «не продаємо»: {n}", ex.build_item("dropt-1", dict(E, name=n), IMGS) is None)
for n in ("Автоматична напувалка для котів", "Намет-автомат туристичний", "Туристичний намет АВТОМАТ", "Бібікар для катання з пультом", "Світильник Феєрверк 28Key", "Круглий кухонний ніж для кавуна", "Автоматичний компресор"):
    chk(f"хибні спрацювання політики усунено (аудит #1099 п.6): {n}", ex.build_item("dropt-1", dict(E, name=n), IMGS) is not None)
chk("політика дивиться на КОРОТКУ назву: пістолет лише в підзаголовку не відсіює", ex.build_item("dropt-1", dict(E, name="Пулевідвід Зелений • Імітація пістолет зі світлом"), IMGS) is not None)
chk("російський опис → без опису, картка лишається", ex.build_item("dropt-1", dict(E, description="Набор для проведения праздника и игры"), IMGS)["description"] == "")

# ---- порядок і бюджет читання, кеш частинами ----
URLS = [f"https://dropt.in.ua/ua/t{i}" for i in range(6)]
PAGES = {u: product(str(1000 + i), name=f"Товар {i}") for i, u in enumerate(URLS)}
PAGES[URLS[5]] = "<html>категорія</html>"
log = []


def fake_fetch(u):
    log.append(u)
    return PAGES[u]


cache, saved = {}, []
st = ex.refresh(URLS, cache, fake_fetch, 1000.0, time_budget=1e9, save=lambda c: saved.append(len(c)))
chk("нові адреси читаються всі; не-товар → kind other; товар → kind product", st["read"] == 6 and cache[URLS[5]]["kind"] == "other" and cache[URLS[0]]["kind"] == "product" and cache[URLS[0]]["sku"] == "1000")
log.clear()
ex.refresh(URLS, cache, fake_fetch, 1000.0 + 600, time_budget=1e9)
chk("свіжі (<40 хв) не перечитуємо", log == [])
cache[URLS[2]]["fetched_at"] = 100.0
cache[URLS[1]]["fetched_at"] = 500.0
cache[URLS[4]]["fetched_at"] = 900.0
log.clear()
ex.refresh(URLS, cache, fake_fetch, 1000.0 + 3600, time_budget=1e9)
chk("протухлі обрані — найстарішими першими", log[:3] == [URLS[2], URLS[1], URLS[4]] and URLS[5] not in log)
log.clear()
ex.refresh(URLS + ["https://dropt.in.ua/ua/new"], cache, lambda u: (log.append(u) or product("7777")), 1000.0 + 3600, time_budget=1e9)
chk("нова адреса читається ПЕРШОЮ", log[0] == "https://dropt.in.ua/ua/new")
ticks = iter(range(100))
log.clear()
ex.refresh(URLS, {}, fake_fetch, 5.0, time_budget=3, clock=lambda: next(ticks))
chk("часовий бюджет: читання зупиняється (не всі 6 прочитано)", 0 < len(log) < 6)
log.clear()
c2 = {}
big = [f"https://dropt.in.ua/ua/b{i}" for i in range(250)]
ex.refresh(big, c2, lambda u: product(str(5000 + int(u.rsplit("b", 1)[1]))), 1.0, time_budget=1e9, save=lambda c: saved.append(len(c)))
chk("кеш пишеться частинами кожні SAVE_EVERY=100 (прогрес не гине з прогоном)", saved[-2:] == [100, 200])


def boom(u):
    raise OSError("мережа")


c3 = {}
st = ex.refresh(big, c3, boom, 1.0, time_budget=1e9)
chk(f"{ex.MAX_CONSEC_ERRORS} збоїв поспіль → aborted, не вигрібаємо весь список", st["aborted"] and st["errors"] == ex.MAX_CONSEC_ERRORS and all(e["kind"] == "error" for e in c3.values()) and len(c3) == ex.MAX_CONSEC_ERRORS)

# ---- аудит #1099 (2-й раунд): отруєння кешу, мертві адреси, свіжість, права кешу ----
cp_ = {}
GOOD_URL = "https://dropt.in.ua/ua/good"
ex.refresh([GOOD_URL], cp_, lambda u: product("4001"), 1000.0, time_budget=1e9)
ex.refresh([GOOD_URL], cp_, lambda u: "<html>технічна заглушка</html>", 1000.0 + 7200, time_budget=1e9)
chk("збій розбору (заглушка без JSON-LD) на раніше відомому ТОВАРІ НЕ перетворює його на «other» — старий запис лишається", cp_[GOOD_URL]["kind"] == "product" and cp_[GOOD_URL]["sku"] == "4001" and cp_[GOOD_URL]["try_at"] == 1000.0 + 7200)
log.clear()
ex.refresh([GOOD_URL], cp_, lambda u: (log.append(u) or product("4001")), 1000.0 + 7200 + 600, time_budget=1e9)
chk("повтор після збою розбору — не раніше, ніж через REFRESH_MIN_AGE_S (не стоїть першим щоразу)", log == [])


class H404(Exception):
    response = type("R", (), {"status_code": 404})()


def f404(u):
    raise H404()


c404 = {}
st = ex.refresh([f"https://dropt.in.ua/ua/d{i}" for i in range(60)], c404, f404, 1.0, time_budget=1e9)
chk("60 адрес 404 — НЕ «Dropt недоступний»: aborted немає, записані як other з датою", not st["aborted"] and st["errors"] == 0 and all(e["kind"] == "other" for e in c404.values()))
dead_cache = {f"https://dropt.in.ua/ua/x{i}": {"kind": "product", "fetched_at": 10.0 + i, "top": "Акустика", "in_stock": True, "sku": str(i)} for i in range(40)}
urls_d = list(dead_cache) + ["https://dropt.in.ua/ua/alive"]
dead_cache["https://dropt.in.ua/ua/alive"] = {"kind": "product", "fetched_at": 500000.0, "top": "Акустика", "in_stock": True, "sku": "999"}
calls = []


def sometimes(u):
    calls.append(u)
    if "/x" in u:
        raise OSError("мертва")
    return product("999")


st1 = ex.refresh(urls_d, dead_cache, sometimes, 1000000.0, time_budget=1e9)
tried1 = set(calls)
calls.clear()
st2 = ex.refresh(urls_d, dead_cache, sometimes, 1000000.0 + 600, time_budget=1e9)
chk("мережеві збої оновлюють try_at: уже пробувані мертві адреси НЕ лізуть у чергу знову за <40 хв, прогін іде далі до решти (прогрес є, не вічна зупинка)", st1["aborted"] and len(tried1) == ex.MAX_CONSEC_ERRORS and not (set(calls) & tried1) and "https://dropt.in.ua/ua/alive" in calls and not st2["aborted"])
fresh_o = {"u1": {"kind": "product", "fetched_at": 1000.0, "top": "Акустика", "in_stock": False, "sku": "1"}, "u2": {"kind": "product", "fetched_at": 1000.0, "top": "Акустика", "in_stock": True, "sku": "2"}, "u3": {"kind": "product", "fetched_at": 1000.0, "top": "Автотовари", "in_stock": False, "sku": "3"}}
calls.clear()
ex.refresh(list(fresh_o), fresh_o, lambda u: (calls.append(u) or product("5")), 1000.0 + 3600, time_budget=1e9)
chk("через 1 год: «в наявності» перечитуємо, OOS (раз на 4 год) і чужий розділ (раз на 30 діб) — ні", calls == ["u2"])
fresh_o = {k: dict(v, fetched_at=1000.0) for k, v in fresh_o.items()}
calls.clear()
ex.refresh(list(fresh_o), fresh_o, lambda u: (calls.append(u) or product("5")), 1000.0 + 5 * 3600 + 5000, time_budget=1e9)
chk("через 5+ год: і OOS обраних перечитуємо, чужий розділ — ні", "u1" in calls and "u3" not in calls)
forn = {}
ex.refresh(["https://dropt.in.ua/ua/f"], forn, lambda u: product("77", top="Автотовари", desc="Довгий опис " * 200), 1.0, time_budget=1e9)
chk("товар поза 7 розділами: мінімальний запис (без опису/фото/ціни) — кеш не роздувається", forn["https://dropt.in.ua/ua/f"] == {"kind": "product", "fetched_at": 1.0, "sku": "77", "top": "Автотовари", "in_stock": False})

# ---- 3-й раунд аудиту #1099 ----
T0 = 5_000_000.0
tail = {f"https://dropt.in.ua/ua/ok{i}": {"kind": "product", "fetched_at": T0 - 7200 - i, "top": "Акустика", "in_stock": True, "sku": str(100 + i), "name": "Т", "price": 10.0, "images": ["https://x/y-1000x1000.webp"], "category": "Акустика"} for i in range(60)}
bad_u = [f"https://dropt.in.ua/ua/bad{i}" for i in range(40)]
for u in bad_u:
    tail[u] = {"kind": "product", "fetched_at": T0 - 90000, "top": "Акустика", "in_stock": True, "sku": "9", "name": "Т", "price": 10.0, "images": ["https://x/y-1000x1000.webp"], "category": "Акустика"}


def f5xx(u):
    if "/bad" in u:
        raise OSError("500")
    return product(u.rsplit("ok", 1)[1] if "/ok" in u else "1")


st = ex.refresh(list(tail), tail, f5xx, T0, time_budget=1e9)
chk("стійкі 5xx на 40 найстаріших адресах: прогін aborted (сигнал), але далі НЕ вічно першими — після спроби йдуть у кінець черги", st["aborted"] and all(tail[u]["try_at"] == T0 for u in list(tail) if u in bad_u and "try_at" in tail[u]))
calls.clear()
st = ex.refresh(list(tail), tail, lambda u: (calls.append(u) or f5xx(u)), T0 + 3000, time_budget=1e9)
chk("наступний прогін спершу читає ще не пробувані адреси (не тих, що вже збоїли), прогрес є", len(calls) > 0 and calls[0] not in bad_u[:ex.MAX_CONSEC_ERRORS])
cp3 = {}
ex.refresh(["https://dropt.in.ua/ua/sel"], cp3, lambda u: product("6001"), T0, time_budget=1e9)
ex.refresh(["https://dropt.in.ua/ua/sel"], cp3, lambda u: product("6001").replace("BreadcrumbList", "Nothing"), T0 + 7200, time_budget=1e9)
chk("сторінка БЕЗ BreadcrumbList на раніше обраному товарі = збій розбору: запис лишається обраним (не «чужий розділ» на 30 діб)", ex.is_selected(cp3["https://dropt.in.ua/ua/sel"]) and cp3["https://dropt.in.ua/ua/sel"]["try_at"] == T0 + 7200)
cp4 = {}
ex.refresh(["https://dropt.in.ua/ua/nb"], cp4, lambda u: product("6002").replace("BreadcrumbList", "Nothing"), T0, time_budget=1e9)
calls.clear()
ex.refresh(["https://dropt.in.ua/ua/nb"], cp4, lambda u: (calls.append(u) or product("6002")), T0 + 5 * 3600, time_budget=1e9)
chk("нова сторінка без розділу (top пустий): перечитується за ≈4 год, а не за 30 діб", cp4["https://dropt.in.ua/ua/nb"]["top"] == "Товари для дітей" and len(calls) == 1)
gone = {"https://dropt.in.ua/ua/g": {"kind": "product", "fetched_at": T0, "top": "Акустика", "in_stock": True, "sku": "7"}}
ex.refresh(list(gone), gone, f404, T0 + 3600, time_budget=1e9)
chk("товар → 404/410: kind gone; через добу перечитується (не 7 діб)", gone["https://dropt.in.ua/ua/g"]["kind"] == "gone")
calls.clear()
ex.refresh(list(gone), gone, lambda u: (calls.append(u) or product("7")), T0 + 3600 + 90000, time_budget=1e9)
chk("gone перечитується через ~24 год і повертається в обрані", len(calls) == 1 and ex.is_selected(gone["https://dropt.in.ua/ua/g"]))
fut = {"https://dropt.in.ua/ua/fu": {"kind": "product", "fetched_at": T0 + 10 ** 6, "top": "Акустика", "in_stock": True, "sku": "8"}}
calls.clear()
ex.refresh(list(fut), fut, lambda u: (calls.append(u) or product("8")), T0, time_budget=1e9)
chk("запис «з майбутнього» (годинник пішов назад) вважається застарілим і перечитується", len(calls) == 1 and fut["https://dropt.in.ua/ua/fu"]["fetched_at"] == T0)

# ---- end-to-end ----
with tempfile.TemporaryDirectory() as td:
    td = Path(td)
    out, cp, imgd = td / "supplier_dropt.json", td / "cache.json", td / "img"
    saved_idx = ex.INDEX_FILE
    ex.INDEX_FILE = td / "dropt_index_ids.json"
    many = [f"https://dropt.in.ua/ua/p{i}" for i in range(ex.MIN_SITEMAP_URLS)]
    html = {u: product(str(2000 + i), name=f"Товар {i} • Х", price="100", top="Акустика", sub="Power bank") for i, u in enumerate(many)}
    html[many[0]] = product("2000", name="Товар 0 • Х", price="100", avail="OutOfStock", top="Акустика")
    html[many[1]] = product("2001", name="Автомобільний інвертор • Х", price="100", top="Автотовари", sub="Інвертори")          # розділ поза вибраними
    html[many[2]] = product("2002", name="Іграшковий пістолет • Х", price="100", top="Товари для дітей")
    html[many[3]] = "<html>категорія</html>"
    fetch_html = lambda u: html[u]   # noqa: E731

    def fake_img(url, dest):
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"jpg")
        return True

    NOW = datetime(2026, 11, 4, 12, 0, tzinfo=timezone.utc)
    ok = ex.run_once(urls=many, fetch=fetch_html, out=out, cache_path=cp, img_dir=imgd, fetch_img=fake_img, now=NOW)
    d = json.loads(out.read_text(encoding="utf-8")) if out.exists() else {}
    n_expect = ex.MIN_SITEMAP_URLS - 4
    chk(f"end-to-end: у файлі {n_expect} карток (OOS, чужий розділ, зброя, не-товар відсіяні), catalog_n = {ex.MIN_SITEMAP_URLS}", ok and len(d.get("items", {})) == n_expect and d["catalog_n"] == ex.MIN_SITEMAP_URLS
        and "dropt-2000" not in d["items"] and "dropt-2001" not in d["items"] and "dropt-2002" not in d["items"] and d["updated"].startswith("2026-11-04T12:00:00"))
    it0 = d["items"]["dropt-2010"]
    chk("елемент: ціна/cost/категорія/фото на нашому домені/index=False/source_url", it0["price"] == rt.site_price(100) and it0["cost"] == 100.0 and it0["category"] == "Акустика / Power bank" and it0["images"][0].startswith("https://plutustoys.com.ua/img/dropt/dropt-2010-") and it0["index"] is False and it0["source_url"] == many[10])
    chk("кеш збережено на диск", cp.exists() and len(json.loads(cp.read_text(encoding="utf-8"))) == ex.MIN_SITEMAP_URLS)
    chk("кожна картка має checked_at; у файлі min_checked_at = час читання (свіжість видно споживачам, аудит #1099 п.D)", it0["checked_at"] == int(NOW.timestamp()) and d["min_checked_at"] == int(NOW.timestamp()))
    if os.name != "nt":
        chk("права файлу 0600 (там cost) і кешу 0600 (там ціна «Дроп»)", oct(out.stat().st_mode & 0o777) == "0o600" and oct(cp.stat().st_mode & 0o777) == "0o600")
    ex.INDEX_FILE.write_text(json.dumps({"ids": ["dropt-2010", "rt-5", "junk"]}), encoding="utf-8")
    ex.run_once(urls=many, fetch=fetch_html, out=out, cache_path=cp, img_dir=imgd, fetch_img=fake_img, now=NOW)
    chk("dropt_index_ids.json → index=True лише для валідного dropt-id", json.loads(out.read_text(encoding="utf-8"))["items"]["dropt-2010"]["index"] is True and json.loads(out.read_text(encoding="utf-8"))["items"]["dropt-2011"]["index"] is False)

    before = out.read_bytes()
    chk("sitemap усічений (<1000) → False, файл не змінено", ex.run_once(urls=many[:10], fetch=fetch_html, out=out, cache_path=cp, img_dir=imgd, fetch_img=fake_img, now=NOW) is False and out.read_bytes() == before)
    chk("sitemap недоступний → False, файл не змінено", ex.run_once(fetch=boom, out=out, cache_path=cp, img_dir=imgd, fetch_img=fake_img, now=NOW) is False and out.read_bytes() == before)
    chk("Dropt «лежить» (збої поспіль) → False, файл не змінено", ex.run_once(urls=[u + "?x" for u in many], fetch=boom, out=out, cache_path=cp, img_dir=imgd, fetch_img=fake_img, now=NOW) is False and out.read_bytes() == before)
    import shutil
    cp_future = td / "cache_future.json"
    shutil.copy(cp, cp_future)
    chk("застарілі записи (>3 год) у файл не потрапляють: через добу без перечитування (мережа лежить) → жодної свіжої картки → файл не змінено",
        ex.run_once(urls=many, fetch=boom, out=out, cache_path=cp_future, img_dir=imgd, fetch_img=fake_img, now=datetime(2026, 11, 5, 12, 0, tzinfo=timezone.utc)) is False and out.read_bytes() == before)
    chk("sitemap на годину усічений до 1000 інших адрес НЕ стирає кеш (прунінг лише для адрес, відсутніх >3 діб)", len(json.loads(cp.read_text(encoding="utf-8"))) >= ex.MIN_SITEMAP_URLS)
    # усадка розділу: 100 карток «Акустика» було, лишилось 20 (решта в Dropt раптом OutOfStock)
    html2 = {u: (product(str(2000 + i), name=f"Товар {i} • Х", price="100", top="Акустика", avail="InStock" if i < 120 else "OutOfStock") if i >= 4 else html[u]) for i, u in enumerate(many)}
    chk("усадка розділу «Акустика» >50% → False, файл не змінено", ex.run_once(urls=many, fetch=lambda u: html2[u], out=out, cache_path=cp, img_dir=imgd, fetch_img=fake_img, now=datetime(2026, 11, 4, 14, 0, tzinfo=timezone.utc)) is False and out.read_bytes() == before)
    out.unlink()
    html3 = {u: product(str(2000 + i), name="Т", price="100", avail="OutOfStock", top="Акустика") for i, u in enumerate(many)}
    cp2 = td / "cache2.json"
    chk("усе OOS → жодної картки → False, файл не створено", ex.run_once(urls=many, fetch=lambda u: html3[u], out=out, cache_path=cp2, img_dir=imgd, fetch_img=fake_img, now=NOW) is False and not out.exists())
    # фото змінилися → старі файли прибираються
    html4 = {u: product(str(2000 + i), name=f"Товар {i} • Х", price="100", top="Акустика", images=["/image/cache/catalog/zz-1000x1000.webp"]) for i, u in enumerate(many)}
    ex.run_once(urls=many, fetch=lambda u: html4[u], out=out, cache_path=cp, img_dir=imgd, fetch_img=fake_img, now=datetime(2026, 11, 4, 16, 0, tzinfo=timezone.utc))
    chk("Dropt змінив фото → старі dropt-<sku>-N.jpg прибрано, лишилось одне нове", sorted(p.name for p in imgd.glob("dropt-2010-*.jpg")) == ["dropt-2010-1.jpg"])
    ex.INDEX_FILE = saved_idx

with tempfile.TemporaryDirectory() as td3:
    td3 = Path(td3)
    many3 = [f"https://dropt.in.ua/ua/q{i}" for i in range(ex.MIN_SITEMAP_URLS)]

    def tail_down(u):
        if int(u.rsplit("q", 1)[1]) >= ex.MIN_SITEMAP_URLS - 40:
            raise OSError("500")
        return product(str(8000 + int(u.rsplit("q", 1)[1])), name="Товар • Х", price="50", top="Акустика")

    ok3 = ex.run_once(urls=many3, fetch=tail_down, out=td3 / "s.json", cache_path=td3 / "c.json", img_dir=td3 / "img", fetch_img=lambda url, dest: (dest.parent.mkdir(parents=True, exist_ok=True), dest.write_bytes(b"j"), True)[2], now=NOW)
    n3 = len(json.loads((td3 / "s.json").read_text(encoding="utf-8"))["items"]) if (td3 / "s.json").exists() else -1
    chk("40 адрес на хвості віддають 5xx: файл усе одно записано з тих, що прочитано (aborted лише сигналить; аудит #1099, 3-й раунд п.1)", ok3 is True and n3 == ex.MIN_SITEMAP_URLS - 40)

with tempfile.TemporaryDirectory() as td2:
    gf = Path(td2) / "s.json"
    big_prev = {f"dropt-{i}": {"category": "Новий Рік / Гірлянди"} for i in range(100)}
    gf.write_text(json.dumps({"updated": "2026-11-04T12:00:00+00:00", "items": big_prev}), encoding="utf-8")
    small = {f"dropt-{i}": {"category": "Новий Рік / Гірлянди"} for i in range(20)}
    t0 = datetime(2026, 11, 4, 12, 0, tzinfo=timezone.utc).timestamp()
    chk("гард усадки: різке звуження за <12 год → файл тримаємо", ex.out_guard_ok(gf, small, t0 + 3600) is False)
    chk("гард усадки: попередній запис ≥12 год — легітимне звуження (сезон) приймаємо, файл не заморожено назавжди", ex.out_guard_ok(gf, small, t0 + 13 * 3600) is True)

svc = (HERE / "dropt-supplier-export.service").read_text(encoding="utf-8")
chk("юніт: venv-python, ExecStart на dropt_supplier_export.py, TimeoutStartSec ≥ бюджет читання + фото", "/opt/plutustoys/venv/bin/python3 /opt/plutustoys/dropt_supplier_export.py" in svc and "TimeoutStartSec=2700" in svc and 2700 >= ex.DETAIL_TIME_BUDGET_S + ex.PREFETCH_BUDGET_S + 600)
chk("розділи = рішення власника 04.11 (7, без Автотоварів/Краси/Техніки/Дому/TV-SHOP)", set(dp.CATEGORIES.values()) == {"Товари для дітей", "Акустика", "Електроніка", "Туризм, відпочинок і спорт", "Новий Рік", "Товари для тварин", "Рюкзаки, сумки, портмоне"})
chk("парсер не звертається до списків ?limit/?page (robots.txt Dropt)", "limit=" not in (HERE / "dropt_parser.py").read_text(encoding="utf-8").replace("?limit= / ?page= / ?sort=", ""))
gi = (HERE / ".gitignore").read_text(encoding="utf-8")
chk(".gitignore: приватний файл і кеш не потраплять у git", "/supplier_dropt.json" in gi and "/dropt_detail_cache.json" in gi and "/site/img/dropt/" in gi)

print("\nFAIL:" if F else "\nOK: усі перевірки пройдені", *F, sep="\n")
sys.exit(1 if F else 0)

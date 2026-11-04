#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_dropt_supplier_export.py — Dropt (рішення власника 04.11.2026): читач публічного каталогу + експорт supplier_dropt.json. Мережі нема: усе на синтетичному HTML.
Інваріанти: (1) закупівля = «Дроп», НЕ «Опт»; ціна — закрита формула сайту; (2) лише «в наявності»; (3) назва/опис українські, зброя/18+ не публікуються; (4) збій розділу, усадка
каталогу, порожній результат → попередній файл НЕ чіпаємо; (5) id dropt-<артикул>; (6) докачування сторінок за бюджетом/TTL; (7) приватність: файл 0600, юніт через venv-python."""
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


def card(sku, name, drop, opt, stock=True, pid="1", href=None):
    return (f'<div class="product-card col-xs-6"><div class="product-thumb"><div class="image"><a href="{href or "https://dropt.in.ua/ua/p-" + sku}"><img src="https://dropt.in.ua/image/cache/catalog/x-{pid}-300x300.webp"></a>'
            f'<button type="button" class="btn-favorite-overlay" data-product-id="{pid}"></button></div><div class="caption"><h4><a href="/ua/p-{sku}">{name}</a></h4>'
            f'<p>Артикул: {sku}</p><p>Дроп: ₴{drop}</p><p>Опт: ₴{opt}</p>' + ("<a>Купити</a>" if stock else "<span>Немає в наявності</span>") + "</div></div></div>")


def page(*cards, menu=""):
    return f'<html><body>{menu}<div class="products-grid">{"".join(cards)}</div></body></html>'


L = dp.parse_listing(page(card("59298", "Ігровий холодильник • Набір", "502.20", "478.95", pid="19357"), card("59080", "Powerbank", "2 069.25", "2022.75", stock=False, pid="2"),
                          '<div class="product-card"><h4><a href="/ua/x">Без ціни</a></h4><p>Артикул: 1</p></div>'))
chk("список: 2 валідні картки, без ціни пропущена", len(L) == 2)
chk("«Дроп» 502.20 (не «Опт» 478.95), артикул, pid, url абсолютний", L[0]["drop"] == 502.2 and L[0]["opt"] == 478.95 and L[0]["sku"] == "59298" and L[0]["pid"] == "19357" and L[0]["url"].startswith("https://dropt.in.ua/"))
chk("ціна з пробілом-роздільником «2 069.25» читається; «Немає в наявності» → in_stock False", L[1]["drop"] == 2069.25 and L[1]["in_stock"] is False and L[0]["in_stock"] is True)

menu = '<div class="hdr-cat-submenu"><a href="https://dropt.in.ua/ua/tovary-dlya-detey/igrovye-nabory">Ігрові набори</a><a href="/ua/tovary-dlya-detey/konstruktory">Конструктори</a><a href="/ua/akustika/x">Чуже</a><a href="/ua/tovary-dlya-detey">Сам розділ</a></div>'
chk("підрозділи лише свого розділу, без самого розділу", dp.parse_subcategories(page(menu=menu), "tovary-dlya-detey") == [("igrovye-nabory", "Ігрові набори"), ("konstruktory", "Конструктори")])

# пагінація: 100 карток на 1-й сторінці → йдемо далі; неповна → стоп; дублі артикулів відсікаються
calls = []


def fake_pages(url):
    calls.append(url)
    if "page=1" in url:
        return page(*[card(str(1000 + i), f"Т{i}", "10", "9", pid=str(i)) for i in range(100)])
    return page(card("1000", "дубль", "10", "9"), card("2000", "Останній", "20", "19"))


got = dp.crawl_listing("x", fake_pages)
chk("пагінація: 2 сторінки, 101 унікальна картка (дубль 1000 відсічено)", len(calls) == 2 and len(got) == 101)

# каталог: підрозділ перекриває розділ, картка поза підрозділами лишається з категорією розділу
TOP = page(card("1", "А", "10", "9"), card("2", "Б", "10", "9"), menu='<div class="hdr-cat-submenu"><a href="/ua/akustika/pb">Power bank</a></div>')
SUB = page(card("1", "А", "10", "9"))
cat, failed = dp.fetch_catalog(lambda u: SUB if "/akustika/pb" in u else TOP, {"akustika": "Акустика"})
chk("категорії: 1 → «Акустика / Power bank», 2 → «Акустика»", cat["1"]["category"] == "Акустика / Power bank" and cat["2"]["category"] == "Акустика" and not failed)


def boom(url):
    raise OSError("мережа")


cat2, failed2 = dp.fetch_catalog(boom, {"akustika": "Акустика"})
chk("збій розділу → у failed, каталог порожній (викликач не перезапише файл)", failed2 == ["akustika"] and cat2 == {})

DET = ('<html><script type="application/ld+json">{"@type":"Product","name":"Рюкзак 669 • Міський","brand":{"@type":"Brand","name":"Asson"},"offers":{"availability":"https://schema.org/InStock"}}</script>'
       '<div id="tab-description"><p>Рюкзак 669 • Міський підзаголовок</p><p>Корисний опис рюкзака.</p><script>x()</script></div>'
       '<a href="/image/cache/catalog/r-19_2-1000x1000.webp"></a><a href="/image/cache/catalog/r-19_3-1000x1000.webp"></a><a href="/image/cache/catalog/r-19-1000x1000.webp"></a></html>')
D = dp.parse_detail(DET)
chk("сторінка товару: бренд, наявність, головне фото першим, далі _2, _3", D["brand"] == "Asson" and D["in_stock"] and [u.rsplit("/", 1)[1] for u in D["images"]] == ["r-19-1000x1000.webp", "r-19_2-1000x1000.webp", "r-19_3-1000x1000.webp"])
chk("опис без скриптів; нема JSON-LD → None", "x()" not in D["description"] and "Корисний опис" in D["description"] and dp.parse_detail("<html></html>") is None)

IMG = ["https://dropt.in.ua/image/cache/catalog/a-1000x1000.webp"]
C = {"sku": "59298", "name": "Ігровий холодильник Mini • Підзаголовок", "drop": 502.2, "opt": 478.95, "in_stock": True, "category": "Товари для дітей / Ігрові набори", "url": "https://dropt.in.ua/ua/p"}
DT = {"name": "Ігровий холодильник Mini • Підзаголовок", "brand": "Asson", "description": "Ігровий холодильник Mini • Підзаголовок\n\nДуже цікавий набір.", "images": IMG}
it = ex.build_item("dropt-59298", C, DT, ["https://plutustoys.com.ua/img/dropt/dropt-59298-1.jpg"])
chk("ціна: cost 502,20 («Дроп») → max(753,3; 548,2) = 754 вгору; cost = 502.2, НЕ 478.95", it and it["price"] == 754 == rt.site_price(502.2) and it["cost"] == 502.2)
chk("назва скорочена до «…Mini», дубль-заголовок з опису прибрано, бренд, vendor_code = артикул, stock = STOCK_IN", it["name"] == "Ігровий холодильник Mini" and it["description"] == "Дуже цікавий набір." and it["vendor"] == "Asson" and it["vendor_code"] == "59298" and it["stock"] == ex.STOCK_IN)
chk("габаритів нема публічно → dims_cm/weight_kg None (не вигадуємо)", it["dims_cm"] is None and it["weight_kg"] is None)
chk("нема в наявності → None", ex.build_item("dropt-1", dict(C, in_stock=False), DT, ["u"]) is None)
chk("без фото → None", ex.build_item("dropt-1", C, DT, []) is None)
chk("неправильний id → None", ex.build_item("rt-1", C, DT, ["u"]) is None and ex.build_item("dropt-x", C, DT, ["u"]) is None)
chk("російська назва (ы/э) → None", ex.build_item("dropt-1", C, dict(DT, name="Набор игрушек Ыгра"), ["u"]) is None)
chk("зброя за назвою → None (політика «не продаємо»)", ex.build_item("dropt-1", C, dict(DT, name="Іграшковий пістолет Бластер"), ["u"]) is None)
chk("ціна 0/нечислова → None", ex.build_item("dropt-1", dict(C, drop=0), DT, ["u"]) is None and ex.build_item("dropt-1", dict(C, drop="x"), DT, ["u"]) is None)
chk("російський опис → без опису, картка лишається", ex.build_item("dropt-1", C, dict(DT, description="Набор для проведения праздника"), ["u"])["description"] == "")

# докачування: нові першими, бюджет, TTL, збій сторінки не валить
cache, fetched = {}, []


def f_det(url):
    fetched.append(url)
    if url.endswith("bad"):
        raise OSError("x")
    return DET


cat3 = {str(i): {"sku": str(i), "url": f"https://dropt.in.ua/ua/{i}", "in_stock": True} for i in range(5)}
cat3["oos"] = {"sku": "oos", "url": "https://dropt.in.ua/ua/oos", "in_stock": False}
cat3["4"]["url"] = "https://dropt.in.ua/ua/bad"
n = ex.refresh_details(cat3, cache, f_det, 1000.0, budget=3)
chk("докачування: бюджет 3, OOS не качаємо", n == 3 and len(fetched) == 3 and "https://dropt.in.ua/ua/oos" not in fetched)
fetched.clear()
n = ex.refresh_details(cat3, cache, f_det, 1001.0, budget=10)
chk("друге: лише ті, що без запису; збій сторінки пропущено без винятку", len(fetched) == 2 and "https://dropt.in.ua/ua/bad" in fetched and len(cache) == 4)
fetched.clear()
ex.refresh_details(cat3, cache, f_det, 1002.0, budget=10, ttl=10 ** 6)
chk("свіжі записи (TTL) не качаємо вдруге (крім тієї, що збоїть)", fetched == ["https://dropt.in.ua/ua/bad"])
fetched.clear()
ex.refresh_details(cat3, cache, f_det, 10 ** 7, budget=10, ttl=100)
chk("протухлі (TTL) оновлюються", len(fetched) == 5)

# end-to-end
with tempfile.TemporaryDirectory() as td:
    td = Path(td)
    out, cp, imgd = td / "supplier_dropt.json", td / "cache.json", td / "img"
    saved_idx = ex.INDEX_FILE
    ex.INDEX_FILE = td / "dropt_index_ids.json"
    catalog = {"59298": dict(C), "59080": dict(C, sku="59080", in_stock=False, name="Powerbank"), "59001": dict(C, sku="59001", name="Пістолет-бластер • Х", url="https://dropt.in.ua/ua/gun")}
    fetch_html = lambda u: DET.replace("Рюкзак 669 • Міський", "Ігровий холодильник Mini • Підзаголовок") if not u.endswith("gun") else DET.replace("Рюкзак 669 • Міський", "Пістолет-бластер • Х")   # noqa: E731

    def fake_img(url, dest):
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"jpg")
        return True

    NOW = datetime(2026, 11, 4, 12, 0, tzinfo=timezone.utc)
    ok = ex.run_once(catalog=catalog, failed=[], fetch=fetch_html, out=out, cache_path=cp, img_dir=imgd, fetch_img=fake_img, now=NOW)
    d = json.loads(out.read_text(encoding="utf-8")) if out.exists() else {}
    chk("end-to-end: у файлі лише dropt-59298 (OOS і зброя відсіяні), catalog_n = 3, updated ISO", ok and list(d.get("items", {})) == ["dropt-59298"] and d["catalog_n"] == 3 and d["updated"].startswith("2026-11-04T12:00:00"))
    chk("фото на диску й URL у файлі на нашому домені; index=False за замовчуванням", (imgd / "dropt-59298-1.jpg").exists() and d["items"]["dropt-59298"]["images"][0] == "https://plutustoys.com.ua/img/dropt/dropt-59298-1.jpg" and d["items"]["dropt-59298"]["index"] is False)
    if os.name != "nt":
        chk("права файлу 0600 (там cost)", oct(out.stat().st_mode & 0o777) == "0o600")
    ex.INDEX_FILE.write_text(json.dumps({"ids": ["dropt-59298", "rt-5", "junk"]}), encoding="utf-8")
    ex.run_once(catalog=catalog, failed=[], fetch=fetch_html, out=out, cache_path=cp, img_dir=imgd, fetch_img=fake_img, now=NOW)
    chk("dropt_index_ids.json → index=True лише для валідного dropt-id", json.loads(out.read_text(encoding="utf-8"))["items"]["dropt-59298"]["index"] is True)
    before = out.read_bytes()
    chk("збій розділу → False, файл не змінено", ex.run_once(catalog=catalog, failed=["akustika"], fetch=fetch_html, out=out, cache_path=cp, img_dir=imgd, fetch_img=fake_img, now=NOW) is False and out.read_bytes() == before)
    chk("порожній каталог → False, файл не змінено", ex.run_once(catalog={}, failed=[], fetch=fetch_html, out=out, cache_path=cp, img_dir=imgd, fetch_img=fake_img, now=NOW) is False and out.read_bytes() == before)
    big = {str(i): dict(C, sku=str(i)) for i in range(10)}
    out.write_text(json.dumps({"updated": "x", "catalog_n": 100, "items": {}}), encoding="utf-8")
    before = out.read_bytes()
    chk("усадка каталогу (10 проти 100) → False, файл не змінено", ex.run_once(catalog=big, failed=[], fetch=fetch_html, out=out, cache_path=cp, img_dir=imgd, fetch_img=fake_img, now=NOW) is False and out.read_bytes() == before)
    out.unlink()
    chk("нічого відсіяти немає (усі OOS) → False, файл не створено", ex.run_once(catalog={"1": dict(C, sku="1", in_stock=False)}, failed=[], fetch=fetch_html, out=out, cache_path=cp, img_dir=imgd, fetch_img=fake_img, now=NOW) is False and not out.exists())
    ex.INDEX_FILE = saved_idx

svc = (HERE / "dropt-supplier-export.service").read_text(encoding="utf-8")
chk("юніт: venv-python, ExecStart на dropt_supplier_export.py", "/opt/plutustoys/venv/bin/python3 /opt/plutustoys/dropt_supplier_export.py" in svc)
chk("розділи = рішення власника 04.11 (7, без Автотоварів/Краси/Техніки/Дому/TV-SHOP)", set(dp.CATEGORIES) == {"tovary-dlya-detey", "akustika", "elektronika", "turizm-otdykh-i-sport", "novyy-god", "tovary-dlya-zhivotnykh", "ryukzaki-sumki-portmone"})
gi = (HERE / ".gitignore").read_text(encoding="utf-8")
chk(".gitignore: приватний файл і кеш (cost) не потраплять у git", "/supplier_dropt.json" in gi and "/dropt_detail_cache.json" in gi and "/site/img/dropt/" in gi)

print("\nFAIL:" if F else "\nOK: усі перевірки пройдені", *F, sep="\n")
sys.exit(1 if F else 0)

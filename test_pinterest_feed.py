#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_pinterest_feed.py — генератор Pinterest-каталогу. `python test_pinterest_feed.py` -> exit 0/1."""
import sys, tempfile
from datetime import date
from pathlib import Path
import generate_pinterest_feed as gp

fails = []
def chk(n, c):
    print(f"[{'OK ' if c else 'FAIL'}] {n}")
    if not c: fails.append(n)

XML = """<?xml version="1.0" encoding="UTF-8"?><rss xmlns:g="http://base.google.com/ns/1.0" version="2.0"><channel>
{items}</channel></rss>"""
def item(i, name, price="200.00 UAH", avail="in stock", img="https://x/1.jpg", desc="Опис\tз табом\nі переносом"):
    return f"<item><g:id>{i}</g:id><title>{name}</title><description>{desc}</description><link>https://plutustoys.com.ua/product-{i}.html</link><g:image_link>{img}</g:image_link><g:price>{price}</g:price><g:availability>{avail}</g:availability><g:condition>new</g:condition><g:brand>B</g:brand></item>"

with tempfile.TemporaryDirectory() as td:
    td = Path(td)
    src = td / "meta.xml"
    src.write_text(XML.format(items="".join([
        item(1, "Пазл Весела ферма"), item(2, "Водний пістолет Динозавр"), item(3, "Лялька Софі", price="50.00 UAH"),
        item(4, "Лялька Дорога", price="3000.00 UAH"), item(5, "Конструктор Блоки", avail="out of stock"),
        item(6, "Настільна гра Квест", img=""), item(7, "Плюшевий ведмідь Тедді", price="320.00 UAH")])), encoding="utf-8")
    items = gp.load_items(src)
    chk("load_items: лише in stock з фото", sorted(p["sku"] for p in items) == ["1", "2", "3", "4", "7"])
    rows = gp.build_rows(items, today=date(2026, 11, 27))
    ids = sorted(r[0] for r in rows)
    chk("відсів: зброя (2), ціна <80 (3) і >1500 (4) прибрані; лишилось 1 і 7", ids == ["1", "7"])
    r1 = next(r for r in rows if r[0] == "1")
    chk("link має UTM pinterest/catalog", "utm_source=pinterest" in r1[3] and "utm_medium=catalog" in r1[3])
    chk("таб/перенос у описі прибрані", "\t" not in r1[2] and "\n" not in r1[2])
    out = td / "p.xml"; gp.write_xml(rows, out)
    back = gp.load_items(out)
    chk("XML: валідний, 2 товари in stock, читається тим самим load_items", sorted(p["sku"] for p in back) == ["1", "7"])
    chk("XML: link зберіг UTM", all("utm_source=pinterest" in p["url"] for p in back))
    chk("with_utm: не дублює utm_source", gp.with_utm("https://a/b.html?utm_source=x") == "https://a/b.html?utm_source=x")
    (td / "bad.xml").write_text("<rss", encoding="utf-8")
    chk("биті XML -> код 1, вихідний файл не створено", gp.main.__globals__["ET"] is not None and (lambda: (sys.argv.__setitem__(slice(None), ["x", "--src", str(td / "bad.xml"), "--out", str(td / "o.xml")]) or gp.main()))() == 1 and not (td / "o.xml").exists())
print("ALL OK" if not fails else f"FAILED: {fails}")
sys.exit(1 if fails else 0)

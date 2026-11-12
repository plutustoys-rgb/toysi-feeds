#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_site_snippet_own_text.py — 12.11.2026 (SEO, варіант Б): сніпет (meta description) і JSON-LD description беруться з НАШОГО тексту картки, коли опис постачальника — «Бренд: …», коротший за 60 знаків або російський.
Інваріанти:
  • ворота SITE_SNIPPET_OWN_TEXT вимкнено (за замовчуванням): сторінка байт-у-байт як була (мета/JSON-LD з опису постачальника);
  • ввімкнено: заглушка «Бренд: …», короткий (<60) і російський опис → перший абзац нашого тексту в <meta name=description>, og:description і JSON-LD description;
  • нормальний український опис постачальника — без змін; нашого тексту нема — без змін (навіть із заглушкою);
  • решта сторінки (тіло, назва) не змінюється ні за яких воріт.
Самодостатній, без мережі."""
import json
import os
import re
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).parent
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
os.environ.setdefault("AUDIT_NO_TELEGRAM", "1")
for k in ("SITE_LIQPAY_LIVE", "SITE_SELLER_TAXID", "SITE_BUILD_LIMIT", "SITE_SNIPPET_OWN_TEXT"):
    os.environ.pop(k, None)
sys.path.insert(0, str(HERE / "site"))
sys.path.insert(0, str(HERE))
import build_site as bs  # noqa: E402

F = []


def chk(n, c):
    print(f"[{'OK ' if c else 'FAIL'}] {n}")
    if not c:
        F.append(n)


chk("ворота за замовчуванням вимкнено", bs.SNIPPET_OWN_TEXT is False)
LONG_UA = "Яскравий набір для творчості з великою кількістю деталей, фарб і пензликів, який складається у зручну коробку для зберігання."
cases = {
    "701": ("Бренд: MIC", "Конструктор Альфа з нашого тексту про набір деталей. Країна виробник — Китай."),
    "702": ("Короткий опис.", "Лялька Бета з нашого тексту, що описує набір і комплект одягу для ляльки."),
    "703": ("Яркие цветочные пазлы на 100 деталей, большой набор для детей и взрослых", "Пазл Гамма — український текст картки про набір із сотні деталей."),
    "704": (LONG_UA, "Машинка Дельта — наш текст, який не має потрапляти в сніпет."),
    "705": ("Бренд: MIC", None),
    "706": ("Бренд: MIC", "Конструктор Дзета: збираєш модель із деталей, а потім граєшся нею. Країна виробник — Китай."),
    "707": ("Бренд: MIC", "Коротко про набір Ета."),
    "708": ("Бренд: MIC", "Малюк тримає в руці пухнасту обіймашку Тета; набір для обіймів, країна виробник — Україна."),
}


def item(pid, desc):
    return {"id": str(pid), "name": f"Іграшка номер {pid}", "vendor": "Test", "price": 300.0, "stock": 5, "category_name": "Машинки", "category_id": "10",
            "pictures": [f"https://toysi.ua/p/enl_{pid}.jpg"], "description": desc, "barcode": "", "supplier": "Toysi"}


catalog = {str(i): item(i, LONG_UA) for i in range(1, 41)}
for pid, (desc, _t) in cases.items():
    catalog[pid] = item(pid, desc)
bs.tp.fetch_toysi_catalog = lambda: catalog
bs.tp.assert_catalog_size_sane = lambda c, *a, **k: None
import telegram_notify  # noqa: E402
telegram_notify.send_throttled_alert = lambda *a, **k: True
import site_photo_probe  # noqa: E402
site_photo_probe.run = lambda idx: None
bs.LIMIT = 0
_tf = Path(tempfile.mkdtemp()) / "product_texts.json"
_tf.write_text(json.dumps({pid: t for pid, (_d, t) in cases.items() if t}, ensure_ascii=False), encoding="utf-8")
bs._PRODUCT_TEXTS_FILE = str(_tf)   # build() сам перечитує тексти з цього файлу


def build(gate):
    out = Path(tempfile.mkdtemp())
    bs.OUT = str(out)
    bs.SNIPPET_OWN_TEXT = gate
    try:
        bs.build()
    finally:
        bs.SNIPPET_OWN_TEXT = False
    return out


def meta(out, pid):
    h = (out / f"product-{pid}.html").read_text(encoding="utf-8")
    md = re.search(r'<meta name="description" content="([^"]*)"', h)
    ld = re.search(r'"description": "((?:[^"\\]|\\.)*)"', h)
    return h, (md.group(1) if md else ""), (json.loads('"' + ld.group(1) + '"') if ld else "")


off = build(False)
on = build(True)
OWN = {pid: t for pid, (_d, t) in cases.items() if t}

for pid in cases:
    h0, m0, l0 = meta(off, pid)
    chk(f"вимкнено {pid}: наш текст у сніпет НЕ потрапляє", pid not in OWN or OWN[pid].split(".")[0] not in m0)
for pid in ("701", "702", "703", "708"):
    h1, m1, l1 = meta(on, pid)
    first = OWN[pid].split("\n")[0]
    chk(f"ввімкнено {pid} (заглушка/короткий/російський): мета-опис і JSON-LD — з нашого тексту", first[:40] in m1 and first[:40] in l1)
_WHY = {"704": "нормальний опис", "705": "нашого тексту нема", "706": "наш перший абзац містить 2-гу особу («збираєш», «граєшся») — у сніпет не йде", "707": "наш текст майже не додає до назви (коротший за назву + 40)"}
for pid in ("704", "705", "706", "707"):
    h0, m0, l0 = meta(off, pid)
    h1, m1, l1 = meta(on, pid)
    chk(f"ввімкнено {pid} ({_WHY[pid]}): мета і JSON-LD без змін", m0 == m1 and l0 == l1)
chk("3-тя особа («Малюк тримає в руці…», «обіймашка») НЕ хибно приймається за 2-гу: 708 бере наш текст", "Малюк тримає в руці" in meta(on, "708")[1])
chk("704: нормальний український опис постачальника лишається у сніпеті", "Яскравий набір" in meta(on, "704")[1])
same_body = all(re.sub(r'<meta name="description"[^>]*>|<meta property="og:description"[^>]*>|"description": "(?:[^"\\]|\\.)*"', "", meta(off, p)[0]) == re.sub(r'<meta name="description"[^>]*>|<meta property="og:description"[^>]*>|"description": "(?:[^"\\]|\\.)*"', "", meta(on, p)[0]) for p in cases)
chk("ворота змінюють ЛИШЕ мета-опис / og:description / JSON-LD description (решта сторінки та сама)", same_body)

print()
if F:
    print(f"FAILED ({len(F)}):")
    for n in F:
        print("  -", n)
    sys.exit(1)
print("ALL OK")

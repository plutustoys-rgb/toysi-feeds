#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_site_stock_badge.py — 12.10.2026 (SMM): бейдж «Залишилось N шт» на картках, коли залишок Toysi 1–3.
Залишок — ТОЧНЕ число з <ostatok> («1 шт.», «2 шт.», «3 шт.»; діапазони типу «10…50» парсер зводить до мінімуму, але в зоні 1–3 діапазонів немає — перевірено на VPS 12.10: «1 шт.» 7486, «2 шт.» 3117, «3 шт.» 1845).
Перевіряємо: бейдж лише при 1..3, текст без вигаданої терміновості, зелений «У наявності» не дублюється, ≥4 і 0 — без бейджа, CSS, клони каруселі несуть бейдж (cloneNode)."""
import os
import re
import sys
from pathlib import Path

HERE = Path(__file__).parent
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
os.environ.setdefault("AUDIT_NO_TELEGRAM", "1")
sys.path.insert(0, str(HERE / "site"))
sys.path.insert(0, str(HERE))
import build_site as bs  # noqa: E402

F = []


def chk(n, c):
    print(f"[{'OK ' if c else 'FAIL'}] {n}")
    if not c:
        F.append(n)


def prod(stock):
    return {"id": "1", "name": "Тест <b>", "price": 200, "photo": "https://x/1.jpg", "stock": stock, "category": "Іграшки"}


def card(stock, **kw):
    kw.setdefault("badge", True)
    return bs.tile(prod(stock), **kw)


for st in (1, 2, 3):
    h = card(st)
    chk(f"залишок {st}: бейдж «Залишилось {st} шт» всередині .ph, без зеленого «У наявності»", f'<span class="stock-badge">Залишилось {st} шт</span>' in h and "У наявності" not in h and 'class="av"' not in h
        and h.index("stock-badge") < h.index('<div class="info">'))
for st in (4, 5, 10, 100):
    h = card(st)
    chk(f"залишок {st}: бейджа нема, є «У наявності»", "stock-badge" not in h and "У наявності" in h)
h0 = card(0)
chk("залишок 0: бейджа нема, «Немає»", "stock-badge" not in h0 and "Немає" in h0 and "Залишилось" not in h0)
chk("межа налаштована константою STOCK_BADGE_MAX = 3", bs.STOCK_BADGE_MAX == 3)
chk("за замовчуванням (каталог, категорії, «схожі», пошук) бейджа НЕМАЄ навіть при залишку 1–3: лишається зелений «У наявності» (рішення SMM 12.10)", all("stock-badge" not in bs.tile(prod(s)) and "У наявності" in bs.tile(prod(s)) for s in (1, 2, 3)) and all("stock-badge" not in bs.grid([prod(s)]) for s in (1, 2, 3)))
chk("grid(badge=True) пропускає бейдж у плитки (для /ig.html і /nabory.html)", "stock-badge" in bs.grid([prod(2)], badge=True) and "stock-badge" not in bs.grid([prod(2)]))
_src = (HERE / "site" / "build_site.py").read_text(encoding="utf-8")
chk("карусель головної і /ig.html викликають плитки з badge=True; каталог/категорії/схожі/новинки — ні", 'tile(p, cls="slide", eager=(i < 3), prio=(i == 0), badge=True)' in _src and "grid(shown, eager_n=min(3, len(shown)), badge=True)" in _src and "grid(chunk, eager_n=(4 if k == 1 else 0))" in _src and "grid(similar)" in _src and "grid(related)" in _src)
chk("лише точний цілий залишок у тексті (без «останній», «лише», таймерів, «у місті»)", all(w not in card(1).lower() for w in ("останній", "лише", "таймер", "у місті", "поспіш", "встигни")))
chk("назва товару екранується поруч із бейджем (нема сирого <b>)", "<b>" not in card(2).replace("<b>Тест", "") and "&lt;b&gt;" in card(2))
chk("карусельна картка (cls=slide, eager, prio): бейдж зберігається", 'class="card slide"' in card(1, cls="slide", eager=True, prio=True) and "stock-badge" in card(1, cls="slide", eager=True, prio=True))
css = (HERE / "site" / "assets" / "styles.css").read_text(encoding="utf-8")
chk("CSS: .card .ph position:relative і .stock-badge абсолютний зверху зліва, помаранчевий акцент, не перехоплює клік", ".card .ph{aspect-ratio:1/1;background:var(--bg);position:relative}" in css and re.search(r"\.card \.stock-badge\{position:absolute;top:8px;left:8px;[^}]*pointer-events:none", css) is not None)
chk("картка товару (сторінка): «У наявності» не змінено бейджем (лише плитки)", 'avail = "У наявності" if p["stock"] > 0 else "Немає в наявності"' in (HERE / "site" / "build_site.py").read_text(encoding="utf-8"))

print()
if F:
    print(f"FAILED ({len(F)}):")
    for n in F:
        print("  -", n)
    sys.exit(1)
print("ALL OK")

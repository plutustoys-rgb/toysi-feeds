#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_site_liqpay_requirements.py — 13.10.2026: вимоги LiqPay до мерчанта (Код): логотип LiqPay у футері всіх сторінок і біля вибору оплати карткою (live), правила оплати карткою й повернення коштів
на ту саму картку (строк 7 робочих днів — рішення власника 13.10) у «Доставці», «Поверненні» й оферті; поки LiqPay не live — чесне застереження «запускається найближчим часом»."""
import os, sys, tempfile
from pathlib import Path

HERE = Path(__file__).parent
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
os.environ.setdefault("AUDIT_NO_TELEGRAM", "1")
os.environ.pop("SITE_LIQPAY_LIVE", None)
sys.path.insert(0, str(HERE / "site"))
sys.path.insert(0, str(HERE))
import importlib
import build_site as bs  # noqa: E402

F = []


def chk(n, c):
    print(f"[{'OK ' if c else 'FAIL'}] {n}")
    if not c:
        F.append(n)


svg = (HERE / "site/assets/liqpay-logo.svg").read_text(encoding="utf-8")
chk("логотип: офіційний SVG із брендбука LiqPay (viewBox 500×104), без скриптів і зовнішніх посилань", "<svg" in svg and 'viewBox="0 0 500 104"' in svg and "<script" not in svg.lower() and "onload" not in svg.lower() and "href=" not in svg.lower())
for live in (False, True):
    bs.LIQPAY_LIVE = live
    out = Path(tempfile.mkdtemp())
    bs.OUT = str(out)
    bs.write_cart([])
    cart = (out / "cart.html").read_text(encoding="utf-8")
    foot = bs.footer()
    tag = "live" if live else "не live"
    chk(f"{tag}: футер містить логотип LiqPay з посиланням на liqpay.ua (alt «LiqPay»), розмір ≥ 100×21", 'href="https://www.liqpay.ua/"' in foot and 'alt="LiqPay"' in foot and (live or "карт" not in foot) and 'width="100" height="21"' in foot and "liqpay-logo.svg" in foot)
    chk(f"{tag}: футер із логотипом є на кошику (усі сторінки мають footer())", "liqpay-logo.svg" in cart)
    if live:
        chk("live: біля вибору «Оплата карткою онлайн» теж логотип", 'value="prepaid"> Оплата карткою онлайн <a href="https://www.liqpay.ua/"' in cart)
    else:
        chk("не live: біля вибору оплати логотипа/радіо немає (картку покупцям ще не пропонуємо)", 'value="prepaid"' not in cart)
    # тексти (трюк: _DELIVERY/_RETURNS/_OFFER збираються при імпорті модуля → перезавантажуємо з потрібним LIQPAY_LIVE)
    os.environ["SITE_LIQPAY_LIVE"] = "1" if live else ""
    m = importlib.reload(bs)
    d, r, o = m._DELIVERY, m._RETURNS, m._OFFER
    pay_ok = ("не передаються та не зберігаються на нашому сайті" in d) if live else ("не передаватимуться та не зберігатимуться на нашому сайті" in d and "Після запуску онлайн-оплати замовлення можна буде оплатити" in d)
    chk(f"{tag}: «Доставка» — оплата карткою через LiqPay, дані картки не зберігаємо (до запуску — майбутній час), повернення на ту саму картку за 7 робочих днів", "через сервіс LiqPay" in d and pay_ok and "ту саму картку" in d and "протягом <b>7 робочих днів</b>" in d and "погодження — після отримання й перевірки товару" in d)
    chk(f"{tag}: теперішній час «Оплатити замовлення можна банківською карткою» лише в live (до запуску картки не обіцяємо)", ("Оплатити замовлення можна банківською карткою онлайн через сервіс LiqPay" in d) == live)
    chk(f"{tag}: «Повернення» — правило повернення коштів на картку (7 робочих днів, залежить від банку)", "ту саму картку" in r and "протягом <b>7 робочих днів</b>" in r and "залежить від банку" in r)
    chk(f"{tag}: оферта — картка через LiqPay і повернення коштів на картку", "через сервіс LiqPay" in o and "протягом <b>7 робочих днів</b>" in o)
    if live:
        chk("live: застережень «запускається найближчим часом» / «(після її запуску…)» нема", "запускається найближчим часом" not in d and "після її запуску" not in o)
    else:
        chk("не live: застереження «Оплата карткою онлайн запускається найближчим часом; зараз доступна оплата накладеним платежем» у «Доставці» й «(після її запуску на сайті)» в оферті", "Оплата карткою онлайн запускається найближчим часом" in d and "(після її запуску на сайті)" in o)
    bs = m
os.environ.pop("SITE_LIQPAY_LIVE", None)
importlib.reload(bs)
print()
if F:
    print(f"FAILED ({len(F)}):")
    for n in F:
        print("  -", n)
    sys.exit(1)
print("ALL OK")

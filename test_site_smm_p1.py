# -*- coding: utf-8 -*-
"""SMM P1 (2026-10-06): cross-sell без уцінки/копійок; оплата карткою не обіцяється, поки LiqPay не бойовий;
футер із IG/FB; радіо оплати не розтягується CSS."""
import os, sys, importlib
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "site"))
F = []
def chk(n, c):
    print(f"[{'OK ' if c else 'FAIL'}] {n}")
    if not c: F.append(n)

def load(live):
    if live: os.environ["SITE_LIQPAY_LIVE"] = "1"
    else: os.environ.pop("SITE_LIQPAY_LIVE", None)
    import build_site
    return importlib.reload(build_site)

b = load(False)
P = lambda **k: {"stock": 5, "price": 200, "category": "Конструктори", "name": "Конструктор", **k}
chk("звичайний товар підходить для cross-sell", b._addon_ok(P()))
chk("уцінка (категорія) відсікається", not b._addon_ok(P(category="Уцінка")))
chk("«Уценка» (рос.) відсікається", not b._addon_ok(P(category="Уценка товаров")))
chk("«розпродаж» у назві відсікається", not b._addon_ok(P(name="Розпродаж. Браслет")))
chk("12 ₴ відсікається", not b._addon_ok(P(price=12)))
chk("30 ₴ ще підходить", b._addon_ok(P(price=30)))
chk("немає в наявності відсікається", not b._addon_ok(P(stock=0)))
chk("без LiqPay футер не обіцяє картку", "карт" not in b.footer())
chk("без LiqPay футер має Instagram і Facebook", "instagram.com/plutustoys.ua" in b.footer() and "facebook.com" in b.footer())
chk("без LiqPay тексти про оплату без картки", all("карт" not in x for x in (b.PAY_HERO, b.PAY_PRODUCT, b.PAY_ABOUT, b.PAY_OFFER)))
chk("без LiqPay «Про нас»/оферта без картки", "карт" not in b._ABOUT and "карт" not in b._OFFER)
b = load(True)
chk("з LiqPay футер згадує картку", "карт" in b.footer())
chk("з LiqPay оферта згадує картку", "карт" in b._OFFER)
css = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "site", "assets", "styles.css"), encoding="utf-8").read()
chk("CSS: радіо оплати не width:100%", "input[type=radio]{width:auto" in css)
load(False)
print(f"\n{'❌ ПРОВАЛЕНО: ' + str(F) if F else '✅ SMM P1 — усі перевірки коректні.'}")
sys.exit(1 if F else 0)

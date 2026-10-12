#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_site_payment_texts.py — 12.10.2026, прохання власника: «постійно повторюється у різних місцях, що оплата після отримання… це просто один з варіантів, LiqPay поки на перевірці».
Що тримає тест (повний build() на синтетичному каталозі; SITE_LIQPAY_LIVE не заданий = LiqPay ще не бойовий):
  • meta description і og:description (головна, картка, категорія, каталог) НЕ містять способу оплати (займав видиму в Google частину опису) — лише асортимент/доставка/повернення;
  • футер без повтору способу оплати (поки LiqPay не бойовий; з SITE_LIQPAY_LIVE=1 — з карткою);
  • картка товару: PAY_PRODUCT («Оплата при отриманні (накладений платіж).») НЕ дублюється в блоці доставки (бейдж довіри вгорі вже це каже); вартість доставки — виміряна «70–90 ₴ до 2 кг»;
  • «Про нас»: жодної згадки способу оплати; «Передоплати на сайті немає» ніде нема (позитивне формулювання «розраховуєтесь при отриманні на відділенні» лише на «Доставка»);
  • ЗАЛИШЕНО: бейдж у героя, «Доставка» (один абзац «Оплата»), кошик біля вибору способу, оферта;
  • архітектура: одна змінна SITE_LIQPAY_LIVE перемикає всі тексти (з нею тексти про картку повертаються)."""
import contextlib
import io
import os
import re
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).parent
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
os.environ.setdefault("AUDIT_NO_TELEGRAM", "1")
os.environ.pop("SITE_LIQPAY_LIVE", None)
sys.path.insert(0, str(HERE / "site"))
sys.path.insert(0, str(HERE))
import build_site as bs  # noqa: E402

F = []


def chk(n, c):
    print(f"[{'OK ' if c else 'FAIL'}] {n}")
    if not c:
        F.append(n)


def item(pid, name, category="Картини за номерами", price=300.0):
    return {"id": str(pid), "name": name, "vendor": "T", "price": price, "stock": 5, "category_name": category, "category_id": "10",
            "pictures": [f"https://toysi.ua/p/enl_{pid}.jpg"], "description": "Опис товару достатньої довжини для картки сайту, понад сорок символів.",
            "params": {}, "url": f"https://toysi.ua/{pid}"}


catalog = {str(i): item(i, f"Товар {i}", price=250 + i) for i in range(1, 30)}
catalog["30"] = dict(item(30, "Без опису"), description="")        # fallback-шлях meta description (name — ціна. Доставка …)
out = Path(tempfile.mkdtemp())
bs.OUT = str(out)
bs.tp.fetch_toysi_catalog = lambda: dict(catalog)
bs.tp.assert_catalog_size_sane = lambda c, *a, **k: None
import site_photo_probe  # noqa: E402
site_photo_probe.run = lambda idx: None
with contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(io.StringIO()):
    bs.build()


def rd(f):
    return (out / f).read_text(encoding="utf-8")


def descs(h):
    return re.findall(r'<meta (?:name="description"|property="og:description") content="([^"]*)"', h)


pages = {"index.html": rd("index.html"), "product-5.html": rd("product-5.html"), "product-30.html": rd("product-30.html"), "category-kartyny-za-nomeramy.html": rd("category-kartyny-za-nomeramy.html"), "catalog.html": rd("catalog.html")}
for f, h in pages.items():
    d = descs(h)
    chk(f"{f}: meta/og description є і БЕЗ способу оплати", len(d) >= 2 and all(not re.search(r"оплат|накладен|при отриманні|карткою", x, re.I) for x in d))
chk("головна: у description — асортимент і доставка", "Конструктори, ляльки" in descs(pages["index.html"])[0] and "доставкою Новою Поштою" in descs(pages["index.html"])[0])
vis = lambda h: re.sub(r"<script.*?</script>", "", h, flags=re.S)
chk("футер (поки LiqPay не бойовий): без способу оплати", all(not re.search(r"оплат|накладен|при отриманні", re.sub(r"<[^>]+>", " ", vis(h).split("<footer")[1]), re.I) for h in pages.values()))
prod = vis(pages["product-5.html"]).split("<footer")[0]
chk("картка: PAY_PRODUCT («Оплата при отриманні (накладений платіж).») не дублюється", "Оплата при отриманні (накладений платіж)." not in prod)
chk("картка: бейдж довіри «✓ Оплата при отриманні» ЗАЛИШЕНО, вартість доставки «орієнтовно 70–90 ₴ до 2 кг»", "✓ Оплата при отриманні" in prod and "орієнтовно 70–90 ₴ до 2 кг" in prod)
chk("картка: «при отриманні» — не більше 2 разів (бейдж + рядок про сплату доставки)", len(re.findall(r"при отриманні", prod)) <= 2)
about = vis(rd("about.html"))
cat_body = vis(rd("category-kartyny-za-nomeramy.html")).split("<footer")[0]
chk("тексти категорій (site/category_texts.json): жодного «оплата при отриманні» у тілі сторінки категорії (аудит #689: у 10 текстах був хвіст)", "кат-інтро" not in cat_body and 'class="cat-intro"' in cat_body and not re.search(r"оплат|при отриманні", cat_body.split('class="cat-intro"')[1].split("</div>")[0], re.I))
chk("«Про нас»: жодної згадки способу оплати", not re.search(r"накладен|при отриманні|оплат", about.split("<footer")[0], re.I))
dl = vis(rd("delivery.html"))
chk("«Доставка»: розділ «Оплата» ЗАЛИШЕНО з позитивним формулюванням (одне «розраховуєтесь при отриманні…», без дубля «при отриманні» в реченні); «Передоплати на сайті немає» нема", "озраховуєтесь при отриманні замовлення на відділенні" in dl and "Передоплати на сайті немає" not in dl and "накладеним платежем</b> при отриманні" not in dl)
chk("«Доставка»: тарифне речення не дублюється (рівно ОДИН блок про вартість)", dl.count("за тарифом перевізника") == 1 and "за тарифами перевізника" not in dl)
home = vis(rd("index.html")).split("<footer")[0]
chk("головна: спосіб оплати РІВНО в чипі героя (було 4 згадки на сторінку)", len(re.findall(r"при отриманні|накладен", home, re.I)) == 1 and "🚚 Нова Пошта · оплата при отриманні" in home)
cart = vis(rd("cart.html"))
chk("кошик: біля вибору способу ЗАЛИШЕНО радіокнопку «Оплата при отриманні»", 'value="cod" checked' in cart and "Оплата при отриманні (накладений платіж на Новій Пошті)" in cart)
chk("оферта: спосіб оплати ЗАЛИШЕНО (юридичний текст)", "накладеним платежем при отриманні Товару" in vis(rd("offer.html")))

# архітектура: SITE_LIQPAY_LIVE=1 повертає картку в тексти (одна змінна перемикає всі)
os.environ["SITE_LIQPAY_LIVE"] = "1"
import importlib  # noqa: E402
importlib.reload(bs)
bs.OUT = str(Path(tempfile.mkdtemp()))
bs.tp.fetch_toysi_catalog = lambda: dict(catalog)
bs.tp.assert_catalog_size_sane = lambda c, *a, **k: None
with contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(io.StringIO()):
    bs.build()
live_footer = vis((Path(bs.OUT) / "index.html").read_text(encoding="utf-8")).split("<footer")[1]
live_delivery = vis((Path(bs.OUT) / "delivery.html").read_text(encoding="utf-8"))
chk("SITE_LIQPAY_LIVE=1: футер і «Доставка» знову згадують оплату карткою онлайн", "карткою онлайн" in live_footer and "карткою онлайн" in live_delivery)
os.environ.pop("SITE_LIQPAY_LIVE", None)

print()
if F:
    print(f"FAILED ({len(F)}):")
    for n in F:
        print("  -", n)
    sys.exit(1)
print("ALL OK")

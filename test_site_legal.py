#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_site_legal.py — відомості про продавця, політика конфіденційності, розділ «Оплата» на сайті (09.10.2026, вимоги ст.7 Закону про е-комерцію + LiqPay «Вимоги до мерчанта» п.3, п.7).
Інваріанти: ПІБ ФОП + адреса є на «Контакти», «Публічна оферта», «Політика конфіденційності»; РНОКПП показується ЛИШЕ коли SITE_SELLER_TAXID заданий і валідний (інакше рядка нема, жодних плейсхолдерів);
privacy.html існує, є у футері й повертається в списку для sitemap; на «Доставка» є розділ «Оплата» (без обіцянки картки, поки LiqPay не бойовий; з карткою — коли SITE_LIQPAY_LIVE=1);
у жодній сторінці не лишається невирішений {PLACEHOLDER}. Самодостатній, мережа не потрібна."""
import importlib
import os
import re
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "site"))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

F = []


def chk(n, c):
    print(f"[{'OK ' if c else 'FAIL'}] {n}")
    if not c:
        F.append(n)


def render(env):
    for k in ("SITE_SELLER_TAXID", "SITE_LIQPAY_LIVE"):
        os.environ.pop(k, None)
    os.environ.update(env)
    import build_site as b
    b = importlib.reload(b)
    tmp = tempfile.mkdtemp()
    b.OUT = tmp
    names = b.write_trust_pages()
    html = {n: open(os.path.join(tmp, n), encoding="utf-8").read() for n in names}
    return b, names, html


b, names, h = render({"SITE_SELLER_TAXID": "1234567890"})
for f in ("contacts.html", "offer.html", "privacy.html"):
    chk(f"{f}: ПІБ ФОП + адреса", "ФОП Чечетенко Олександр Юрійович" in h[f] and "м. Київ, просп. Берестейський, 89а" in h[f])
    chk(f"{f}: РНОКПП показано, коли заданий", "1234567890" in h[f])
chk("privacy.html створено і є в списку для sitemap", "privacy.html" in names)
chk("посилання на політику у футері (будь-яка сторінка)", 'href="privacy.html"' in h["contacts.html"])
chk("політика називає Нову Пошту, постачальника і Meta (реальні отримувачі даних)", all(x in h["privacy.html"] for x in ("Нова Пошта", "постачальник товару", "хешовані")))
chk("жодного невирішеного {PLACEHOLDER} на сторінках довіри", not any(re.search(r"\{[A-Z_]{4,}\}", x) for x in h.values()))
chk("«Доставка» має розділ «Оплата»", "<h2>Оплата</h2>" in h["delivery.html"])
chk("поки LiqPay не бойовий: «Передоплати на сайті немає», картки онлайн не обіцяно", "Передоплати на сайті немає" in h["delivery.html"] and "карткою онлайн" not in h["delivery.html"])

b, names, h = render({})
chk("без SITE_SELLER_TAXID: РНОКПП не показується, плейсхолдера нема", all("РНОКПП" not in h[f] for f in ("contacts.html", "offer.html", "privacy.html")))
b, names, h = render({"SITE_SELLER_TAXID": "<script>1</script>"})
chk("невалідний SITE_SELLER_TAXID не потрапляє в HTML (захист від ін'єкції)", all("<script>1" not in x for x in h.values()))
b, names, h = render({"SITE_LIQPAY_LIVE": "1"})
chk("SITE_LIQPAY_LIVE=1: у «Оплата» є картка онлайн", "карткою онлайн" in h["delivery.html"])
render({})

import subprocess
_ign = subprocess.run(["git", "check-ignore", "-q", "site/privacy.html"], cwd=os.path.dirname(os.path.abspath(__file__)))
chk("site/privacy.html у .gitignore (щоб згенерований РНОКПП не потрапив у публічний git)", _ign.returncode == 0)
b, names, h = render({"SITE_SELLER_TAXID": "1234567890"})
chk("політика: хеші названо псевдонімізацією, не «неможливо перетворити»", "псевдонім" in h["privacy.html"] and "неможливо перетворити" not in h["privacy.html"])
chk("політика: нема неіснуючого механізму «видаляються або знеособлюються»", "знеособлюються" not in h["privacy.html"])
chk("політика: Checkbox названо отримувачем", "Checkbox" in h["privacy.html"])
chk("«Оплата»: умова накладеного платежу до 3 000 ₴", "3 000" in h["delivery.html"])
render({})

print()
if F:
    print("FAILED:", F)
    sys.exit(1)
print("ALL OK")

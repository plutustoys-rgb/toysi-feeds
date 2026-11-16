#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_feed_image_placeholder.py — Google/Meta/Bing: image_link не може бути відносною заглушкою Prom «no-image» (GMC 16.11.2026: invalid_url [image_link], 40 товарів).
ІНВАРІАНТИ: придатне фото = абсолютний http(s) URL і не заглушка; з кількох кандидатів береться ПЕРШЕ придатне; жодного придатного → None (товар пропускається).
`python test_feed_image_placeholder.py`
"""
import os
import re
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
os.environ["AUDIT_NO_TELEGRAM"] = "1"
sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent / "site"))

import generate_google_feed as gg  # noqa: E402

F = []


def chk(label, ok):
    print(("[OK ] " if ok else "[FAIL] ") + label)
    if not ok:
        F.append(label)


u = gg.usable_feed_image
PH = "/cloud-cgi/static/uaprom-static/image/company_site/no-image-200-abc123.png"
chk("відносна заглушка Prom → непридатна", u(PH) is False)
chk("абсолютна заглушка Prom (no-image) → непридатна", u("https://images.prom.ua/static/no-image-200.png") is False)
chk("відносний шлях узагалі → непридатний", u("/img/p/1.jpg") is False)
chk("порожнє/None/число → непридатне", u("") is False and u(None) is False and u(5) is False and u("   ") is False)
chk("фото Prom → придатне", u("https://images.prom.ua/6883260421_w1024_h1024_x.jpg") is True)
chk("фото Toysi → придатне", u("https://toysi.ua/p/297821.jpg") is True)
chk("http:// → придатне (як було)", u("http://example.com/a.jpg") is True)

# вибір першого придатного — той самий вираз, що в build (читаємо з джерела, щоб тест ловив регрес)
src = Path(gg.__file__).read_text(encoding="utf-8")
m = re.search(r"image = next\(\(c for c in \[prom_product\.get\(\"main_image\"\), \*\(item\.get\(\"pictures\"\) or \[\]\)\] if usable_feed_image\(c\)\), None\)", src)
chk("у збірці фіду фото вибирається через usable_feed_image (є вираз next(...))", m is not None)


def pick(prom_main, pictures):
    return next((c for c in [prom_main, *(pictures or [])] if gg.usable_feed_image(c)), None)


chk("заглушка Prom + фото Toysi → береться фото Toysi (товар не втрачається)", pick(PH, ["https://toysi.ua/p/5.jpg"]) == "https://toysi.ua/p/5.jpg")
chk("чисте фото Prom + фото Toysi → береться Prom (як було)", pick("https://images.prom.ua/1.jpg", ["https://toysi.ua/p/5.jpg"]) == "https://images.prom.ua/1.jpg")
chk("заглушка + порожні pictures → None (товар пропускається, не потрапляє в фід з битим URL)", pick(PH, []) is None and pick(PH, None) is None)
chk("заглушка Prom + заглушка в pictures + третє придатне → третє", pick(PH, [PH, "https://toysi.ua/p/9.jpg"]) == "https://toysi.ua/p/9.jpg")

if F:
    print(f"\n❌ ПРОВАЛЕНО: {len(F)}")
    sys.exit(1)
print("\n✅ усе ок")

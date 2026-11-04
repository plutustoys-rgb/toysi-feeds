# -*- coding: utf-8 -*-
"""dropt_parser.py — читач ПУБЛІЧНОГО каталогу Dropt (dropt.in.ua, OpenCart, готовий HTML; вхід у кабінет не потрібен).

Рішення власника 04.11.2026: беремо 7 розділів (CATEGORIES), закупівля — ціна «Дроп» (НЕ «Опт»), формула ціни сайту — та сама, що для RoyalToys.
robots.txt Dropt (перевірено 04.11 і аудитом #1099) ЗАБОРОНЯЄ списки з ?limit= / ?page= / ?sort=, тому списки розділів НЕ читаємо. Джерело — те, що дозволено:
  • sitemap.xml (≈7 800 адрес /ua/<slug>, ≈11,7 МБ) — перелік сторінок;
  • сторінка товару: JSON-LD Product (sku = «Артикул» Dropt, offers.price = «Дроп» — звірено з карткою списку, offers.availability InStock/OutOfStock, brand),
    JSON-LD BreadcrumbList (Розділ / Підрозділ), опис у `#tab-description`, фото `…-1000x1000.<ext>`.
Модуль лише ЧИТАЄ: жодних POST, входу, кошика. Мережа — через fetch(url) → html (підміняється в тестах)."""
import json
import re
import sys
import time
from urllib.parse import urljoin

BASE = "https://dropt.in.ua"
USER_AGENT = "PlutusToys-catalog-sync/1.0 (+https://plutustoys.com.ua)"
REQUEST_DELAY_S = 0.25           # ввічлива пауза між запитами до чужого сайту (robots.txt Dropt Crawl-delay не задає)
RETRIES = 2                      # повторів на мережеву помилку/5xx (аудит #1099: одна помилка не має зупиняти годину)

# рішення власника 04.11.2026 («товари для дітей, акустика, електроніка, туризм відпочинок і спорт, новий рік, товари для тварин, рюкзаки сумки»)
CATEGORIES = {
    "tovary-dlya-detey": "Товари для дітей",
    "akustika": "Акустика",
    "elektronika": "Електроніка",
    "turizm-otdykh-i-sport": "Туризм, відпочинок і спорт",
    "novyy-god": "Новий Рік",
    "tovary-dlya-zhivotnykh": "Товари для тварин",
    "ryukzaki-sumki-portmone": "Рюкзаки, сумки, портмоне",
}
TOP_NAMES = frozenset(CATEGORIES.values())


def make_fetch(session=None, delay=REQUEST_DELAY_S, retries=RETRIES):
    """fetch(url) → html. Мережева помилка/5xx — до `retries` повторів із паузою; 4xx — одразу виняток (рішення про пропуск приймає викликач)."""
    import requests
    s = session or requests.Session()
    last = [0.0]

    def fetch(url, timeout=40):
        for attempt in range(retries + 1):
            wait = delay - (time.monotonic() - last[0])
            if wait > 0:
                time.sleep(wait)
            last[0] = time.monotonic()
            try:
                r = s.get(url, headers={"User-Agent": USER_AGENT, "Accept-Language": "uk"}, timeout=timeout)
                if (r.status_code >= 500 or r.status_code == 429) and attempt < retries:
                    wait429 = 30 if r.status_code == 429 else 2 * (attempt + 1)      # 429 — довша пауза (аудит #1099)
                    time.sleep(min(wait429, 60))
                    continue
                r.raise_for_status()
                return r.text
            except (requests.ConnectionError, requests.Timeout):
                if attempt >= retries:
                    raise
                time.sleep(2 * (attempt + 1))
    return fetch


def parse_sitemap(xml):
    """Адреси сторінок /ua/<slug> (без кореня /ua/, без /ru/) з sitemap.xml, унікальні, у порядку файлу."""
    seen, out = set(), []
    for loc in re.findall(r"<loc>\s*([^<\s]+)\s*</loc>", xml or ""):
        if not loc.startswith(BASE + "/ua/"):
            continue
        slug = loc[len(BASE + "/ua/"):].strip("/")
        if not slug or "/" in slug or loc in seen:
            continue
        seen.add(loc)
        out.append(loc)
    return out


def _price(v):
    try:
        x = float(str(v).replace(" ", "").replace("\xa0", "").replace(",", "."))
    except ValueError:
        return None
    return x if x == x and x not in (float("inf"), float("-inf")) else None


def _ld_blocks(soup):
    for s in soup.find_all("script", type="application/ld+json"):
        try:
            d = json.loads(s.string or "")
        except ValueError:
            continue
        for x in (d if isinstance(d, list) else [d]):
            if isinstance(x, dict):
                yield x


def parse_detail(html):
    """Сторінка товару → {sku, name, brand, price, in_stock, category, top, description, images} або None (не товар/нема артикулу/ціни).
    price = offers.price = «Дроп»; in_stock = ЛИШЕ offers.availability InStock (усе інше, включно з відсутнім полем, — нема в наявності: fail-closed);
    category = «Розділ / Підрозділ» з BreadcrumbList (проміжні елементи між «головною» і назвою товару); top = перший елемент."""
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html or "", "html.parser")
    ld = crumbs = None
    for x in _ld_blocks(soup):
        if x.get("@type") == "Product" and ld is None:
            ld = x
        elif x.get("@type") == "BreadcrumbList" and crumbs is None:
            crumbs = x
    if not ld:
        return None
    offers = ld.get("offers") if isinstance(ld.get("offers"), dict) else {}
    price, sku = _price(offers.get("price")), str(ld.get("sku") or "").strip()
    if not sku.isdigit():
        return None
    no_price = not price or price <= 0
    if no_price:
        price = 0.0                       # товар є, але ціни нема (напр. «₴0.00» на знятій картці): запис товару з price=0 і in_stock=False — не «не-товар» (аудит #1099 п.A)
    brand = ld.get("brand")
    brand = (brand.get("name") if isinstance(brand, dict) else brand) or ""
    names = []
    if crumbs:
        names = [re.sub(r"<[^>]+>", "", str(i.get("name") or "")).strip() for i in (crumbs.get("itemListElement") or []) if isinstance(i, dict)]
    mid = [n for n in names[1:-1] if n]
    tab = soup.select_one("#tab-description")
    desc = ""
    if tab:
        for t in tab.find_all(["script", "style"]):
            t.decompose()
        desc = re.sub(r"\n{3,}", "\n\n", "\n".join(ln.strip() for ln in tab.get_text("\n").splitlines())).strip()
    # фото: усі `…-1000x1000.<jpg|jpeg|png|webp>` у порядку DOM (аудит #1099: старі товари мають .jpg у /products/…); головне (збігається з ld.image без суфікса розміру) — першим
    anchors = []
    for a in soup.select('a[href*="-1000x1000."]'):
        h = urljoin(BASE, a.get("href") or "")
        if re.search(r"-1000x1000\.(?:jpe?g|png|webp)$", h, re.I) and h not in anchors:
            anchors.append(h)
    main = ld.get("image") if isinstance(ld.get("image"), str) else ""
    if isinstance(ld.get("image"), list) and ld["image"] and isinstance(ld["image"][0], str):
        main = ld["image"][0]
    base_main = re.sub(r"\.\w+$", "", main.replace("/image/cache/", "/image/")) if main else ""
    anchors.sort(key=lambda h: 0 if base_main and re.sub(r"-1000x1000\.\w+$", "", h.replace("/image/cache/", "/image/")) == base_main else 1)      # sort стабільний: решта лишається в порядку DOM
    images = anchors or ([main] if main.startswith("https://") else [])
    return {"sku": sku, "name": str(ld.get("name") or "").strip(), "brand": str(brand).strip(), "price": price,
            "in_stock": (not no_price) and str(offers.get("availability") or "").rstrip("/").endswith("InStock"),
            "category": " / ".join(mid[:2]), "top": mid[0] if mid else "", "description": desc, "images": [u for u in images if u.startswith("https://")]}

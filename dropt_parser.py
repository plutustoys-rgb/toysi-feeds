# -*- coding: utf-8 -*-
"""dropt_parser.py — читач ПУБЛІЧНОГО каталогу Dropt (dropt.in.ua, OpenCart, сторінки віддаються готовим HTML; вхід у кабінет не потрібен).

Рішення власника 04.11.2026: беремо 7 розділів (CATEGORIES), закупівля — ціна «Дроп» (НЕ «Опт»), формула ціни сайту — та сама, що для RoyalToys.
Що читаємо (виміряно 04.11 на живому сайті):
  • список розділу: /ua/<slug>?limit=100&page=N — картки `.product-card`: артикул Dropt («Артикул: 59298» — саме за ним замовляють у кабінеті), «Дроп: ₴502.20», «Опт: ₴478.95»,
    мітка «Немає в наявності» (кількості в залишку Dropt публічно НЕ показує — лише є/нема); виміряно: з 2 410 карток 7 розділів у наявності 835.
  • сторінка товару: JSON-LD Product (name, brand, sku, offers.availability) + опис у `#tab-description` + фото-посилання `…-1000x1000.webp`.
Модуль лише ЧИТАЄ: жодних POST, входу, кошика. Мережа — через fetch(url) → html (підміняється в тестах)."""
import re
import sys
import time
from urllib.parse import urljoin

BASE = "https://dropt.in.ua"
LIST_URL = BASE + "/ua/{slug}?limit=100&page={page}"
PAGE_SIZE = 100
MAX_PAGES = 40                   # запобіжник від нескінченного циклу (найбільший вибраний розділ — 862 картки = 9 сторінок)
USER_AGENT = "PlutusToys-catalog-sync/1.0 (+https://plutustoys.com.ua)"
REQUEST_DELAY_S = 0.25           # ввічлива пауза між запитами до чужого сайту

# рішення власника 04.11.2026 (слова: «товари для дітей, акустика, електроніка, туризм відпочинок і спорт, новий рік, товари для тварин, рюкзаки сумки»)
CATEGORIES = {
    "tovary-dlya-detey": "Товари для дітей",
    "akustika": "Акустика",
    "elektronika": "Електроніка",
    "turizm-otdykh-i-sport": "Туризм, відпочинок і спорт",
    "novyy-god": "Новий Рік",
    "tovary-dlya-zhivotnykh": "Товари для тварин",
    "ryukzaki-sumki-portmone": "Рюкзаки, сумки, портмоне",
}

_SKU_RE = re.compile(r"Артикул:\s*(\d+)")
_DROP_RE = re.compile(r"Дроп:\s*₴\s*([\d\s.,]+?)(?=\s*(?:Опт|Купити|Немає|$))")
_OPT_RE = re.compile(r"Опт:\s*₴\s*([\d\s.,]+?)(?=\s*(?:Купити|Немає|$))")
OOS_MARK = "Немає в наявності"


def _num(s):
    try:
        return float(str(s).replace(" ", "").replace("\xa0", "").replace(",", "."))
    except ValueError:
        return None


def make_fetch(session=None, delay=REQUEST_DELAY_S):
    """fetch(url) → html. Помилка мережі/коду ≠ 200 → виняток (рішення про пропуск приймає викликач)."""
    import requests
    s = session or requests.Session()
    last = [0.0]

    def fetch(url):
        wait = delay - (time.monotonic() - last[0])
        if wait > 0:
            time.sleep(wait)
        last[0] = time.monotonic()
        r = s.get(url, headers={"User-Agent": USER_AGENT, "Accept-Language": "uk"}, timeout=40)
        r.raise_for_status()
        return r.text
    return fetch


def parse_listing(html, with_raw=False):
    """[{pid, sku, url, name, thumb, drop, opt, in_stock}] з однієї сторінки списку. Картка без артикулу або без числової ціни «Дроп» пропускається.
    with_raw=True → (список, кількість усіх карток на сторінці, ВКЛЮЧНО з пропущеними): за нею визначається, чи була сторінка повною (пагінація)."""
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "html.parser")
    out = []
    cards = soup.select(".product-card")
    for c in cards:
        text = c.get_text(" ", strip=True)
        m_sku, m_drop, m_opt = _SKU_RE.search(text), _DROP_RE.search(text), _OPT_RE.search(text)
        a = c.select_one("h4 a")
        drop = _num(m_drop.group(1)) if m_drop else None
        if not (m_sku and a and drop and drop > 0):
            continue
        fav = c.select_one("[data-product-id]")
        img = c.select_one(".image img")
        out.append({"pid": (fav.get("data-product-id") if fav else "") or "", "sku": m_sku.group(1), "url": urljoin(BASE, a.get("href") or ""),
                    "name": a.get_text(" ", strip=True), "thumb": (img.get("src") if img else "") or "",
                    "drop": drop, "opt": _num(m_opt.group(1)) if m_opt else None, "in_stock": OOS_MARK not in text})
    return (out, len(cards)) if with_raw else out


def parse_subcategories(html, top_slug):
    """[(slug_підрозділу, назва)] з меню шапки сторінки розділу: посилання виду /ua/<top_slug>/<sub>."""
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "html.parser")
    seen, out = set(), []
    pref = f"/ua/{top_slug}/"
    for a in soup.select(".hdr-cat-submenu a"):
        href = urljoin(BASE, a.get("href") or "")
        path = href.replace(BASE, "")
        if path.startswith(pref):
            sub = path[len(pref):].strip("/")
            if sub and "/" not in sub and sub not in seen:
                seen.add(sub)
                out.append((sub, a.get_text(" ", strip=True)))
    return out


def crawl_listing(slug, fetch, path=None):
    """Усі картки розділу/підрозділу (сторінки по 100, до першої неповної). path — повний шлях відносно /ua/ (для підрозділів: '<top>/<sub>')."""
    items, seen = [], set()
    for page in range(1, MAX_PAGES + 1):
        html = fetch(LIST_URL.format(slug=path or slug, page=page))
        cards, raw_n = parse_listing(html, with_raw=True)
        for c in cards:
            if c["sku"] not in seen:
                seen.add(c["sku"])
                items.append(c)
        if raw_n < PAGE_SIZE:
            break
    return items


def fetch_catalog(fetch, categories=None, log=None):
    """{sku: картка + category 'Розділ / Підрозділ'} по всіх вибраних розділах. Підрозділ береться з першого підрозділу, де картка знайдена; картки поза підрозділами — просто 'Розділ'.
    Збій одного розділу/підрозділу не валить решту: розділ помічається у failed і повертається в другому значенні (викликач вирішує, чи довіряти неповному каталогу)."""
    cats = categories or CATEGORIES
    log = log or (lambda m: print(m, file=sys.stderr))
    out, failed = {}, []
    for slug, top_name in cats.items():
        try:
            top_items = crawl_listing(slug, fetch)
            top_html = fetch(LIST_URL.format(slug=slug, page=1))
        except Exception as e:  # noqa: BLE001
            failed.append(slug)
            log(f"[dropt] розділ {slug}: збій {type(e).__name__}")
            continue
        sub_of = {}
        for sub, sub_name in parse_subcategories(top_html, slug):
            try:
                for c in crawl_listing(slug, fetch, path=f"{slug}/{sub}"):
                    sub_of.setdefault(c["sku"], sub_name)
            except Exception as e:  # noqa: BLE001
                log(f"[dropt] підрозділ {slug}/{sub}: збій {type(e).__name__} (картки лишаться з категорією розділу)")
        for c in top_items:
            if c["sku"] in out:
                continue
            c = dict(c)
            c["category"] = f"{top_name} / {sub_of[c['sku']]}" if c["sku"] in sub_of else top_name
            out[c["sku"]] = c
    return out, failed


def parse_detail(html):
    """{name, brand, in_stock, description, images[https://…]} зі сторінки товару. Нема JSON-LD Product → None."""
    import json
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "html.parser")
    ld = None
    for s in soup.find_all("script", type="application/ld+json"):
        try:
            d = json.loads(s.string or "")
        except ValueError:
            continue
        if isinstance(d, dict) and d.get("@type") == "Product":
            ld = d
            break
    if not ld:
        return None
    offers = ld.get("offers") if isinstance(ld.get("offers"), dict) else {}
    brand = ld.get("brand")
    brand = (brand.get("name") if isinstance(brand, dict) else brand) or ""
    tab = soup.select_one("#tab-description")
    desc = ""
    if tab:
        for t in tab.find_all(["script", "style"]):
            t.decompose()
        desc = re.sub(r"\n{3,}", "\n\n", "\n".join(ln.strip() for ln in tab.get_text("\n").splitlines())).strip()
    big = {}
    for a in soup.select('a[href*="-1000x1000.webp"]'):
        href = urljoin(BASE, a.get("href") or "")
        m = re.search(r"(?:_(\d+))?-1000x1000\.webp$", href)
        if m:
            big.setdefault(href, int(m.group(1)) if m.group(1) else 0)
    images = [h for h, _n in sorted(big.items(), key=lambda kv: (kv[1], kv[0]))]          # головне фото (без _N) першим, далі _2, _3…
    if not images and isinstance(ld.get("image"), str):
        images = [ld["image"]]
    return {"name": str(ld.get("name") or "").strip(), "brand": str(brand).strip(), "in_stock": "InStock" in str(offers.get("availability") or ""),
            "description": desc, "images": [u for u in images if u.startswith("https://")]}

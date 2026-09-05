#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
PlutusToys — статичний генератор власного магазину plutustoys.com.ua.

Будує ЗВ'ЯЗАНИЙ сайт із живого каталогу Toysi (без CMS/платформи — рішення брифу):
  index.html            — головна («Новинки» + категорії)
  catalog.html          — увесь відібраний каталог (сітка + чипси категорій)
  category-<slug>.html   — сторінка кожної категорії
  product-<id>.html      — картка кожного товару (кнопка «У кошик»)
  cart.html              — кошик + checkout (LiqPay/НП підключаються бекендом, крок 3)
  index.json             — індекс для клієнтського пошуку
Спільні ассети: assets/styles.css, assets/app.js.

Ціна = real_toysi_discounted × 1.5 (без комісії маркетплейсу — сенс власного сайту).
LIMIT (env) обмежує к-ть карток для швидкого демо-прогону; порожній = увесь in-stock+фото.
"""
import os, re, sys, json, html, unicodedata
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)) + os.sep + "..")

import parser as tp
import competitor_pricing as cp

OUT = os.path.dirname(os.path.abspath(__file__))
PRICE_MULT = 1.5
MIN_PRICE = 120          # відсікаємо дрібницю-капкан (антистрес тощо) з вітрини
LIMIT = int(os.environ.get("LIMIT", "0") or "0")   # 0 = без ліміту

CAT_EMOJI = [
    ("самокат", "🛴"), ("біговел", "🚲"), ("велосипед", "🚲"), ("ролик", "🛼"),
    ("каталк", "🛒"), ("толокар", "🚗"), ("машин", "🚗"), ("трек", "🏎️"),
    ("конструктор", "🧱"), ("лего", "🧱"), ("лял", "🪆"), ("пазл", "🧩"),
    ("м'яч", "⚽"), ("мяч", "⚽"), ("настільн", "🎲"), ("розвива", "🧠"),
    ("твор", "🎨"), ("малюв", "🎨"), ("кухн", "🍳"), ("зброя", "🔫"),
    ("меч", "⚔️"), ("нож", "🔪"), ("музичн", "🎵"), ("книг", "📚"),
]
def cat_emoji(name):
    n = (name or "").lower()
    for key, emo in CAT_EMOJI:
        if key in n:
            return emo
    return "🧸"

def slugify(s):
    s = (s or "").strip().lower()
    s = unicodedata.normalize("NFKD", s)
    tr = {"а":"a","б":"b","в":"v","г":"h","ґ":"g","д":"d","е":"e","є":"ie","ж":"zh","з":"z",
          "и":"y","і":"i","ї":"i","й":"i","к":"k","л":"l","м":"m","н":"n","о":"o","п":"p",
          "р":"r","с":"s","т":"t","у":"u","ф":"f","х":"kh","ц":"ts","ч":"ch","ш":"sh",
          "щ":"shch","ь":"","ю":"iu","я":"ia","'":"","’":""}
    out = "".join(tr.get(ch, ch) for ch in s)
    out = re.sub(r"[^a-z0-9]+", "-", out).strip("-")
    return out or "cat"

def price_of(it):
    return int(round(cp.toysi_discounted_price(it) * PRICE_MULT))

def esc(s):
    return html.escape(str(s or ""))

# ── спільні шматки розмітки ─────────────────────────────────────────────
def header():
    return (
      '<header class="top">'
      '<a class="logo" href="index.html">Plutus<span>Toys</span> 🦊</a>'
      '<div class="search" id="open-search">'
        '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">'
        '<circle cx="11" cy="11" r="7"/><path d="m21 21-4.3-4.3"/></svg>'
        '<input type="search" placeholder="Пошук іграшки…" readonly aria-label="Пошук">'
      '</div>'
      '<a class="cartbtn" href="cart.html" aria-label="Кошик">'
        '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">'
        '<circle cx="9" cy="21" r="1"/><circle cx="20" cy="21" r="1"/>'
        '<path d="M1 1h4l2.7 13.4a2 2 0 0 0 2 1.6h9.7a2 2 0 0 0 2-1.6L23 6H6"/></svg>'
        '<span class="badge" id="cart-badge">0</span>'
      '</a>'
      '</header>'
    )

def search_overlay():
    return (
      '<div class="overlay" id="search-overlay">'
      '<div class="obar">'
        '<input type="search" placeholder="Що шукаємо?" aria-label="Пошук">'
        '<button class="x" id="close-search">Готово</button>'
      '</div><div class="results"></div></div>'
    )

def footer():
    return (
      '<footer class="foot">PlutusToys — іграшки з доставкою Новою Поштою по Україні.<br>'
      'Оплата карткою (LiqPay) або накладений платіж.</footer>'
    )

def page(title, body, extra_head=""):
    return (
      "<!doctype html>\n<html lang=\"uk\">\n<head>\n"
      "<meta charset=\"utf-8\">\n"
      "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">\n"
      f"<title>{esc(title)} — PlutusToys</title>\n"
      "<link rel=\"stylesheet\" href=\"assets/styles.css\">\n"
      f"{extra_head}</head>\n<body>\n<div class=\"wrap\">\n"
      f"{header()}\n{body}\n{footer()}\n</div>\n"
      f"{search_overlay()}\n"
      "<script src=\"assets/app.js\"></script>\n</body>\n</html>\n"
    )

def tile(p):
    av = '<span class="av">є</span>' if p["stock"] > 0 else '<span class="av oos">нема</span>'
    img = (f'<img src="{esc(p["photo"])}" alt="{esc(p["name"])}" loading="lazy">'
           if p["photo"] else '<div class="ph"></div>')
    return (
      f'<a class="card" href="product-{esc(p["id"])}.html">'
      f'<div class="ph">{img}</div>'
      f'<div class="info"><div class="nm">{esc(p["name"])}</div>'
      f'<div class="foot"><span class="pr">{p["price"]} ₴</span>{av}</div></div></a>'
    )

def grid(prods):
    return '<div class="grid">\n' + "\n".join(tile(p) for p in prods) + '\n</div>'

# ── збірка ─────────────────────────────────────────────────────────────
def build():
    print("[build] тягну каталог Toysi…")
    cat = tp.fetch_toysi_catalog()
    prods = []
    for it in cat.values():
        if str(it.get("stock") or "0") in ("0", ""):
            continue
        pics = it.get("pictures")
        if isinstance(pics, str):
            pics = [pics]
        name = it.get("name") or ""
        if not pics or not name:
            continue
        catname = it.get("category_name") or "Інше"
        # не ведемо вітрину уціненим/дефектним товаром — виключаємо «Уцінку»
        if "уцінк" in catname.lower() or "уценк" in catname.lower() or name.lower().startswith("уцінка"):
            continue
        price = price_of(it)
        if price < MIN_PRICE:
            continue
        prods.append({
            "id": str(it.get("id")), "name": name, "price": price,
            "category": it.get("category_name") or "Інше",
            "photo": pics[0], "stock": int(it.get("stock") or 0),
            "desc": it.get("description") or "",
        })
    # для демо-ліміту наповнюємо НАЙБІЛЬШІ категорії (щоб сторінки категорій були не порожні)
    csize = {}
    for p in prods:
        csize[p["category"]] = csize.get(p["category"], 0) + 1
    # порядок: спершу великі категорії, всередині — дорожчі товари
    prods.sort(key=lambda p: (-csize[p["category"]], p["category"], -p["price"]))
    if LIMIT:
        prods = prods[:LIMIT]
    print(f"[build] карток до генерації: {len(prods)}")

    # категорії
    cats = {}
    for p in prods:
        cats.setdefault(p["category"], []).append(p)
    cat_list = sorted(cats.keys(), key=lambda c: -len(cats[c]))
    cat_slug = {c: slugify(c) for c in cat_list}

    n = 0
    # 1) картки товарів
    for p in prods:
        write_product(p)
        n += 1
    # 2) сторінки категорій
    for c in cat_list:
        write_catalog(f"Каталог • {c}", cats[c], cat_list, cat_slug, f"category-{cat_slug[c]}.html", active=c)
    # 3) повний каталог
    write_catalog("Каталог іграшок", prods, cat_list, cat_slug, "catalog.html", active=None)
    # 4) головна
    write_home(prods, cats, cat_list, cat_slug)
    # 5) кошик + checkout
    write_cart()
    # 6) індекс пошуку
    idx = [{"id": p["id"], "n": p["name"], "pr": p["price"], "p": p["photo"]} for p in prods]
    with open(os.path.join(OUT, "index.json"), "w", encoding="utf-8") as f:
        json.dump(idx, f, ensure_ascii=False)

    print(f"[build] готово: {n} карток, {len(cat_list)} категорій, +index/catalog/cart/index.json")

def chips(cat_list, cat_slug, active):
    out = [f'<a class="chip{"" if active else " active"}" href="catalog.html">Усі</a>']
    for c in cat_list[:12]:
        cls = " active" if active == c else ""
        out.append(f'<a class="chip{cls}" href="category-{cat_slug[c]}.html">{esc(c)}</a>')
    return '<div class="chips">' + "".join(out) + '</div>'

def write_catalog(title, prods, cat_list, cat_slug, fname, active):
    body = (
        chips(cat_list, cat_slug, active) +
        f'\n<h1 class="page">{esc(title)}</h1>\n' +
        grid(prods)
    )
    _write(fname, page(title, body))

def write_home(prods, cats, cat_list, cat_slug):
    # «Новинки» (правка SMM: не «Хіти продажів») — різноманітно: по 1 товару з топ-категорій,
    # беремо позицію біля медіани ціни, щоб не вести лише найдорожчим
    novelties = []
    for c in cat_list[:12]:
        lst = cats[c]
        if lst:
            novelties.append(lst[len(lst) // 2])
        if len(novelties) >= 8:
            break
    cat_tiles = "".join(
        f'<a class="cattile" href="category-{cat_slug[c]}.html">'
        f'<div class="emo">{cat_emoji(c)}</div><div class="t">{esc(c)}</div></a>'
        for c in cat_list[:10]
    )
    body = (
      '<div class="hero"><div class="fox">🦊</div>'
      '<h2>Іграшки, що радують</h2>'
      '<p>Доставка Новою Поштою по всій Україні. Оплата карткою або накладений платіж.</p>'
      '<a class="btn" href="catalog.html">Перейти в каталог</a></div>'
      '<div class="sec-title"><h2>Категорії</h2><a href="catalog.html">Усі →</a></div>'
      f'<div class="catrow">{cat_tiles}</div>'
      '<div class="sec-title"><h2>Новинки</h2><a href="catalog.html">Дивитись усі →</a></div>'
      + grid(novelties)
    )
    _write("index.html", page("PlutusToys — іграшки з доставкою", body))

def write_product(p):
    raw = p["desc"]
    parts = [x.strip() for x in re.split(r"<br\s*/?>", raw) if x.strip()]
    lead = parts[0] if parts else ""
    if lead in ("-", "—", "–") or len(re.sub(r"<[^>]+>", "", lead)) < 3:
        lead = ""  # порожній/сміттєвий опис не показуємо
    specs = [x for x in parts[1:] if x.startswith("<b>") and "</b>" in x and ":" in x
             and not x.rstrip().endswith("</b>")][:6]
    desc_html = f"<p>{lead}</p>" if lead else ""
    if specs:
        desc_html += "<ul>" + "".join(f"<li>{s}</li>" for s in specs) + "</ul>"
    photo = (f'<img src="{esc(p["photo"])}" alt="{esc(p["name"])}">'
             if p["photo"] else '<div class="ph">Фото готуємо</div>')
    avail = "У наявності" if p["stock"] > 0 else "Немає в наявності"
    oos = "" if p["stock"] > 0 else "oos"
    add = json.dumps({"id": p["id"], "name": p["name"], "price": p["price"], "photo": p["photo"]},
                     ensure_ascii=False)
    body = (
      '<div class="pad-bar">'
      f'<div class="photo">{photo}</div>'
      '<div class="body">'
      f'<div class="cat">{esc(p["category"])}</div>'
      f'<h1 class="prod">{esc(p["name"])}</h1>'
      f'<div class="price-row"><div class="price">{p["price"]} ₴</div>'
      f'<div class="avail {oos}">{avail}</div></div>'
      '<div class="delivery"><span class="fox">🦊</span>'
      '<div><b>Доставка Новою Поштою</b> — від 65 ₴. Замовлення до 12:00 йдуть того ж дня, '
      'далі 1–3 робочі дні. Оплата карткою на сайті або накладений платіж.</div></div>'
      f'<div class="desc"><h2>Опис</h2>{desc_html}</div>'
      '</div></div>'
      '<div class="buybar"><div class="buybar-inner">'
      f'<div class="p">{p["price"]} ₴</div>'
      f"<button class=\"btn\" data-add='{esc(add)}'>У кошик</button>"
      '</div></div>'
    )
    _write(f"product-{p['id']}.html", page(p["name"], body))

def write_cart():
    body = (
      '<h1 class="page">Кошик</h1>'
      '<div style="padding:0 16px"><div id="cart-body"></div>'
      '<div class="summary" id="cart-summary">'
        '<div class="row"><span>Товари</span><span id="sum-goods">0 ₴</span></div>'
        '<div class="row"><span>Доставка Новою Поштою</span><span id="sum-delivery">≈ 70 ₴</span></div>'
        '<div class="row total"><span>Разом</span><span id="sum-total">0 ₴</span></div>'
        '<div class="note">Точну вартість доставки НП порахуємо на кроці оформлення за обраним відділенням.</div>'
      '</div>'
      # checkout
      '<h1 class="page" style="margin-left:0">Оформлення</h1>'
      '<form id="checkout-form">'
        '<div class="field"><label>Ім’я та прізвище</label><input name="name" required placeholder="Отримувач посилки"></div>'
        '<div class="field"><label>Телефон</label><input name="phone" type="tel" required placeholder="+380…"></div>'
        '<div class="field"><label>Email <span style="opacity:.6">(необовʼязково, для чека й статусу)</span></label>'
          '<input name="email" type="email" placeholder="you@example.com"></div>'
        '<div class="field"><label>Місто</label><input name="city" required placeholder="Почніть вводити місто…"></div>'
        '<div class="field"><label>Відділення Нової Пошти</label>'
          '<input name="warehouse" required placeholder="Спершу оберіть місто"></div>'
        '<button class="btn" type="submit">Оформити й оплатити</button>'
        '<div class="note" id="checkout-msg"></div>'
      '</form></div>'
    )
    _write("cart.html", page("Кошик", body))

def _write(fname, content):
    with open(os.path.join(OUT, fname), "w", encoding="utf-8") as f:
        f.write(content)

if __name__ == "__main__":
    build()

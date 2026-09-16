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
import os, re, sys, json, html, math, unicodedata
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)) + os.sep + "..")

import parser as tp
import competitor_pricing as cp

OUT = os.path.dirname(os.path.abspath(__file__))
PRICE_MULT = 1.5
MIN_PRICE = 120          # відсікаємо дрібницю-капкан (антистрес тощо) з вітрини
# Логістика на ЗАМОВЛЕННЯ (Нова Пошта + Checkbox SMS), яку КОДВ тримає у ЗМІННИХ
# витратах, а не в собівартості товару: серпень 481,00 ₴ (НП 350 + SMS 131) на 27
# замовлень із доходом = 17,81 ₴/замовлення (Консультант 2026-09-15, з реєстрів КОДВ).
# Додаємо у site-floor, бо конкурентний floor покривав лише товар+еквайринг — а для
# 60% пулу з конкурентом (собівартість < 200 ₴) без цього ціна опускалась НИЖЧЕ реальної
# беззбитковості (возили б у мінус). ⚠️ Це на ЗАМОВЛЕННЯ, не на товар; floor рахується
# по-товарно, тож у багатопозиційному кошику закладеться кілька разів (безпечно, в бік
# обережності — середній кошик ≈ 1 товар). Число оновлювати за свіжими даними КОДВ.
SITE_FULFILL_PER_ORDER = 17.81
LIMIT = int(os.environ.get("LIMIT", "0") or "0")   # 0 = без ліміту
PER_PAGE = 24            # товарів на сторінку каталогу/категорії (мобільна пагінація: легкий перший екран)
# Абсолютний домен для canonical/OG/sitemap (SEO). Той самий, що SITE_BASE_URL у site_order_api.
SITE_URL = os.environ.get("SITE_BASE_URL", "https://plutustoys.com.ua").rstrip("/")
# Брендове OG-прев'ю (маскот Плутус) для соц-репостів головної/лістингів (товар має власне фото).
OG_IMAGE = f"{SITE_URL}/assets/og-home.jpg"

CAT_EMOJI = [
    # транспорт/колеса
    ("самокат", "🛴"), ("біговел", "🚲"), ("велосипед", "🚲"), ("ролик", "🛼"),
    ("каталк", "🚗"), ("толокар", "🚗"), ("машинк", "🚗"), ("машин", "🚗"),
    ("на колес", "🚗"), ("трек", "🏎️"), ("модел", "🚙"), ("літак", "✈️"), ("залізниц", "🚂"),
    # творчість/малювання
    ("картин", "🖼️"), ("мозаїк", "💎"), ("алмазн", "💎"), ("розмальов", "🖍️"),
    ("малюв", "🖍️"), ("гравюр", "🖌️"), ("аплікац", "✂️"), ("оріга", "✂️"),
    ("бісер", "📿"), ("намист", "📿"), ("твор", "🎨"),
    # конструктори/пазли/логіка
    ("конструктор", "🧱"), ("лего", "🧱"), ("пазл", "🧩"), ("головоломк", "🧩"),
    ("настільн", "🎲"), ("навчальн", "🧠"), ("розвива", "🧠"), ("англійськ", "🔤"),
    # ляльки/персонажі/тварини
    ("ведмед", "🧸"), ("лял", "🪆"), ("lol", "🪆"), ("пупс", "🍼"), ("брязкальц", "🔔"),
    ("тварин", "🐾"), ("персонаж", "🦸"), ("герої", "🦸"), ("marvel", "🦸"),
    ("мультф", "🦸"), ("глазаст", "👀"),
    # зброя/спорт/активність
    ("зброя", "🔫"), ("пістолет", "🔫"), ("автомат", "🔫"), ("арбалет", "🏹"),
    ("лук", "🏹"), ("меч", "⚔️"), ("дартс", "🎯"), ("баскетбол", "🏀"),
    ("волейбол", "🏀"), ("футбол", "⚽"), ("бокс", "🥊"), ("боулінг", "🎳"),
    ("басейн", "🏊"), ("м'яч", "⚽"), ("мяч", "⚽"), ("гойдалк", "🛝"), ("ігров", "🎮"),
    # розваги/дрібне
    ("розважальн", "🎉"), ("день народж", "🎈"), ("гірлянд", "✨"), ("ялинк", "🎄"),
    ("антистрес", "🫧"), ("слайм", "🫧"), ("брелок", "🔑"), ("блокнот", "📓"),
    ("книг", "📚"), ("літератур", "📚"), ("музичн", "🎵"), ("ванн", "🛁"),
    ("кухн", "🍳"), ("гаджет", "🎧"), ("аксесуар", "🎧"), ("батарейк", "🔋"),
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

def price_of(it, competitor=None):
    """Ціна товару на сайті.

    Базова (без тиску ринку): знижена ціна Toysi × PRICE_MULT (×1.5) — як і раніше.

    Якщо для товару є ЖИВИЙ конкурент (competitor > 0, зі свіжого
    prom_competitor_price_state), рахуємо конкурентну ціну через ту саму канонічну
    формулу власника, що й репрайсер Prom, але для платформи "site" з НУЛЬОВОЮ
    маркет-комісією (decide_price_for_platform: undercut = competitor − PRICE_STEP,
    floor = собівартість×(1+3%) + candidate×еквайринг — прибуток гарантований).

    ВАЖЛИВО — лише ПІДРІЗАЄМО вниз: беремо min(базова, конкурентна). Ніколи не
    піднімаємо ціну над нашою ×1.5. При конкуренті ВИЩЕ за flat+PRICE_STEP лишаємось
    рівно на ×1.5; у вузькій смузі (flat, flat+PRICE_STEP] ціна = конкурент−PRICE_STEP,
    тобто трохи нижче flat (усе одно НЕ вище — маржа лише більшає). Це прицільно лікує
    дефект «сайт найдорожчий на ринку саме на ходових товарах з конкурентом»
    (Консультант 2026-09-15), не чіпаючи товари без конкурента.

    No-loss покриває товар + еквайринг + ЛОГІСТИКУ: у floor подаємо не голу
    собівартість, а cost + SITE_FULFILL_PER_ORDER (Нова Пошта + SMS на замовлення).
    Інакше на дешевих SKU (60% пулу з конкурентом < 200 ₴) конкурентний floor давав
    ціну, за якої після сплати доставки ми в мінусі (Консультант 2026-09-15, з чисел
    КОДВ). Так net-виручка ≥ cost + логістика на будь-якому товарі, а не лише товар+комісія.

    Округлення конкурентної ціни — math.ceil (ВГОРУ): decide_price_for_platform
    гарантує net≥(поданий cost) на float-ціні, а округлення вниз (int(round)) після
    застосування еквайрингу до заниженого цілого пробивало б floor на ≤0.5₴ (аудит PR
    #536). ceil зберігає інваріант ціною ≤0.97₴ маржі.

    Нема даних конкурента (локальна збірка без свіжого state / товар без живого
    конкурента) → рівно поточна поведінка ×1.5 (безпечна деградація)."""
    flat = int(round(cp.toysi_discounted_price(it) * PRICE_MULT))
    if competitor and competitor > 0:
        try:
            cost = cp.real_toysi_cost(it)
        except (ValueError, TypeError):
            return flat
        if cost > 0:
            # floor рахуємо на cost + логістика (беззбитковість з доставкою), undercut
            # candidate лишається = competitor − PRICE_STEP усередині функції.
            cost_floor = cost + SITE_FULFILL_PER_ORDER
            comp_price = math.ceil(cp.decide_price_for_platform(cost_floor, competitor, "site")["price"])
            if comp_price < flat:          # лише вниз до ринку, ніколи не вище нашої ×1.5
                return comp_price
    return flat

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

FOOTER_LINKS = [
    ("catalog.html", "Каталог"), ("categories.html", "Усі категорії"),
    ("about.html", "Про нас"), ("delivery.html", "Доставка"),
    ("returns.html", "Повернення"), ("contacts.html", "Контакти"),
    ("offer.html", "Публічна оферта"),
]
def footer():
    nav = " · ".join(f'<a href="{u}">{esc(t)}</a>' for u, t in FOOTER_LINKS)
    return (
      '<footer class="foot">'
      f'<nav class="fnav">{nav}</nav>'
      '<p class="fphone"><a href="tel:+380730150815">📞 +380 (73) 015-08-15</a> · '
      '<a href="mailto:plutustoys@gmail.com">plutustoys@gmail.com</a></p>'
      '<p class="ftag">PlutusToys — іграшки з доставкою Новою Поштою по Україні.<br>'
      'Оплата карткою (LiqPay) або накладений платіж.</p>'
      '</footer>'
    )

def page(title, body, extra_head="", description="", canonical="", og_image="", og_type="website", noindex=False):
    full_title = f"{title} — PlutusToys"
    # Головна: канонічна адреса — КОРІНЬ '/', а не '/index.html' (щоб не плодити дубль root vs index.html).
    if canonical == "index.html":
        url = f"{SITE_URL}/"
    else:
        url = f"{SITE_URL}/{canonical}" if canonical else ""
    meta = [
        f'<meta name="description" content="{esc(description)}">' if description else "",
        '<meta name="robots" content="noindex,follow">' if noindex else "",
        f'<link rel="canonical" href="{esc(url)}">' if url else "",
        '<meta property="og:site_name" content="PlutusToys">',
        '<meta property="og:locale" content="uk_UA">',
        f'<meta property="og:type" content="{esc(og_type)}">',
        f'<meta property="og:title" content="{esc(full_title)}">',
        f'<meta property="og:description" content="{esc(description)}">' if description else "",
        f'<meta property="og:url" content="{esc(url)}">' if url else "",
        f'<meta property="og:image" content="{esc(og_image)}">' if og_image else "",
        '<meta name="twitter:card" content="summary_large_image">',
    ]
    head_meta = "".join(m + "\n" for m in meta if m)
    return (
      "<!doctype html>\n<html lang=\"uk\">\n<head>\n"
      "<meta charset=\"utf-8\">\n"
      "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">\n"
      f"<title>{esc(full_title)}</title>\n"
      f"{head_meta}"
      "<link rel=\"preconnect\" href=\"https://toysi.ua\" crossorigin>\n"
      "<link rel=\"dns-prefetch\" href=\"https://toysi.ua\">\n"
      "<link rel=\"stylesheet\" href=\"assets/styles.css\">\n"
      f"{extra_head}</head>\n<body>\n<div class=\"wrap\">\n"
      f"{header()}\n{body}\n{footer()}\n</div>\n"
      f"{search_overlay()}\n"
      "<script src=\"assets/app.js\"></script>\n</body>\n</html>\n"
    )

def tile(p):
    av = '<span class="av">У наявності</span>' if p["stock"] > 0 else '<span class="av oos">Немає</span>'
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
    # Живі ціни конкурентів (свіжі записи prom_competitor_price_state) — щоб сайт
    # підрізав ринок на ходових товарах, а не тримав ×1.5 наосліп. На VPS (де йде
    # site-rebuild.service) файл повний і свіжий; локально/без state → {} → усі
    # товари по ×1.5 (поточна поведінка). Ключ — pid (той самий external_id).
    comp_prices = cp.load_fresh_prom_competitor_prices()
    print(f"[build] живих конкурентів у стані: {len(comp_prices)}")
    n_undercut = 0
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
        # Видимість вітрини вирішує БАЗОВА (×1.5) ціна — «дрібниця-капкан» відсікається
        # за ВЛАСНОЮ вартістю товару, а не за тим, що конкурент демпінгує. Інакше підріз
        # міг би сховати легітивний товар (самокат тощо) лише через дешевого конкурента
        # (зауваження аудиту #536, п. g). Набір товарів вітрини не залежить від підрізу.
        flat = int(round(cp.toysi_discounted_price(it) * PRICE_MULT))
        if flat < MIN_PRICE:
            continue
        competitor = comp_prices.get(str(it.get("id")))
        price = price_of(it, competitor)
        if competitor and price < flat:
            n_undercut += 1
        prods.append({
            "id": str(it.get("id")), "name": name, "price": price,
            "category": it.get("category_name") or "Інше",
            "photo": pics[0], "stock": int(it.get("stock") or 0),
            "desc": it.get("description") or "",
        })
    print(f"[build] підрізано до ринку (конкурентна ціна < ×1.5): {n_undercut} товарів")
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
    # унікальні слаги: дві різні категорії з однаковим slugify() не перезаписують файл одна одної
    cat_slug, _used = {}, {}
    for c in cat_list:
        base = slugify(c)
        if base in _used:
            _used[base] += 1
            cat_slug[c] = f"{base}-{_used[base]}"
        else:
            _used[base] = 1
            cat_slug[c] = base

    # крос-сел «З цим купують»: до 4 товарів ТІЄЇ Ж категорії, найближчих за ціною —
    # пріоритет не дорожчим (добір, не апсел), потім дорожчі, якщо мало (консультант:
    # та сама категорія + близька ціна). Рахуємо раз, O(n log n) на категорію.
    related_map = {}
    for c, lst in cats.items():
        sp = sorted(lst, key=lambda x: x["price"])
        for i in range(len(sp)):
            lower = sp[max(0, i - 4):i][::-1]   # до 4 дешевших, найближчі перші
            higher = sp[i + 1:i + 5]            # до 4 дорожчих (фолбек)
            related_map[sp[i]["id"]] = (lower + higher)[:4]

    n = 0
    # 1) картки товарів
    for p in prods:
        write_product(p, related=related_map.get(p["id"]))
        n += 1
    # 2) сторінки категорій (з пагінацією) + 3) повний каталог — збираємо ВСІ записані сторінки
    paged = set()
    for c in cat_list:
        paged |= write_catalog(f"Каталог • {c}", cats[c], cat_list, cat_slug, f"category-{cat_slug[c]}.html", active=c)
    paged |= write_catalog("Каталог іграшок", prods, cat_list, cat_slug, "catalog.html", active=None)
    # 4) головна
    write_home(prods, cats, cat_list, cat_slug)
    # 5) кошик + checkout + сторінка подяки
    write_cart()
    write_thanks()
    # 5б) індекс усіх категорій (закриває orphan) + сторінки довіри (Про нас/Доставка/…)
    write_categories_index(cat_list, cat_slug, cats)
    trust = write_trust_pages()
    # 6) індекс пошуку
    # index.json — клієнтський пошук + фільтр/сорт каталогу. c=категорія, s=наявність(1/0).
    idx = [{"id": p["id"], "n": p["name"], "pr": p["price"], "p": p["photo"],
            "c": p["category"], "s": 1 if p["stock"] > 0 else 0} for p in prods]
    with open(os.path.join(OUT, "index.json"), "w", encoding="utf-8") as f:
        json.dump(idx, f, ensure_ascii=False)
    # 7) SEO: sitemap + robots
    static_urls = [("categories.html", "0.6", "weekly")] + [(f, "0.4", "monthly") for f in trust]
    write_sitemap(prods, paged, static_urls)
    write_robots()

    # 8) ПРИБИРАННЯ ЗАСТАРІЛИХ сторінок від попередніх білдів: build пише лише поточний вибір,
    # а старі product-/category-/catalog-*.html лишались у site/ і віддавались із застарілими цінами/сміттям
    # (реально знайдено 84 такі). Видаляємо ті, що не входять у поточний набір (пишемо УСІ актуальні
    # вище ДО цього кроку, тож видаляємо лише справжні залишки). `paged` містить усі сторінки
    # каталогу/категорій (вкл. пагіновані catalog_N/category-slug_N), тож застарілі сторінки
    # пагінації від більшого попереднього білда теж приберуться.
    valid = {f"product-{p['id']}.html" for p in prods} | paged
    removed = 0
    for fn in os.listdir(OUT):
        if (fn.startswith(("product-", "category-", "catalog")) and fn.endswith(".html")
                and fn not in valid):
            try:
                os.remove(os.path.join(OUT, fn))
                removed += 1
            except OSError:
                pass

    print(f"[build] готово: {n} карток, {len(cat_list)} категорій, прибрано застарілих {removed}, "
          "+index/catalog/cart/index.json/sitemap.xml/robots.txt")

def chips(cat_list, cat_slug, active):
    out = [f'<a class="chip{"" if active else " active"}" href="catalog.html">Усі</a>']
    for c in cat_list[:12]:
        cls = " active" if active == c else ""
        out.append(f'<a class="chip{cls}" href="category-{cat_slug[c]}.html">{esc(c)}</a>')
    return '<div class="chips">' + "".join(out) + '</div>'

def _page_fname(fname, k):
    """Ім'я файлу k-ї сторінки. Стор.1 = базове ім'я (щоб наявні посилання жили).
    Стор.k≥2 → '<stem>_<k>.html'. Роздільник '_' навмисне: slugify НІКОЛИ не породжує
    '_' (re.sub[^a-z0-9]+→'-'), тож 'category-foo_2.html' не може збігтися з page-1
    файлом дедупленої категорії зі слагом на кшталт 'foo-2' → без колізій імен."""
    if k <= 1:
        return fname
    stem = fname[:-5] if fname.endswith(".html") else fname
    return f"{stem}_{k}.html"

def _pager(fname, k, pages):
    """Навігація сторінками (вікно ±2 + перша/остання). Порожньо, якщо сторінка одна."""
    if pages <= 1:
        return ""
    def lnk(i, txt, cls="pg"):
        return f'<a class="{cls}" href="{_page_fname(fname, i)}">{txt}</a>'
    out = ['<nav class="pager" aria-label="Сторінки">']
    if k > 1:
        out.append(lnk(k - 1, "← Назад", "pg nav"))
    lo, hi = max(1, k - 2), min(pages, k + 2)
    if lo > 1:
        out.append(lnk(1, "1"))
        if lo > 2:
            out.append('<span class="pg gap">…</span>')
    for i in range(lo, hi + 1):
        out.append(f'<span class="pg cur">{i}</span>' if i == k else lnk(i, str(i)))
    if hi < pages:
        if hi < pages - 1:
            out.append('<span class="pg gap">…</span>')
        out.append(lnk(pages, str(pages)))
    if k < pages:
        out.append(lnk(k + 1, "Далі →", "pg nav"))
    out.append('</nav>')
    return "".join(out)

def _catalog_controls():
    """Панель сортування/фільтрів каталогу (керує app.js initCatalog з index.json).
    hidden до JS-ініціалізації — щоб без JS не показувати неробочі контроли."""
    return (
      '<div id="cat-controls" class="cat-controls" hidden>'
        '<select id="cf-sort" aria-label="Сортування">'
          '<option value="pop">Спочатку популярні</option>'
          '<option value="cheap">Спершу дешевші</option>'
          '<option value="dear">Спершу дорожчі</option>'
          '<option value="az">За назвою А–Я</option>'
        '</select>'
        '<select id="cf-cat" aria-label="Категорія"><option value="">Усі категорії</option></select>'
        '<span class="cf-price"><input id="cf-min" type="number" inputmode="numeric" min="0" placeholder="ціна від">'
          '<input id="cf-max" type="number" inputmode="numeric" min="0" placeholder="до"></span>'
        '<label class="cf-stock"><input id="cf-instock" type="checkbox"> лише в наявності</label>'
        '<button id="cf-reset" class="cf-reset" type="button">Скинути</button>'
      '</div>'
      '<p id="cat-count" class="pagenote" hidden></p>'
      '<div id="cat-js" class="grid" hidden></div>'
      '<div id="cat-more-wrap" hidden><button id="cat-more" class="btn ghost">Показати ще</button></div>'
    )

def write_catalog(title, prods, cat_list, cat_slug, fname, active):
    """Пише каталог/категорію З ПАГІНАЦІЄЮ (PER_PAGE/стор.). Повертає set імен усіх
    записаних сторінок (для valid-набору прибирання й sitemap)."""
    total = len(prods)
    pages = max(1, (total + PER_PAGE - 1) // PER_PAGE)
    written = set()
    for k in range(1, pages + 1):
        chunk = prods[(k - 1) * PER_PAGE : k * PER_PAGE]
        cur = _page_fname(fname, k)
        ptitle = title if k == 1 else f"{title} — сторінка {k}"
        desc = f"{title} — {total} іграшок з доставкою Новою Поштою по Україні. Ціни, наявність, купити онлайн у PlutusToys."
        if k > 1:
            desc = f"Сторінка {k} з {pages}. " + desc
        head = ""
        if k > 1:
            head += f'<link rel="prev" href="{_page_fname(fname, k - 1)}">\n'
        if k < pages:
            head += f'<link rel="next" href="{_page_fname(fname, k + 1)}">\n'
        # Панель сорт/фільтр — лише на головному каталозі (стор.1). Працює клієнтом з index.json
        # (усі товари), тож фільтрує/сортує ВЕСЬ каталог, не лише сторінку. Прогресивне покращення:
        # без JS лишається статична пагінована сітка (SEO), контроли сховані (hidden) до ініціалізації.
        controls = _catalog_controls() if (active is None and k == 1) else ""
        static_grid = grid(chunk) + _pager(fname, k, pages)
        if controls:
            static_grid = f'<div id="cat-static">{static_grid}</div>'
        body = (
            chips(cat_list, cat_slug, active) +
            f'\n<h1 class="page">{esc(title)}</h1>\n' +
            (f'<p class="pagenote">Сторінка {k} з {pages}</p>\n' if pages > 1 else "") +
            controls +
            static_grid
        )
        _write(cur, page(ptitle, body, extra_head=head, description=desc, canonical=cur, og_image=OG_IMAGE))
        written.add(cur)
    return written

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
      '<h1>Іграшки, що радують</h1>'
      '<p>Обираємо іграшки, які роблять дитину щасливою, а маму — спокійною. '
      'Привезе Нова Пошта, а Плутус подбає про решту.</p>'
      '<a class="btn" href="catalog.html">Обрати іграшку</a>'
      '<p class="hero-note">🚚 Доставка Новою Поштою по Україні · оплата при отриманні або карткою</p></div>'
      '<div class="sec-title"><h2>Категорії</h2><a href="catalog.html">Усі →</a></div>'
      f'<div class="catrow">{cat_tiles}</div>'
      '<div class="sec-title"><h2>Новинки</h2><a href="catalog.html">Дивитись усі →</a></div>'
      + grid(novelties)
    )
    _write("index.html", page(
        "Іграшки з доставкою Новою Поштою", body,
        description="Дитячі іграшки, від яких світяться очі 🦊 Конструктори, ляльки, машинки, розвиваючі — з доставкою Новою Поштою по всій Україні. Оплата при отриманні або карткою.",
        canonical="index.html", og_image=OG_IMAGE))

def _cut(text, n):
    """Обрізка по МЕЖІ СЛОВА (не посеред слова) + '…', якщо реально різали."""
    text = (text or "").strip()
    if len(text) <= n:
        return text
    return text[:n].rsplit(" ", 1)[0].rstrip(" ,.;:—–-") + "…"


def _clean_desc(html):
    """Прибирає сміття від вставки з ChatGPT: атрибути data-*/style, розкриває зайву вкладеність <p>.
    Лишає лише простий inline-текст (жирний/списки формуються окремо)."""
    html = re.sub(r'\s(?:data-[a-z-]+|style|class)="[^"]*"', "", html or "")
    html = re.sub(r"</?p\b[^>]*>", "", html)   # прибираємо самі <p>-обгортки (текст лишається)
    return html.strip()


def write_product(p, related=None):
    raw = p["desc"]
    parts = [x.strip() for x in re.split(r"<br\s*/?>", raw) if x.strip()]
    lead = _clean_desc(parts[0]) if parts else ""
    if lead in ("-", "—", "–") or len(re.sub(r"<[^>]+>", "", lead)) < 3:
        lead = ""  # порожній/сміттєвий опис не показуємо
    specs = [_clean_desc(x) for x in parts[1:] if x.startswith("<b>") and "</b>" in x and ":" in x
             and not x.rstrip().endswith("</b>")][:6]
    desc_html = f"<p>{lead}</p>" if lead else ""
    if specs:
        desc_html += "<ul>" + "".join(f"<li>{s}</li>" for s in specs) + "</ul>"
    photo = (f'<img src="{esc(p["photo"])}" alt="{esc(p["name"])}">'
             if p["photo"] else '<div class="ph">Фото готуємо</div>')
    avail = "У наявності" if p["stock"] > 0 else "Немає в наявності"
    oos = "" if p["stock"] > 0 else "oos"
    in_stock = p["stock"] > 0
    add = json.dumps({"id": p["id"], "name": p["name"], "price": p["price"], "photo": p["photo"]},
                     ensure_ascii=False)
    # OOS: кнопку деактивуємо (немає data-add) — не даємо покласти в кошик те, чого нема (баг рев'ю).
    buy_btn = (f"<button class=\"btn\" data-add='{esc(add)}'>У кошик</button>" if in_stock
               else '<button class="btn" disabled>Немає в наявності</button>')
    body = (
      '<div class="pad-bar">'
      f'<div class="photo">{photo}</div>'
      '<div class="body">'
      f'<div class="cat">{esc(p["category"])}</div>'
      f'<h1 class="prod">{esc(p["name"])}</h1>'
      f'<div class="price-row"><div class="price">{p["price"]} ₴</div>'
      f'<div class="avail {oos}">{avail}</div></div>'
      '<div class="trust-badges">'
        '<span class="tb">✓ Оплата при отриманні</span>'
        '<a class="tb" href="returns.html">✓ Повернення 14 днів</a>'
        '<span class="tb">✓ Доставка Новою Поштою</span>'
      '</div>'
      '<div class="delivery"><span class="fox">🦊</span>'
      '<div><b>Доставка Новою Поштою</b> — від 65 ₴. Замовлення до 12:00 йдуть того ж дня, '
      'далі 1–3 робочі дні. Оплата карткою на сайті або накладений платіж.</div></div>'
      f'<div class="desc"><h2>Опис</h2>{desc_html}</div>'
      '</div></div>'
      + (f'<section class="related"><h2>З цим купують</h2>{grid(related)}</section>' if related else "")
      + '<div class="buybar"><div class="buybar-inner">'
      f'<div class="p">{p["price"]} ₴</div>'
      f'{buy_btn}'
      '</div></div>'
    )
    # SEO: опис для сніпета (без HTML, ОБРІЗАНИЙ ПО СЛОВУ) + JSON-LD Product для Google Rich Results
    plain = re.sub(r"<[^>]+>", "", lead).strip()
    meta_desc = _cut(plain, 155) or f'{p["name"]} — купити з доставкою Новою Поштою по Україні. PlutusToys.'
    # brand зі специфікацій «<b>Бренд:</b> X» — Google Rich Results цінує brand у Product.
    brand = ""
    for s in specs:
        m = re.search(r"бренд\s*:?\s*</b>\s*(.+)$", s, re.I)
        if m:
            brand = re.sub(r"<[^>]+>", "", m.group(1)).strip()
            break
    ld = {
        "@context": "https://schema.org", "@type": "Product",
        "name": p["name"], "category": p.get("category") or "",
        "sku": str(p["id"]),
        "image": [p["photo"]] if p["photo"] else [],
        "description": _cut(plain, 500) or p["name"],
        "offers": {
            "@type": "Offer",
            "price": str(p["price"]), "priceCurrency": "UAH",
            "availability": "https://schema.org/InStock" if p["stock"] > 0 else "https://schema.org/OutOfStock",
            "url": f'{SITE_URL}/product-{p["id"]}.html',
        },
    }
    if brand:
        ld["brand"] = {"@type": "Brand", "name": brand}
    # безпечно в <script>: екрануємо КОЖЕН '<' у < (валідний JSON) — жоден HTML-вектор
    # (</script>, <!--, <script) не може вийти літерально, навіть із назви товару.
    # chr(92) = '\' — однозначно, без крихкого backslash-літерала.
    ld_json = json.dumps(ld, ensure_ascii=False).replace("<", chr(92) + "u003c")
    extra = f'<script type="application/ld+json">{ld_json}</script>\n'
    _write(f"product-{p['id']}.html", page(
        p["name"], body, extra_head=extra, description=meta_desc,
        canonical=f'product-{p["id"]}.html', og_image=p["photo"], og_type="product"))

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
      '<form id="checkout-form" autocomplete="off">'
        '<div class="field"><label>Ім’я та прізвище</label>'
          '<input id="f-name" name="name" required placeholder="Отримувач посилки"></div>'
        '<div class="field"><label>Телефон</label>'
          '<input id="f-phone" name="phone" type="tel" required placeholder="+380…"></div>'
        '<div class="field"><label>Email <span style="opacity:.6">(необовʼязково, для чека й статусу)</span></label>'
          '<input id="f-email" name="email" type="email" placeholder="you@example.com"></div>'
        '<div class="field ac-wrap"><label>Місто</label>'
          '<input id="f-city" name="city" required placeholder="Почніть вводити місто…">'
          '<div class="ac" id="ac-city"></div></div>'
        '<div class="field ac-wrap"><label>Відділення Нової Пошти</label>'
          '<input id="f-warehouse" name="warehouse" required placeholder="Спершу оберіть місто" disabled>'
          '<div class="ac" id="ac-warehouse"></div></div>'
        # Спосіб оплати — накладений (оплата при отриманні) за замовчуванням: для незнайомого магазину
        # це головний аргумент довіри (рев'ю), і LiqPay поки sandbox. Вибір явний, у payload іде payment.
        '<div class="field"><label>Спосіб оплати</label>'
          '<label class="pay"><input type="radio" name="payment" value="cod" checked> '
            'Оплата при отриманні (накладений платіж на Новій Пошті)</label>'
          '<label class="pay"><input type="radio" name="payment" value="prepaid"> '
            'Оплата карткою онлайн</label></div>'
        '<button class="btn" type="submit" id="checkout-submit">Оформити замовлення</button>'
        '<div class="note" id="checkout-msg"></div>'
      '</form></div>'
    )
    _write("cart.html", page("Кошик і оформлення", body,
        description="Ваш кошик і оформлення замовлення в PlutusToys.", noindex=True))

def write_thanks():
    body = (
      '<div class="done"><div class="fox">🦊</div>'
      '<h2>Дякуємо за замовлення!</h2>'
      '<p>Ми отримали ваше замовлення <span class="oid" id="thanks-oid"></span> і готуємо його до відправки.</p>'
      '<p>Про статус повідомимо за номером замовлення. Доставка — Новою Поштою.</p>'
      '<p style="margin-top:20px"><a class="btn ghost" href="index.html" style="display:inline-block;max-width:260px">На головну</a></p>'
      '</div>'
    )
    _write("thanks.html", page("Дякуємо за замовлення", body, noindex=True))

def write_categories_index(cat_list, cat_slug, cats):
    """Сторінка «Усі категорії» — закриває проблему orphan-категорій (усі 259 доступні
    з одного індексу + футера, а не лише з sitemap). Повертає ім'я файлу."""
    tiles = "".join(
        f'<a class="cattile" href="category-{cat_slug[c]}.html">'
        f'<div class="emo">{cat_emoji(c)}</div>'
        f'<div class="t">{esc(c)}</div>'
        f'<div class="cnt">{len(cats[c])} товар{_plural(len(cats[c]))}</div></a>'
        for c in cat_list
    )
    body = (
        '<h1 class="page">Усі категорії</h1>\n'
        f'<p class="pagenote">{len(cat_list)} категорій іграшок</p>\n'
        f'<div class="catgrid">{tiles}</div>'
    )
    desc = (f"Усі {len(cat_list)} категорій іграшок PlutusToys: конструктори, ляльки, машинки, "
            "творчість, розвиваючі та інші. Доставка Новою Поштою по всій Україні.")
    _write("categories.html", page("Усі категорії", body, description=desc, canonical="categories.html", og_image=OG_IMAGE))
    return "categories.html"

def _plural(n):
    """Українське закінчення для слова «товар» (1 товар / 2 товари / 5 товарів)."""
    n = abs(n) % 100
    if 11 <= n <= 14:
        return "ів"
    d = n % 10
    return "" if d == 1 else ("и" if 2 <= d <= 4 else "ів")

# ── Сторінки довіри (контент від SMM; реквізити/контакти — плейсхолдери для власника) ──
_ABOUT = """<h1>Про нас</h1>
<p>PlutusToys — це український інтернет-магазин дитячих іграшок. Ми молода команда, і саме тому кожне замовлення для нас важливе: ми будуємо магазин, у який хочеться повертатися.</p>
<p>Ми ретельно добираємо асортимент, щоб знайти іграшку для будь-якого віку та настрою — від першої брязкальця до наборів для маленьких дослідників і винахідників. Нам важливо, щоб іграшка була безпечною, цікавою і приносила радість дитині, а батькам — спокій.</p>
<h2>Чому нам довіряють</h2>
<ul>
<li><b>Швидка відправка.</b> Замовлення, оформлені до 12:00, зазвичай відправляємо того ж дня.</li>
<li><b>Доставка по всій Україні.</b> Працюємо з Новою Поштою — до відділення або поштомату.</li>
<li><b>Зручна оплата.</b> Накладений платіж при отриманні або оплата карткою онлайн.</li>
<li><b>Чесність.</b> Ми не обіцяємо того, чого не можемо виконати, і завжди на зв'язку, якщо виникають питання.</li>
</ul>
<h2>Наші цінності</h2>
<p>Турбота про маленького покупця, уважність до батьків і повага до вашого часу. Ми хочемо, щоб покупка іграшки була приємною й простою — від вибору на сайті до моменту, коли дитина відкриває свою нову улюблену річ.</p>"""

_DELIVERY = """<h1>Доставка</h1>
<p>Ми доставляємо замовлення по всій Україні через <b>Нову Пошту</b>. Оберіть зручний для вас варіант отримання під час оформлення замовлення.</p>
<h2>Способи доставки</h2>
<ul>
<li><b>Відділення Нової Пошти</b> — отримання у найближчому до вас відділенні.</li>
<li><b>Поштомат Нової Пошти</b> — зручно, якщо вам простіше забрати посилку самостійно у будь-який час.</li>
</ul>
<h2>Терміни</h2>
<p>Замовлення, оформлені <b>до 12:00</b>, ми зазвичай відправляємо того ж дня. Далі доставка Новою Поштою займає <b>1–3 дні</b> залежно від вашого міста.</p>
<h2>Вартість</h2>
<p>Доставка оплачується <b>за тарифами перевізника</b> (Нової Пошти) під час отримання посилки. Точну суму визначає Нова Пошта відповідно до ваги та напрямку відправлення.</p>
<h2>Як відстежити замовлення</h2>
<p>Після відправлення ви отримаєте <b>номер ТТН у СМС від Нової Пошти</b>. За цим номером можна відстежувати рух посилки на сайті чи в застосунку Нової Пошти, а також отримати сповіщення про прибуття у відділення чи поштомат.</p>"""

_RETURNS = """<h1>Повернення і обмін</h1>
<p>Ми хочемо, щоб ви залишалися задоволені покупкою. Якщо іграшка вам не підійшла, ви можете скористатися правом на повернення або обмін відповідно до Закону України «Про захист прав споживачів».</p>
<h2>Умови повернення</h2>
<ul>
<li>Повернути або обміняти товар належної якості можна протягом <b>14 днів</b> з моменту отримання.</li>
<li>Товар має бути <b>не у вжитку</b>, зі збереженим товарним виглядом, споживчими властивостями, а також <b>оригінальною упаковкою</b> і комплектацією.</li>
<li>Бажано зберегти документ, що підтверджує покупку (чек, квитанцію або підтвердження замовлення).</li>
<li>Товар неналежної якості (з браком чи дефектом) повертається на загальних підставах, передбачених законом.</li>
</ul>
<h2>Як оформити</h2>
<p>Щоб оформити повернення чи обмін, <b>зв'яжіться з нами</b> за контактами, вказаними на сторінці «Контакти». Ми підкажемо, як правильно оформити відправлення й відповімо на всі запитання.</p>
<h2>Хто оплачує пересилку</h2>
<p>Витрати на зворотну пересилку товару належної якості (коли товар просто не підійшов) несе покупець. Якщо ж товар виявився неналежної якості або сталася помилка з нашого боку, витрати на пересилку компенсуємо ми.</p>
<p>Повернення коштів здійснюється тим самим способом, яким була проведена оплата, після отримання й перевірки товару.</p>"""

_CONTACTS = """<h1>Контакти</h1>
<p>Маєте запитання щодо товару, замовлення чи доставки? Зв'яжіться з нами — ми завжди раді допомогти й підкажемо з вибором.</p>
<h2>Як з нами зв'язатися</h2>
<ul>
<li><b>Телефон:</b> <a href="tel:+380730150815">+380 (73) 015-08-15</a></li>
<li><b>Електронна пошта:</b> <a href="mailto:plutustoys@gmail.com">plutustoys@gmail.com</a></li>
<li><b>Ми в Києві</b> — доставляємо по всій Україні Новою Поштою.</li>
</ul>
<p>Телефонуйте або пишіть на пошту у зручний для вас спосіб — ми відповімо якнайшвидше в робочий час. Якщо не вдалося додзвонитися, залиште повідомлення, і ми обов'язково передзвонимо.</p>"""

_OFFER = """<h1>Публічна оферта</h1>
<p>Цей документ є офіційною пропозицією (публічною офертою) інтернет-магазину PlutusToys (далі — «Продавець») укласти договір купівлі-продажу товарів на умовах, викладених нижче. Оформлення замовлення на сайті означає повну й беззастережну згоду Покупця з умовами цієї оферти.</p>
<h2>1. Загальні положення</h2>
<p>Продавець — інтернет-магазин дитячих іграшок PlutusToys (plutustoys.com.ua). Зв'язок із Продавцем — за телефоном +380 (73) 015-08-15 та електронною поштою plutustoys@gmail.com (сторінка «Контакти»). Покупець — будь-яка дієздатна особа, яка оформила замовлення на сайті plutustoys.com.ua.</p>
<h2>2. Предмет договору</h2>
<p>Продавець зобов'язується передати у власність Покупцеві дитячі іграшки та супутні товари (далі — «Товар»), представлені на сайті, а Покупець зобов'язується прийняти й оплатити Товар на умовах цієї оферти.</p>
<h2>3. Порядок оформлення замовлення</h2>
<ul>
<li>Покупець самостійно оформлює замовлення на сайті, обираючи Товар, спосіб доставки та оплати.</li>
<li>Покупець несе відповідальність за достовірність наданих під час замовлення даних.</li>
<li>Продавець може зв'язатися з Покупцем для підтвердження замовлення.</li>
</ul>
<h2>4. Ціна та оплата</h2>
<ul>
<li>Ціни на Товар вказані на сайті у гривнях.</li>
<li>Оплата здійснюється одним зі способів: накладений платіж при отриманні або оплата банківською карткою онлайн.</li>
<li>Вартість доставки оплачується окремо за тарифами перевізника.</li>
</ul>
<h2>5. Доставка</h2>
<p>Доставка Товару здійснюється Новою Поштою по всій території України — до відділення або поштомату. Терміни та вартість доставки визначаються згідно з умовами перевізника й інформацією на сторінці «Доставка».</p>
<h2>6. Повернення та обмін</h2>
<p>Повернення й обмін Товару здійснюються відповідно до Закону України «Про захист прав споживачів» та умов, викладених на сторінці «Повернення і обмін».</p>
<h2>7. Відповідальність сторін</h2>
<ul>
<li>Сторони несуть відповідальність згідно з чинним законодавством України.</li>
<li>Продавець не несе відповідальності за неналежне використання Товару Покупцем, а також за затримки з боку перевізника.</li>
<li>Продавець не відповідає за збитки, спричинені наданням Покупцем недостовірних даних.</li>
</ul>
<h2>8. Контакти Продавця</h2>
<p>Інтернет-магазин PlutusToys<br>Телефон: +380 (73) 015-08-15<br>Ел. пошта: plutustoys@gmail.com<br>Сайт: plutustoys.com.ua</p>"""

TRUST_PAGES = [
    ("about.html",    "Про нас",             _ABOUT,
     "Про інтернет-магазин дитячих іграшок PlutusToys: як ми працюємо, чому нам довіряють, доставка Новою Поштою."),
    ("delivery.html", "Доставка",            _DELIVERY,
     "Доставка іграшок Новою Поштою по всій Україні: відділення та поштомати, терміни 1–3 дні, відстеження за ТТН."),
    ("returns.html",  "Повернення і обмін",  _RETURNS,
     "Умови повернення та обміну іграшок PlutusToys: 14 днів за Законом про захист прав споживачів, як оформити."),
    ("contacts.html", "Контакти",            _CONTACTS,
     "Контакти інтернет-магазину PlutusToys — телефон, месенджери, електронна пошта, графік роботи."),
    ("offer.html",    "Публічна оферта",     _OFFER,
     "Публічна оферта інтернет-магазину дитячих іграшок PlutusToys — умови договору купівлі-продажу."),
]

def write_trust_pages():
    """Пише 5 сторінок довіри. Повертає список імен файлів (для sitemap)."""
    written = []
    for fname, title, content, desc in TRUST_PAGES:
        body = f'<article class="doc">\n{content}\n</article>'
        _write(fname, page(title, body, description=desc, canonical=fname))
        written.append(fname)
    return written

def write_sitemap(prods, paged, static_urls=None):
    """paged = усі сторінки каталогу/категорій (з пагінацією). Стор.≥2 (мають '_N' перед .html)
    ідуть з нижчим пріоритетом; catalog.html — найвищий серед лістингів."""
    from datetime import date
    today = date.today().isoformat()
    # головна — КОРІНЬ '/', а не '/index.html': збігається з canonical головної (page()),
    # інакше конфліктний сигнал каноніку (loc {SITE_URL}/{u} з u='' дає {SITE_URL}/).
    urls = [("", "1.0", "daily")]
    urls += list(static_urls or [])            # categories.html + сторінки довіри
    for fn in sorted(paged):
        secondary = bool(re.search(r"_\d+\.html$", fn))   # сторінка ≥2
        if fn == "catalog.html":
            pr, cf = "0.9", "daily"
        elif secondary:
            pr, cf = "0.5", "weekly"
        else:                                             # page-1 категорії/каталогу
            pr, cf = "0.7", "weekly"
        urls.append((fn, pr, cf))
    urls += [(f"product-{p['id']}.html", "0.6", "weekly") for p in prods]
    items = "\n".join(
        f"  <url><loc>{SITE_URL}/{u}</loc><lastmod>{today}</lastmod>"
        f"<changefreq>{cf}</changefreq><priority>{pr}</priority></url>"
        for u, pr, cf in urls
    )
    _write("sitemap.xml",
           '<?xml version="1.0" encoding="UTF-8"?>\n'
           '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
           f"{items}\n</urlset>\n")

def write_robots():
    _write("robots.txt",
           "User-agent: *\n"
           "Allow: /\n"
           "Disallow: /cart.html\n"
           "Disallow: /thanks.html\n"
           f"Sitemap: {SITE_URL}/sitemap.xml\n")

def _write(fname, content):
    with open(os.path.join(OUT, fname), "w", encoding="utf-8") as f:
        f.write(content)

if __name__ == "__main__":
    build()

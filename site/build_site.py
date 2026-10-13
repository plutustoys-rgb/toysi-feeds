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
import os, re, sys, json, html, math, unicodedata, hashlib, random, bisect, heapq, functools
from datetime import datetime, timedelta, timezone
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)) + os.sep + "..")

import parser as tp
import competitor_pricing as cp
from trademark_filter import is_uno_trademark_blocked
from forbidden_products import is_forbidden
from weapon_policy import is_weapon_replica

OUT = os.path.dirname(os.path.abspath(__file__))
PRICE_MULT = cp.SITE_PRICE_MULT   # єдине джерело — competitor_pricing.site_retail_price (збіг з Google/Meta/Bing-фідами)
# Логістика на ЗАМОВЛЕННЯ (Нова Пошта + Checkbox SMS), яку КОДВ тримає у ЗМІННИХ
# витратах, а не в собівартості товару: серпень 481,00 ₴ (НП 350 + SMS 131) на 27
# замовлень із доходом = 17,81 ₴/замовлення (Консультант 2026-09-15, з реєстрів КОДВ).
# Використовується ЛИШЕ для оцінки ВНЕСКУ (ранжування «Рекомендовані» і головна): ціна сайту фіксована
# (знижена ціна Toysi × PRICE_MULT, як у EVA) і від логістики не залежить. ⚠️ Це на ЗАМОВЛЕННЯ, не на товар.
# Число оновлювати за свіжими даними КОДВ.
SITE_FULFILL_PER_ORDER = cp.SITE_FULFILL_PER_ORDER   # єдине джерело — competitor_pricing.py (ним же користуються фіди Google/Meta/Bing)
ADDON_MIN_PRICE = 60   # ₴: нижче — не пропонуємо як доповнення (аудит #623: при 30 пул з 80 найдешевших = самі 30–31 ₴)
# Оплата карткою онлайн показується покупцю ЛИШЕ коли LiqPay реально бойовий (SMM 2026-10-06: футер обіцяв картку, якої нема).
# Вмикається змінною середовища SITE_LIQPAY_LIVE=1 під час збірки (виставити після появи бойових ключів LiqPay).
LIQPAY_LIVE = os.environ.get("SITE_LIQPAY_LIVE", "").strip() == "1"
# Вартість доставки НП — ВИМІРЯНА (SEO 12.10.2026: Нова Пошта getDocumentPrice із нашого акаунту; Київ→Київ 70 ₴, інші великі міста 90 ₴, накладений платіж +23,8 ₴; лише до 2 кг).
# Для посилок >2 кг суму НЕ пишемо (не міряли); shippingDetails у розмітці НЕ ставимо (рішення SEO). Аудит #689: вимір зроблено з НАШОГО акаунту НП, а ТТН для замовлень сайту створює Toysi під СВОЇМ — тому лише «орієнтовно»;
# 12.10 (Код, getDocumentPrice): комісія НП за накладений платіж = 20 ₴ + 2% від суми (точно на 190/400/1000/3000) — тепер це ВИМІРЯНО й друкується формулою, а не діапазоном.
# Тариф до 2 кг: база 70 ₴ Київ / 90 ₴ інші великі міста + 0,5% × S, якщо S > 500 (Toysi ставить оголошену вартість = сумі накладеного, мінімум 500; Код звірила з реальними ТТН 704/881/982 до копійки). Вага — ОЦІНКА за габаритами:
#   у 2 реальних ТТН вагою >2 кг було +25 ₴, а ваги в каталозі Toysi немає — тому цифри лише «орієнтовно», а точну суму називає Нова Пошта.
# Оголошену вартість НЕ передаємо ми (toysi_order_submit.py:146 — поле необов'язкове): це рішення Toysi, воно може змінитись без попередження. Оголошена вартість для ПЕРЕДОПЛАТИ (LiqPay) не міряна — сайтових передоплат ще не було.
# Накладений платіж лише до SITE_COD_CEILING=3000 ₴ (комісія там 80 ₴). Хто платить саме комісію — не доведено (PayerType=Recipient у 3/3 ТТН стосується доставки): текст так не стверджує.
STOCK_BADGE_MAX = 3   # бейдж «Залишилось N шт» у добірках (карусель, /nabory.html, /ig.html), коли залишок Toysi ≤ цього (SMM 12.10)
DELIVERY_COST = "орієнтовно 70–90 ₴ до 2 кг"
DELIVERY_FEE_FORMULA = "комісія за накладений платіж: 20 ₴ + 2% від суми замовлення"
DELIVERY_FEE_FORMULA_SHORT = "20 ₴ + 2% від суми замовлення"
DELIVERY_NOTE = ("Доставка за тарифом Нової Пошти — " + DELIVERY_COST + " (для замовлень понад 500 ₴ тариф трохи зростає, для важчих посилок сума вища), плюс " + DELIVERY_FEE_FORMULA
                 + ". Суму в рядку «Доставка» ми розраховуємо орієнтовно для вашого кошика; точну суму назве Нова Пошта при отриманні посилки (для великих і важких замовлень вона може бути вищою). Оплачується при отриманні посилки.")
# Оплата карткою онлайн (prepaid): комісії накладеного платежу НЕМАЄ, тож підсумок доставки без неї (тест. 13.10: кошик показував COD-івські 99–119 ₴ для платника карткою — завищено ≈29 ₴). Для передоплати оголошена вартість/тариф НЕ міряні
# (Код: сайтових передоплат ще не було) — друкуємо лише базовий тариф «орієнтовно 70–90 ₴ до 2 кг» без надбавки 0,5% і без твердження про платника.
DELIVERY_LABEL_PREPAID = "Доставка Новою Поштою"
DELIVERY_NOTE_PREPAID = "Доставка за тарифом Нової Пошти — " + DELIVERY_COST + " (для важчих посилок сума вища). Точну суму назве Нова Пошта."
PAY_HERO = "оплата при отриманні або карткою" if LIQPAY_LIVE else "оплата при отриманні на Новій Пошті"
PAY_ABOUT = ("Накладений платіж при отриманні або оплата карткою онлайн." if LIQPAY_LIVE
             else "Накладений платіж: платите при отриманні на відділенні Нової Пошти.")
PAY_OFFER = ("одним зі способів: накладений платіж при отриманні або оплата банківською карткою онлайн" if LIQPAY_LIVE
             else "накладеним платежем при отриманні Товару на відділенні перевізника")
PAY_DELIVERY = ("<p>Оплатити замовлення можна <b>банківською карткою онлайн</b> на сайті або <b>накладеним платежем</b> при отриманні на відділенні чи в поштоматі Нової Пошти (накладений платіж — для замовлень до 3 000 ₴). "
                "</p>" if LIQPAY_LIVE else
                "<p>Оплата — <b>накладеним платежем</b>: розраховуєтесь при отриманні замовлення на відділенні або в поштоматі Нової Пошти. "
                "Накладений платіж доступний для замовлень на суму до 3 000 ₴, вище — лише за передоплатою (зв'яжіться з нами).</p>")
# Вимоги LiqPay до мерчанта (Код 13.10, https://www.liqpay.ua/uk/information/requirements): логотип LiqPay на сайті (футер усіх сторінок + біля вибору оплати карткою), правила оплати карткою й повернення коштів на ту саму картку.
# Тексти й строк (7 робочих днів) ЗАТВЕРДЖЕНО власником 13.10. Поки LiqPay не live (SITE_LIQPAY_LIVE≠1) — чесне застереження, що онлайн-оплата запускається найближчим часом (покупцям картку ще не пропонуємо).
CARD_PAY_INFO = (
    '<p><b>Оплата карткою онлайн.</b> Оплатити замовлення можна банківською карткою онлайн через сервіс LiqPay. Дані вашої картки вводяться на захищеній сторінці LiqPay '
    'і не передаються та не зберігаються на нашому сайті.</p>' if LIQPAY_LIVE else
    # до запуску — майбутній час (аудит #717: теперішній час «можна оплатити» обіцяв б картку, якої ще нема)
    '<p><b>Оплата карткою онлайн.</b> Після запуску онлайн-оплати замовлення можна буде оплатити банківською карткою через сервіс LiqPay. Дані вашої картки вводитимуться на захищеній сторінці LiqPay '
    'і не передаватимуться та не зберігатимуться на нашому сайті.</p>')
CARD_PAY_SOON = "" if LIQPAY_LIVE else "<p>Оплата карткою онлайн запускається найближчим часом; зараз доступна оплата накладеним платежем.</p>"
_REFUND_TERM = ("протягом <b>7 робочих днів</b> з моменту погодження повернення (погодження — після отримання й перевірки товару). "
                "Фактичний термін зарахування залежить від банку, що випустив картку.</p>")
CARD_REFUND_INFO = (
    '<p><b>Повернення коштів за замовлення, оплачене карткою.</b> У разі повернення товару або скасування замовлення кошти повертаються на ту саму картку, з якої була оплата, ' + _REFUND_TERM if LIQPAY_LIVE else
    '<p><b>Повернення коштів за замовлення, оплачене карткою.</b> Після запуску онлайн-оплати у разі повернення товару або скасування замовлення кошти повертатимуться на ту саму картку, з якої була оплата, ' + _REFUND_TERM)
LIQPAY_LOGO_HTML = ('<a href="https://www.liqpay.ua/" rel="noopener" target="_blank"><img src="{src}" alt="LiqPay" width="{w}" height="{h}" loading="lazy"></a>')
# Відомості про продавця (Закон «Про електронну комерцію», ст. 7 ч.1: ПІБ ФОП, місце реєстрації/проживання, e-mail, реєстраційний номер; LiqPay «Вимоги до мерчанта» п.7).
# 09.10.2026, пряме доручення власника: адреса — м. Київ, просп. Берестейський, 89а. РНОКПП у ПУБЛІЧНИЙ git не кладемо: береться зі змінної SITE_SELLER_TAXID під час збірки (.env VPS);
# не задано — рядок РНОКПП просто не показується (сторінка не ламається).
SELLER_NAME = "ФОП Чечетенко Олександр Юрійович"
SELLER_ADDRESS = "м. Київ, просп. Берестейський, 89а"
_tax = os.environ.get("SITE_SELLER_TAXID", "").strip()
SELLER_TAXID = _tax if re.fullmatch(r"[0-9]{8,10}", _tax) else ""
SELLER_TAX_LI = f"<li><b>РНОКПП:</b> {SELLER_TAXID}</li>" if SELLER_TAXID else ""
SELLER_TAX_BR = f"<br>РНОКПП: {SELLER_TAXID}" if SELLER_TAXID else ""
# Аналітика (SMM P1.1, 2026-10-06): ID задаються змінними середовища під час збірки; без них сніпетів у HTML нема
# (сайт працює як раніше). Формат перевіряється — ID потрапляє в <script>, довільний рядок не пускаємо.
_ga = os.environ.get("SITE_GA4_ID", "").strip()
GA4_ID = _ga if re.fullmatch(r"G-[A-Z0-9]{6,14}", _ga) else ""
_px = os.environ.get("SITE_META_PIXEL_ID", "").strip()
META_PIXEL_ID = _px if re.fullmatch(r"[0-9]{8,20}", _px) else ""   # ASCII-цифри: \d приймає й юнікод-цифри (аудит #626)
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

def price_of(it):
    """Ціна товару на сайті = ЦІНА TOYSI З НАШОЮ ЗНИЖКОЮ × PRICE_MULT (1.5) — та сама націнка, що в EVA-фіді
    (generate_eva_feed._eva_price: toysi_discounted_price × EVA_PRICE_MULTIPLIER; рішення власника 2026-10-05:
    «наценка як у EVA»). Округлення вгору до гривні. Сайт НЕ залежить від жодного маркетплейсу: жодних цін
    конкурентів, підрізань і стану чужих кабінетів. EVA-специфічні надбавки (промо-коеф 2.143, підлога під
    15%-комісію EVA) на власному сайті не застосовуються — там нема комісії маркетплейсу."""
    return cp.site_retail_price(it)

def esc(s):
    return html.escape(str(s or ""))

# ── спільні шматки розмітки ─────────────────────────────────────────────
def header():
    return (
      '<header class="top">'
      '<a class="logo" href="/">Plutus<span>Toys</span>'
      ' <img class="mascot" src="assets/plutus_mascot_s.png" alt="Плутус" width="24" height="22" decoding="async"></a>'
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
    ("offer.html", "Публічна оферта"), ("privacy.html", "Політика конфіденційності"),
]
def footer():
    nav = " · ".join(f'<a href="{u}">{esc(t)}</a>' for u, t in FOOTER_LINKS)
    return (
      '<footer class="foot">'
      f'<nav class="fnav">{nav}</nav>'
      '<p class="fphone"><a href="tel:+380730150815">📞 +380 (73) 015-08-15</a> · '
      '<a href="mailto:plutustoys@gmail.com">plutustoys@gmail.com</a></p>'
      '<p class="flp">' + LIQPAY_LOGO_HTML.format(src=asset_url('assets/liqpay-logo.svg'), w=100, h=21) + '</p>'
      f'<p class="fsoc"><a href="{_footer_utm(SOCIAL_URLS[0])}" rel="noopener" target="_blank">Instagram</a> · '
      f'<a href="{_footer_utm(SOCIAL_URLS[1])}" rel="noopener" target="_blank">Facebook</a></p>'
      '<p class="ftag">PlutusToys — іграшки з доставкою Новою Поштою по Україні.'
      + ('<br>Оплата карткою онлайн або накладений платіж.' if LIQPAY_LIVE else '') +
      '</p>'
      '</footer>'
    )

def analytics_head(defer=True) -> str:
    """Сніпети GA4 (gtag) і Meta Pixel + `window.PT_ANALYTICS` для app.js (події view_item/add_to_cart/begin_checkout/purchase). Порожній рядок, якщо ID не задано.
    defer=True (усі сторінки, КРІМ «Дякуємо»; рішення Консультанта 12.10: Lighthouse mobile TBT 1,4–3,3 с майже весь від трекерів — Pixel ≈1,3 с, gtag ≈0,7–1,2 с): стаби gtag/fbq (черга) ставимо ОДРАЗУ —
    події app.js не губляться, — а САМІ СКРИПТИ (gtag.js, fbevents.js) підвантажуємо через 3 с після load або на першу взаємодію (pointerdown/keydown/scroll/touchstart), що раніше. Втрата: хто пішов раніше —
    це відмови, нам не потрібні. thanks.html — defer=False: подія `purchase` вантажиться без затримки (єдине замовлення втрачати в аналітиці неприпустимо).
    ВІДОМЕ ОБМЕЖЕННЯ (аудит #693, виміряно): якщо перша взаємодія — «У кошик» і покупець за <≈1,8 с (slow 4G; ≈0,5 с на швидкій) одразу переходить на іншу сторінку, події цієї сторінки
    (page_view/view_item/add_to_cart) і fbclid/gclid не доходять. Пом'якшення rel=preload через 1 с після load ВИМІРЯНО й ВІДХИЛЕНО: Lighthouse чекає мережу довше й втягує виконання трекерів
    у вікно виміру (TBT назад 1,8–3,3 с) заради рідкісного випадку."""
    if not (GA4_ID or META_PIXEL_ID):
        return ""
    out = [f'<script>window.PT_ANALYTICS={json.dumps({"ga4": GA4_ID, "fb": META_PIXEL_ID})};</script>\n']
    gtag_src = f"https://www.googletagmanager.com/gtag/js?id={GA4_ID}"
    fb_src = "https://connect.facebook.net/en_US/fbevents.js"
    if not defer:
        if GA4_ID:
            out.append(f'<script async src="{gtag_src}"></script>\n'
                       "<script>window.dataLayer=window.dataLayer||[];function gtag(){dataLayer.push(arguments);}"
                       f"gtag('js',new Date());gtag('config','{GA4_ID}');</script>\n")
        if META_PIXEL_ID:
            out.append("<script>!function(f,b,e,v,n,t,s){if(f.fbq)return;n=f.fbq=function(){n.callMethod?n.callMethod.apply(n,arguments):n.queue.push(arguments)};"
                       "if(!f._fbq)f._fbq=n;n.push=n;n.loaded=!0;n.version='2.0';n.queue=[];t=b.createElement(e);t.async=!0;"
                       "t.src=v;s=b.getElementsByTagName(e)[0];s.parentNode.insertBefore(t,s)}(window,document,'script',"
                       f"'{fb_src}');fbq('set','autoConfig',false,'{META_PIXEL_ID}');"
                       f"fbq('init','{META_PIXEL_ID}');fbq('track','PageView');</script>\n")   # autoConfig=false: без Automatic Advanced Matching (PII форми не хешується в Meta)
        return "".join(out)
    js = []
    if GA4_ID:
        js.append("window.dataLayer=window.dataLayer||[];function gtag(){dataLayer.push(arguments);}"
                  f"gtag('js',new Date());gtag('config','{GA4_ID}');")
    if META_PIXEL_ID:
        js.append("(function(f){var n=f.fbq=function(){n.callMethod?n.callMethod.apply(n,arguments):n.queue.push(arguments)};if(!f._fbq)f._fbq=n;n.push=n;n.loaded=!0;n.version='2.0';n.queue=[];})(window);"
                  f"fbq('set','autoConfig',false,'{META_PIXEL_ID}');fbq('init','{META_PIXEL_ID}');fbq('track','PageView');")   # autoConfig=false: без Automatic Advanced Matching
    loads = []
    if GA4_ID:
        loads.append(f"a('{gtag_src}');")
    if META_PIXEL_ID:
        loads.append(f"a('{fb_src}');")
    js.append("(function(){var d=document,w=window,go=0;function a(u){var s=d.createElement('script');s.async=!0;s.src=u;d.head.appendChild(s);}"
              "function load(){if(go)return;go=1;" + "".join(loads) + "}"
              "['pointerdown','keydown','scroll','touchstart'].forEach(function(e){w.addEventListener(e,load,{once:!0,passive:!0});});"
              "function t(){setTimeout(load,3000);}if(d.readyState==='complete')t();else w.addEventListener('load',t);})();")
    out.append("<script>" + "".join(js) + "</script>\n")
    return "".join(out)


_ASSET_VER = {}


def asset_url(rel):
    """`assets/app.js` -> `assets/app.js?v=<8 hex sha1 вмісту>`. Браузер кешує /assets/ на добу (Cache-Control max-age=86400, деплой Apache), а імена файлів без хешів:
    після деплою фіксу HTML новий, а app.js/styles.css у відвідувача ще старі до доби (так 10.10 браузер виконував старий app.js при новому HTML). Версія в URL змінюється
    лише коли змінився вміст → фікс доїжджає одразу. Хеш по вмісту з нормалізованими кінцями рядків (CRLF/LF не змінює версію). Нема файлу (тест/тека без assets) — URL без версії."""
    if rel not in _ASSET_VER:
        try:
            with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), rel), "rb") as f:
                data = f.read().replace(b"\r\n", b"\n")
            _ASSET_VER[rel] = hashlib.sha1(data).hexdigest()[:8]
        except OSError:
            _ASSET_VER[rel] = ""
    v = _ASSET_VER[rel]
    return f"{rel}?v={v}" if v else rel


def page(title, body, extra_head="", description="", canonical="", og_image="", og_type="website", noindex=False, title_full="", base_root=False, defer_trackers=True):
    full_title = title_full or f"{title} — PlutusToys"
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
      # base_root лише для 404: її віддають за БУДЬ-ЯКОЇ адреси (/a/b/c), а шляхи в шаблоні відносні (assets/…, index.json, api/…)
      + ("<base href=\"/\">\n" if base_root else "") +
      f"<title>{esc(full_title)}</title>\n"
      f"{head_meta}"
      "<link rel=\"icon\" href=\"assets/favicon.ico\" sizes=\"any\">\n"
      "<link rel=\"icon\" type=\"image/png\" sizes=\"32x32\" href=\"assets/favicon-32.png\">\n"
      "<link rel=\"apple-touch-icon\" href=\"assets/apple-touch-icon.png\">\n"
      "<link rel=\"preconnect\" href=\"https://toysi.ua\" crossorigin>\n"
      "<link rel=\"dns-prefetch\" href=\"https://toysi.ua\">\n"
      f"<link rel=\"stylesheet\" href=\"{asset_url('assets/styles.css')}\">\n"
      f"{analytics_head(defer_trackers)}{extra_head}</head>\n<body>\n<div class=\"wrap\">\n"
      f"{header()}\n{body}\n{footer()}\n</div>\n"
      f"{search_overlay()}\n"
      f"<script src=\"{asset_url('assets/app.js')}\"></script>\n</body>\n</html>\n"
    )

def tile(p, cls="", eager=False, prio=False, badge=False):
    """Картка товару. cls — додатковий клас (напр. «slide» у каруселі); eager=True — без loading=lazy (перші слайди каруселі = кандидат на LCP)."""
    # Бейдж «Залишилось N шт» (SMM 12.10): лише коли залишок Toysi 1–3 («1 шт.»/«2 шт.»/«3 шт.» — ТОЧНІ числа з <ostatok>, перевірено на VPS: діапазонів «1…5» у цій зоні немає; значення береться на момент збірки, раз на ~2 год).
    # Без вигаданих «останній у місті»/таймерів. Де є бейдж — зелений «У наявності» не дублюємо.
    # Лише там, де tile(badge=True): карусель головної, /nabory.html, /ig.html (рішення SMM 12.10: залишок 1–3 у 12 448 з ~21 тис. позицій — у каталозі/категоріях/«схожих» бейдж був би на більшості карток
    # і перестав би бути правдивим сигналом). Не в каталозі, категоріях, «схожих», пошуку.
    low = badge and 0 < p["stock"] <= STOCK_BADGE_MAX
    badge = f'<span class="stock-badge">Залишилось {int(p["stock"])} шт</span>' if low else ""
    av = "" if low else ('<span class="av">У наявності</span>' if p["stock"] > 0 else '<span class="av oos">Немає</span>')
    lazy = (' fetchpriority="high"' if prio else "") if eager else ' loading="lazy"'
    img = (f'<img src="{esc(p["photo"])}" alt="{esc(p["name"])}"{lazy}>'
           if p["photo"] else '<div class="ph"></div>')
    klass = ("card " + cls) if cls else "card"
    return (
      f'<a class="{klass}" href="product-{esc(p["id"])}.html">'
      f'<div class="ph">{img}{badge}</div>'
      f'<div class="info"><div class="nm">{esc(p["name"])}</div>'
      f'<div class="foot"><span class="pr">{p["price"]} ₴</span>{av}</div></div></a>'
    )

def grid(prods, eager_n=0, badge=False):
    """Сітка карток. eager_n — скільки ПЕРШИХ карток без loading=lazy (перша — fetchpriority=high): на сторінках списків перше фото було lazy,
    і воно ж є LCP-елементом (Lighthouse mobile 11.10.2026: LCP 5,3–6,5 с на категорії)."""
    return '<div class="grid">\n' + "\n".join(tile(p, eager=(i < eager_n), prio=(i == 0 and eager_n > 0), badge=badge) for i, p in enumerate(prods)) + '\n</div>'

def _addon_ok(p):
    """Чи можна пропонувати товар як доповнення (cross-sell): без уцінки/«розпродажу» й копійчаних позицій."""
    c, nm = p["category"].lower(), p["name"].lower()
    return (p["stock"] > 0 and p["price"] >= ADDON_MIN_PRICE
            and "уцінк" not in c and "уценк" not in c
            and not any(m in nm for m in cp._BAD_NAME_MARKERS))

# ── збірка ─────────────────────────────────────────────────────────────
SHRINK_GUARD_MIN_PREV = 1000     # попередня збірка менша за це — гард не діє (перший запуск/демо)
SHRINK_GUARD_FRACTION = 0.7      # нова збірка менша за цю частку попередньої → стоп до запису


class CatalogShrinkError(RuntimeError):
    """Каталог «в наявності» різко зменшився проти попередньої збірки — не пишемо й не прибираємо сторінки."""


def build():
    _load_category_texts()
    _load_product_texts()
    _load_category_related()
    print("[build] тягну каталог Toysi…")
    cat = tp.fetch_toysi_catalog()
    # Захист (аудит #675, ВАЖЛИВО-1): порожній/усічений каталог Toysi (структурно валідний XML без HTTP-помилки) раніше давав index.json=[] і прибирання
    # ВСІХ product-*.html (а з OOS-сторінками — ще ~7 тис.). Тепер збірка зупиняється ДО будь-якого запису: чинний сайт лишається як є, таймер спробує за 2 год.
    try:
        _min_cat = int(os.environ.get("SITE_MIN_CATALOG", "") or tp.TOYSI_EXPECTED_MIN_SIZE)   # свідоме зниження порога (Toysi законно зменшився) — без правки коду
        tp.assert_catalog_size_sane(cat, min_expected=_min_cat)
    except Exception as e:  # noqa: BLE001 — CatalogSizeError; без запису й без прибирання
        print(f"[build] 🚨 ЗБІРКУ ЗУПИНЕНО, сайт не чіпаю: {e}", file=sys.stderr)
        try:
            from telegram_notify import send_throttled_alert
            send_throttled_alert("site_build_catalog_size", f"🚨 site-rebuild: збірку зупинено, каталог Toysi підозріло малий. {str(e)[:300]} Чинні сторінки сайту НЕ чіпано.",
                                 cooldown_sec=6 * 3600)
        except Exception:  # noqa: BLE001 — алерт best-effort
            pass
        raise
    prods = []
    # Товари БЕЗ залишку (рішення Консультанта 11.10.2026, GSC: 7 605 адрес «404» з 8 960 непроіндексованих — це самі зниклі зі складу товари):
    # їхня сторінка НЕ зникає, а лишається живою з «Немає в наявності» + schema.org/OutOfStock. Збираємо окремо: у каталог, категорії, пошук
    # (index.json), головну, крос-сел і фіди Google/Meta/Bing (вони читають index.json) ці позиції НЕ потрапляють; замовити їх теж не можна
    # (site_order_api бере ціни лише з index.json).
    oos_prods = []
    oos_skipped_no_price = 0
    for it in cat.values():
        is_oos = str(it.get("stock") or "0") in ("0", "")
        pics = it.get("pictures")
        if isinstance(pics, str):
            pics = [pics]
        name = it.get("name") or ""
        if not pics or not name:
            continue
        if is_forbidden(it):
            continue   # політика «що не продаємо» (forbidden_products, рішення власника 2026-10-07): ножі/зброя; старі сторінки прибере крок 8 build()
        if is_uno_trademark_blocked(name, it.get("vendor") or ""):
            continue   # ТМ «UNO» (претензія Mattel): не продаємо на власному сайті (SEO-замовлення 2026-10-06)
        catname = it.get("category_name") or "Інше"
        # Каталог ПОВНИЙ відповідно до Toysi (рішення власника 2026-10-05): без відсікань за ціною чи категорією
        # (включно з «Уцінкою»); єдина умова — товар є в наявності й має фото/назву (вище).
        price = price_of(it)
        if is_oos and not price > 0:
            oos_skipped_no_price += 1   # Offer із price 0 + OutOfStock — помилка Rich Results/GMC («Не указана цена»): такий OOS не публікуємо
            continue
        # ВНЕСОК (маржа на замовлення) для ранжування «Рекомендовані» + home-floor:
        # ціна − собівартість − еквайринг(ціна·site-payment) − логістика/замовлення.
        # Комісії маркетплейсу на власному сайті НЕМА (у цьому й сенс сайту). Невідома
        # собівартість → contribution=None: такий товар іде в кінець сорту й не пускається
        # на головну (не ведемо вітрину товаром з невідомою маржею).
        try:
            cost = cp.real_toysi_cost(it)
        except (ValueError, TypeError):
            cost = 0.0
        contribution = (price - cost - price * cp.PAYMENT_COMMISSION.get("site", 0.0)
                        - SITE_FULFILL_PER_ORDER) if cost > 0 else None
        (oos_prods if is_oos else prods).append({
            "id": str(it.get("id")), "name": name, "price": price,
            "category": it.get("category_name") or "Інше",
            "photo": pics[0], "stock": int(it.get("stock") or 0),
            "desc": it.get("description") or "",
            "contribution": contribution,
        })
    # Порядок «Рекомендовані» = за ВНЕСКОМ спадно (рішення Консультанта: на перший екран —
    # позиції, що приносять маржу, а не найбільша категорія/найдорожче). Товари з невідомою
    # собівартістю (contribution=None) — у кінець. Тай-брейк: ціна спадно, потім назва (детермінізм).
    def _rank_key(p):
        c = p["contribution"] if p["contribution"] is not None else -1e9
        return (-c, -p["price"], p["name"])
    prods.sort(key=_rank_key)
    oos_prods.sort(key=lambda p: p["id"])
    if LIMIT:
        prods = prods[:LIMIT]
        oos_prods = oos_prods[:LIMIT]
    # Відносний гард (аудит #675 (a)): абсолютний поріг не ловить помірно усічений фід (26 тис. проходить), а це ~2,5 тис. сторінок → 404.
    # Порівнюємо з index.json ПОПЕРЕДНЬОЇ збірки (те саме «в наявності»): різке падіння → стоп ДО запису. Свідоме — SITE_ALLOW_SHRINK=1.
    try:
        with open(os.path.join(OUT, "index.json"), encoding="utf-8") as _f:
            _old_n = len(json.load(_f))
    except (OSError, ValueError):
        _old_n = 0
    if _old_n >= SHRINK_GUARD_MIN_PREV and len(prods) < _old_n * SHRINK_GUARD_FRACTION and os.environ.get("SITE_ALLOW_SHRINK") != "1" and not LIMIT:
        _msg = (f"товарів у наявності {len(prods)} проти {_old_n} у попередній збірці (< {SHRINK_GUARD_FRACTION:.0%}) — схоже на усічений фід Toysi; "
                "якщо падіння справжнє — SITE_ALLOW_SHRINK=1. Чинні сторінки сайту НЕ чіпано.")
        print(f"[build] 🚨 ЗБІРКУ ЗУПИНЕНО: {_msg}", file=sys.stderr)
        try:
            from telegram_notify import send_throttled_alert
            send_throttled_alert("site_build_shrink", f"🚨 site-rebuild: збірку зупинено, {_msg}", cooldown_sec=6 * 3600)
        except Exception:  # noqa: BLE001 — алерт best-effort
            pass
        raise CatalogShrinkError(_msg)
    print(f"[build] карток до генерації: {len(prods)} (+ без залишку, сторінка жива: {len(oos_prods)}; без залишку й без ціни — пропущено: {oos_skipped_no_price})")

    # категорії
    cats = {}
    for p in prods:
        cats.setdefault(p["category"], []).append(p)
    # «Уцінка» лишається в каталозі (повний каталог Toysi), але НЕ претендує на плитки/чипси/«Новинки» головної:
    # без цього найбільша категорія (≈20% позицій) ставала б №1 на головній (аудит PR #614, знахідка А).
    cat_list = sorted(cats.keys(), key=lambda c: ("уцінк" in c.lower() or "уценк" in c.lower(), -len(cats[c])))
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

    # крос-сел як ДОПОВНЕННЯ (add-on), НЕ замінник: до 4 ДЕШЕВШИХ товарів з ІНШОЇ
    # категорії. Стара логіка брала ту саму категорію за близькою ціною → на картці
    # виходили 4 копії того самого типу товару за тією ж ціною (замінники), що НЕ
    # піднімає чек (та сама ціна, та сама категорія). Тепер пропонуємо дешеві позиції
    # з інших категорій — класичний add-on до замовлення: додає ПОЗИЦІЮ → росте чек і
    # сукупний внесок. Пул обмежений найдешевшими в наявності (кеп), тож O(n·CAP), не O(n²).
    _ADDON_POOL_CAP = 80
    # Пул add-on НЕ бере уцінку, «розпродаж»-назви й копійчані позиції: «Уцінка. Браслет… 12 ₴» як
    # доповнення до дорогої іграшки виглядає сміттям (SMM 2026-10-06, наслідок PR #614 «каталог повний»).
    _addon_pool = sorted((p for p in prods if _addon_ok(p)),
                         key=lambda x: x["price"])[:_ADDON_POOL_CAP]
    related_map = {}
    for p in prods:
        picks, seen_cat = [], set()
        # найближчі знизу (найдорожчі серед дешевших за p), не більше 1 з категорії
        for cand in reversed(_addon_pool):
            if cand["id"] == p["id"] or cand["price"] >= p["price"]:
                continue
            if cand["category"] == p["category"] or cand["category"] in seen_cat:
                continue
            picks.append(cand)
            seen_cat.add(cand["category"])
            if len(picks) == 4:
                break
        related_map[p["id"]] = picks

    n = 0
    # 1) картки товарів
    # Внутрішня перелінковка (SEO 12.10.2026): «Схожі товари» = до 6 товарів В НАЯВНОСТІ з тієї ж категорії, найближчих за ціною (індекс по категорії + bisect, не O(n²)); «Уцінка» — без блоку.
    sim_idx = build_similar_index(prods)
    for p in prods:
        write_product(p, related=related_map.get(p["id"]), cat_slug=cat_slug, similar=similar_products(sim_idx, p))
        n += 1
    # 1б) сторінки товарів без залишку: адреса жива (OutOfStock), без кошика/крос-селу
    for p in oos_prods:
        # внутрішні посилання з OOS-сторінки на товари в наявності тієї ж категорії: шлях покупця і проти soft-404 (сторінка не «глухий кут»)
        write_product(p, related=None, cat_slug=cat_slug, similar=similar_products(sim_idx, p), similar_title="Схожі товари в наявності")
    # 2) сторінки категорій (з пагінацією) + 3) повний каталог — збираємо ВСІ записані сторінки
    paged = set()
    for c in cat_list:
        paged |= write_catalog(f"Каталог • {c}", rec_order(cats[c]), cat_list, cat_slug, f"category-{cat_slug[c]}.html", active=c)
    prods_rec = rec_order(prods)
    paged |= write_catalog("Каталог іграшок", prods_rec, cat_list, cat_slug, "catalog.html", active=None)
    # 4) головна
    write_home(prods, cats, cat_list, cat_slug)
    write_ig(prods)
    has_nabory = write_nabory(prods)
    # 5) кошик + checkout + сторінка подяки
    write_cart(_cart_addons(prods))
    write_thanks()
    write_404()
    # 5б) індекс усіх категорій (закриває orphan) + сторінки довіри (Про нас/Доставка/…)
    write_categories_index(cat_list, cat_slug, cats)
    trust = write_trust_pages()
    # 6) індекс пошуку
    # index.json — клієнтський пошук + фільтр/сорт каталогу. c=категорія, s=наявність(1/0).
    idx = [{"id": p["id"], "n": p["name"], "pr": p["price"], "p": p["photo"],
            "c": p["category"], "s": 1 if p["stock"] > 0 else 0} for p in prods_rec]
    with open(os.path.join(OUT, "index.json"), "w", encoding="utf-8") as f:
        json.dump(idx, f, ensure_ascii=False)
    # 7) SEO: sitemap + robots
    static_urls = [("categories.html", "0.6", "weekly")] + ([("nabory.html", "0.7", "weekly")] if has_nabory else []) + [(f, "0.4", "monthly") for f in trust]
    write_sitemap(prods, paged, static_urls, oos_prods=oos_prods)
    write_robots()

    # 8) ПРИБИРАННЯ ЗАСТАРІЛИХ сторінок від попередніх білдів: build пише лише поточний вибір,
    # а старі product-/category-/catalog-*.html лишались у site/ і віддавались із застарілими цінами/сміттям
    # (реально знайдено 84 такі). Видаляємо ті, що не входять у поточний набір (пишемо УСІ актуальні
    # вище ДО цього кроку, тож видаляємо лише справжні залишки). `paged` містить усі сторінки
    # каталогу/категорій (вкл. пагіновані catalog_N/category-slug_N), тож застарілі сторінки
    # пагінації від більшого попереднього білда теж приберуться.
    valid = {f"product-{p['id']}.html" for p in prods} | {f"product-{p['id']}.html" for p in oos_prods} | paged
    removed = 0
    for fn in os.listdir(OUT):
        if (fn.startswith(("product-", "category-", "catalog")) and fn.endswith(".html")
                and fn not in valid):
            try:
                os.remove(os.path.join(OUT, fn))
                removed += 1
            except OSError:
                pass

    _written_slugs = {m.group(1) for m in (re.fullmatch(r"category-(.+)\.html", f) for f in paged if not re.search(r"_\d+\.html$", f)) if m}
    _unmatched = sorted(set(_CATEGORY_TEXTS) - _written_slugs)
    if _unmatched:
        print(f"[build] ⚠ category_texts.json: slug без сторінки категорії (друкарська помилка? текст не з'явиться): {_unmatched[:10]}", file=sys.stderr)
    _prod_ids = {p["id"] for p in prods} | {p["id"] for p in oos_prods}
    _pt_unmatched = sorted(set(_PRODUCT_TEXTS) - _prod_ids, key=_id_key)
    if _pt_unmatched:
        print(f"[build] ⚠ product_texts.json: {len(_pt_unmatched)} id без картки в цій збірці (товару нема/зник/LIMIT; текст не з'явиться): {_pt_unmatched[:10]}", file=sys.stderr)
    _rel_unmatched = sorted(set(_CATEGORY_RELATED) - _written_slugs)
    if _rel_unmatched:
        print(f"[build] ⚠ category_related.json: ключ-slug без сторінки категорії (друкарська помилка? блок не з'явиться): {_rel_unmatched[:10]}", file=sys.stderr)
    _rel_targets = sorted({t for v in _CATEGORY_RELATED.values() for t in v} - _written_slugs)
    if _rel_targets:
        print(f"[build] ⚠ category_related.json: slug-цілі без сторінки категорії (посилання не показуємо): {_rel_targets[:10]}", file=sys.stderr)
    print(f"[build] внутрішня перелінковка: текстів карток показано {len(_prod_ids & set(_PRODUCT_TEXTS))}, категорій із блоком «Схожі категорії» {len(set(_CATEGORY_RELATED) & _written_slugs)}")
    print(f"[build] готово: {n} карток, {len(cat_list)} категорій, прибрано застарілих {removed}, сторінок без залишку (OutOfStock) {len(oos_prods)}, "
          "+index/catalog/cart/index.json/sitemap.xml/robots.txt")

    # 9) детектор «тихого зникнення фото» (Консультант 10.10.2026): вибірка фото з нашим Referer; best-effort, не валить збірку
    try:
        import site_photo_probe
        site_photo_probe.run(idx)
    except Exception as e:  # noqa: BLE001
        print(f"[build] photo_probe пропущено: {type(e).__name__}: {e}")

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

PAGER_ALL_MAX = 7        # до цієї кількості сторінок показуємо ВСІ номери
PAGER_MAX_CLICKS = 3     # будь-яка сторінка листингу досяжна з першої не більше ніж за стільки кліків


@functools.lru_cache(maxsize=None)
def _pager_plan(pages):
    """(l, m, c): розмір «локального» блоку, кількість «середніх» блоків у «великому», кількість «великих» блоків. c·m·l ≥ pages, мінімум c+m+l (≈ ∛pages кожне):
    саме стільки посилань треба показувати, щоб із першої сторінки будь-яка була за 3 кліки (великий → середній → локальний)."""
    best = None
    for l in range(1, 61):
        for m in range(1, 61):
            c = -(-pages // (l * m))
            if c > 60:
                continue
            key = (c + m + l, -l, -m)
            if best is None or key < best[0]:
                best = (key, (l, m, c))
    return best[1] if best else (60, 60, -(-pages // 3600))


def _pager_pages(k, pages):
    """Номери, що показуються в пейджері сторінки k з pages. ≤7 — усі. Інакше: 1 … k-2 k-1 k k+1 k+2 … остання (як було) ПЛЮС «орієнтири», бо одного віконця ±2
    замало: з 1-ї сторінки до 21-ї з 42 було б ~10 кліків. Орієнтири (3 рівні): перші сторінки всіх «великих» блоків; перші сторінки «середніх» блоків у ПОТОЧНОМУ великому;
    усі сторінки ПОТОЧНОГО середнього блоку. Шлях з 1-ї до t: 1 → початок великого блоку t → початок середнього → t = ≤3 кліки."""
    if pages <= PAGER_ALL_MAX:
        return set(range(1, pages + 1))
    show = {1, pages} | set(range(max(1, k - 2), min(pages, k + 2) + 1))
    l, m, c = _pager_plan(pages)
    big, i = l * m, k - 1
    show |= {j * big + 1 for j in range(c) if j * big + 1 <= pages}
    cb = (i // big) * big
    show |= {cb + j * l + 1 for j in range(m) if cb + j * l + 1 <= pages}
    mb = (i // l) * l
    show |= {mb + j + 1 for j in range(l) if mb + j + 1 <= pages}
    return show


def _pager(fname, k, pages):
    """Навігація сторінками: вікно ±2 + перша/остання + орієнтири (_pager_pages), щоб будь-яка сторінка була ≤3 кліків від першої. Порожньо, якщо сторінка одна."""
    if pages <= 1:
        return ""
    def lnk(i, txt, cls="pg"):
        return f'<a class="{cls}" href="{_page_fname(fname, i)}">{txt}</a>'
    out = ['<nav class="pager" aria-label="Сторінки">']
    if k > 1:
        out.append(lnk(k - 1, "← Назад", "pg nav"))
    prev = 0
    for i in sorted(_pager_pages(k, pages)):
        if i - prev > 1:
            out.append('<span class="pg gap">…</span>')
        out.append(f'<span class="pg cur">{i}</span>' if i == k else lnk(i, str(i)))
        prev = i
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
          '<option value="rec">Рекомендовані</option>'
          '<option value="cheap">Спершу дешевші</option>'
          '<option value="dear">Спершу дорожчі</option>'
          '<option value="az">За назвою А–Я</option>'
        '</select>'
        '<select id="cf-cat" aria-label="Категорія"><option value="">Усі категорії</option></select>'
        '<span class="cf-price"><input id="cf-min" type="number" inputmode="numeric" min="0" placeholder="ціна від">'
          '<input id="cf-max" type="number" inputmode="numeric" min="0" placeholder="до"></span>'
        # Чекбокс «лише в наявності» прибрано (тестувальник 13.10): index.json лишає лише товари В НАЯВНОСТІ (s завжди 1), тож фільтр нічого не міняв. Повернути — коли в index.json з'являться позиції без залишку.
        '<button id="cf-reset" class="cf-reset" type="button">Скинути</button>'
      '</div>'
      '<p id="cat-count" class="pagenote" hidden></p>'
      '<div id="cat-js" class="grid" hidden></div>'
      '<div id="cat-more-wrap" hidden><button id="cat-more" class="btn ghost">Показати ще</button></div>'
    )

# ── SEO-замовлення 2026-10-10 (п.2–3, фінальні шаблони — SEO_CHANNEL.md): title/description, хлібні крихти, розмітка Product/ItemList ──
PRICE_VALID_DAYS = 7     # ціни/наявність перебудовуються кожні 2 год (site-rebuild.timer); якщо збірка стане >7 діб — розмітка сама «протухне»
# (до 12.10.2026 суму доставки НЕ друкували — рішення Консультанта 10.10: «нічим не підкріплене»; тепер лише виміряне й «орієнтовно», див. DELIVERY_COST) — стара примітка: (рішення Консультанта 10.10.2026): «від 65/70 ₴» нічим не підкріплене; повернути конкретику — коли бухгалтер дасть виміряну цифру (фактичні тарифи НП у книзі КОДВ).
PAY_SHORT = "оплата карткою або при отриманні" if LIQPAY_LIVE else "оплата при отриманні"
TITLE_MAX = 65
DESC_MAX = 160
_PROD_SUFFIX = " — купити | PlutusToys"


def _ld_script(d) -> str:
    # безпечно в <script>: кожен '<' екрануємо (chr(92) + "u003c") — жоден HTML-вектор не вийде літерально
    return '<script type="application/ld+json">' + json.dumps(d, ensure_ascii=False).replace("<", chr(92) + "u003c") + "</script>" + chr(10)


def crumbs_html(trail) -> str:
    """Видимі хлібні крихти. trail = [(назва, href|None)], останній елемент — поточна сторінка (без посилання)."""
    out = []
    for i, (name, href) in enumerate(trail):
        last = i == len(trail) - 1
        out.append(f'<span aria-current="page">{esc(name)}</span>' if (last or not href)
                   else f'<a href="{"/" if href == "index.html" else esc(href)}">{esc(name)}</a>')   # «/» а не index.html: /index.html віддає 301 на корінь
    return '<nav class="crumbs" aria-label="Хлібні крихти">' + ' <span class="sep">›</span> '.join(out) + '</nav>' + chr(10)


def breadcrumb_ld(trail) -> str:
    """JSON-LD BreadcrumbList: ті самі крихти, що й видимі; href → абсолютний URL; поточна сторінка — без item (допускається)."""
    items = []
    for i, (name, href) in enumerate(trail):
        it = {"@type": "ListItem", "position": i + 1, "name": name}
        if href and i < len(trail) - 1:
            it["item"] = f"{SITE_URL}/" if href == "index.html" else f"{SITE_URL}/{href}"
        items.append(it)
    return _ld_script({"@context": "https://schema.org", "@type": "BreadcrumbList", "itemListElement": items})


# Стан товару за НАЗВОЮ (SEO п.3б: «не вигадуй — перевір по назві»). «Уцінка» у Toysi = нетоварний вигляд
# (пошкодження упаковки АБО самого товару). Дефект самого товару → DamagedCondition; лише упаковка → NewCondition;
# «вітринний» → UsedCondition; незрозуміло → поле НЕ ставимо (краще пропустити, ніж збрехати).
_PACK_WORD = r"(?:упаков|коробк|коробц|пакуван|віконц|блістер|ярлик|етикет|плівк)\w*"
_PACK_DMG = r"(?:пошкоджен|пом.ят|м.ят|деформован|надірван|порван|розірван|подряпан|потерт|брудн|мнут|стерт|відсутн|подерт)\w*"
# фрази про шкоду САМЕ упаковці: «пошкоджена упаковка» / «упаковка пошкоджена» / «потертості на коробці»
_PACK_PHRASE_RE = re.compile(rf"{_PACK_DMG}\s+(?:на\s+|у\s+|в\s+)?{_PACK_WORD}(?:\s+(?:і|та|й)\s+{_PACK_WORD})*|{_PACK_WORD}\s+{_PACK_DMG}", re.I)
_DEFECT_RE = re.compile(r"подряп|тріщ|трещ|трісну|тріскан|треснут|зламан|зломан|поламан|полом|сломан|відлам|(?<!\w)не\s+\w|(?<!\w)ні\s+склада|"
                        r"дефект|потерт|відсутн|немає|нема(?!\w)|неповн|відірван|відпал|відпав|розбит|побит|погнут|сліди?(?!\w)|слід\s+від|вм.ят|"
                        r"протіка|пропуска|здува|стерт|здер|брудн|розірван|брак|несправн|відклеїв|подерт|пошкодж|"
                        r"відбит|битий|бите(?!\w)|заклин|засох|затверд|погано|рве(?!\w)|рветьс|рвана|мутн|ржав|"
                        r"відкривал|відходить|відійшов|пляма|плями|работа", re.I)
_UNSURE_RE = re.compile(r"без(?!\w)|стали(?!\w)|закінч|термін|придатн|уцінк\w*\s*:|уц[еі]нк", re.I)   # слова, що ще не дефект, але New вже не гарантують
_PREFIX_RE = re.compile(r"^\s*(?:уц[еі]нк\w*\.?\s*)+", re.I)


def item_condition(name) -> str:
    """Стан за НАЗВОЮ. NewCondition — лише за ВУЗЬКИМ білим списком: уся назва після префікса «Уцінка/Уценка» = оригінальна назва
    товару + РІВНО фраза про упаковку в кінці (без жодного іншого слова-описувача). Будь-який дефект → Damaged; решта → поле не ставимо
    (аудит PR #657: краще пропустити, ніж позначити товар із дефектом як новий)."""
    n = (name or "").strip()
    if not re.search(r"уц[еі]нк", n, re.I):
        return "https://schema.org/NewCondition"       # не уцінка → звичайний новий товар
    if not _PREFIX_RE.match(n):
        return ""                                       # «уцінка» не на початку — незвична назва, не вгадуємо
    if "вітрин" in n.lower():
        return "https://schema.org/UsedCondition"
    body = _PREFIX_RE.sub("", n)
    rest, nsub = _PACK_PHRASE_RE.subn(" ", body)       # прибираємо фрази про шкоду упаковці й дивимось, що лишилось
    if _DEFECT_RE.search(rest):
        return "https://schema.org/DamagedCondition"
    if nsub == 1 and not _UNSURE_RE.search(rest) and re.search(_PACK_PHRASE_RE.pattern + r"[\s.,;!]*$", body, re.I):
        tail = re.split(r"\s[-–—]\s", body, maxsplit=1)
        if len(tail) == 1 or not re.sub(r"[\s.,;!]|(?:та|і|й|на|у|в)(?!\w)|" + _PACK_WORD, "", _PACK_PHRASE_RE.sub("", tail[1]), flags=re.I):
            return "https://schema.org/NewCondition"
    return ""


def _fmt_int(n) -> str:
    return f"{int(n):,}".replace(",", " ")


_PROD_SUFFIX_OOS = " — немає в наявності | PlutusToys"


def product_title(name, in_stock=True) -> str:
    """{назва} — купити | PlutusToys (без залишку: «— немає в наявності | PlutusToys»); якщо довше за 65 — обрізаємо НАЗВУ по межі слова + «…», суфікс лишаємо."""
    name = (name or "").strip()
    suffix = _PROD_SUFFIX if in_stock else _PROD_SUFFIX_OOS
    if len(name) + len(suffix) <= TITLE_MAX:
        return name + suffix
    room = TITLE_MAX - len(suffix) - 1
    cut = name[:room].rsplit(" ", 1)[0].rstrip(" ,.;:—–-") if " " in name[:room] else name[:room]
    return cut + "…" + suffix


def product_description(name, price, plain) -> str:
    """(1) реальний опис Toysi ≥40 симв. і не «Бренд: …» → ~110 симв. по межі слова + ціна/доставка; (2) інакше — шаблон із назвою."""
    t = html.unescape((plain or "").strip())
    if len(t) >= 40 and not re.match(r"бренд\s*:", t, re.I):
        tail = f" Ціна {price} ₴, доставка Новою Поштою."
        lead = _cut(t, 110)
        if len(lead) + len(tail) > DESC_MAX:
            lead = _cut(t, max(40, DESC_MAX - len(tail) - 1))
        if lead and lead[-1] not in ".!?…":
            lead += "."
        return (lead + tail)[:DESC_MAX]
    fb = f"{name} — {price} ₴. Доставка Новою Поштою по Україні, повернення 14 днів."
    if len(fb) > DESC_MAX:   # дуже довга назва: ріжемо назву, хвіст лишаємо
        tailf = f" — {price} ₴. Доставка Новою Поштою по Україні, повернення 14 днів."
        fb = _cut(name, DESC_MAX - len(tailf) - 1).rstrip("…") + "…" + tailf
    return fb


def category_meta(name, total, min_price, page_no=1, pages=1):
    """(title, description) категорії/каталогу за шаблоном SEO; множина «товар/товари/товарів»."""
    cnt = f"{_fmt_int(total)} товар{_plural(total)}"
    if name is None:   # повний каталог
        base = "Каталог іграшок"
        title = f"{base} — купити в Україні | PlutusToys" if page_no == 1 else f"{base} — сторінка {page_no} | PlutusToys"
        desc = f"Каталог іграшок PlutusToys: {cnt}, ціни від {min_price} ₴, доставка Новою Поштою по Україні."
    else:
        title = f"{name} — купити в Україні | PlutusToys" if page_no == 1 else f"{name} — сторінка {page_no} | PlutusToys"
        desc = f"{cnt} у категорії «{name}»: ціни від {min_price} ₴, доставка Новою Поштою по Україні."
    if page_no > 1:
        desc = f"Сторінка {page_no} з {pages}. " + desc
    return title, desc[:DESC_MAX]


def offer_extras(p) -> dict:
    """Поля Product.offers для Merchant free listings (SEO п.3б). Значення — з текстів сторінок «Доставка»/«Повернення»."""
    from datetime import date, timedelta
    extra = {
        "priceValidUntil": (date.today() + timedelta(days=PRICE_VALID_DAYS)).isoformat(),
        # shippingDetails без виміряної ставки не пишемо (shippingRate обов'язковий, вигадане число — розбіжність розмітки зі сторінкою); повернемо разом із цифрою.
        "hasMerchantReturnPolicy": {
            "@type": "MerchantReturnPolicy", "applicableCountry": "UA",
            "returnPolicyCategory": "https://schema.org/MerchantReturnFiniteReturnWindow",
            "merchantReturnDays": 14, "returnMethod": "https://schema.org/ReturnByMail",
            "returnFees": "https://schema.org/ReturnShippingFees",
        },
    }
    cond = item_condition(p["name"])
    if cond:
        extra["itemCondition"] = cond
    return extra


# ── Порядок «Рекомендовані» для КАТАЛОГУ/категорій/index.json (рішення Консультанта 10.10.2026; зауваження Тестувальника) ──
# Було: за внеском спадно → перший екран = велосипед за 10 434 ₴, найдешевше на стор.1 — 3 426 ₴ (99,9% асортименту дешевше). Тепер:
#   кошик A (першим): є фото, у наявності, НЕ уцінка, внесок відомий і >0, ціна в міжквартильному діапазоні індексу 97–381 ₴;
#   кошик B: усе інше. Усередині кошика — «перемішування» з ФІКСОВАНИМ зерном (SHA-1 від id): порядок однаковий між перебудовами
#   (статичні сторінки, пагінація й індексація не плавають). Дорогі позиції нікуди не зникають: сортування/фільтр ціни лишаються.
# Головна («Новинки»), крос-сел і внутрішні розрахунки далі користуються порядком за внеском — вони НЕ чіпаються.
REC_PRICE_LO, REC_PRICE_HI = 97, 381
REC_SEED = "plutus-rec-v1"


def _is_markdown_item(p) -> bool:
    return bool(re.search(r"уц[еі]нк", (p.get("category") or "") + " " + (p.get("name") or ""), re.I))


def _rec_bucket(p) -> int:
    c = p.get("contribution")
    ok = (p.get("photo") and p.get("stock", 0) > 0 and not _is_markdown_item(p)
          and c is not None and c > 0 and REC_PRICE_LO <= p["price"] <= REC_PRICE_HI)
    return 0 if ok else 1


def rec_order(items):
    return sorted(items, key=lambda p: (_rec_bucket(p), hashlib.sha1(f"{REC_SEED}:{p['id']}".encode()).hexdigest()))


_CATEGORY_TEXTS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "category_texts.json")
_CATEGORY_TEXTS = {}


def _load_category_texts():
    """site/category_texts.json: {slug: "абзац 1\\n\\nабзац 2"} — унікальні вступні тексти категорій від SEO (11.10.2026). Нема файлу — порожньо (так і задумано).
    ГОЛОСНО (stderr), а не мовчки, коли файл ЄСТЬ, але не прочитався (BOM читаємо як utf-8-sig; битий JSON, не-словник) або в ньому відкинуто значення: тоді SEO бачить причину в журналі збірки."""
    global _CATEGORY_TEXTS
    _CATEGORY_TEXTS = {}
    try:
        with open(_CATEGORY_TEXTS_FILE, encoding="utf-8-sig") as f:
            raw = f.read()
    except FileNotFoundError:
        return _CATEGORY_TEXTS
    except (OSError, UnicodeDecodeError) as e:
        print(f"[build] ⚠ category_texts.json НЕ прочитано ({type(e).__name__}: {e}) — вступів категорій на сайті не буде", file=sys.stderr)
        return _CATEGORY_TEXTS
    try:
        data = json.loads(raw)
    except (ValueError, RecursionError) as e:
        print(f"[build] ⚠ category_texts.json — невалідний JSON ({e}); частий випадок: справжній Enter усередині рядка (пиши \\n\\n) — вступів категорій на сайті не буде", file=sys.stderr)
        return _CATEGORY_TEXTS
    if not isinstance(data, dict):
        print(f"[build] ⚠ category_texts.json — очікується об'єкт {{slug: текст}}, отримано {type(data).__name__} — вступів не буде", file=sys.stderr)
        return _CATEGORY_TEXTS
    bad = [k for k, v in data.items() if not isinstance(v, str)]
    if bad:
        print(f"[build] ⚠ category_texts.json: значення НЕ рядок, відкинуто: {bad[:10]}", file=sys.stderr)
    _CATEGORY_TEXTS = {str(k): v for k, v in data.items() if isinstance(v, str) and v.strip()}
    return _CATEGORY_TEXTS


def _category_intro_html(fname):
    """Вступний текст категорії (лише 1-ша сторінка; на пагінації не дублюємо). slug = ім'я файлу без «category-» і «.html». HTML у тексті екранується, абзаци — за порожнім рядком."""
    m = re.fullmatch(r"category-(.+)\.html", fname or "")
    text = _CATEGORY_TEXTS.get(m.group(1)) if m else None
    if not text:
        return ""
    paras = [p.strip() for p in re.split(r"\n\s*\n", text.replace("\r\n", "\n")) if p.strip()]
    return '<div class="cat-intro">' + "".join(f"<p>{esc(p)}</p>" for p in paras) + "</div>\n"


# ── Внутрішня перелінковка (SEO-замовлення 12.10.2026) ──────────────────────────────────────────────────────────
# А) текст картки: site/product_texts.json {"<toysi_id>": "абзац\n\nабзац"} — plain text, esc(), абзаци за порожнім рядком;
# Б) «Схожі товари» на картці; В) «Схожі категорії» на 1-й сторінці категорії: site/category_related.json {"related": {"<slug>": ["<slug>", …]}}.
_HERE = os.path.dirname(os.path.abspath(__file__))
_PRODUCT_TEXTS_FILE = os.path.join(_HERE, "product_texts.json")
_PRODUCT_TEXTS = {}
PRODUCT_TEXT_WARN_LEN = 900          # довший текст — попередження (SEO: абзац-«простирадло» виштовхує характеристики)
_CATEGORY_RELATED_FILE = os.path.join(_HERE, "category_related.json")
_CATEGORY_RELATED = {}
RELCATS_MAX = 6
SIMILAR_PRODUCTS_MAX = 6
_MISSING = object()


def _read_json_loud(path, label, consequence):
    """JSON-файл, який ПОВИНЕН читатися, коли він є. Нема файлу — _MISSING без шуму (так і задумано). Є, але не прочитався (BOM читаємо як utf-8-sig; не-UTF-8; битий JSON;
    RecursionError на глибоко вкладеному) — _MISSING і ГОЛОСНО в stderr: SEO бачить причину в журналі збірки, а не «блоку чомусь нема»."""
    try:
        with open(path, encoding="utf-8-sig") as f:
            raw = f.read()
    except FileNotFoundError:
        return _MISSING
    except (OSError, UnicodeDecodeError) as e:
        print(f"[build] ⚠ {label} НЕ прочитано ({type(e).__name__}: {e}) — {consequence}", file=sys.stderr)
        return _MISSING
    try:
        return json.loads(raw)
    except (ValueError, RecursionError) as e:
        print(f"[build] ⚠ {label} — невалідний JSON ({e}); частий випадок: справжній Enter усередині рядка (пиши \\n\\n) — {consequence}", file=sys.stderr)
        return _MISSING


def _utf8_clean(v):
    """Текст можна безпечно записати в UTF-8 HTML: без одиноких сурогатів (\\ud800 → UnicodeEncodeError) і NUL (сирий NUL у HTML)."""
    try:
        v.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return "\x00" not in v


def _load_product_texts():
    """site/product_texts.json: {"<toysi_id>": "абзац 1\\n\\nабзац 2"}. Не довіряємо даним: корінь — об'єкт; ключ — лише ASCII-цифри; значення — непорожній рядок
    (інше відкидається ГОЛОСНО); довше PRODUCT_TEXT_WARN_LEN або з символами < > — попередження (текст усе одно екранується й показується)."""
    global _PRODUCT_TEXTS
    _PRODUCT_TEXTS = {}
    data = _read_json_loud(_PRODUCT_TEXTS_FILE, "product_texts.json", "текстів карток на сайті не буде")
    if data is _MISSING:
        return _PRODUCT_TEXTS
    if not isinstance(data, dict):
        print(f"[build] ⚠ product_texts.json — очікується об'єкт {{id: текст}}, отримано {type(data).__name__} — текстів карток не буде", file=sys.stderr)
        return _PRODUCT_TEXTS
    bad_val = [k for k, v in data.items() if not isinstance(v, str)]
    if bad_val:
        print(f"[build] ⚠ product_texts.json: значення НЕ рядок, відкинуто: {bad_val[:10]}", file=sys.stderr)
    bad_id = [k for k in data if not re.fullmatch(r"[0-9]+", k)]
    if bad_id:
        print(f"[build] ⚠ product_texts.json: id не з цифр, відкинуто: {bad_id[:10]}", file=sys.stderr)
    out = {k: v for k, v in data.items() if isinstance(v, str) and v.strip() and re.fullmatch(r"[0-9]+", k)}
    unenc = [k for k, v in out.items() if not _utf8_clean(v)]
    if unenc:
        print(f"[build] ⚠ product_texts.json: одинокий сурогат/NUL у тексті — відкинуто (інакше збірка падала б під час запису HTML): {unenc[:10]}", file=sys.stderr)
        out = {k: v for k, v in out.items() if k not in set(unenc)}
    too_long = [k for k, v in out.items() if len(v.strip()) > PRODUCT_TEXT_WARN_LEN]
    if too_long:
        print(f"[build] ⚠ product_texts.json: текст довший за {PRODUCT_TEXT_WARN_LEN} символів (показано, але скоротіть): {too_long[:10]}", file=sys.stderr)
    angle = [k for k, v in out.items() if "<" in v or ">" in v]
    if angle:
        print(f"[build] ⚠ product_texts.json: у тексті є < або > (екранується як звичайний текст, розмітка НЕ спрацює): {angle[:10]}", file=sys.stderr)
    _PRODUCT_TEXTS = out
    return _PRODUCT_TEXTS


def _product_text_html(pid):
    """Блок тексту картки (plain text → esc(), абзаци за порожнім рядком). Нема тексту для id — порожньо (блоку нема)."""
    text = _PRODUCT_TEXTS.get(str(pid))
    if not text:
        return ""
    paras = [p.strip() for p in re.split(r"\n\s*\n", text.replace("\r\n", "\n")) if p.strip()]
    if not paras:
        return ""
    return '<div class="prod-text">' + "".join(f"<p>{esc(p)}</p>" for p in paras) + "</div>"


def _load_category_related():
    """site/category_related.json: {"related": {"<slug>": ["<slug>", …]}} (ключ "description" ігнорується). slug = ім'я файлу категорії без «category-» і «.html».
    Ключ і цілі — лише [a-z0-9-]+ (інше відкидається ГОЛОСНО); значення — список. Існування сторінок перевіряється під час збірки (_relcats_html + попередження наприкінці build())."""
    global _CATEGORY_RELATED
    _CATEGORY_RELATED = {}
    data = _read_json_loud(_CATEGORY_RELATED_FILE, "category_related.json", "блоків «Схожі категорії» не буде")
    if data is _MISSING:
        return _CATEGORY_RELATED
    rel = data.get("related") if isinstance(data, dict) else None
    if not isinstance(rel, dict):
        print("[build] ⚠ category_related.json — очікується {\"related\": {slug: [slug, …]}} — блоків «Схожі категорії» не буде", file=sys.stderr)
        return _CATEGORY_RELATED
    bad = []
    out = {}
    for k, v in rel.items():
        if not re.fullmatch(r"[a-z0-9-]+", k) or not isinstance(v, list):
            bad.append(k)
            continue
        good = [x for x in v if isinstance(x, str) and re.fullmatch(r"[a-z0-9-]+", x)]
        if len(good) != len(v):
            bad.append(f"{k}: {[x for x in v if x not in good][:3]}")
        if good:
            out[k] = good
    if bad:
        print(f"[build] ⚠ category_related.json: некоректні ключі/значення відкинуто: {bad[:10]}", file=sys.stderr)
    _CATEGORY_RELATED = out
    return _CATEGORY_RELATED


def _relcats_html(fname, cat_slug, active):
    """«Схожі категорії» (nav.relcats): до RELCATS_MAX посилань із category_related.json для slug цієї категорії; лише slug-и, сторінка яких є в збірці, без самої категорії й без дублів.
    Лише 1-ша сторінка категорії (пагінація не дублює) — це викликається тільки з k == 1."""
    if not active:
        return ""
    m = re.fullmatch(r"category-(.+)\.html", fname or "")
    targets = _CATEGORY_RELATED.get(m.group(1)) if m else None
    if not targets:
        return ""
    by_slug = {sl: c for c, sl in cat_slug.items()}
    links, seen = [], {m.group(1)}
    for sl in targets:
        if sl in seen or sl not in by_slug:
            continue
        seen.add(sl)
        links.append(f'<a href="category-{esc(sl)}.html">{esc(by_slug[sl])}</a>')
        if len(links) >= RELCATS_MAX:
            break
    if not links:
        return ""
    return '<nav class="relcats" aria-label="Схожі категорії"><h2>Схожі категорії</h2>' + "".join(links) + '</nav>\n'


def _id_key(pid):
    """Детермінований порядок id: числові — за значенням (9 < 10), решта — після них за рядком."""
    s = str(pid)
    return (0, int(s), s) if re.fullmatch(r"[0-9]+", s) else (1, 0, s)


def _is_markdown_category(c):
    c = (c or "").lower()
    return "уцінк" in c or "уценк" in c


_TIE_MOD = 1 << 64


def _tie_key(q, p):
    """Тай-брейк «схожих» на ОДНАКОВІЙ відстані за ціною (аудит #692: за id від найменшого ВСІ товари серії на однаковій ціні лінкували на одні й ті самі 6 id — п'ять «хабів» по 196 вхідних,
    «Пазли G-Toys» 94 ₴ × 195 карток): ЦИКЛІЧНО від id самої картки p — перші ті, чий id одразу за id p (далі по колу), тож вхідні посилання розподіляються рівномірно.
    Детерміновано. Числові id: (id_q − id_p) mod 2^64; нечислові — після числових, за рядком."""
    kq = _id_key(q["id"])
    if kq[0] != 0:
        return kq
    kp = _id_key(p["id"])
    base = kp[1] if kp[0] == 0 else -1
    return (0, (kq[1] - base) % _TIE_MOD, "")


def build_similar_index(prods):
    """{категорія: (відсортовані унікальні ціни, [(товари з цією ціною за id, числові id для bisect, кількість числових)])} лише для товарів В НАЯВНОСТІ, не з «Уцінки» (картка-«Уцінка» блоку не має).
    Пошук найближчих по ціні — bisect, тож на ~28 тис. сторінок немає O(n²)."""
    by = {}
    for p in prods:
        if p.get("stock", 0) > 0 and not _is_markdown_category(p.get("category")):
            by.setdefault(p["category"], {}).setdefault(p["price"], []).append(p)
    idx = {}
    for c, runs in by.items():
        prices = sorted(runs)
        packed = []
        for x in prices:
            items = sorted(runs[x], key=lambda q: _id_key(q["id"]))
            nums = [_id_key(q["id"])[1] for q in items if _id_key(q["id"])[0] == 0]
            packed.append((items, nums, len(nums)))
        idx[c] = (prices, packed)
    return idx


def _rotated(group, p):
    """Група однакової ціни в циклічному порядку від id картки p (див. _tie_key)."""
    items, nums, nn = group
    kp = _id_key(p["id"])
    j = bisect.bisect_right(nums, kp[1]) if kp[0] == 0 else 0
    return items[j:nn] + items[:j] + items[nn:]


def similar_products(idx, p, k=SIMILAR_PRODUCTS_MAX):
    """До k товарів в наявності з ТІЄЇ Ж категорії, найближчих за ціною до p: порядок (|ціна − ціна p|, _tie_key), без самого p і без OOS. «Уцінка»/«Уценка» — порожньо.
    Іде від позиції p у списку цін двома вказівниками (ліворуч/праворуч) за зростанням відстані; рівні відстані з обох боків зливаються за _tie_key (heapq.merge)."""
    if _is_markdown_category(p.get("category")):
        return []
    ent = idx.get(p.get("category"))
    if not ent:
        return []
    prices, runs = ent
    price, n = p["price"], len(prices)
    left, right = bisect.bisect_left(prices, price) - 1, bisect.bisect_left(prices, price)
    out = []
    while len(out) < k and (left >= 0 or right < n):
        dl = price - prices[left] if left >= 0 else None
        dr = prices[right] - price if right < n else None
        d = dl if dr is None else (dr if dl is None else min(dl, dr))
        groups = []
        if dl is not None and dl == d:
            groups.append(_rotated(runs[left], p))
            left -= 1
        if dr is not None and dr == d:
            groups.append(_rotated(runs[right], p))
            right += 1
        for q in heapq.merge(*groups, key=lambda q: _tie_key(q, p)):
            if q["id"] == p["id"]:
                continue
            out.append(q)
            if len(out) >= k:
                break
    return out


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
        full_t, desc = category_meta(active, total, min((x["price"] for x in prods), default=0), k, pages)
        trail = [("Головна", "index.html")] + ([("Каталог", "catalog.html")] if active else []) + [(active or "Каталог", None)]
        head = breadcrumb_ld(trail)
        head += _ld_script({"@context": "https://schema.org", "@type": "ItemList",
                            "itemListElement": [{"@type": "ListItem", "position": (k - 1) * PER_PAGE + i + 1,
                                                 "url": f"{SITE_URL}/product-{x['id']}.html"} for i, x in enumerate(chunk)]})
        if k > 1:
            head += f'<link rel="prev" href="{_page_fname(fname, k - 1)}">\n'
        if k < pages:
            head += f'<link rel="next" href="{_page_fname(fname, k + 1)}">\n'
        # Панель сорт/фільтр — лише на головному каталозі (стор.1). Працює клієнтом з index.json
        # (усі товари), тож фільтрує/сортує ВЕСЬ каталог, не лише сторінку. Прогресивне покращення:
        # без JS лишається статична пагінована сітка (SEO), контроли сховані (hidden) до ініціалізації.
        controls = _catalog_controls() if (active is None and k == 1) else ""
        static_grid = grid(chunk, eager_n=(4 if k == 1 else 0)) + _pager(fname, k, pages)
        if controls:
            static_grid = f'<div id="cat-static">{static_grid}</div>'
        body = (
            crumbs_html(trail) +
            chips(cat_list, cat_slug, active) +
            f'\n<h1 class="page">{esc(active or title)}</h1>\n' +   # SEO 11.10: H1 категорії = лише назва («Каталог •» — у title/крихтах); повний каталог — «Каталог іграшок»
            (f'<p class="pagenote">Сторінка {k} з {pages}</p>\n' if pages > 1 else "") +
            (_category_intro_html(fname) if (active and k == 1) else "") +
            controls +
            static_grid +
            (_relcats_html(fname, cat_slug, active) if (active and k == 1) else "")
        )
        _write(cur, page(ptitle, body, extra_head=head, description=desc, canonical=cur, og_image=OG_IMAGE, title_full=full_t))
        written.add(cur)
    return written

SOCIAL_URLS = ["https://www.instagram.com/plutustoys.ua/", "https://www.facebook.com/profile.php?id=61590292022031"]


def _footer_utm(url):
    """Посилання на соцмережі з футера несуть utm (SMM 12.10: бачити зворотний трафік сайт→соцмережі). У JSON-LD sameAs лишаються чисті SOCIAL_URLS."""
    return url + ("&" if "?" in url else "?") + "utm_source=site&utm_medium=footer&utm_campaign=social_link"


def home_jsonld() -> str:
    """JSON-LD головної: Organization + WebSite (SEO-замовлення 2026-10-06, п.4). Контакти/соцмережі — ті самі, що в підвалі й
    на сторінці «Контакти». WebSite БЕЗ SearchAction: пошук сайту — клієнтський оверлей (index.json), сторінки результатів
    з URL-шаблоном немає, а Google не приймає SearchAction без неї."""
    org = {
        "@context": "https://schema.org", "@type": "Organization", "name": "PlutusToys", "url": f"{SITE_URL}/",
        "logo": f"{SITE_URL}/assets/plutus_mascot.png", "sameAs": SOCIAL_URLS,
        "contactPoint": [{"@type": "ContactPoint", "telephone": "+380730150815", "contactType": "customer service",
                          "email": "plutustoys@gmail.com", "areaServed": "UA", "availableLanguage": ["uk"]}],
    }
    web = {"@context": "https://schema.org", "@type": "WebSite", "name": "PlutusToys", "url": f"{SITE_URL}/",
           "inLanguage": "uk"}
    out = ""
    for d in (org, web):
        # безпечно в <script>: кожен '<' екрануємо (chr(92) + "u003c") — жоден HTML-вектор не вийде літерально
        out += '<script type="application/ld+json">' + json.dumps(d, ensure_ascii=False).replace("<", chr(92) + "u003c") + "</script>" + chr(10)
    return out


# ── Головна: «Добірка Плутуса» (SMM 09.10.2026, рішення власника «герой крінж, зверху карусель») ──
# Анімований Плутус у герої (SMM 09.10/12.10): poster + кліпи ЛИШЕ з хвостом праворуч (tail_right у manifest креатива: tailtrip, ear_scratch), тривалість у мс
HERO_MASCOT_POSTER = "assets/mascot/plutus_hero_poster.webp"
# Десять рухів (SMM 12.10; власник схвалив і чотири з дефектами — manifest note owner_approved_with_defects). tail_left (grooming, sniff, shakeoff) дзеркалено ПРИ КОДУВАННІ (make_mascot_clips.py), CSS-дзеркала нема.
# Вага в ротації: 2 — «чисті» рухи, 1 — sneeze/tailchase/sleep/rolljump (відомі дефекти: sneeze не чхає, tailchase розмита пляма, sleep «знепритомнів», rolljump нерухомий) — рідше, щоб не домінували; stretch_yawn не ПЕРШИМ
# (широко відкритий рот; прибрати з ротації одним рядком — видалити запис). Ms = сума тривалостей кадрів файлу (+400 мс запасу додає JS). Вага в репо ≈2,5 МБ (Q=32 у make_mascot_clips.py), клипи вантажаться по одному перед показом.
# (name, ms, weight, first_ok)
HERO_MASCOT_CLIPS = (("tailtrip", 3154, 2, True), ("ear_scratch", 4000, 2, True), ("grooming", 4400, 2, True), ("sniff", 4400, 2, True), ("shakeoff", 1992, 2, True),
                     ("stretch_yawn", 4400, 2, False), ("sneeze", 3500, 1, True), ("tailchase", 5976, 1, True), ("sleep", 4980, 1, True), ("rolljump", 4316, 1, True))
HOME_MIN_CONTRIB = 100.0           # внесок на замовлення, ₴ (рішення Консультанта: не ведемо вітрину позицією без маржі) — існуючий поріг «Новинок»
HOME_PICK_PRICE_MIN, HOME_PICK_PRICE_MAX = 150, 700    # ₴: імпульсна ціна (SMM): рамка, не мірило
HOME_PICK_COUNT, HOME_PICK_MAX_PER_CAT = 10, 2
# Категорії, яких у каруселі не буде: картини за номерами й брелоки (SMM: перші 10 не вони), зброя/репліки (як у фідах Google/Meta — перша вітрина не місце для спірного).
HOME_PICK_EXCLUDED_CATS = frozenset({"картини за номерами", "брелоки", "автомати і пістолети", "набори зі зброєю", "арбалети і луки", "зброя"})


HOME_POPULAR_CATS = 30             # «Популярні категорії» на головній (SEO 11.10: 24–30 посилань)
HOME_PICK_CAT_POOL = 40           # з скількох найкращих за внеском категорій щодня обираємо (решта — лише якщо не вистачило до 10)
HOME_PICK_SEED = 20261011           # фіксоване зерно: перемішування добірки стабільне (не стрибає між збірками, поки не змінились дані)
_PICK_STOP = frozenset({"для", "та", "від", "дитячий", "дитяча", "дитяче", "дитячі", "дитячих", "іграшка", "іграшки", "мʼяка", "м'яка", "мя",
                        "набір", "набори", "гра", "великий", "великі", "маленький", "новий", "nbsp"})
_PICK_LATIN_STOP = frozenset({"kids", "baby", "toys", "toy", "game", "games", "with", "set", "super", "mini", "play", "girl", "girls", "boys", "pink", "blue", "big", "little"})   # загальні англійські слова — не бренд


def _pick_key(name):
    """Ключ «основи назви» для дедуплікації добірки (SMM 11.10: не більше 1 картки на лінійку/модель): текст у лапках (перші 2 слова) → перше латинське слово-бренд (≥4 літер)
    → перші 2 значущі слова. Колір/розмір у дужках відкидаємо; префікси «Мʼяка іграшка»/«Набір» — у стоп-списку."""
    n = html.unescape(name or "").lower().replace("ʼ", "'").replace("’", "'")
    n = re.sub(r"\([^)]*\)", " ", n)
    q = re.search(r'["«“„]([^"»”“]{3,}?)["»”“]', n)
    if q:
        words = re.findall(r"[a-zа-яіїєґ0-9']+", q.group(1))
        if words:
            return " ".join(words[:2])
    lat = [w for w in re.findall(r"[a-z][a-z0-9]{3,}", n) if w not in _PICK_LATIN_STOP]
    if lat:
        return lat[0]
    words = [w for w in re.findall(r"[а-яіїєґё0-9']{3,}", n) if w not in _PICK_STOP]
    return " ".join(words[:2]) or n.strip()


def _today():
    """Сьогодні за Києвом (UTC+3); env SITE_PICK_DATE=YYYY-MM-DD — для тестів і відтворення (той самий, що для ротації добірки)."""
    raw = os.environ.get("SITE_PICK_DATE", "").strip()
    if re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", raw):
        y, m, d = (int(x) for x in raw.split("-"))
        return datetime(y, m, d).date()
    return (datetime.now(timezone.utc) + timedelta(hours=3)).date()


def _pick_day():
    """Номер дня для щоденної ротації добірки (власник 13.10: «в каруселі крутяться одні й ті самі товари»). Київ (UTC+3), env SITE_PICK_DATE=YYYY-MM-DD — для тестів/відтворення.
    Добірка стабільна ПРОТЯГОМ доби (збірка кожні 2 год дає ту саму), а наступного дня — інша."""
    raw = os.environ.get("SITE_PICK_DATE", "").strip()
    if re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", raw):
        y, m, d = (int(x) for x in raw.split("-"))
        return datetime(y, m, d).toordinal()
    return (datetime.now(timezone.utc) + timedelta(hours=3)).date().toordinal()


def home_pick(prods, pmin=None, pmax=None, min_contrib=None, extra_excluded_cats=frozenset(), name_re=None, bad_words=(), bad_cat_re=None, bad_name_re=None):
    """Відбір для каруселі: у наявності, фото, ціна 150–700 ₴, внесок ≥ HOME_MIN_CONTRIB, не «Уцінка»/«пошкоджено…» (ні за категорією, ні за назвою), не з
    HOME_PICK_EXCLUDED_CATS, не репліка зброї (за назвою — weapon_policy). Різноманітність (SMM 11.10): не більше 1 картки на «основу назви» (_pick_key), ≤2 з категорії,
    round-robin по категоріях (порядок — за кращим внеском категорії), з кожної категорії — ПОЗИЦІЯ З МЕДІАННИМ внеском (а не найвищим: найвищий внесок упирається в стелю діапазону —
    на реальних даних усі 10 коштували 695–700 ₴, медіанний вибір дає ≈470–650 ₴), далі перемішування з фіксованим зерном. «Знижок» і «Хітів продажів» нема."""
    # pmin/pmax/min_contrib — для наборів чипів над каруселлю (SMM 12.10); за замовчуванням — основна добірка.
    pmin = HOME_PICK_PRICE_MIN if pmin is None else pmin
    pmax = HOME_PICK_PRICE_MAX if pmax is None else pmax
    min_contrib = HOME_MIN_CONTRIB if min_contrib is None else min_contrib
    elig = []
    for p in prods:
        c = p.get("contribution")
        cat = (p.get("category") or "").strip().lower()
        name = p.get("name") or ""
        if c is None or c < min_contrib or not p.get("photo") or (p.get("stock") or 0) <= 0:
            continue
        if not (pmin <= p["price"] <= pmax):
            continue
        if cat in HOME_PICK_EXCLUDED_CATS or cat in extra_excluded_cats or _is_markdown_item(p) or any(m in name.lower() for m in cp._BAD_NAME_MARKERS):
            continue
        if is_weapon_replica(str(p["id"]), name):
            continue
        if name_re is not None and not name_re.search(name):
            continue   # тематичний набір (сезонні чипи): лише назви за ключовими словами
        if bad_words and any(w in name.lower() for w in bad_words):
            continue
        if bad_cat_re is not None and bad_cat_re.search(p.get("category") or ""):
            continue
        if bad_name_re is not None and bad_name_re.search(name):
            continue
        elig.append(p)
    by = {}
    for p in sorted(elig, key=lambda p: (-p["contribution"], str(p["id"]))):
        by.setdefault(p["category"], []).append(p)
    ranked = sorted(by, key=lambda c: (-by[c][0]["contribution"], str(c)))   # str(c): категорія None не ламає порівняння при однаковому внеску
    # Щоденна ротація: серед HOME_PICK_CAT_POOL найкращих за внеском категорій порядок щодня інший (детермінований від дня й параметрів набору), позиція в категорії — з середньої смуги (25–75% за внеском), не завжди медіана.
    rng = random.Random(HOME_PICK_SEED * 1000003 + _pick_day() * 7919 + int(pmin) * 31 + int(pmax))
    head = ranked[:HOME_PICK_CAT_POOL]
    rng.shuffle(head)
    order = head + ranked[HOME_PICK_CAT_POOL:]
    out, used = [], set()
    for _ in range(HOME_PICK_MAX_PER_CAT):
        for c in order:
            if len(out) >= HOME_PICK_COUNT:
                break
            lst = [p for p in by[c] if _pick_key(p["name"]) not in used]
            if not lst:
                continue
            lo, hi = len(lst) // 4, max(len(lst) // 4 + 1, (3 * len(lst)) // 4 + 1)
            p = lst[rng.randrange(lo, min(hi, len(lst)))]
            used.add(_pick_key(p["name"]))
            out.append(p)
            by[c] = [x for x in by[c] if x is not p]
    rng.shuffle(out)
    return out


# Чипи над каруселлю головної (SMM 12.10): «До 300 ₴» (внесок ≥50), «300–700 ₴» (внесок ≥100, як основна добірка), «Набори» (відбір /nabory.html, перші 10 по колах груп). Набори — у <template> (інертні: фото не вантажаться,
# поки чип не натиснуто), у кожному ≤10 карток із бейджем залишку ≤3; перші картки НЕ eager. Ключі латиницею — у назвах подій home_chip_<key>.
HOME_CHIPS = (("u300", "До 300 ₴"), ("m700", "300–700 ₴"), ("nab", "Набори"))
# Сезонні чипи (SMM 13.10; правила й підписи — її): показуються лише в своє вікно дат (за Києвом, на момент збірки; збірка кожні 2 год), для кожного — ключові слова в назві, stock ≥ 3 (як у плані SMM:
# build бере лише товари в наявності, окремого порога залишку нема), ціна 100–700, внесок ≥ 30; мілітарі/зброя (NABORY_BAD_WORDS) відсіюється. Менше HOME_SEASONAL_MIN карток — чип не показуємо.
# (key, назва чипа, підпис під чипами, ключові слова (regex, регістр ігнорується), перший день, останній день) — дні у вигляді (місяць, день).
HOME_SEASONAL = (
    ("hel", "Хелловін", "Страшно весело — до Гелловіну", r"хелловін|гелловін|halloween|привид|гарбуз|скелет|вампір|відьм", (10, 1), (10, 31)),
    ("ny", "Миколай і Новий рік", "Подарунки на Миколая і під ялинку", r"новорічн|ялинк|сніговик|санта|різдв|дід мороз|сніг|санки|зимов", (11, 25), (12, 31)),
)
# Інлайн-SVG іконки тематичних чипів (SMM 13.10: без картинок і емодзі, лише векторні; aria-hidden, текст чипа — окремо)
CHIP_ICONS = {
    "hel": '<svg class="chip-ico ico-hel" viewBox="0 0 24 24" width="20" height="20" aria-hidden="true" focusable="false"><path d="M12 5.2c.2-1.2.9-2 2-2.4" fill="none" stroke="#7BC043" stroke-width="2" stroke-linecap="round"/><ellipse cx="12" cy="14" rx="9" ry="7.6" fill="#FF7A1A"/><path d="M8 11.4l2 1.8-2.2.2zM16 11.4l-2 1.8 2.2.2zM8.6 16.4q3.4 2.6 6.8 0l-.8 1.7-1.4-.9-1.2 1-1.2-1-1.4.9z" fill="#1B1030"/></svg>',
    "ny": '<svg class="chip-ico ico-ny" viewBox="0 0 24 24" width="20" height="20" aria-hidden="true" focusable="false"><g stroke="#fff" stroke-width="2" stroke-linecap="round"><path d="M12 2v20M3.3 7l17.4 10M3.3 17L20.7 7"/><path d="M9.5 3.6L12 6l2.5-2.4M9.5 20.4L12 18l2.5 2.4" fill="none" stroke-width="1.6"/></g></svg>',
    "nab": '<svg class="chip-ico ico-nab" viewBox="0 0 24 24" width="20" height="20" aria-hidden="true" focusable="false"><path d="M12 12c-4-5-9-3-8 0s6 2 8 0zm0 0c4-5 9-3 8 0s-6 2-8 0z" fill="#E04E39"/><path d="M12 12l-3 8M12 12l3 8" stroke="#E8B04A" stroke-width="2.2" stroke-linecap="round"/><circle cx="12" cy="12" r="2.2" fill="#E8B04A"/></svg>',
}
HOME_SEASONAL_MIN = 6
# «Популярне зараз» — чип за ПОПИТОМ Google (власник 13.10: «чому не маркетинг за попитом»): список id від SEO у site/demand_featured.json
# {"date": "YYYY-MM-DD", "source": "Search Console 28d", "ids": ["..."], "categories": ["..."]}. Нема файлу / застарів (> DEMAND_MAX_AGE_DAYS) / менше DEMAND_MIN карток у наявності — чипа нема (не вигадуємо).
DEMAND_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "demand_featured.json")
DEMAND_MAX_IDS = 40
DEMAND_MAX_AGE_DAYS = 21
DEMAND_MIN = 6


def _load_demand_featured(today=None):
    """({'ids': [...], 'categories': [...], 'date': date|None, 'source': str}) або None. Нема файлу — None без шуму; є, але зламаний/застарілий — None ГОЛОСНО (stderr)."""
    try:
        with open(DEMAND_FILE, encoding="utf-8-sig") as f:
            raw = f.read()
    except FileNotFoundError:
        return None
    except (OSError, UnicodeDecodeError) as e:
        print(f"[build] ⚠ demand_featured.json НЕ прочитано ({type(e).__name__}: {e}) — чипа «Популярне зараз» нема", file=sys.stderr)
        return None
    try:
        data = json.loads(raw)
    except (ValueError, RecursionError) as e:
        print(f"[build] ⚠ demand_featured.json — невалідний JSON ({e}) — чипа нема", file=sys.stderr)
        return None
    if not isinstance(data, dict) or not isinstance(data.get("ids"), list):
        print('[build] ⚠ demand_featured.json: очікується {"date":..., "ids":[...]} — чипа нема', file=sys.stderr)
        return None
    dt = None
    m = re.fullmatch(r"([0-9]{4})-([0-9]{2})-([0-9]{2})", str(data.get("date") or "").strip())
    if m:
        try:
            dt = datetime(int(m.group(1)), int(m.group(2)), int(m.group(3))).date()
        except ValueError:
            dt = None
    if dt is None:
        print("[build] ⚠ demand_featured.json: нема коректної date (YYYY-MM-DD) — чипа нема (без дати свіжість невідома)", file=sys.stderr)
        return None
    if ((today or _today()) - dt).days > DEMAND_MAX_AGE_DAYS:
        print(f"[build] ⚠ demand_featured.json застарів ({dt}, > {DEMAND_MAX_AGE_DAYS} днів) — чипа нема", file=sys.stderr)
        return None
    ids = []
    for x in data["ids"]:
        if isinstance(x, bool) or not isinstance(x, (str, int)):
            continue
        sx = str(x).strip()
        if sx and sx not in ids:
            ids.append(sx)
            if len(ids) >= DEMAND_MAX_IDS:
                break
    cats = [str(c).strip() for c in (data.get("categories") or []) if isinstance(c, str) and c.strip()][:DEMAND_MAX_IDS] if isinstance(data.get("categories"), list) else []
    return {"ids": ids, "categories": cats, "date": dt, "source": str(data.get("source") or "")[:80]}


def demand_pick(prods, demand=None):
    """До HOME_PICK_COUNT карток із списку попиту: у наявності, внесок відомий і ≥ 0, не «Уцінка»/зброя/мілітарі; щодня інший зріз серед топ-списку (стабільний у добі)."""
    d = demand if demand is not None else _load_demand_featured()
    if not d or not d["ids"]:
        return []
    by_id = {str(p["id"]): p for p in prods}
    ok = []
    for i in d["ids"]:
        p = by_id.get(i)
        if not p or (p.get("stock") or 0) <= 0 or not p.get("photo"):
            continue
        c = p.get("contribution")
        if c is None or c < 0:
            continue
        name = p.get("name") or ""
        if _is_markdown_item(p) or any(m in name.lower() for m in cp._BAD_NAME_MARKERS) or is_weapon_replica(str(p["id"]), name) or any(w in name.lower() for w in NABORY_BAD_WORDS):
            continue
        ok.append(p)
    if len(ok) < DEMAND_MIN:
        return []
    rng = random.Random(HOME_PICK_SEED * 7 + _pick_day() * 104729)
    head = ok[:DEMAND_MAX_IDS]
    if len(head) > HOME_PICK_COUNT:
        # перші 4 найвищого попиту — завжди; решта слотів — щоденний зріз із решти списку (різноманітність без втрати топу)
        top, rest = head[:4], head[4:]
        rng.shuffle(rest)
        head = top + rest[:HOME_PICK_COUNT - 4]
    return head[:HOME_PICK_COUNT]
# SMM 13.10: з сезонних чипів прибрати книжки й мангу (категорія книг/манга/комікс/художн/літератур або «манга»/«роман» у назві), ключові слова лишаються (привид, відьм — доречні на іграшках).
HOME_SEASONAL_BAD_CAT = re.compile(r"книг|манга|комікс|художн|літератур", re.I)
HOME_SEASONAL_BAD_NAME = re.compile(r"манга|роман", re.I)
HOME_SEASONAL_MIN_CONTRIB = 30.0


def seasonal_active(first, last, today=None):
    t = today or _today()
    return (first[0], first[1]) <= (t.month, t.day) <= (last[0], last[1])
HOME_CHIP_U300_MAX, HOME_CHIP_U300_MIN = 299.99, 50
HOME_CHIP_U300_CONTRIB = 50.0
# Не іграшки (аудит 13.10: перша картка «До 300 ₴» на живих даних — «Тренировочні трусики…» з категорії «Клейонки, килимки і підгузники») — з чипів прибираємо.
HOME_CHIP_EXCLUDED_CATS = frozenset({"клейонки, килимки і підгузники", "гігієнічні вироби", "догляд за тілом", "догляд за тілом та волоссям", "догляд за волоссям", "косметичні засоби",
                                     "засоби для прання", "засоби для миття посуду", "прання та миття посуду", "догляд за будинком", "new: побутова хімія", "батарейки"})   # точні назви з index.json (аудит #714); не іграшки


def home_chip_sets(prods):
    """{key: [товари]} для чипів; порожні набори не повертаємо (чип не показуємо)."""
    sets = {
        "u300": home_pick(prods, pmin=HOME_CHIP_U300_MIN, pmax=HOME_CHIP_U300_MAX, min_contrib=HOME_CHIP_U300_CONTRIB, extra_excluded_cats=HOME_CHIP_EXCLUDED_CATS),
        "m700": home_pick(prods, pmin=300, pmax=700, extra_excluded_cats=HOME_CHIP_EXCLUDED_CATS),
        "nab": [p for _g, p in nabory_pick(prods)[:HOME_PICK_COUNT]],
    }
    dp = demand_pick(prods)
    if dp:
        sets["pop"] = dp
    for key, _label, _sub, rx, first, last in HOME_SEASONAL:
        if seasonal_active(first, last):
            pk = home_pick(prods, pmin=100, pmax=700, min_contrib=HOME_SEASONAL_MIN_CONTRIB, extra_excluded_cats=HOME_CHIP_EXCLUDED_CATS,
                           name_re=re.compile(rx, re.I), bad_words=NABORY_BAD_WORDS,
                           bad_cat_re=HOME_SEASONAL_BAD_CAT, bad_name_re=HOME_SEASONAL_BAD_NAME)
            if len(pk) >= HOME_SEASONAL_MIN:
                sets[key] = pk
    return {k: v for k, v in sets.items() if v}


def home_chip_defs(chip_sets):
    """[(key, label, sub)] у порядку показу: активні сезонні (з наборами) → «До 300 ₴», «300–700 ₴», «Набори»."""
    seasonal = [(k, lb, sb) for k, lb, sb, _rx, f, l in HOME_SEASONAL if k in chip_sets]
    pop = [("pop", "Популярне зараз", "Те, що зараз шукають найчастіше")] if "pop" in chip_sets else []
    return seasonal + pop + [(k, t, "") for k, t in HOME_CHIPS if k in chip_sets]


# Перший слайд каруселі — плитка-банер (SMM 13.10): не товар, а тема дня (щодня інша серед активних): текст + емодзі + помаранчевий CTA, без картинки й без знижки.
# (key, емодзі, заголовок, підпис, куди: «chip:<ключ чипа>» — розкриває чип на місці, або URL; умова показу)
HOME_BANNERS = (
    # (key, заголовок, підзаголовок, CTA, куди)
    ("hel", "Страшно весело", "Іграшки до Гелловіну", "Обрати страшилки →", "chip:hel"),
    ("ny", "Миколай і Новий рік", "Миколай — 19.12", "Подарунки під ялинку →", "chip:ny"),
    ("nab", "Набори в подарунок", "Усе потрібне в комплекті", "Відкрити набори →", "nabory.html"),
)
BANNER_ART = ("hel", "ny", "nab")   # для цих тем у site/assets/banners/<key>.webp може лежати ілюстрація Плутуса (300×300, прозорий фон, ≤40 КБ): є файл — показуємо, нема — лише векторний декор


def home_banner_html(chip_sets, has_nabory):
    """HTML плитки-банера на сьогодні або '' (немає активних тем). Тема дня = індекс за номером дня серед активних (hel — лише коли є сезонний чип; nab — лише коли є сторінка /nabory.html)."""
    active = [b for b in HOME_BANNERS if (b[4] == "nabory.html" and has_nabory) or (b[4].startswith("chip:") and b[4][5:] in chip_sets)]
    if not active:
        return ""
    key, title, sub, cta, dest = active[_pick_day() % len(active)]
    chip = dest[5:] if dest.startswith("chip:") else ""
    href = "#home-chips" if chip else dest
    art_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "banners", f"{key}.webp")
    art = (f'<img class="b-art" src="{asset_url(f"assets/banners/{key}.webp")}" alt="" width="120" height="120" loading="lazy" decoding="async">' if os.path.exists(art_file) else "")
    deco = {"hel": '<svg class="b-deco b-web" viewBox="0 0 80 80" aria-hidden="true" focusable="false"><g stroke="#FFB066" fill="none" stroke-width="1"><path d="M80 0L0 80M80 0L28 80M80 0L80 80M80 0L0 28M80 0L0 0"/><path d="M80 12Q62 18 56 38M80 26Q56 30 42 52M80 42Q60 44 48 64" /></g></svg>',
            "ny": '<svg class="b-deco b-snow" viewBox="0 0 120 40" aria-hidden="true" focusable="false"><g fill="#fff"><circle cx="10" cy="10" r="2.2"/><circle cx="34" cy="26" r="1.6"/><circle cx="58" cy="8" r="2.6"/><circle cx="84" cy="22" r="1.8"/><circle cx="108" cy="9" r="2.2"/></g></svg>',
            "nab": '<svg class="b-deco b-ribbon" viewBox="0 0 80 80" aria-hidden="true" focusable="false"><path d="M0 28L52 80H72L0 8z" fill="#E8B04A" opacity=".45"/><path d="M28 0L80 52V28L52 0z" fill="#E04E39" opacity=".22"/></svg>'}[key]
    return (f'<a class="card slide banner theme-{key}" href="{esc(href)}" data-banner="{key}"' + (f' data-chip="{chip}"' if chip else "") + '>'
            f'{deco}<div class="b-text"><div class="nm">{esc(title)}</div><div class="b-sub">{esc(sub)}</div><span class="b-cta">{esc(cta)}</span></div>{art}</a>')


def write_home(prods, cats, cat_list, cat_slug):
    picks = home_pick(prods)
    pick_ids = {p["id"] for p in picks}
    # Секція нижче каруселі: по 1 товару з топ-категорій з внеском ≥ HOME_MIN_CONTRIB (як і було), без повторів із каруселлю. Назва НЕ «Новинки»:
    # ми не знаємо, що нове (SMM 09.10) — «Більше іграшок».
    novelties = []
    for c in cat_list[:12]:
        lst = [p for p in cats[c] if p["id"] not in pick_ids
               and p.get("contribution") is not None and p["contribution"] >= HOME_MIN_CONTRIB]
        if lst:
            novelties.append(lst[len(lst) // 2])
        if len(novelties) >= 8:
            break
    cat_tiles = "".join(
        f'<a class="cattile" href="category-{cat_slug[c]}.html">'
        f'<div class="emo">{cat_emoji(c)}</div><div class="t">{esc(c)}</div></a>'
        for c in cat_list[:10]
    )
    # «Популярні категорії» (SEO 11.10: на головній було 10 посилань на категорії з 286 → розподіл внутрішньої ваги й коротший шлях до карток):
    # топ за кількістю товарів, без «Уцінки»; cat_list уже відсортований за кількістю (Уцінка в кінці).
    pop = [c for c in cat_list if "уцінк" not in c.lower() and "уценк" not in c.lower()][:HOME_POPULAR_CATS]
    popular = ""
    if pop:
        popular = ('<div class="sec-title"><h2>Популярні категорії</h2><a href="categories.html">Усі категорії →</a></div>'
                   '<nav class="catlinks" aria-label="Популярні категорії">'
                   + "".join(f'<span class="cl"><a href="category-{cat_slug[c]}.html">{esc(c)}</a><span>{len(cats[c])}</span></span>' for c in pop)
                   + '</nav>')
    carousel = ""
    if picks:
        # перші 3 слайди без lazy (мобільний: видно 1,5–2; SMM 11.10), перший — fetchpriority=high (кандидат на LCP); слайди 4–5 на широкому екрані JS переводить в eager (initHomeCarousel)
        chip_sets = home_chip_sets(prods)
        slides = home_banner_html(chip_sets, bool(nabory_pick(prods))) + "".join(tile(p, cls="slide", eager=(i < 3), prio=(i == 0), badge=True) for i, p in enumerate(picks))
        chips_html = ""
        if chip_sets:
            chips_html = (
              '<noscript><style>.home-chips{display:none}</style></noscript>'
              '<div class="chips home-chips" id="home-chips" role="group" aria-label="Підбір іграшок">'
              '<button type="button" class="chip active" data-set="all" aria-pressed="true">Усі</button>'
              + "".join(f'<button type="button" class="chip{" chip-theme" if k in CHIP_ICONS else ""}" data-set="{k}" data-sub="{esc(sb)}" aria-pressed="false">{CHIP_ICONS.get(k, "")}<span>{esc(t)}</span></button>' for k, t, sb in home_chip_defs(chip_sets))
              + '</div><p class="chip-sub" id="home-chip-sub" aria-live="polite"></p>'
              + "".join(f'<template id="car-set-{k}">' + "".join(tile(p, cls="slide", badge=True) for p in chip_sets[k]) + '</template>' for k in chip_sets))
        carousel = (
          '<div class="sec-title"><h2>Добірка Плутуса</h2><a href="catalog.html">Дивитись усі →</a></div>'
          + chips_html +
          '<div class="car" id="home-car" role="region" aria-label="Добірка Плутуса" data-list="Добірка Плутуса">'
            '<button class="arrow l" type="button" aria-label="Попередні товари">‹</button>'
            f'<div class="track">{slides}</div>'
            '<button class="arrow r" type="button" aria-label="Наступні товари">›</button>'
          '</div>'
        )
    body = (
      # компактний герой (SMM 09.10: смуга ≤180 px; Плутус ліворуч, заголовок + кнопка + чип доставки; без великого абзацу)
      # poster завжди під низом, кліп кросфейдом поверх (див. initMascot): обидва <img> із width/height — CLS 0
      f'<div class="hero2"><span class="mascot-wrap"><img class="mascot" id="hero-mascot" src="{asset_url(HERO_MASCOT_POSTER)}" alt="Плутус — маскот PlutusToys" width="130" height="100" decoding="async" '
      f'data-clips="{"|".join(esc(asset_url(f"assets/mascot/plutus_{n}.webp")) for n, _ms, _w, _f in HERO_MASCOT_CLIPS)}" data-ms="{",".join(str(c[1]) for c in HERO_MASCOT_CLIPS)}" data-w="{",".join(str(c[2]) for c in HERO_MASCOT_CLIPS)}" data-nofirst="{",".join(str(i) for i, c in enumerate(HERO_MASCOT_CLIPS) if not c[3])}">'
      '<img class="mascot mascot-clip" id="hero-mascot-clip" alt="" aria-hidden="true" width="130" height="100" decoding="async"></span>'
      '<div class="txt"><h1>Іграшки, що радують</h1><p>Обираємо те, що радує дитину, а маму робить спокійною.</p></div>'
      '<div class="act"><a class="btn" href="nabory.html">Набори в подарунок</a><a class="hero-all" href="catalog.html">Усі іграшки</a>'
      '<div class="chip">🚚 Нова Пошта · ' + PAY_SHORT + '</div></div></div>'
      + carousel +
      '<div class="sec-title"><h2>Категорії</h2><a href="catalog.html">Усі →</a></div>'
      f'<div class="catrow">{cat_tiles}</div>'
      + ('<div class="sec-title"><h2>Більше іграшок</h2><a href="catalog.html">Дивитись усі →</a></div>'
         + grid(novelties) if novelties else "")
      + popular
    )
    _write("index.html", page(
        "Іграшки з доставкою Новою Поштою", body, extra_head=home_jsonld(),
        description="Дитячі іграшки, від яких світяться очі 🦊 Конструктори, ляльки, машинки, розвиваючі та творчі набори — з доставкою Новою Поштою по всій Україні, повернення 14 днів.",
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


def write_product(p, related=None, cat_slug=None, related_title="Додайте до замовлення", similar=None, similar_title="Схожі товари"):
    raw = p["desc"]
    parts = [x.strip() for x in re.split(r"<br\s*/?>", raw) if x.strip()]
    lead = _clean_desc(parts[0]) if parts else ""
    if lead in ("-", "—", "–") or len(re.sub(r"<[^>]+>", "", lead)) < 3:
        lead = ""  # порожній/сміттєвий опис не показуємо
    specs = [_clean_desc(x) for x in parts[1:] if x.startswith("<b>") and "</b>" in x and ":" in x
             and not x.rstrip().endswith("</b>")][:6]
    desc_html = f"<p>{lead}</p>" if lead else ""
    specs_html = ("<ul>" + "".join(f"<li>{s}</li>" for s in specs) + "</ul>") if specs else ""
    # Текст картки від SEO (site/product_texts.json): під описом Toysi, ПЕРЕД його характеристиками (якщо вони є — вони йдуть окремим блоком нижче), лише після блоку ціни/оплати/доставки,
    # тож перший екран мобільного (фото, ціна, бейджі, закріплена панель «У кошик») не зміщується. Нема тексту — розмітка рівно як була.
    prod_text_html = _product_text_html(p["id"])
    if prod_text_html and specs_html:
        desc_block = (f'<div class="desc"><h2>Опис</h2>{desc_html}</div>' + prod_text_html + f'<div class="desc specs">{specs_html}</div>')
    else:
        desc_block = f'<div class="desc"><h2>Опис</h2>{desc_html}{specs_html}</div>' + prod_text_html
    # Головне фото = LCP картки: fetchpriority=high каже браузеру тягнути його першим (решта фото сторінки — loading=lazy).
    # width/height не ставимо: розмір оригіналу toysi.ua невідомий, а місце під кадр вже резервує `.photo img{aspect-ratio:1/1}` у styles.css.
    photo = (f'<img src="{esc(p["photo"])}" alt="{esc(p["name"])}" fetchpriority="high" decoding="async">'
             if p["photo"] else '<div class="ph">Фото готуємо</div>')
    avail = "У наявності" if p["stock"] > 0 else "Немає в наявності"
    oos = "" if p["stock"] > 0 else "oos"
    in_stock = p["stock"] > 0
    # Без залишку: чесний текст + шлях далі (категорія, якщо вона є в каталозі; інакше повний каталог). Кнопка «У кошик» вимкнена нижче.
    _oos_cat_href = f"category-{cat_slug[p['category']]}.html" if (cat_slug and p.get("category") in cat_slug) else "catalog.html"
    _phone_line = '<p class="ask">Є питання? <a href="tel:+380730150815">📞 +380 (73) 015-08-15</a></p>'
    # Без залишку: НЕ обіцяємо оплату/доставку/повернення на сторінці з вимкненою кнопкою (аудит #675, Д2) — лише телефон.
    buy_info = (
        '<div class="trust-badges">'
          '<span class="tb">✓ Оплата при отриманні</span>'
          '<a class="tb" href="returns.html">✓ Повернення 14 днів</a>'
          '<span class="tb">✓ Доставка Новою Поштою</span>'
          '<span class="tb">✓ Фіскальний чек</span>'
        '</div>' + _phone_line +
        '<div class="delivery"><span class="fox"><img class="mascot" src="assets/plutus_mascot_s.png" alt="Плутус" width="31" height="28" decoding="async"></span>'
        '<div><b>Доставка Новою Поштою</b> — ' + DELIVERY_COST + ' за тарифом перевізника; при накладеному платежі додається комісія НП: ' + DELIVERY_FEE_FORMULA_SHORT + ' (для замовлень понад 500 ₴ тариф трохи зростає). Доставка сплачується при отриманні посилки. Замовлення до 12:00 йдуть того ж дня, '
        'далі 1–3 робочі дні.</div></div>'
    ) if in_stock else _phone_line
    oos_note = ("" if in_stock else
                f'<div class="oos-note"><b>Зараз цього товару немає в наявності.</b> '
                f'<a href="{_oos_cat_href}">Подивитися схожі{(" в категорії «" + esc(p["category"]) + "»") if _oos_cat_href != "catalog.html" else " в каталозі"} →</a></div>')
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
      + oos_note + buy_info +
      desc_block +
      '</div></div>'
      + (f'<section class="related"><h2>{esc(related_title)}</h2>{grid(related)}</section>' if related else "")
      + (f'<section class="related"><h2>{esc(similar_title)}</h2>{grid(similar)}</section>' if similar else "")
      + '<div class="buybar"><div class="buybar-inner">'
      f'<div class="p">{p["price"]} ₴</div>'
      f'{buy_btn}'
      '</div></div>'
    )
    # SEO: опис для сніпета (без HTML, ОБРІЗАНИЙ ПО СЛОВУ) + JSON-LD Product для Google Rich Results
    plain = re.sub(r"<[^>]+>", "", lead).strip()
    meta_desc = product_description(p["name"], p["price"], plain)
    if not in_stock:
        meta_desc = _cut(f"Немає в наявності: {p['name']}. Подивіться схожі іграшки в каталозі PlutusToys.", DESC_MAX)
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
            **offer_extras(p),
        },
    }
    if brand:
        ld["brand"] = {"@type": "Brand", "name": brand}
    # безпечно в <script>: екрануємо КОЖЕН '<' у < (валідний JSON) — жоден HTML-вектор
    # (</script>, <!--, <script) не може вийти літерально, навіть із назви товару.
    # chr(92) = '\' — однозначно, без крихкого backslash-літерала.
    ld_json = json.dumps(ld, ensure_ascii=False).replace("<", chr(92) + "u003c")
    cat_href = f"category-{cat_slug[p['category']]}.html" if (cat_slug and p.get("category") in cat_slug) else None
    trail = [("Головна", "index.html")] + ([(p["category"], cat_href)] if cat_href else []) + [(p["name"], None)]
    extra = f'<script type="application/ld+json">{ld_json}</script>' + chr(10) + breadcrumb_ld(trail)
    body = crumbs_html(trail) + body
    _write(f"product-{p['id']}.html", page(
        p["name"], body, extra_head=extra, title_full=product_title(p["name"], in_stock), description=meta_desc,
        canonical=f'product-{p["id"]}.html', og_image=p["photo"], og_type="product"))

CART_ADDONS_MAX = 12     # пул «Додайте до замовлення» у кошику (на сторінці показуємо 3, яких ще нема в кошику)


def _cart_addons(prods):
    """Пул add-on для кошика (SMM 12.10): той самий критерій, що в картці (_addon_ok: в наявності, ≥ADDON_MIN_PRICE, без уцінки/«розпродажу»), по одному з категорії, детермінізм:
    спершу дорожчі з дешевого пулу (цінніший add-on), далі за id. Повертає [{id,name,price,photo}] — вбудовується JSON-ом у cart.html, вибір 3 «яких нема в кошику» робить app.js."""
    pool = sorted((p for p in prods if _addon_ok(p)), key=lambda x: (x["price"], x["id"]))[:80]
    out, seen = [], set()
    for p in sorted(pool, key=lambda x: (-x["price"], x["id"])):
        if p["category"] in seen or not p["photo"]:
            continue
        seen.add(p["category"])
        out.append({"id": p["id"], "name": p["name"], "price": p["price"], "photo": p["photo"]})
        if len(out) >= CART_ADDONS_MAX:
            break
    return out


def write_cart(addons=None):
    # двоколонково на десктопі (SMM 12.10: права половина була порожня): ліворуч кошик + форма, праворуч sticky-колонка (А «Ваше замовлення», Б довіра, В «Додайте до замовлення», Г Плутус);
    # на мобільному — одна колонка: підсумок над формою (як було), «Додайте до замовлення» над кнопкою «Оформити», довіра й Плутус нижче форми.
    addons_json = json.dumps(addons or [], ensure_ascii=False).replace("</", "<\\/")
    body = (
      '<h1 class="page">Кошик</h1>'
      '<div class="cart-grid"><div class="cart-main"><div id="cart-body"></div>'
      '<div class="summary" id="cart-summary">'
        '<div class="row"><span>Товари</span><span id="sum-goods">0 ₴</span></div>'
        '<div class="row"><span id="sum-delivery-label" data-prepaid="' + DELIVERY_LABEL_PREPAID + '">Доставка Новою Поштою (з комісією за накладений платіж)</span><span id="sum-delivery">' + DELIVERY_COST + '</span></div>'
        '<div class="row total"><span>Разом за товари</span><span id="sum-total">0 ₴</span></div>'
        '<div class="free-ship" id="free-ship-block"><div class="free-ship-track"><div class="free-ship-fill" id="free-ship-bar"></div></div>'
          '<div class="note" id="free-ship-note"></div></div>'
        '<div class="note" id="delivery-note-sum" data-prepaid="' + esc(DELIVERY_NOTE_PREPAID) + '">' + DELIVERY_NOTE + '</div>'
      '</div>'
      # checkout
      '<h1 class="page" id="checkout-title" style="margin-left:0">Оформлення</h1>'
      '<form id="checkout-form">'
        '<div class="field"><label>Ім’я та прізвище</label>'
          '<input id="f-name" name="name" required autocomplete="name" placeholder="Іван Петренко">'
          '<div class="note">Спершу ім’я, потім прізвище — без по батькові.</div></div>'
        '<div class="field"><label>Телефон</label>'
          '<input id="f-phone" name="phone" type="tel" inputmode="tel" required autocomplete="tel" placeholder="+380 50 123 45 67"></div>'
        '<div class="field"><label>Email <span style="opacity:.6">(необовʼязково, для чека й статусу)</span></label>'
          '<input id="f-email" name="email" type="email" inputmode="email" autocomplete="email" placeholder="you@example.com"></div>'
        '<div class="field ac-wrap"><label>Місто</label>'
          '<input id="f-city" name="city" required autocomplete="off" placeholder="Почніть вводити місто…">'
          # Довідник НП може не відповісти (реальний інцидент на телефоні). Ручний ввід приймається наскрізь: поле відділення не блокується
          # (assets/app.js, initNpAutocomplete), сервер вимагає лише непорожні тексти міста й відділення (site_order_api.py:171-175).
          '<div class="note">Оберіть своє місто зі списку підказок (так посилка не потрапить в однойменне). Довідник не працює або міста в ньому нема? Впишіть назву самі — а нижче номер відділення.</div>'
          '<div class="ac" id="ac-city"></div></div>'
        '<div class="field ac-wrap"><label>Відділення Нової Пошти</label>'
          '<input id="f-warehouse" name="warehouse" required autocomplete="off" placeholder="Спершу оберіть місто" disabled>'
          '<div class="ac" id="ac-warehouse"></div></div>'
        # Спосіб оплати — накладений (оплата при отриманні) за замовчуванням: для незнайомого магазину
        # це головний аргумент довіри (рев'ю), і LiqPay поки sandbox. Вибір явний, у payload іде payment.
        '<div class="note cod-limit" id="cod-limit-note" style="display:none">Накладений платіж доступний для замовлень до 3 000 ₴; для більших сум — лише за передоплатою (зв’яжіться з нами).</div>'
        # Коментар покупця (запит власника 13.10): до 300 символів, необов'язково; йде в замовлення (order["customer_comment"]) і в коментар для Toysi (Код: orders_db/order_router). Не вмикати без її PR.
        '<div class="field"><label for="f-comment">Коментар до замовлення <span style="opacity:.6">(необовʼязково)</span></label>'
          '<textarea id="f-comment" name="comment" rows="3" maxlength="300" placeholder="Наприклад: це подарунок, зателефонуйте перед відправкою"></textarea>'
          '<div class="note" id="f-comment-count" style="margin:4px 0 0">до 300 символів</div></div>'
        '<div class="field"><label>Спосіб оплати</label>'
          '<label class="pay"><input type="radio" name="payment" value="cod" checked> '
            'Оплата при отриманні (накладений платіж на Новій Пошті)</label>'
          + ('<label class="pay"><input type="radio" name="payment" value="prepaid"> '
            'Оплата карткою онлайн ' + LIQPAY_LOGO_HTML.format(src=asset_url('assets/liqpay-logo.svg'), w=70, h=15) + '</label>' if LIQPAY_LIVE else
            # Прихований вхід для ТЕСТОВОЇ оплати (запит Коду 12.10): до карти LiqPay live покупці її не бачать; радіо розкривається лише з ?liqpay_test=1 (пам'ятається в sessionStorage, див. app.js).
            # Бекенд у sandbox не передає такі замовлення в Toysi й не фіскалізує, тож випадковий клік шкоди не завдасть; тексти для всіх покупців не змінюємо.
            # Радіо НЕ рендериться в HTML (аудит 13.10: hidden програвав `.field label.pay{display:flex}` — тестове радіо було видно всім): порожній слот, радіо створює JS лише з ?liqpay_test=1.
            '<div id="pay-test-slot"></div>') +
        '</div>'
        '<div class="addon-box addon-m" data-addon></div>'
        '<button class="btn" type="submit" id="checkout-submit">Оформити замовлення</button>'
        '<p class="note consent">Оформлюючи замовлення, ви погоджуєтесь з <a href="offer.html">публічною офертою</a> '
        'та <a href="privacy.html">політикою конфіденційності</a>.</p>'
        '<div class="note" id="checkout-msg" role="alert" aria-live="polite"></div>'
      '</form></div>'
      '<aside class="cart-aside" id="cart-aside" aria-label="Інформація про замовлення">'
        '<div class="aside-card" id="aside-order"><h2>Ваше замовлення</h2><div id="aside-items"></div>'
          '<div class="row"><span>Товари</span><span id="aside-goods">0 ₴</span></div>'
          '<div class="row"><span id="aside-delivery-label" data-prepaid="' + DELIVERY_LABEL_PREPAID + '">Доставка Новою Поштою (з комісією за накладений платіж)</span><span id="aside-delivery">' + DELIVERY_COST + '</span></div>'
          '<div class="row total"><span>Разом за товари</span><span id="aside-total">0 ₴</span></div>'
          '<div class="note" id="delivery-note-aside" data-prepaid="' + esc(DELIVERY_NOTE_PREPAID) + '">' + DELIVERY_NOTE + '</div></div>'
        '<div class="aside-card trust-card"><ul class="trust-list">'
          '<li>🧾 Фіскальний чек на кожне замовлення</li>'
          '<li><a href="returns.html">↩ Повернення протягом 14 днів</a></li>'
          '<li class="trust-pay">💳 Оплата при отриманні — розраховуєтесь на відділенні</li>'
          '<li id="ship-today">🚚 Замовлення до 12:00 зазвичай відправляємо того ж дня</li>'
        '</ul></div>'
        '<div class="aside-card addon-box addon-d" data-addon></div>'
        '<div class="aside-card pack-card"><img class="mascot" src="assets/plutus_mascot_s.png" alt="Плутус" width="80" height="73" decoding="async">'
          '<span>Плутус пакує вашу посилку 🎁</span></div>'
      '</aside></div>'
      '<script type="application/json" id="cart-addons">' + addons_json + '</script>'
    )
    _write("cart.html", page("Кошик і оформлення", body,
        description="Ваш кошик і оформлення замовлення в PlutusToys.", noindex=True))

def write_thanks():
    body = (
      '<div class="done"><div class="fox"><img class="mascot" src="assets/plutus_mascot_s.png" alt="Плутус" width="105" height="96" decoding="async"></div>'
      '<h2>Дякуємо за замовлення!</h2>'
      '<p>Ми отримали ваше замовлення <span class="oid" id="thanks-oid"></span> і готуємо його до відправки.</p>'
      '<p>Про статус повідомимо за номером замовлення. Доставка — Новою Поштою.</p>'
      '<p style="margin-top:20px"><a class="btn ghost" href="/" style="display:inline-block;max-width:260px">На головну</a></p>'
      '</div>'
    )
    _write("thanks.html", page("Дякуємо за замовлення", body, noindex=True, defer_trackers=False))   # purchase — без відкладання (рішення Консультанта 12.10)

# ── /ig.html: посадкова сторінка для bio-посилання Instagram (SMM 12.10.2026: у підписах IG посилання не клікабельні, єдине клікабельне місце — шапка профілю) ──
IG_FEATURED_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ig_featured.json")
IG_MAX_ITEMS = 6      # скільки карток показуємо (після відсіву товарів не в наявності)
IG_MAX_IDS = 50       # скільки id читаємо з файлу (запас: закінчені товари не мають «з’їдати» слоти)
IG_LIST_NAME = "ig_landing"


def _load_ig_featured():
    """site/ig_featured.json: {"date": "2026-10-12", "ids": ["196937", ...]} — товари з ОСТАННЬОГО рілса/допису; заповнює SMM. Повертає список id (рядки, без дублів, до IG_MAX_ITEMS).
    Нема файлу — порожньо без шуму; файл є, але не прочитався/не той формат — ГОЛОСНО (stderr), сторінка збирається з повідомленням «скоро»."""
    try:
        with open(IG_FEATURED_FILE, encoding="utf-8-sig") as f:
            raw = f.read()
    except FileNotFoundError:
        return []
    except (OSError, UnicodeDecodeError) as e:
        print(f"[build] ⚠ ig_featured.json НЕ прочитано ({type(e).__name__}: {e}) — /ig.html без товарів", file=sys.stderr)
        return []
    try:
        data = json.loads(raw)
    except (ValueError, RecursionError) as e:
        print(f"[build] ⚠ ig_featured.json — невалідний JSON ({e}) — /ig.html без товарів", file=sys.stderr)
        return []
    ids = data.get("ids") if isinstance(data, dict) else None
    if not isinstance(ids, list):
        print("[build] ⚠ ig_featured.json: очікується {\"date\": ..., \"ids\": [...]} — /ig.html без товарів", file=sys.stderr)
        return []
    out = []
    for x in ids:
        if isinstance(x, bool) or not isinstance(x, (str, int)):
            print(f"[build] ⚠ ig_featured.json: id {x!r} не рядок/число — пропущено", file=sys.stderr)
            continue
        sx = str(x).strip()
        if sx and sx not in out:
            out.append(sx)
            if len(out) >= IG_MAX_IDS:
                break         # O(n²) дедуп на гігантському файлі гальмував би збірку (аудит #686): читаємо рівно стільки, скільки потрібно
    return out


def write_ig(prods):
    """Сторінка /ig.html: сітка з товарів ig_featured.json, ЛИШЕ в наявності (нема — не показуємо, у лог іде попередження). noindex (кампанійна сторінка), canonical без параметрів
    (utm не плодять дублів). Не в sitemap (noindex). Трекінг: view_item_list/select_item з item_list_name «ig_landing» (assets/app.js, initListTracking)."""
    by_id = {p["id"]: p for p in prods}
    want = _load_ig_featured()
    shown = [by_id[i] for i in want if i in by_id and by_id[i]["stock"] > 0][:IG_MAX_ITEMS]
    missing = [i for i in want if i not in by_id or by_id[i]["stock"] <= 0]
    if missing:
        print(f"[build] ⚠ ig_featured.json: нема в наявності/каталозі — не показано: {missing}", file=sys.stderr)
    if want and not shown:
        print("[build] ⚠ ig_featured.json: жодного товару в наявності — /ig.html показує лише кнопку каталогу", file=sys.stderr)
    if shown:
        grid_html = (f'<div class="ig-list" id="ig-list" data-list="{IG_LIST_NAME}">' + grid(shown, eager_n=min(3, len(shown)), badge=True) + '</div>')
        lead = "Ці товари з нашого останнього відео."
    else:
        grid_html = ""
        lead = "Добірка з нашого останнього відео скоро з\u2019явиться. А поки — увесь каталог."
    body = (
        '<h1 class="page">З Instagram: що показали Плутус</h1>'
        f'<p class="ig-lead">{esc(lead)}</p>'
        f'{grid_html}'
        '<p class="ig-all"><a class="btn" href="catalog.html">Усі іграшки</a></p>'
    )
    _write("ig.html", page("З Instagram: що показали Плутус", body, canonical="ig.html", noindex=True,
                           description="Товари з нашого останнього відео в Instagram — PlutusToys."))

# ── /nabory.html: «Набори, які займуть дитину надовго» (SMM 12.10) ──
# Відбір (SMM): у назві «набір», в наявності, фото, ціна 150–700 ₴, внесок ≥ 50 ₴ (нижчий за поріг каруселі 100 — це окрема добірка), не «Уцінка»/«пошкоджено…», не репліка зброї, не зброя за категорією.
# Чипи — групи категорій; невідомі категорії лишаються лише у «Усі». Усередині групи: перемішування з фіксованим зерном (стабільне між збірками, поки не змінились дані), ≤1 картка на «основу назви» (_pick_key), ≤ NABORY_PER_GROUP.
NABORY_PRICE_MIN, NABORY_PRICE_MAX = 150, 700
NABORY_MIN_CONTRIB = 50.0
NABORY_PER_GROUP, NABORY_OTHER_MAX = 30, 24
NABORY_LIST_NAME = "nabory_page"
NABORY_VIEW_LIMIT = 24
NABORY_EXCLUDED_CATS = frozenset({"автомати і пістолети", "набори зі зброєю", "арбалети і луки", "зброя", "солдатики", "військова техніка", "спецтехніка"})
# Аудит #706: is_weapon_replica ловить не все — «набір військового з пістолетом», «Людина Павук … механічна зброя», БТР/військові самоскиди. Тримаємо добірку «подарунок дитині» без мілітарі/зброї за назвою.
NABORY_BAD_WORDS = ("пістолет", "зброя", "зброї", "військов", "солдат", "swat", "бтр", "автомат", "гранат", "армі")
NABORY_GROUPS = (
    ("tvorchist", "Творчість", frozenset({"інша творчість", "картини за номерами", "бісер і намистини", "розмальовки та розпис", "набори для вишивання, в'язання, плетіння"})),
    ("lipl", "Ліплення", frozenset({"тісто для ліплення і пластилін"})),
    ("nauka", "Наукові ігри", frozenset({"наукові ігри"})),
    ("kuhni", "Кухні й посуд", frozenset({"кухні і посуд", "дитячий посуд"})),
    ("prykrasy", "Прикраси", frozenset({"прикраси"})),
    ("fokusy", "Фокуси", frozenset({"набори для фокусів"})),
    ("likarski", "Лікарські", frozenset({"лікарські набори"})),
)


def nabory_pick(prods):
    """[(group_key, product)] у порядку round-robin по групах (перший екран різноманітний); group_key «other» — лише у «Усі»."""
    cat2g = {c: k for k, _t, cs in NABORY_GROUPS for c in cs}
    rng = random.Random(HOME_PICK_SEED)
    pools = {}
    for p in sorted(prods, key=lambda p: str(p["id"])):
        c = p.get("contribution")
        cat = (p.get("category") or "").strip().lower().replace("’", "'").replace("ʼ", "'")
        name = p.get("name") or ""
        if "набір" not in name.lower() or c is None or c < NABORY_MIN_CONTRIB or not p.get("photo") or (p.get("stock") or 0) <= 0:
            continue
        if not (NABORY_PRICE_MIN <= p["price"] <= NABORY_PRICE_MAX):
            continue
        if cat in NABORY_EXCLUDED_CATS or _is_markdown_item(p) or any(m in name.lower() for m in cp._BAD_NAME_MARKERS) or is_weapon_replica(str(p["id"]), name) or any(w in name.lower() for w in NABORY_BAD_WORDS):
            continue
        pools.setdefault(cat2g.get(cat, "other"), []).append(p)
    picked = {}
    for g, lst in pools.items():
        rng.shuffle(lst)
        used, out = set(), []
        for p in lst:
            k = _pick_key(p["name"])
            if k in used:
                continue
            used.add(k)
            out.append(p)
            if len(out) >= (NABORY_OTHER_MAX if g == "other" else NABORY_PER_GROUP):
                break
        picked[g] = out
    order = [k for k, _t, _c in NABORY_GROUPS] + ["other"]
    res, i = [], 0
    while any(i < len(picked.get(g, [])) for g in order):
        for g in order:
            if i < len(picked.get(g, [])):
                res.append((g, picked[g][i]))
        i += 1
    return res


def write_nabory(prods):
    """Сторінка /nabory.html: заголовок, чипи за типом набору (JS ховає/показує, усі картки вже в HTML), сітка з бейджем залишку 1–3 (badge=True), аналітика item_list «nabory_page»."""
    picked = nabory_pick(prods)
    if not picked:
        print("[build] ⚠ nabory: жодного товару за відбором — /nabory.html не пишемо", file=sys.stderr)
        return False
    present = {g for g, _p in picked}
    chips = ['<button type="button" class="chip active" data-g="all" aria-pressed="true">Усі</button>'] + [
        f'<button type="button" class="chip" data-g="{k}" aria-pressed="false">{esc(t)}</button>' for k, t, _c in NABORY_GROUPS if k in present]
    cards = []
    for i, (g, p) in enumerate(picked):
        cards.append(tile(p, eager=(i < 3), prio=(i == 0), badge=True).replace('<a class="card"', f'<a class="card" data-g="{g}"', 1))
    body = (
        '<h1 class="page">Набори, які займуть дитину надовго</h1>'
        '<p class="nb-lead">Готові набори — усе потрібне в комплекті.</p>'
        '<noscript><style>.nb-chips{display:none}</style></noscript>'
        f'<div class="chips nb-chips" id="nb-chips" role="group" aria-label="Тип набору">{"".join(chips)}</div>'
        f'<div class="nb-list" id="nabory-list" data-list="{NABORY_LIST_NAME}" data-limit="{NABORY_VIEW_LIMIT}"><div class="grid">\n' + "\n".join(cards) + '\n</div></div>'
        '<p class="nb-more"><a class="btn ghost" href="catalog.html" style="display:inline-block;max-width:260px">Усі іграшки</a></p>'
    )
    _write("nabory.html", page("Набори, які займуть дитину надовго", body, canonical="nabory.html",
                               description="Готові набори для дітей: творчість, ліплення, наукові ігри, кухні й посуд, прикраси, фокуси. Усе в коробці — відкрив і граєш. Доставка Новою Поштою."))
    return True


def write_404():
    """Власна сторінка 404 (Apache: `ErrorDocument 404 /404.html` у deploy/activate_site_apache.sh). Без неї покупець
    з помилковою/застарілою адресою бачив дефолт Webuzo: логотип Softaculous з чужого домену по http:// (mixed content)
    і клікабельне посилання на сайт хостинг-панелі. Статус лишається 404 (ErrorDocument зі шляхом, не URL)."""
    body = (
      '<div class="done"><div class="fox"><img class="mascot" src="assets/plutus_mascot_s.png" alt="Плутус" width="105" height="96" decoding="async"></div>'
      '<h2>Такої сторінки немає</h2>'
      '<p>Можливо, товар уже розпродано або адресу набрано з помилкою.</p>'
      '<p>Скористайтеся пошуком угорі або перейдіть до каталогу.</p>'
      '<p style="margin-top:20px"><a class="btn" href="catalog.html" style="display:inline-block;max-width:260px">До каталогу</a></p>'
      '<p style="margin-top:10px"><a class="btn ghost" href="categories.html" style="display:inline-block;max-width:260px">Усі категорії</a></p>'
      '</div>'
    )
    _write("404.html", page("Сторінку не знайдено", body, noindex=True, base_root=True))

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
<p>Ми ретельно добираємо асортимент, щоб знайти іграшку для будь-якого віку та настрою — від першого брязкальця до наборів для маленьких дослідників і винахідників. Нам важливо, щоб іграшка була безпечною, цікавою і приносила радість дитині, а батькам — спокій.</p>
<h2>Чому нам довіряють</h2>
<ul>
<li><b>Швидка відправка.</b> Замовлення, оформлені до 12:00, зазвичай відправляємо того ж дня.</li>
<li><b>Доставка по всій Україні.</b> Працюємо з Новою Поштою — до відділення або поштомату.</li>
<li><b>Прозорі умови.</b> Повернення протягом 14 днів і фіскальний чек на кожне замовлення.</li>
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
<p>Доставка Новою Поштою за тарифом перевізника — <b>орієнтовно 70–90 ₴ до 2 кг</b>; точна сума залежить від міста та оголошеної вартості. Для посилок важчих за 2 кг сума вища й визначається Новою Поштою за вагою та напрямком відправлення. При накладеному платежі Нова Пошта додає комісію: <b>20 ₴ + 2% від суми замовлення</b>. Для замовлень понад 500 ₴ тариф трохи зростає (0,5% від суми замовлення). Для замовлення до 1 000 ₴ разом це <b>орієнтовно 90–135 ₴</b> (наприклад, замовлення на 200 ₴: 70–90 ₴ + 24 ₴ = 94–114 ₴). Точну суму назве Нова Пошта при отриманні посилки. Доставку оплачує одержувач при отриманні посилки.</p>
<h2>Оплата</h2>
{PAY_DELIVERY}
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
<p>Повернення коштів здійснюється тим самим способом, яким була проведена оплата, після отримання й перевірки товару.</p>
{CARD_REFUND_INFO}"""

_CONTACTS = """<h1>Контакти</h1>
<p>Маєте запитання щодо товару, замовлення чи доставки? Зв'яжіться з нами — ми завжди раді допомогти й підкажемо з вибором.</p>
<h2>Як з нами зв'язатися</h2>
<ul>
<li><b>Телефон:</b> <a href="tel:+380730150815">+380 (73) 015-08-15</a></li>
<li><b>Електронна пошта:</b> <a href="mailto:plutustoys@gmail.com">plutustoys@gmail.com</a></li>
<li><b>Продавець:</b> {SELLER_NAME}</li>
<li><b>Адреса:</b> {SELLER_ADDRESS}</li>
{SELLER_TAX_LI}
<li>Доставляємо по всій Україні Новою Поштою.</li>
</ul>
<p>Телефонуйте або пишіть на пошту у зручний для вас спосіб — ми відповімо якнайшвидше в робочий час. Якщо не вдалося додзвонитися, залиште повідомлення, і ми обов'язково передзвонимо.</p>"""

_OFFER = """<h1>Публічна оферта</h1>
<p>Цей документ є офіційною пропозицією (публічною офертою) інтернет-магазину PlutusToys (далі — «Продавець») укласти договір купівлі-продажу товарів на умовах, викладених нижче. Оформлення замовлення на сайті означає повну й беззастережну згоду Покупця з умовами цієї оферти.</p>
<h2>1. Загальні положення</h2>
<p>Продавець — {SELLER_NAME}, інтернет-магазин дитячих іграшок PlutusToys (plutustoys.com.ua), адреса: {SELLER_ADDRESS}. Зв'язок із Продавцем — за телефоном +380 (73) 015-08-15 та електронною поштою plutustoys@gmail.com (сторінка «Контакти»). Покупець — будь-яка дієздатна особа, яка оформила замовлення на сайті plutustoys.com.ua.</p>
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
<li>Оплата здійснюється {PAY_OFFER}.</li>
<li>Оплата банківською карткою онлайн{CARD_SOON_OFFER} здійснюється через сервіс LiqPay; дані картки вводяться на захищеній сторінці LiqPay і не передаються та не зберігаються Продавцем.</li>
<li>Вартість доставки оплачується окремо за тарифами перевізника.</li>
</ul>
<h2>5. Доставка</h2>
<p>Доставка Товару здійснюється Новою Поштою по всій території України — до відділення або поштомату. Терміни та вартість доставки визначаються згідно з умовами перевізника й інформацією на сторінці «Доставка».</p>
<h2>6. Повернення та обмін</h2>
<p>Повернення й обмін Товару здійснюються відповідно до Закону України «Про захист прав споживачів» та умов, викладених на сторінці «Повернення і обмін».</p>
{CARD_REFUND_INFO}
<h2>7. Відповідальність сторін</h2>
<ul>
<li>Сторони несуть відповідальність згідно з чинним законодавством України.</li>
<li>Продавець не несе відповідальності за неналежне використання Товару Покупцем, а також за затримки з боку перевізника.</li>
<li>Продавець не відповідає за збитки, спричинені наданням Покупцем недостовірних даних.</li>
</ul>
<h2>8. Контакти Продавця</h2>
<p>Інтернет-магазин PlutusToys<br>{SELLER_NAME}<br>Адреса: {SELLER_ADDRESS}{SELLER_TAX_BR}<br>Телефон: +380 (73) 015-08-15<br>Ел. пошта: plutustoys@gmail.com<br>Сайт: plutustoys.com.ua</p>"""
_PRIVACY = """<h1>Політика конфіденційності</h1>
<p>Ця політика пояснює, які персональні дані інтернет-магазин PlutusToys ({SELLER_NAME}, {SELLER_ADDRESS}) збирає під час оформлення замовлення, навіщо й кому їх передає. Ми діємо відповідно до Закону України «Про захист персональних даних».</p>
<h2>Які дані ми збираємо</h2>
<ul>
<li>ім'я та прізвище отримувача, номер телефону;</li>
<li>місто та відділення або поштомат Нової Пошти, обрані для доставки; адреса, якщо доставка адресна;</li>
<li>електронна пошта — якщо ви її вказали;</li>
<li>склад і сума замовлення.</li>
</ul>
<h2>Навіщо ми використовуємо дані</h2>
<p>Лише для виконання вашого замовлення: підтвердження, передача на комплектацію та відправлення, зв'язок щодо замовлення, повернення чи обміну, бухгалтерський і податковий облік, як того вимагає закон.</p>
<h2>Кому ми передаємо дані</h2>
<ul>
<li><b>Нова Пошта</b> — для доставки замовлення;</li>
<li><b>постачальник товару</b>, який комплектує та відправляє замовлення, — ім'я, телефон і дані про доставку в обсязі, необхідному для відправлення;</li>
<li><b>платіжний сервіс</b> (LiqPay, АТ КБ «ПриватБанк») — лише коли доступна оплата карткою онлайн і ви обрали її;</li>
<li><b>рекламна платформа Meta</b> — факт покупки (сума, склад і номер замовлення) та контактні дані у <b>псевдонімізованому вигляді</b> (хешовані: напряму ім'я чи номер не передаються, але хеш можна зіставити з відомими Meta даними) — для оцінки ефективності реклами, лише якщо ввімкнено рекламну аналітику;</li>
<li><b>сервіс фіскальних чеків Checkbox</b> — номер телефону для надсилання електронного чека (SMS/Viber), якщо чек оформлюється.</li>
</ul>
<p>Ми не продаємо ваші дані й не передаємо їх іншим третім особам, окрім випадків, коли цього вимагає закон.</p>
<h2>Скільки зберігаємо</h2>
<p>Стільки, скільки потрібно для виконання замовлення, гарантійних зобов'язань і вимог законодавства про облік; після цього ми припиняємо їх використання, окрім випадків, коли закон вимагає їх зберігати довше. Видалення даних за вашою вимогою — див. розділ «Ваші права».</p>
<h2>Cookie та аналітика</h2>
<p>Сайт може використовувати cookie та інструменти аналітики (Google Analytics, піксель Meta), якщо вони ввімкнені; їх можна заблокувати в налаштуваннях браузера. Без цих інструментів сайт працює повністю.</p>
<h2>Ваші права</h2>
<p>Ви можете дізнатися, які ваші дані ми зберігаємо, вимагати їх виправлення чи видалення, відкликати згоду на обробку. Напишіть нам на <a href="mailto:plutustoys@gmail.com">plutustoys@gmail.com</a> — ми відповімо протягом 30 днів.</p>
<h2>Контакти</h2>
<p>{SELLER_NAME}<br>Адреса: {SELLER_ADDRESS}{SELLER_TAX_BR}<br>Ел. пошта: plutustoys@gmail.com<br>Телефон: +380 (73) 015-08-15</p>"""
_ABOUT = _ABOUT.replace("{PAY_ABOUT}", PAY_ABOUT)
_OFFER = _OFFER.replace("{PAY_OFFER}", PAY_OFFER).replace("{CARD_SOON_OFFER}", "" if LIQPAY_LIVE else " (після її запуску на сайті)").replace("{CARD_REFUND_INFO}", CARD_REFUND_INFO)
_RETURNS = _RETURNS.replace("{CARD_REFUND_INFO}", CARD_REFUND_INFO)
_DELIVERY = _DELIVERY.replace("{PAY_DELIVERY}", PAY_DELIVERY + CARD_PAY_SOON + CARD_PAY_INFO + CARD_REFUND_INFO)
for _nm in ("SELLER_NAME", "SELLER_ADDRESS", "SELLER_TAX_LI", "SELLER_TAX_BR"):
    _val = globals()[_nm]
    _CONTACTS = _CONTACTS.replace("{" + _nm + "}", _val)
    _OFFER = _OFFER.replace("{" + _nm + "}", _val)
    _PRIVACY = _PRIVACY.replace("{" + _nm + "}", _val)

TRUST_PAGES = [
    ("about.html",    "Про нас",             _ABOUT,
     "Про інтернет-магазин дитячих іграшок PlutusToys: як ми працюємо, чому нам довіряють, доставка Новою Поштою."),
    ("delivery.html", "Доставка",            _DELIVERY,
     "Доставка іграшок Новою Поштою по всій Україні: відділення та поштомати, терміни 1–3 дні, відстеження за ТТН."),
    ("returns.html",  "Повернення і обмін",  _RETURNS,
     "Умови повернення та обміну іграшок PlutusToys: 14 днів за Законом про захист прав споживачів, як оформити."),
    ("contacts.html", "Контакти",            _CONTACTS,
     "Контакти інтернет-магазину PlutusToys — телефон, електронна пошта, адреса продавця."),
    ("offer.html",    "Публічна оферта",     _OFFER,
     "Публічна оферта інтернет-магазину дитячих іграшок PlutusToys — умови договору купівлі-продажу."),
    ("privacy.html",  "Політика конфіденційності", _PRIVACY,
     "Політика конфіденційності PlutusToys: які персональні дані ми збираємо під час замовлення, навіщо, кому передаємо й як їх видалити."),
]

def write_trust_pages():
    """Пише 5 сторінок довіри. Повертає список імен файлів (для sitemap)."""
    written = []
    for fname, title, content, desc in TRUST_PAGES:
        body = f'<article class="doc">\n{content}\n</article>'
        _write(fname, page(title, body, description=desc, canonical=fname))
        written.append(fname)
    return written

def write_sitemap(prods, paged, static_urls=None, oos_prods=()):
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
        if secondary:
            continue   # SEO 11.10.2026: у sitemap лише 1-ша сторінка категорії/каталогу; пагінація лишається доступною через посилання, canonical і rel=prev/next не змінено; усі картки перелічені нижче напряму
        if fn == "catalog.html":
            pr, cf = "0.9", "daily"
        else:                                             # page-1 категорії/каталогу
            pr, cf = "0.7", "weekly"
        urls.append((fn, pr, cf))
    urls += [(f"product-{p['id']}.html", "0.6", "weekly") for p in prods]
    # без залишку: адреса жива (OutOfStock), лишається в sitemap — інакше Google знову побачить 404 на вже проіндексованому; пріоритет нижчий
    urls += [(f"product-{p['id']}.html", "0.3", "monthly") for p in oos_prods]
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

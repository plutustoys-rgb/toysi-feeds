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
import os, re, sys, json, html, unicodedata, hashlib, random, bisect, heapq, functools, time
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
STOCK_BADGE_MAX = 1   # бейдж «Остання штука» (SMM 21.10: поріг 1; було «Залишилось N шт» при 1–3 — на половині карток /nabory і /ig сигнал став шаблонним тиском) у добірках (карусель, /nabory.html, /ig.html), коли залишок Toysi ≤ цього (SMM 12.10)
DELIVERY_COST = "за тарифами оператора"   # власник 21.10: орієнтовну суму доставки НЕ показуємо ніде — лише «за тарифами оператора» (сума залежить від міста, ваги й розмірів, її називає Нова Пошта)
DELIVERY_FEE_FORMULA = "комісія за накладений платіж: 20 ₴ + 2% від суми замовлення"
DELIVERY_FEE_FORMULA_SHORT = "20 ₴ + 2% від суми замовлення"
DELIVERY_NOTE = ("Доставка Новою Поштою — " + DELIVERY_COST + " (сума залежить від міста, ваги та розмірів посилки), плюс " + DELIVERY_FEE_FORMULA
                 + ". Точну суму назве Нова Пошта при отриманні посилки. Оплачується при отриманні посилки.")
# Оплата карткою онлайн (prepaid): комісії накладеного платежу НЕМАЄ, тож підсумок доставки без неї (тест. 13.10: кошик показував COD-івські 99–119 ₴ для платника карткою — завищено ≈29 ₴). Для передоплати оголошена вартість/тариф НЕ міряні
# (Код: сайтових передоплат ще не було) — друкуємо лише базовий тариф «орієнтовно 70–90 ₴ до 2 кг» без надбавки 0,5% і без твердження про платника.
DELIVERY_LABEL_PREPAID = "Доставка Новою Поштою"
DELIVERY_NOTE_PREPAID = "Доставка Новою Поштою — " + DELIVERY_COST + " (сума залежить від міста, ваги та розмірів посилки). Точну суму назве Нова Пошта."
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
# Рекламний тег Google Ads (власник 21.10.2026, акаунт 280-525-6388; SEO): додається НА ТОМУ Ж gtag.js поруч із GA4 — `gtag('config','AW-…')`, другого завантаження gtag.js нема.
# Діє лише коли задано GA4_ID (gtag.js вантажиться з його id); SITE_GADS_ID="" вимикає; формат AW-<цифри> валідується (id потрапляє в <script>).
_gads = os.environ.get("SITE_GADS_ID", "AW-18528978608").strip()
GADS_ID = _gads if (GA4_ID and re.fullmatch(r"AW-[0-9]{8,12}", _gads)) else ""
# Мітка конверсії «Покупка на сайте» Google Ads (SEO 21.10.2026): send_to AW-<акаунт>/<мітка>. Іде в window.PT_GADS_CONV; app.js шле gtag('event','conversion',…) у тому ж місці й за тим самим тригером, що GA4 purchase.
# Валідується (потрапляє в <script>): формат AW-<цифри>/<мітка> і префікс = GADS_ID; SITE_GADS_CONV="" вимикає. Розширене відстеження конверсій (хеші даних клієнта) НЕ вмикаємо — рішення власника.
_gconv = os.environ.get("SITE_GADS_CONV", "AW-18528978608/W1AACIPZ0KQdELCNp4NF").strip()
GADS_CONV = _gconv if (GADS_ID and re.fullmatch(r"AW-[0-9]{8,12}/[A-Za-z0-9_-]{8,40}", _gconv) and _gconv.startswith(GADS_ID + "/")) else ""
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
    if it.get("site_price"):   # RoyalToys (rt-…): ціну сайту рахує Код за закритою формулою, а не 1.5× від ціни Toysi
        return int(it["site_price"])
    return cp.site_retail_price(it)

# Російськомовні товари на сайті НЕ показуємо (власник 20.10: «російськомовні прибери»; для фідів Rozetka/Prom таке правило з 21.08 — generate_*_feed._RUSSIAN_LANG_RE).
# Лише МОВНИЙ маркер у назві: «(рос)»/«(рус)», standalone «рос»/«рус», «російською», «російськомов…», «рос. звучення». НЕ голе «російськ…» (змістом, напр. «російські народні казки»,
# не мова). Двомовні («рос. і укр. мовами», «укр./рос.») лишаємо: це укр. продукт із російською версією правил.
_RU_LANG_RE = re.compile(r"\bрос\b|\bрус\b|російською|російськомов|рос\.\s*звуч", re.IGNORECASE)
_UA_LANG_RE = re.compile(r"\bукр\b|\bукр\.|українськ", re.IGNORECASE)


# Російські НАЗВИ (власник 20.10: «російськомовні прибери»; тестувальник 20.10 показав 60+ карток Toysi з російським текстом у назві): (1) літери, яких нема в українській (ы э ъ ё) — тверда ознака;
# (2) російські СЛОВА, лише ТОЧНІ форми (без \w* після «набор»: укр. «набір» відмінюється «набору/набором/наборі/наборах/наборів», вони починаються на «набор» — з \w* зникли б 189 звичайних укр. карток).
# Слова: набор/наборы/наборов (нижче — відмінки НЕ беремо), игрушк*, детск*, цвет*, размер*, мальчик*, девоч*, проведени*, раскоп*, расписани*, ламинирован*, деревянн*, развивающ*, подарочн* — у цих основ нема укр. збігів.
_RU_LETTERS_RE = re.compile("[ыэъёЫЭЪЁ]")
_RU_WORDS_RE = re.compile(r"\b(?:набор|наборы|наборов|игрушк\w*|детск\w*|цвет\w*|размер\w*|мальчик\w*|девоч\w*|проведени\w*|раскоп\w*|расписани\w*|ламинирован\w*|деревянн\w*|развивающ\w*|подарочн\w*)\b", re.IGNORECASE)


def is_russian_language_item(name):
    n = str(name or "")
    if _RU_LETTERS_RE.search(n) or _RU_WORDS_RE.search(n):
        return True   # російський текст у назві — навіть якщо є «(укр)» (покупець бачить російську назву)
    return bool(_RU_LANG_RE.search(n)) and not _UA_LANG_RE.search(n)   # мовний маркер («рос», «російською…») — двомовні «рос. і укр.» лишаються



# 10.11.2026 (власник: «мені треба щоб покупець не бачив» постачальників): публічні адреса картки, код на картці, sku і шлях фото НЕ містять імен постачальників.
# ЗА ВОРОТАМИ SITE_PUBLIC_IDS_LIVE=1 вмикає Код ПІСЛЯ того, як Apache вже має 301 зі старих адрес і rewrite нового шляху фото (deploy/activate_site_apache.sh);
# вимкнено → збірка як була (product-rt-…, /img/rt/…, PT-R…). Внутрішній id (rt-…) лишається ключем даних, кошика й замовлень.
PUBLIC_IDS_LIVE = os.environ.get("SITE_PUBLIC_IDS_LIVE") == "1"
PUBLIC_SLUG_PREFIX = {"rt-": "y", "kv-": "k"}    # нейтральна літера (не ініціал постачальника): RoyalToys «rt-69616» -> «y69616»; Toysi — лише ASCII-цифри, тож слаги однозначні
# KIDDISVIT (kv-<id>, 17.11.2026, Код → Shopify-code): нових адрес до воріт не існувало, тож публічний slug «k<id>», код «PT-K<id>» і фото «/img/p/k<id>-N.jpg» діють ЗАВЖДИ (не залежать від SITE_PUBLIC_IDS_LIVE);
# Apache має віддавати /img/p/k<id>-N.jpg з файлу /img/kv/kv-<id>-N.jpg (rewrite — Код, ДО SITE_KV_LIVE=1). Внутрішній id (kv-…) лишається ключем даних, кошика й замовлень.
_KV_ID_RX = re.compile(r"kv-[0-9]+")
_PUBLIC_IMG_KV_RX = re.compile(r"https://plutustoys\.com\.ua/img/kv/(kv-[0-9]+)-([0-9]{1,2})\.jpg")
_PUBLIC_IMG_RX = re.compile(r"https://plutustoys\.com\.ua/img/(rt)/(\1-[0-9A-Za-z_]+)-([0-9]{1,2})\.jpg")   # каталог зв'язаний з префіксом id (\1): /img/rt/<чужий id> не переписуємо


def public_slug(pid):
    """Публічна частина адреси картки. Toysi — як є; RoyalToys при SITE_PUBLIC_IDS_LIVE=1 — нейтральний slug (y69616); сміттєві id не перетворюються."""
    pid = str(pid)
    if _KV_ID_RX.fullmatch(pid):
        return PUBLIC_SLUG_PREFIX["kv-"] + pid[3:]   # KIDDISVIT: нейтральний slug завжди (k<id>)
    if not PUBLIC_IDS_LIVE:
        return pid
    if _RT_ID_RX.fullmatch(pid):
        return PUBLIC_SLUG_PREFIX["rt-"] + pid[3:]
    return pid


def product_file(pid):
    """Ім'я файлу/адреса картки (відносна): product-<публічний slug>.html. Єдине місце, де id перетворюється на адресу."""
    return f"product-{public_slug(pid)}.html"


def public_photo(pid, url):
    """Фото RoyalToys з нашого сервера: https://plutustoys.com.ua/img/rt/<id>-N.jpg -> https://plutustoys.com.ua/img/p/<slug>-N.jpg (Apache переписує назад). Інші URL (Toysi тощо) — без змін."""
    if isinstance(url, str) and _KV_ID_RX.fullmatch(str(pid)):
        mk = _PUBLIC_IMG_KV_RX.fullmatch(url)   # KIDDISVIT: https://plutustoys.com.ua/img/kv/kv-<id>-N.jpg -> /img/p/k<id>-N.jpg (Apache переписує назад); чужий id у шляху не переписуємо
        return f"https://plutustoys.com.ua/img/p/{public_slug(pid)}-{mk.group(2)}.jpg" if mk and mk.group(1) == str(pid) else url
    if not PUBLIC_IDS_LIVE or not isinstance(url, str):
        return url
    m = _PUBLIC_IMG_RX.fullmatch(url)
    if not m or m.group(2) != str(pid) or public_slug(pid) == str(pid):
        return url
    return f"https://plutustoys.com.ua/img/p/{public_slug(pid)}-{m.group(3)}.jpg"


# 10.11.2026 (власник: «роби проксі»): фото Toysi покупець не повинен бачити з адресою постачальника. ЗА ВОРОТАМИ SITE_TOYSI_PHOTO_PROXY=1 (вимкнено за замовчуванням; вмикає Код ПІСЛЯ того, як на VPS працює проксі /img/t/ -> https://toysi.ua/p/): публічне фото = https://plutustoys.com.ua/img/t/<шлях після /p/>.
# Лише шлях виду [NN/][enl_]<цифри>.jpg|png|webp (інакше — URL як є: довільні адреси не проксуємо). Інші хости (images.prom.ua, наш власний) не чіпаємо. Єдина точка входу — p["photo"] (index.json, <img>, og:image, JSON-LD, каруселі, preload).
TOYSI_PHOTO_PROXY = os.environ.get("SITE_TOYSI_PHOTO_PROXY") == "1"
_TOYSI_PHOTO_RX = re.compile(r"https://toysi\.ua/p/((?:[0-9]{1,2}/)?(?:enl_)?[0-9]{1,12}\.(?:jpg|png|webp))")


def toysi_proxy_photo(url):
    """https://toysi.ua/p/3/enl_265377.jpg -> https://plutustoys.com.ua/img/t/3/enl_265377.jpg (лише при SITE_TOYSI_PHOTO_PROXY=1; інші URL — без змін)."""
    if not TOYSI_PHOTO_PROXY or not isinstance(url, str):
        return url
    m = _TOYSI_PHOTO_RX.fullmatch(url)
    return f"https://plutustoys.com.ua/img/t/{m.group(1)}" if m else url


# ── Назви-капсом (SEO 10.11.2026, GMC title_all_caps): на сайті назва картки проходить feed_title_norm.normalize_title — ТОЙ САМИЙ детермінований модуль, що й фіди Google (регістр назви на сторінці й у фіді однаковий; ВИНЯТОК — назви довші за 150 символів: фід обрізає їх до 150 (≈72 назви), сайт показує повну назву).
# Без опори на каталог і без замороженого переліку: вихід залежить лише від самої назви. Запобіжник: приймаємо результат тільки якщо він відрізняється від назви постачальника ЛИШЕ регістром
# (casefold збігається) — модуль не може прибрати слова/пунктуацію з назви на сайті. Обрізання до 150 символів (потрібне фіду) для сайту вимкнено: limit = довжина назви.
try:
    from feed_title_norm import normalize_title as _feed_normalize_title
except ImportError:   # модуля нема — назви як є (збірка не падає), але ГУЧНО: інакше всі капс-назви тихо лишились би
    _feed_normalize_title = None
    print("[build] ⚠ feed_title_norm недоступний — назви-капсом на сайті НЕ нормалізовано", file=sys.stderr)
_TITLE_NORM_COUNT = [0]   # скільки назв змінила нормалізація за збірку (0 на повному каталозі — сигнал, що модуль не працює)


def _norm_case(t):
    return str(t).casefold().replace("ʼ", "'").replace("’", "'")


def normalize_site_title(name):
    """Назва картки для сайту: регістр нормалізовано feed_title_norm (лише зміна регістру), інакше — назва постачальника без змін."""
    if not _feed_normalize_title or not isinstance(name, str) or not name:
        return name
    try:
        new = _feed_normalize_title(name, limit=max(150, len(name) + 1))
    except Exception:   # noqa: BLE001 — нормалізація не повинна валити збірку
        return name
    if new != name and isinstance(new, str) and _norm_case(new) == _norm_case(name):
        _TITLE_NORM_COUNT[0] += 1
        return new
    return name


def site_product_code(pid):
    """Власний код товару для покупця: Toysi «176003» -> «PT-176003»; RoyalToys при SITE_PUBLIC_IDS_LIVE=1 — «PT-Y69616» (нейтральна літера = публічний slug); до відкриття воріт — як було: RoyalToys «rt-X» -> «PT-RX»."""
    pid = str(pid)
    if _KV_ID_RX.fullmatch(pid):
        return "PT-K" + pid[3:]   # KIDDISVIT: завжди «PT-K<id>» (не залежить від воріт публічних id)
    if PUBLIC_IDS_LIVE and public_slug(pid) != pid:
        return "PT-" + public_slug(pid)[0].upper() + public_slug(pid)[1:]   # регістр решти id зберігаємо (rt-id, що різняться регістром, не зливаються в один код)
    return "PT-R" + pid[3:] if pid.startswith("rt-") else "PT-" + pid


def esc(s):
    return html.escape(str(s or ""))

# ── спільні шматки розмітки ─────────────────────────────────────────────
# Кнопка «Написати» у шапці (власник 14.10, варіант А): меню месенджерів. Viber — за номером загального телефону; Telegram з'явиться, коли буде бот (CHAT_TELEGRAM_URL — посилання t.me/<бот>; порожньо = пункт не показуємо; запит ботом спрямовується на Аудитора — це частина Code-Agent); Instagram — особисті повідомлення.
CHAT_VIBER_URL = "viber://chat?number=%2B380730150815"
CHAT_TELEGRAM_URL = "https://t.me/plutustoys_support_bot"   # бот підтримки (Code-Agent 14.10): повідомлення клієнта іде власнику, відповідь повертається в той самий чат; часу відповіді в UI не обіцяємо
CHAT_INSTAGRAM_URL = ""   # власник 14.10: «Instagram не треба» — пункт не показуємо (повернути: https://ig.me/m/plutustoys.ua)


def chat_menu_html():
    items = [("viber", "Viber", CHAT_VIBER_URL), ("telegram", "Telegram", CHAT_TELEGRAM_URL), ("instagram", "Instagram", CHAT_INSTAGRAM_URL)]
    links = "".join(f'<a class="chat-item" role="menuitem" data-chat="{k}" href="{esc(u)}" target="_blank" rel="noopener">{t}</a>' for k, t, u in items if u)
    if not links:
        return ""
    return ('<div class="chatwrap"><button type="button" class="chatbtn" id="chat-toggle" aria-haspopup="true" aria-expanded="false" aria-controls="chat-menu" aria-label="Підтримка: написати нам">'
            f'<img class="chat-ava" src="{asset_url("assets/support_avatar.webp")}" alt="" aria-hidden="true" width="28" height="28" decoding="async">'   # лого підтримки: Плутус у навушниках (власник 15.10)
            '<span class="chat-lbl">Підтримка</span></button>'
            f'<div class="chat-menu" id="chat-menu" role="menu" aria-label="Підтримка: написати нам" hidden>{links}</div></div>')


def header_socials_html():
    """Значки Instagram / Facebook / пошта у шапці (власник 14.10: «це поле пустує»): на ПК заповнюють проміжок між кошиком і кнопкою «Підтримка»; на вузьких екранах приховані (там вони в підвалі). Посилання — ті самі, що в підвалі, utm_medium=header."""
    ig, fb = (_footer_utm(u).replace("utm_medium=footer", "utm_medium=header") for u in SOCIAL_URLS)
    ico = 'width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"'
    return (
      '<div class="hsoc">'
      f'<a class="hsoc-a" href="{ig}" target="_blank" rel="noopener" aria-label="Instagram" data-hsoc="instagram">'
        f'<svg {ico}><rect x="3" y="3" width="18" height="18" rx="5"/><circle cx="12" cy="12" r="4"/><circle cx="17.2" cy="6.8" r=".6" fill="currentColor"/></svg></a>'
      f'<a class="hsoc-a" href="{fb}" target="_blank" rel="noopener" aria-label="Facebook" data-hsoc="facebook">'
        f'<svg {ico}><path d="M14 8h2.5V4.5H14a4 4 0 0 0-4 4V11H7.5v3.5H10V21h3.5v-6.5H16L16.5 11h-3V8.8c0-.5.3-.8.8-.8Z"/></svg></a>'
      '<a class="hsoc-a" href="mailto:plutustoys@gmail.com" aria-label="Написати на пошту plutustoys@gmail.com" data-hsoc="email">'
        f'<svg {ico}><rect x="3" y="5" width="18" height="14" rx="2.5"/><path d="m3.5 7.5 8.5 6 8.5-6"/></svg></a>'
      '</div>'
    )


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
      + chat_menu_html()
      + header_socials_html() +
      # кошик — крайній праворуч, акцентна «пігулка» (власник 15.10, варіант Б; ≤560 px — лише іконка з лічильником)
      '<a class="cartbtn" href="cart.html" aria-label="Кошик">'
        '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">'
        '<circle cx="9" cy="21" r="1"/><circle cx="20" cy="21" r="1"/>'
        '<path d="M1 1h4l2.7 13.4a2 2 0 0 0 2 1.6h9.7a2 2 0 0 0 2-1.6L23 6H6"/></svg>'
        '<span class="cart-lbl">Кошик</span>'
        '<span class="badge" id="cart-badge">0</span>'
      '</a>'
      '</header>'
    )

SEARCH_POPULAR = []   # [(назва, файл)] популярних категорій для порожнього стану пошуку; заповнює build() до запису сторінок


def search_empty_html():
    """Порожній стан пошуку (власник 13.10, тестувальник #16: екран із одним словом «Готово» виглядав як «сторінка не завантажилась»): підказка + популярні категорії + «Усі іграшки»."""
    pills = "".join(f'<a class="sr-pill" href="{esc(u)}">{esc(t)}</a>' for t, u in SEARCH_POPULAR)
    return ('<div class="sr-empty"><p class="sr-hint">Почніть вводити назву іграшки (від 2 літер) або оберіть категорію:</p>'
            + (f'<div class="sr-pills">{pills}</div>' if pills else "")
            + '<p class="sr-all"><a href="catalog.html">Усі іграшки →</a></p></div>')


# Рядок про повернення біля кнопки «У кошик» на картці й у кошику (SMM/власник 13.10): рівно умови зі сторінки «Повернення» (14 днів, товар належної якості; зворотна пересилка — коштом покупця), зміст не міняти.
RETURN_LINE = "Повернення якісного товару — 14 днів; зворотна пересилка за рахунок покупця"


def search_overlay():
    return (
      '<div class="overlay" id="search-overlay" role="dialog" aria-label="Пошук">'
      '<div class="sp">'
      '<div class="obar">'
        '<input type="search" placeholder="Що шукаємо?" aria-label="Пошук">'
        '<button class="x" id="close-search">Готово</button>'
      '</div><div class="results"></div>'
      f'<template id="search-empty">{search_empty_html()}</template></div></div>'
    )

FOOTER_LINKS = [
    ("categories.html", "Усі категорії товарів"),   # власник 19.10: хаб усіх категорій — ПЕРЕД «Про нас» (замість переліку 30 категорій у футері)
    ("about.html", "Про нас"), ("delivery.html", "Доставка"),
    ("returns.html", "Повернення"), ("contacts.html", "Контакти"),
    ("offer.html", "Публічна оферта"), ("privacy.html", "Політика конфіденційності"),
]
def footer():
    _fl = list(FOOTER_LINKS)
    if GIFT_BUILT:   # хаб подарунків (SEO 15.11): посилання лише коли хаб справді побудовано в цій збірці
        _fl.insert(1, (GIFT_HUB_FILE, "Подарунки дитині"))
    if BRAND_BUILT:   # хаб брендів (SEO 17.11): посилання лише коли хаб справді побудовано в цій збірці
        _fl.insert(2 if GIFT_BUILT else 1, (BRAND_HUB_FILE, "Бренди"))
    if COLORING_BUILT:   # безкоштовні розмальовки з Плутусом (SEO 18.11): посилання лише коли хаб справді побудовано
        _fl.insert(1, (COLORING_HUB_FILE, "Розмальовки для друку"))
    nav = " · ".join(f'<a href="{u}">{esc(t)}</a>' for u, t in _fl)
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
    out = [f'<script>window.PT_ANALYTICS={json.dumps({"ga4": GA4_ID, "fb": META_PIXEL_ID})};' + (f'window.PT_GADS_CONV={json.dumps(GADS_CONV)};' if GADS_CONV else "") + '</script>\n']
    gtag_src = f"https://www.googletagmanager.com/gtag/js?id={GA4_ID}"
    gtag_cfg = f"gtag('config','{GA4_ID}');" + (f"gtag('config','{GADS_ID}');" if GADS_ID else "")      # GA4 + (опційно) Google Ads на одному gtag.js
    fb_src = "https://connect.facebook.net/en_US/fbevents.js"
    if not defer:
        if GA4_ID:
            out.append(f'<script async src="{gtag_src}"></script>\n'
                       "<script>window.dataLayer=window.dataLayer||[];function gtag(){dataLayer.push(arguments);}"
                       f"gtag('js',new Date());{gtag_cfg}</script>\n")
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
                  f"gtag('js',new Date());{gtag_cfg}")
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


META_DOMAIN_VERIFICATION = "zrxty9pynr6k0qh1y1tpgzkfyliv7s"   # Meta Business «PlutusToys», домен plutustoys.com.ua (SEO 18.10, «Not Verified» → після деплою SEO натискає «Підтвердити домен») — у <head> КОЖНОЇ сторінки
GSC_VERIFICATION = "AzAID7jv3z26c8wFyrtnzjRTa_2xDyEnFx_R-neaoZY"   # підтвердження права власності Search Console (код власника з 17.07, був на вітрині Prom; SEO 18.10: на новому сайті злетіло) — у <head> КОЖНОЇ сторінки


def page(title, body, extra_head="", description="", canonical="", og_image="", og_type="website", noindex=False, title_full="", base_root=False, defer_trackers=True, analytics=True, head_first=""):
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
      "<meta charset=\"utf-8\">\n" + head_first +   # head_first — лише review.html (meta referrer до будь-якого ресурсу); порожній — розмітка як була
      "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">\n"
      f"<meta name=\"google-site-verification\" content=\"{GSC_VERIFICATION}\">\n"
      f"<meta name=\"facebook-domain-verification\" content=\"{META_DOMAIN_VERIFICATION}\" />\n"
      # base_root лише для 404: її віддають за БУДЬ-ЯКОЇ адреси (/a/b/c), а шляхи в шаблоні відносні (assets/…, index.json, api/…)
      + ("<base href=\"/\">\n" if base_root else "") +
      f"<title>{esc(full_title)}</title>\n"
      f"{head_meta}"
      "<link rel=\"icon\" href=\"assets/favicon.ico\" sizes=\"any\">\n"
      "<link rel=\"icon\" type=\"image/png\" sizes=\"32x32\" href=\"assets/favicon-32.png\">\n"
      "<link rel=\"apple-touch-icon\" href=\"assets/apple-touch-icon.png\">\n"
      + ("" if TOYSI_PHOTO_PROXY else ("<link rel=\"preconnect\" href=\"https://toysi.ua\" crossorigin>\n"
                                        "<link rel=\"dns-prefetch\" href=\"https://toysi.ua\">\n")) +
      f"<link rel=\"stylesheet\" href=\"{asset_url('assets/styles.css')}\">\n"
      f"{analytics_head(defer_trackers) if analytics else ''}{extra_head}</head>\n<body>\n<div class=\"wrap\">\n"   # analytics=False лише review.html (токен в адресі)
      f"{header()}\n{body}\n{footer()}\n</div>\n"
      f"{search_overlay()}\n"
      f"<script src=\"{asset_url('assets/app.js')}\"></script>\n</body>\n</html>\n"
    )

# ── Постійна акція (власник 14.10; з 15.10 ПОСТІЙНА: щодня нова партія на 8 діб; з 16.10 ≈72 товари, знижка КОЖНОГО товару випадкова 5–15%; бекенд Code-Agent: site_sale.py пише site/sale_week.json щодня) ──
# КОНТРАКТ: {"week","pct","from","until","created_at","items":{"<id>":{"old":int,"new":int,"pct":int 5..15}}}; from/until — ISO із поясом; new = ceil(old × (100−pct)/100).
# build_site у вікні from…until ставить ціну pr=new і op=old (index.json) для id з файлу; ПОЗА вікном файл ігнорується (навіть якщо лишився). Ціна — єдине джерело для замовлення/LiqPay/чека (site_order_api читає pr, op ігнорує).
# Захисні перевірки на КОЖНІЙ збірці (стара ціна має бути РЕАЛЬНОЮ, збитку нема): поточна ціна сайту == old; new < old; внесок після знижки ≥ SALE_RESERVE_UAH (запас 25 ₴); невідома собівартість — без знижки. Товар, що не пройшов, випадає з акції (ціна лишається звичайною).
SALE_FILE = os.path.join(OUT, "sale_week.json")
SALE_WEEK_ENABLED = True
SALE_RESERVE_UAH = 25.0
SALE_MIN_APPLIED_ALERT = 3   # менше застосованих позицій при діючій акції — алерт у Telegram
SALE_ACTIVE = None   # виставляє build(): {"week","pct","from","until","items":{id:(old,new)}} або None


def _aware_dt(s):
    try:
        d = datetime.fromisoformat(str(s))
    except (ValueError, TypeError):
        return None
    return d if d.tzinfo else None


def _sale_now():
    raw = os.environ.get("SITE_SALE_NOW", "").strip()   # лише для тестів
    return _aware_dt(raw) if raw else None


def _parse_sale_week(raw, now):
    """Один тиждень контракту → dict або None (поза вікном — тихо; зламаний — None з попередженням у stderr)."""
    def bad(why):
        print(f"[build] ⚠ sale_week.json: тиждень відхилено: {why}", file=sys.stderr)
        return None

    if not isinstance(raw, dict) or not isinstance(raw.get("items"), dict):
        return bad("очікується {\"items\": {...}}")
    pct = raw.get("pct")
    if not isinstance(pct, int) or isinstance(pct, bool) or not (1 <= pct <= 50):
        return bad(f"pct={pct!r}")
    start, end = _aware_dt(raw.get("from")), _aware_dt(raw.get("until"))
    if start is None or end is None or not start < end:
        return bad("from/until мають бути ISO із поясом і from < until")
    if (end - start) > timedelta(days=8, hours=1):   # партія = 8 календарних діб 00:00…23:59:59 (Київ): при переході на зимовий час це 8 діб 0:59:59 за UTC, тож запас +1 год; місяць/помилка плану відхиляються
        return bad("вікно акції довше 8 діб (партії акцій — одна доба; запас до 8 діб лишено для старого формату)")
    if not (start <= now <= end):
        return None   # поза вікном цього тижня: ігноруємо
    items = {}
    for pid, v in raw["items"].items():
        if not isinstance(v, dict):
            return bad(f"позиція {pid}")
        old, new = v.get("old"), v.get("new")
        if not all(isinstance(x, int) and not isinstance(x, bool) and x > 0 for x in (old, new)):
            return bad(f"позиція {pid}: old/new не цілі додатні")
        ipct = v.get("pct", pct)          # відсоток товару (з 16.10 випадковий 5–15%); нема — відсоток партії
        if not isinstance(ipct, int) or isinstance(ipct, bool) or not (1 <= ipct <= 50):
            return bad(f"позиція {pid}: pct={ipct!r}")
        if new != -(-old * (100 - ipct) // 100) or new >= old:
            return bad(f"позиція {pid}: new={new} ≠ ceil(old×{100 - ipct}/100) (old={old})")
        items[str(pid)] = (old, new)
    if not items:
        return None
    return {"week": str(raw.get("week") or ""), "pct": pct, "from": start, "until": end, "items": items}


def load_sale_week(now=None, path=None):
    """Дійсний набір акції на ЗАРАЗ або None. Файл — {"weeks":[партія, партія, …]} (Code-Agent з 15.10: ЩОДНЯ о 18:00 дописує партію на 8 діб; одночасно діє до ~8 партій = «постійна акція»); плоский формат (одна партія без weeks) теж читаємо.
    Діють усі партії, у чиє вікно from…until потрапляє «зараз», результат — їх об'єднання (items; until_by_id = кінець партії кожного товару); прострочені/майбутні/зламані ігноруємо (свіжості файлу не довіряємо). Нема файлу — тихо None; зламаний файл — None із попередженням."""
    path = path or SALE_FILE
    now = now or _sale_now() or datetime.now(timezone.utc)
    try:
        with open(path, encoding="utf-8-sig") as f:
            raw = json.load(f)
    except FileNotFoundError:
        return None
    except (OSError, ValueError, UnicodeDecodeError) as e:
        print(f"[build] ⚠ sale_week.json НЕ прочитано ({type(e).__name__}: {e}) — акції нема", file=sys.stderr)
        return None
    if isinstance(raw, dict) and "weeks" in raw:
        weeks = raw["weeks"]
        if not isinstance(weeks, list):
            print("[build] ⚠ sale_week.json: weeks має бути списком — акції нема", file=sys.stderr)
            return None
    else:
        weeks = [raw]
    merged = None
    for w in weeks:                       # ПОСТІЙНА акція: діють УСІ партії, чиє вікно містить «зараз» (щодня нова партія на 8 днів); об'єднуємо
        got = _parse_sale_week(w, now)
        if not got:
            continue
        if merged is None:
            merged = {"week": got["week"], "pct": got["pct"], "from": got["from"], "until": got["until"], "items": {}, "until_by_id": {}}

        merged["from"] = min(merged["from"], got["from"])
        merged["until"] = max(merged["until"], got["until"])
        for pid, v in got["items"].items():
            if pid not in merged["items"]:          # той самий товар у двох партіях (не мало б бути): діє перша, кінець — за нею
                merged["items"][pid] = v
                merged["until_by_id"][pid] = got["until"]
    return merged


def apply_sale(pid, price, sale, cost):
    """(ціна, стара ціна|None): акційна ціна лише коли поточна ціна сайту == old, new < price і внесок після знижки ≥ SALE_RESERVE_UAH (невідома собівартість — без знижки)."""
    if not sale or str(pid) not in sale["items"]:
        return price, None
    old, new = sale["items"][str(pid)]
    if price != old or not (0 < new < price) or not cost or cost <= 0:
        return price, None
    margin = new - cost - new * cp.PAYMENT_COMMISSION.get("site", 0.0) - SITE_FULFILL_PER_ORDER
    if round(margin, 2) < SALE_RESERVE_UAH:   # до копійок: похибка float не має відкидати внесок «рівно 25,00»
        return price, None
    return new, old


_WEEKDAY_GEN = ("понеділка", "вівторка", "середи", "четверга", "пʼятниці", "суботи", "неділі")


def _sale_until_of(pid):
    if not SALE_ACTIVE:
        return None
    return (SALE_ACTIVE.get("until_by_id") or {}).get(str(pid), SALE_ACTIVE["until"]) if pid is not None else SALE_ACTIVE["until"]


def sale_until_label(pid=None):
    """«22.10 23:59» (за поясом з файлу): коли закінчується знижка товару pid (без pid — найпізніша партія)."""
    u = _sale_until_of(pid)
    return f"{u:%d.%m} {u:%H:%M}" if u else ""


def sale_badge_html(p) -> str:
    if not p.get("old_price"):
        return ""
    return f'<span class="sale-badge">−{max(1, round(100 * (p["old_price"] - p["price"]) / p["old_price"]))}%</span>'      # реальний відсоток товару з його цін (знижка у кожного своя)


def sale_until_html(pid=None) -> str:
    """Рядок «Акція до 22.10 23:59» + зворотний відлік (JS заповнює [data-sale-until]) для товару pid; без pid або без знижки — порожньо (на сторінці акцій кінець у кожного товару свій)."""
    u = _sale_until_of(pid)
    if not u:
        return ""
    return (f'<div class="sale-until">Акція до <b>{sale_until_label(pid)}</b><span class="sale-left" data-sale-until="{esc(u.isoformat())}"></span></div>')


def index_entries(prods):
    """Записи index.json (клієнтський пошук/сорт, site_order_api): pr — ціна, за якою ПРОДАЄМО (акційна під час «Знижки тижня»), op — стара ціна акції (лише інформація: замовлення її ігнорує)."""
    return [{"id": p["id"], **({"u": public_slug(p["id"])} if public_slug(p["id"]) != p["id"] else {}), "n": p["name"], "pr": p["price"], "p": p["photo"],
             "c": p["category"], "s": 1 if p["stock"] > 0 else 0, **({"op": p["old_price"]} if p.get("old_price") else {}),
             **({"rt": 1, "ix": 0 if p.get("noindex") else 1} if p.get("rt") else {}), **({"sup": "kv"} if p.get("kv") else {})} for p in prods]   # rt/ix — для фідів Google/Meta/Bing (Код): RoyalToys лише з ix=1


def tile(p, cls="", eager=False, prio=False, badge=False):
    """Картка товару. cls — додатковий клас (напр. «slide» у каруселі); eager=True — без loading=lazy (перші слайди каруселі = кандидат на LCP)."""
    # Бейдж «Остання штука» (SMM 21.10; було «Залишилось N шт» при 1–3, SMM 12.10): лише коли залишок Toysi рівно 1 (ТОЧНЕ число з <ostatok>, перевірено на VPS: діапазонів «1…5» у цій зоні немає; значення береться на момент збірки, раз на ~2 год).
    # Без вигаданих «останній у місті»/таймерів. Де є бейдж — зелений «У наявності» не дублюємо.
    # Лише там, де tile(badge=True): карусель головної, /nabory.html, /ig.html (рішення SMM 12.10: залишок 1–3 у 12 448 з ~21 тис. позицій — у каталозі/категоріях/«схожих» бейдж був би на більшості карток
    # і перестав би бути правдивим сигналом). Не в каталозі, категоріях, «схожих», пошуку.
    low = badge and 0 < p["stock"] <= STOCK_BADGE_MAX
    badge = '<span class="stock-badge">Остання штука</span>' if low else ""
    av = "" if low else ('<span class="av">У наявності</span>' if p["stock"] > 0 else '<span class="av oos">Немає</span>')
    md = '<span class="md-badge">Уцінка</span>' if _is_markdown_item(p) else ""
    sb = sale_badge_html(p)
    price_html = (f'<span class="pr pr-sale">{p["price"]} ₴</span><s class="pr-old">{p["old_price"]} ₴</s>' if p.get("old_price") else f'<span class="pr">{p["price"]} ₴</span>')
    lazy = (' fetchpriority="high"' if prio else "") if eager else ' loading="lazy"'
    img = (f'<img src="{esc(p["photo"])}" alt="{esc(p["name"])}" width="400" height="400"{lazy}>'   # SEO 18.10: width/height на всіх <img>; розмір оригіналу невідомий, кадр завжди 1:1 (.card .ph{aspect-ratio:1/1} + img 100%×100% object-fit:contain) — атрибути лише резервують пропорцію
           if p["photo"] else '<div class="ph"></div>')
    klass = ("card " + cls) if cls else "card"
    return (
      f'<a class="{klass}" href="{esc(product_file(p["id"]))}">'
      f'<div class="ph">{img}{badge}{md}{sb}</div>'
      f'<div class="info"><div class="nm">{esc(p["name"])}</div>'
      f'<div class="foot">{price_html}{av}</div></div></a>'
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


# ── Категорії, які Google відхиляє за політиками (SEO 22.10; власник: «приберай, якщо зайве») ──────────────────────────────────────────────────────────────────────
# «Азартні» (звичайні настільні ігри: лото, карти, Sequence — Google читав як «азартні ігри») і «Арбалети і луки» (іграшкова зброя — політика «зброя»; зброю 07.10 уже прибрали). Сторінка категорії ЖИВА (прямий адрес),
# але noindex,follow і поза sitemap-categories/priority, хабом /categories.html, групами/сайдбаром, чипами, «Схожими категоріями», фільтром категорій. Значення: чи ховати й ТОВАРИ
# (True — noindex і поза sitemap-products; False — товари звичайні, лишаються в sitemap). Ключ — casefold назви категорії Toysi.
NOINDEX_CATEGORIES = {"азартні": False, "арбалети і луки": True}


NOINDEX_CATEGORIES_NAMES = ["Азартні", "Арбалети і луки"]   # відображувані назви (для фільтра категорій у браузері; casefold-ключі — NOINDEX_CATEGORIES)


def category_hidden(name) -> bool:
    return str(name or "").strip().casefold() in NOINDEX_CATEGORIES


def product_hidden(p) -> bool:
    """Товар категорії з NOINDEX_CATEGORIES[...] = True: noindex і поза sitemap."""
    return NOINDEX_CATEGORIES.get(str(p.get("category") or "").strip().casefold()) is True


# ── Другий постачальник (пілот RoyalToys, 19.10.2026; дані й замовлення — Код, сайт — тут) ─────────────────────────────────────────
# <корінь репо>/supplier_rt.json (НЕ в site/): {"updated": ISO, "items": {"rt-<id>": {name, description, vendor, vendor_code, category (рос., назва RoyalToys), images[], price, cost, stock}}}
# site/rt_category_map.json: {назва категорії RoyalToys → наша категорія сайту}. Невідома категорія = картка НЕ публікується (голосне попередження), а не «Інше».
# Картки з'являються лише коли онлайн-оплата ввімкнена (LIQPAY_LIVE): RoyalToys працює лише за передоплатою, без картки такий товар замовити не можна.
# УВАГА (Код 19.10, витік опту): файл містить «cost» (опт RoyalToys) → лежить у КОРЕНІ репо, ПОЗА site/ (DocumentRoot Apache = site/, файл віддавався б публічно); у .gitignore; шлях можна задати змінною SUPPLIER_RT_FILE.
SUPPLIER_RT_FILE = os.environ.get("SUPPLIER_RT_FILE") or os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "supplier_rt.json")
RT_CATEGORY_MAP_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "rt_category_map.json")
SUPPLIER_RT_MAX_AGE_DAYS = 3
# Точкова заміна МЕРТВОГО головного фото Toysi на живу адресу з pictures (HEAD усіх 32 264 карток сайту 20.10.2026: 404 лише у двох; SEO: «підхопи живі /p/3/ і /p/2/»). Жива альтернатива перевірена HEAD=200.
PHOTO_OVERRIDES = {"265377": "https://toysi.ua/p/3/enl_265377.jpg", "255663": "https://toysi.ua/p/2/enl_255663.jpg"}
RT_MIN_DESC_INDEX = 50
_RT_ID_RX = re.compile(r"rt-[0-9A-Za-z_]+")
_RT_COD_SENTENCE_RX = re.compile(r"[^.!?\n]*(?:накладен|при отриманні|післяплат)[^.!?\n]*[.!?]?", re.I)   # у картках rt- не обіцяємо оплату при отриманні


_BROKEN_NUM_ENTITY_RX = re.compile(r"&([0-9]{2,5});")   # «110&730;» = «110&#730;» без «#» (SEO 10.11, y45563): без «#» браузер показує сирий текст


def _supplier_text_plain(text):
    """Назва/бренд постачальника: HTML-сутності з фіду («м&#39;яконабивна», «Build&amp;Play») розкодовуються ОДИН раз, бо далі назва проходить esc() — інакше покупець бачить «&#39;»/«&amp;» (SEO 10.11: y22528, y24014). Без «&» — рядок як був."""
    return html.unescape(text) if isinstance(text, str) and "&" in text else text


def _rt_clean_description(text):
    t = _RT_COD_SENTENCE_RX.sub("", str(text or "")).strip()
    return re.sub(r"[ \t]{2,}", " ", t)


def _load_supplier_rt(now=None):
    """{id: елемент у форматі Toysi} для відібраних карток RoyalToys. Нема файлу/LIQPAY не live/файл застарілий — порожньо; поламаний — ГОЛОСНО (stderr), збірка не падає."""
    try:
        with open(SUPPLIER_RT_FILE, encoding="utf-8-sig") as f:
            raw = json.load(f)
    except FileNotFoundError:
        return {}
    except (OSError, ValueError, RecursionError) as e:
        print(f"[build] ⚠ supplier_rt.json НЕ прочитано ({type(e).__name__}: {e}) — карток RoyalToys не буде", file=sys.stderr)
        return {}
    items = raw.get("items") if isinstance(raw, dict) else None
    if not isinstance(items, dict):
        print("[build] ⚠ supplier_rt.json: очікується {\"updated\":…, \"items\":{…}} — карток RoyalToys не буде", file=sys.stderr)
        return {}
    if not LIQPAY_LIVE:
        print("[build] ⚠ supplier_rt.json: онлайн-оплата не ввімкнена (SITE_LIQPAY_LIVE) — картки RoyalToys не публікуємо (лише передоплата)", file=sys.stderr)
        return {}
    try:
        upd = datetime.fromisoformat(str(raw.get("updated")).replace("Z", "+00:00"))
        if upd.tzinfo is None:
            upd = upd.replace(tzinfo=timezone.utc)
        age = ((now or datetime.now(timezone.utc)) - upd).total_seconds() / 86400.0
    except (ValueError, TypeError):
        print("[build] ⚠ supplier_rt.json: нема коректного «updated» — карток RoyalToys не буде", file=sys.stderr)
        return {}
    if age > SUPPLIER_RT_MAX_AGE_DAYS:
        print(f"[build] ⚠ supplier_rt.json застарів ({age:.1f} діб > {SUPPLIER_RT_MAX_AGE_DAYS}) — карток RoyalToys не буде (ціни/залишки не свіжі)", file=sys.stderr)
        return {}
    try:
        with open(RT_CATEGORY_MAP_FILE, encoding="utf-8-sig") as f:
            cmap = json.load(f)
    except (OSError, ValueError, RecursionError):
        cmap = {}
    if not isinstance(cmap, dict):
        cmap = {}
    out, skipped = {}, {}
    for k, v in items.items():
        why = None
        if not isinstance(k, str) or not _RT_ID_RX.fullmatch(k) or not isinstance(v, dict):
            why = "id"
        else:
            imgs = [u for u in (v.get("images") or []) if isinstance(u, str) and u.startswith("https://")]
            cat_name = cmap.get(str(v.get("category") or "").strip())
            try:
                price, stock = int(v.get("price")), int(v.get("stock"))
            except (TypeError, ValueError):
                price, stock = 0, -1
            if not str(v.get("name") or "").strip():
                why = "name"
            elif not imgs:
                why = "images"
            elif price <= 0 or stock < 0:
                why = "price/stock"
            elif not isinstance(cat_name, str) or not cat_name.strip():
                why = "category:" + str(v.get("category"))[:40]
        if why:
            skipped.setdefault(why, []).append(str(k)[:30])
            continue
        try:
            rt_cost = int(v.get("cost") or 0)
        except (TypeError, ValueError):
            rt_cost = 0
        _desc = _BROKEN_NUM_ENTITY_RX.sub(lambda m: "&#" + m.group(1) + ";", _rt_clean_description(v.get("description")))   # RT: опис вставляється як HTML — «&730;» стає «&#730;» (˚)
        out[k] = {"id": k, "name": _supplier_text_plain(str(v["name"]).strip()), "description": _desc, "vendor": _supplier_text_plain(str(v.get("vendor") or "").strip()),
                  "vendor_code": str(v.get("vendor_code") or "").strip(), "category_name": cat_name.strip(), "pictures": imgs, "stock": stock,
                  "site_price": price, "rt_cost": rt_cost, "supplier": "royaltoys", "price": price,
                  # хвилі відкриття до Google (SEO 19.10): "index": false → noindex,follow і поза sitemap; нема поля → true (пілот); опис <50 знаків (тонка картка) → noindex завжди
                  "rt_noindex": v.get("index") is False or len(re.sub(r"<[^>]+>", "", _desc).strip()) < RT_MIN_DESC_INDEX,
                  "dup_of": str(v.get("dup_of")) if re.fullmatch(r"[0-9]{1,12}", str(v.get("dup_of") or "")) else "",
                  "kv_dup_of": str(v.get("kv_dup_of")) if re.fullmatch(r"kv-[0-9]{1,12}", str(v.get("kv_dup_of") or "")) else ""}   # збіг із товаром Toysi (рішення власника 20.10, варіант 1 «резерв»)
    if skipped:
        print("[build] ⚠ supplier_rt.json: не опубліковано " + ", ".join(f"{len(ids)}× {why} ({', '.join(ids[:3])}…)" for why, ids in skipped.items()), file=sys.stderr)
    print(f"[build] RoyalToys: {len(out)} карток із supplier_rt.json")
    return out


# ── Третій постачальник (KIDDISVIT, B2B API; рішення власника 17.11.2026: «додаєш їхні товари у всі фіди, подивимось як вони працюють із замовленнями»; дані й замовлення — Код, сайт — тут) ──
# <корінь репо>/supplier_kv.json (НЕ в site/: там "cost" — наша закупівля): {"updated": ISO, "catalog_n": N, "items": {"kv-<productID>": {name (укр.), description, vendor, vendor_code (=sku), barcode (цифри), category (листок), category_path[…],
# images[https://plutustoys.com.ua/img/kv/kv-<id>-N.jpg], price (закрита формула сайту — НЕ перераховуємо), cost, rrp, stock (склад Dnepr, кап 100), dims_cm, weight_kg, index (false до хвилі SEO)}}}.
# Картки з'являються ЛИШЕ за воротами SITE_KV_LIVE=1 (за замовчуванням вимкнено) І коли онлайн-оплата live (kv- — лише передоплата, як rt-). Файл старший за 3 доби — карток нема.
# site/kv_category_map.json: {"paths": {«Кореневий > … > листок» → категорія сайту}, "brands": {бренд (casefold) → категорія}}: їхнє дерево хибне місцями, тож бренд має пріоритет. Шлях без запису = картка НЕ публікується (голосне попередження), а не «Інше».
# «index» не True (false або поля нема) → noindex,follow і поза sitemap (хвилі відкриття до Google — лише за словом власника/SEO). Уцінка `kvu-<id>` цим завантажувачем НЕ приймається (окреме рішення).
SITE_KV_LIVE = os.environ.get("SITE_KV_LIVE") == "1"
KV_DESC_MAX_CHARS = 20000   # опис постачальника довший — обрізаємо до очищення (захист від патологічних входів)
# Ім'я постачальника/внутрішній id не мають потрапити в назву, бренд, код, опис (аудит #1694). Шукаємо в «згорнутому» рядку: кирилиця-двійники → латиниця (кіддісвіт/Кiддiсвiт/Kiddisvit → kiddisvit), розділювачі в імені
# (Kiddi-Svit, kiddi_svit, «Кідді Світ») і id з розділювачем (kv-1, kv_1, «KV 1», kvu-2) теж ловимо; «KIDDI SMART» — окремий бренд, не збігається. Згортка 1:1 за довжиною — вирізаємо за позиціями з оригіналу.
_KV_FOLD = str.maketrans({"і": "i", "ї": "i", "к": "k", "с": "s", "в": "v", "т": "t", "и": "i", "д": "d", "ы": "i", "у": "y", "й": "y"})
_KV_NAME_RX = re.compile(r"kidd?[iy][\W_]*svit")   # ім'я постачальника: після NFKC/згортки; розділювач — будь-які не-літери/цифри (Kiddi-Svit, kiddi.svit, «Кідді Світ», nbsp, ‑)
_KV_ID_LEAK_RX = re.compile(r"(?<![a-z0-9])kvu?[-_][0-9]+")   # внутрішній id: лише з дефісом/підкресленням і лише латиницею (звичайне «KV 250», «кв 3 м» не чіпаємо)
_KV_ALLOWED_TAG_RX = re.compile(r"&lt;(/?)(br|p|b|strong|em|ul|ol|li)\s*/?&gt;", re.I)
_KV_TRANSPARENT_TAG_RX = re.compile(r"</?(?:p|b|strong|em|ul|ol|li|br)\s*/?>", re.I)   # дозволені теги опису не розривають ім'я («Kiddi<b>svit</b>»)


def _kv_norm(s, transparent_tags=False):
    """(рядок для пошуку, мапа позицій → індекс в оригіналі): NFKC, без невидимих (Cf: ZWSP, м'який перенос, word joiner), нижній регістр, кирилиця-двійники → латиниця; за потреби дозволені теги прозорі."""
    s = str(s or "")
    skip = set()
    if transparent_tags:
        for m in _KV_TRANSPARENT_TAG_RX.finditer(s):
            skip.update(range(m.start(), m.end()))
    out, idx = [], []
    for i, ch in enumerate(s):
        if i in skip or unicodedata.category(ch) == "Cf":
            continue
        for c2 in unicodedata.normalize("NFKC", ch):
            out.append(c2.lower().translate(_KV_FOLD))
            idx.append(i)
    return "".join(out), idx


def _kv_leak_spans(s, transparent_tags=False):
    """Проміжки [a, b) в ОРИГІНАЛЬНОМУ рядку, що містять ім'я постачальника або внутрішній id kv-/kvu-."""
    norm, idx = _kv_norm(s, transparent_tags)
    spans = []
    for rx in (_KV_NAME_RX, _KV_ID_LEAK_RX):
        for m in rx.finditer(norm):
            spans.append((idx[m.start()], idx[m.end() - 1] + 1))
    return sorted(spans)


def _kv_leaks(s):
    return bool(_kv_leak_spans(s))


def _kv_leak_cut(s, transparent_tags=False):
    s = str(s or "")
    cut = []
    for a, b in _kv_leak_spans(s, transparent_tags):   # злиття перекритих проміжків
        if cut and a <= cut[-1][1]:
            cut[-1] = (cut[-1][0], max(cut[-1][1], b))
        else:
            cut.append((a, b))
    for a, b in reversed(cut):
        s = s[:a] + s[b:]
    return s


def _kv_strip_cf(s):
    """Невидимі символи (Cf: ZWSP, м'який перенос, word joiner…) із назви/бренду постачальника — назавжди."""
    return "".join(ch for ch in str(s or "") if unicodedata.category(ch) != "Cf")


def _kv_strict_int(x):
    """Ціле з файлу постачальника: int, ціле float (959.0) або рядок із цифр; bool, дріб (959.9), «1 200» — None (ціна закрита формулою — тихо не округлюємо; аудит #1694)."""
    if isinstance(x, bool):
        return None
    if isinstance(x, int):
        return x
    if isinstance(x, float):
        return int(x) if x.is_integer() else None
    if isinstance(x, str) and re.fullmatch(r"[0-9]{1,9}", x.strip()):
        return int(x.strip())
    return None


def _kv_clean_cod(text):
    """Те саме, що _rt_clean_description (у картках постачальника не обіцяємо оплату при отриманні: прибираємо речення з «накладен/при отриманні/післяплат»), але за ЛІНІЙНИЙ час: ділимо на речення, а не регексом [^.!?]*…[^.!?]*."""
    pieces = re.split(r"(?<=[.!?\n])", str(text or ""))
    t = "".join(x for x in pieces if not re.search(r"накладен|при отриманні|післяплат", x, re.I)).strip()
    return re.sub(r"[ \t]{2,}", " ", t)


def _kv_safe_html(text):
    """Опис постачальника → безпечний HTML: усе екранується, лишаються лише <br>/<p>/<b>/<strong>/<em>/<ul>/<ol>/<li> БЕЗ атрибутів (новий зовнішній канал B2B API; опис вставляється в сторінку як HTML — stored XSS, аудит #1694).
    «&730;» (без #) спершу лагодиться до «&#730;» (SEO 10.11), потім сутності розкодовуються і екранування вже коректне."""
    t = _BROKEN_NUM_ENTITY_RX.sub(lambda m: "&#" + m.group(1) + ";", _kv_clean_cod(str(text or "")[:KV_DESC_MAX_CHARS]))   # ліміт довжини + лінійне очищення (RT-регекс квадратичний на довгих рядках без крапок, аудит #1694)
    t = _kv_leak_cut(html.unescape(t), transparent_tags=True)   # ім'я постачальника/id — з тексту (після розкодування сутностей; дозволені теги не розривають ім'я)
    t = html.escape(t, quote=False)
    t = _KV_ALLOWED_TAG_RX.sub(lambda m: "<" + m.group(1) + m.group(2).lower() + ">", t)
    # незакриті дозволені теги закриваємо (інакше <ul><li>… ламає верстку решти блоку опису); зайві закривні — прибираємо
    stack, parts = [], []
    for piece in re.split(r"(</?(?:p|b|strong|em|ul|ol|li)>)", t):
        m = re.fullmatch(r"<(/?)(p|b|strong|em|ul|ol|li)>", piece)
        if not m:
            parts.append(piece)
        elif not m.group(1):
            stack.append(m.group(2))
            parts.append(piece)
        elif m.group(2) in stack:
            while stack:
                top = stack.pop()
                parts.append(f"</{top}>")
                if top == m.group(2):
                    break
    return "".join(parts) + "".join(f"</{x}>" for x in reversed(stack))
SUPPLIER_KV_FILE = os.environ.get("SUPPLIER_KV_FILE") or os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "supplier_kv.json")
KV_CATEGORY_MAP_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "kv_category_map.json")
SUPPLIER_KV_MAX_AGE_DAYS = 3


def _load_supplier_kv(now=None):
    """{id: елемент у форматі Toysi/RT} для карток KIDDISVIT. Ворота SITE_KV_LIVE не відкриті / нема файлу / LIQPAY не live / файл застарілий — порожньо; поламаний — ГОЛОСНО (stderr), збірка не падає."""
    if not SITE_KV_LIVE:
        return {}
    try:
        with open(SUPPLIER_KV_FILE, encoding="utf-8-sig") as f:
            raw = json.load(f)
    except FileNotFoundError:
        print("[build] ⚠ SITE_KV_LIVE=1, але supplier_kv.json нема — карток KIDDISVIT не буде", file=sys.stderr)
        return {}
    except (OSError, ValueError, RecursionError) as e:
        print(f"[build] ⚠ supplier_kv.json НЕ прочитано ({type(e).__name__}: {e}) — карток KIDDISVIT не буде", file=sys.stderr)
        return {}
    items = raw.get("items") if isinstance(raw, dict) else None
    if not isinstance(items, dict):
        print("[build] ⚠ supplier_kv.json: очікується {\"updated\":…, \"items\":{…}} — карток KIDDISVIT не буде", file=sys.stderr)
        return {}
    if not LIQPAY_LIVE:
        print("[build] ⚠ supplier_kv.json: онлайн-оплата не ввімкнена (SITE_LIQPAY_LIVE) — картки KIDDISVIT не публікуємо (лише передоплата)", file=sys.stderr)
        return {}
    try:
        upd = datetime.fromisoformat(str(raw.get("updated")).replace("Z", "+00:00"))
        if upd.tzinfo is None:
            upd = upd.replace(tzinfo=timezone.utc)
        age = ((now or datetime.now(timezone.utc)) - upd).total_seconds() / 86400.0
    except (ValueError, TypeError):
        print("[build] ⚠ supplier_kv.json: нема коректного «updated» — карток KIDDISVIT не буде", file=sys.stderr)
        return {}
    if age > SUPPLIER_KV_MAX_AGE_DAYS:
        print(f"[build] ⚠ supplier_kv.json застарів ({age:.1f} діб > {SUPPLIER_KV_MAX_AGE_DAYS}) — карток KIDDISVIT не буде (ціни/залишки не свіжі)", file=sys.stderr)
        return {}
    try:
        with open(KV_CATEGORY_MAP_FILE, encoding="utf-8-sig") as f:
            cmap = json.load(f)
    except (OSError, ValueError, RecursionError):
        cmap = {}
    paths = cmap.get("paths") if isinstance(cmap, dict) and isinstance(cmap.get("paths"), dict) else {}
    brands = {str(b).strip().casefold(): c for b, c in (cmap.get("brands") or {}).items()} if isinstance(cmap, dict) and isinstance(cmap.get("brands"), dict) else {}
    out, skipped = {}, {}
    for k, v in items.items():
        why = None
        if not isinstance(k, str) or not _KV_ID_RX.fullmatch(k) or not isinstance(v, dict):
            why = "id"
        else:
            imgs = [u for u in (v.get("images") or []) if isinstance(u, str) and (lambda m: bool(m) and m.group(1) == k)(_PUBLIC_IMG_KV_RX.fullmatch(u))]   # лише НАШІ файли цього id (https://plutustoys.com.ua/img/kv/<id>-N.jpg): чужий хост/чужий id не публікуємо (аудит #1694)
            vendor_name = _kv_strip_cf(_supplier_text_plain(str(v.get("vendor") or "").strip()))
            if _kv_leaks(vendor_name):
                vendor_name = ""
            cpath = [str(x).strip() for x in (v.get("category_path") or []) if str(x).strip()] if isinstance(v.get("category_path"), list) else []
            cat_name = brands.get(vendor_name.casefold()) or paths.get(" > ".join(cpath) if cpath else str(v.get("category") or "").strip())
            price, stock = _kv_strict_int(v.get("price")), _kv_strict_int(v.get("stock"))
            if price is None or stock is None:
                price, stock = 0, -1
            if not str(v.get("name") or "").strip():
                why = "name"
            elif _kv_leaks(_supplier_text_plain(str(v.get("name")))):
                why = "leak"   # у назві ім'я постачальника/внутрішній id — не публікуємо
            elif not imgs:
                why = "images"
            elif price <= 0 or stock < 0:
                why = "price/stock"
            elif not isinstance(cat_name, str) or not cat_name.strip():
                why = "category:" + (" > ".join(cpath) if cpath else str(v.get("category")))[:60]
        if why:
            skipped.setdefault(why, []).append(str(k)[:30])
            continue
        try:
            kv_cost = round(float(v.get("cost") or 0), 2)
        except (TypeError, ValueError):
            kv_cost = 0.0
        _desc = _kv_safe_html(v.get("description"))
        _vcode = str(v.get("vendor_code") or "").strip()
        if _kv_leaks(_vcode):
            _vcode = ""
        _bc = re.sub(r"\D", "", str(v.get("barcode") or ""))
        out[k] = {"id": k, "name": _kv_strip_cf(_supplier_text_plain(str(v["name"]).strip())), "description": _desc, "vendor": vendor_name,
                  "vendor_code": _vcode, "category_name": cat_name.strip(), "pictures": imgs, "stock": stock,
                  "site_price": price, "rt_cost": kv_cost, "supplier": "kiddisvit", "price": price, "barcode": _bc,
                  "kv_src_path": " > ".join(str(x).strip() for x in (v.get("source_category_path") or v.get("category_path") or []) if str(x).strip()) if isinstance(v.get("source_category_path") or v.get("category_path"), list) else "",
                  # «index» не True (false або поля нема) → noindex,follow і поза sitemap; опис <50 знаків (тонка картка) → noindex завжди
                  "rt_noindex": v.get("index") is not True or len(re.sub(r"<[^>]+>", "", _desc).strip()) < RT_MIN_DESC_INDEX,
                  "dup_of": str(v.get("dup_of")) if re.fullmatch(r"[0-9]{1,12}", str(v.get("dup_of") or "")) else "", "kv_dup_of": ""}   # збіг із товаром Toysi (Код 17.11, kv_dup_map.json): kv — тінь, поки Toysi в наявності
    if skipped:
        print("[build] ⚠ supplier_kv.json: не опубліковано " + ", ".join(f"{len(ids)}× {why} ({', '.join(ids[:3])}…)" for why, ids in skipped.items()), file=sys.stderr)
    print(f"[build] KIDDISVIT: {len(out)} карток із supplier_kv.json")
    return out


def build():
    global SALE_ACTIVE
    _lastmod_reset()
    SALE_ACTIVE = load_sale_week() if SALE_WEEK_ENABLED else None
    if SALE_ACTIVE:
        print(f"[build] акція {SALE_ACTIVE['week']}: −{SALE_ACTIVE['pct']}%, {len(SALE_ACTIVE['items'])} позицій у файлі, до {SALE_ACTIVE['until'].isoformat()}")
    _load_category_texts()
    _load_category_ru_alt()
    _load_product_texts()
    _load_category_related()
    _load_reviews()
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
    cat = dict(cat)
    _load_category_overrides({str(v.get("category_name") or "").strip() for v in cat.values() if str(v.get("stock") or "0") not in ("0", "")} - {""})   # перенесення товарів між категоріями даними (SEO 23.10)
    cat.update(_load_supplier_kv())   # третій постачальник (KIDDISVIT, Код 17.11): картки «kv-…» за воротами SITE_KV_LIVE=1
    cat.update(_load_supplier_rt())   # другий постачальник (пілот RoyalToys, Код 19.10): картки «rt-…» у ЗАГАЛЬНОМУ каталозі
    prods = []
    # Товари БЕЗ залишку (рішення Консультанта 11.10.2026, GSC: 7 605 адрес «404» з 8 960 непроіндексованих — це самі зниклі зі складу товари):
    # їхня сторінка НЕ зникає, а лишається живою з «Немає в наявності» + schema.org/OutOfStock. Збираємо окремо: у каталог, категорії, пошук
    # (index.json), головну, крос-сел і фіди Google/Meta/Bing (вони читають index.json) ці позиції НЕ потрапляють; замовити їх теж не можна
    # (site_order_api бере ціни лише з index.json).
    oos_prods = []
    oos_skipped_no_price = 0
    _cat_ov_applied = 0
    for it in cat.values():
        is_oos = str(it.get("stock") or "0") in ("0", "")
        pics = it.get("pictures")
        if isinstance(pics, str):
            pics = [pics]
        name = it.get("name") or ""
        if not pics or not name:
            continue
        if is_russian_language_item(name):
            continue   # російськомовний товар (власник 20.10) — і Toysi, і RoyalToys
        if is_forbidden(it):
            continue   # політика «що не продаємо» (forbidden_products, рішення власника 2026-10-07): ножі/зброя; старі сторінки прибере крок 8 build()
        if is_uno_trademark_blocked(name, it.get("vendor") or ""):
            continue   # ТМ «UNO» (претензія Mattel): не продаємо на власному сайті (SEO-замовлення 2026-10-06)
        name = normalize_site_title(name if it.get("supplier") in ("royaltoys", "kiddisvit") else _supplier_text_plain(name))   # Toysi (parser.py: supplier="Toysi"): сутності з фіду («Build &amp; Play», SEO 10.11: 131989, 154061) розкодовуємо до esc(); RT вже розкодовані при завантаженні (подвійний прохід не потрібен). Далі: після всіх фільтрів за оригінальною назвою: регістр назви-капсом (feed_title_norm, лише зміна регістру)
        catname = it.get("category_name") or "Інше"
        _cat_ov = _CATEGORY_OVERRIDES.get(str(it.get("id")))
        if _cat_ov and not str(it.get("id")).startswith(("rt-", "kv-")) and _cat_ov != catname:
            catname = _cat_ov   # перенесення в іншу категорію сайту (category_overrides.json)
            _cat_ov_applied += 1
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
            cost = float(it["rt_cost"]) if it.get("rt_cost") else cp.real_toysi_cost(it)   # rt-: опт RoyalToys із supplier_rt.json (без поля — внесок None, картка не йде на головну/в акції)
        except (ValueError, TypeError):
            cost = 0.0
        _old_price = None
        if SALE_ACTIVE and not is_oos:
            price, _old_price = apply_sale(str(it.get("id")), price, SALE_ACTIVE, cost)   # акційна ціна (перевірки: ціна==old, внесок ≥ запасу)
        contribution = (price - cost - price * cp.PAYMENT_COMMISSION.get("site", 0.0)
                        - SITE_FULFILL_PER_ORDER) if cost > 0 else None
        (oos_prods if is_oos else prods).append({
            "id": str(it.get("id")), "name": name, "price": price, "old_price": _old_price,
            "category": catname,
            "photo": toysi_proxy_photo(PHOTO_OVERRIDES.get(str(it.get("id")), public_photo(str(it.get("id")), pics[0]))), "stock": int(it.get("stock") or 0),
            "desc": it.get("description") or "",
            "contribution": contribution,
            "rt": 1 if str(it.get("id")).startswith(("rt-", "kv-")) else 0, "kv": 1 if str(it.get("id")).startswith("kv-") else 0, "noindex": bool(it.get("rt_noindex")) or NOINDEX_CATEGORIES.get(str(catname or "").strip().casefold()) is True, "dup_of": str(it.get("dup_of") or ""), "kv_dup_of": str(it.get("kv_dup_of") or ""), "src_path": str(it.get("kv_src_path") or ""),
            "vendor": (it.get("vendor") or "").strip(), "vendor_code": (it.get("vendor_code") or "").strip(), "barcode": (it.get("barcode") or "").strip(),   # JSON-LD: brand (запасний), gtin (лише валідний штрихкод)
        })
    if _CATEGORY_OVERRIDES:
        print(f"[build] перенесення між категоріями: у файлі {len(_CATEGORY_OVERRIDES)}, застосовано до карток {_cat_ov_applied}")
    # RoyalToys-дублі Toysi, варіант 1 «резерв» (власник 20.10): rt-картка з dup_of на товар Toysi, що Є В НАЯВНОСТІ, — «тінь»: покупцям не показуємо (нема в каталозі/пошуку/sitemap/
    # блоках), сторінка жива, але noindex і canonical на сторінку Toysi. Щойно товар Toysi вичерпався (його нема в prods) — rt-картка стає звичайною.
    # 17.11.2026 (Код: kv_dup_map/rt_kv_dup_map; SEO: canonical БЕЗ ланцюжків — завжди на кінцеву ЖИВУ картку): (1) rt- і kv- картка з dup_of на Toysi, що в наявності → тінь Toysi (пріоритет Toysi і в rt з обома dup_of/kv_dup_of);
    # (2) rt- картка з kv_dup_of на kv-картку, що в наявності й сама НЕ тінь → тінь kv; якщо та kv — тінь Toysi, rt → одразу на Toysi (ланцюжок rt → kv → Toysi стискається). Ціль не опублікована/вичерпана → тінь не ставимо.
    _toysi_live = {q["id"] for q in prods if not q.get("rt")}
    _by_id = {q["id"]: q for q in prods}
    shadows = []
    for q in prods:
        if q.get("rt") and q.get("dup_of") in _toysi_live:
            q["shadow_to"] = q["dup_of"]
            shadows.append(q)
    _toysi_shadow_ids = {q["id"] for q in shadows}
    _rt_kv = 0
    for q in prods:
        if q.get("rt") and not q.get("kv") and q["id"] not in _toysi_shadow_ids and str(q.get("kv_dup_of") or "") in _by_id and _by_id[q["kv_dup_of"]].get("kv"):
            _tgt = _by_id[q["kv_dup_of"]]
            if _tgt["id"] in _toysi_shadow_ids:
                q["shadow_to"] = _tgt["shadow_to"]   # ланцюжок rt → kv → Toysi: canonical одразу на кінцеву картку (Toysi)
            else:
                q["shadow_to"] = _tgt["id"]
            shadows.append(q)
            _rt_kv += 1
    if shadows:
        _sh_ids = {q["id"] for q in shadows}
        prods[:] = [q for q in prods if q["id"] not in _sh_ids]
        for q in shadows:
            q["shadow"] = True
            q["noindex"] = True
        print(f"[build] Дублі: {len(shadows)} карток-тіней (Toysi/KIDDISVIT є в наявності; з них RT→KIDDISVIT {_rt_kv}) — не показуємо, noindex + canonical на кінцеву картку", file=sys.stderr)
    # Порядок «Рекомендовані» = за ВНЕСКОМ спадно (рішення Консультанта: на перший екран —
    # позиції, що приносять маржу, а не найбільша категорія/найдорожче). Товари з невідомою
    # собівартістю (contribution=None) — у кінець. Тай-брейк: ціна спадно, потім назва (детермінізм).
    def _rank_key(p):
        c = p["contribution"] if p["contribution"] is not None else -1e9
        return (-c, -p["price"], p["name"])
    if SALE_ACTIVE:
        _applied = sum(1 for p in prods if p.get("old_price"))
        print(f"[build] акція: застосовано {_applied} з {len(SALE_ACTIVE['items'])} позицій файлу (решта випала: ціна ≠ old / нема в наявності / внесок < запасу)")
        if _applied < SALE_MIN_APPLIED_ALERT:   # акція ідe, а позицій майже нема (ціни змінились/собівартість зросла): тихо не лишаємо
            try:
                from telegram_notify import send_throttled_alert
                send_throttled_alert("site_sale_few_applied", f"⚠️ site-rebuild: «Знижки» {SALE_ACTIVE['week']}: застосовано лише {_applied} з {len(SALE_ACTIVE['items'])} позицій файлу (ціна ≠ old, нема в наявності або внесок < {SALE_RESERVE_UAH:.0f} ₴).", cooldown_sec=6 * 3600)
            except Exception:  # noqa: BLE001 — алерт best-effort
                pass
    prods.sort(key=_rank_key)
    oos_prods.sort(key=lambda p: p["id"])
    if LIMIT:
        prods = prods[:LIMIT]
        oos_prods = oos_prods[:LIMIT]
    _tc = {}
    for _p in prods:
        if _p.get("rt"):
            continue   # rt — власна формула (RT_TITLE_CODES), не «№id»
        _t = product_title(_p["name"], True); _tc[_t] = _tc.get(_t, 0) + 1
    for _p in oos_prods:
        if _p.get("rt"):
            continue
        _t = product_title(_p["name"], False); _tc[_t] = _tc.get(_t, 0) + 1
    PRODUCT_DUP_TITLES.clear()
    PRODUCT_DUP_TITLES.update(t for t, c in _tc.items() if c > 1)
    if PRODUCT_DUP_TITLES:
        print(f"[build] title: {len(PRODUCT_DUP_TITLES)} збігів після обрізання — у цих карток додано №id", file=sys.stderr)
    _h1, _h1st = assign_product_h1(prods + oos_prods)   # тіні (shadows) сюди не входять: noindex + canonical на Toysi
    PRODUCT_H1.clear(); PRODUCT_H1.update(_h1)
    _h1in = assign_product_h1(prods)[1]   # лише в наявності — та сама база, що в заміру SEO (index.json)
    print(f"[build] h1: {_h1st['groups']} груп з однаковою назвою ({_h1st['cards']} карток; у наявності {_h1in['groups']} груп / {_h1in['cards']} карток) — розведено атрибутом "
          f"{_h1st['attr_cards']} карток у {_h1st['attr_groups']} групах ({', '.join(f'{k} {v}' for k, v in sorted(_h1st['by'].items(), key=lambda kv: (-kv[1], kv[0]))) or '—'}), «№id» {_h1st['id_cards']} карток, "
          f"не розведено {_h1st['unresolved']}", file=sys.stderr)
    _rt_items = [(_p, True) for _p in prods if _p.get("rt")] + [(_p, False) for _p in oos_prods if _p.get("rt")]
    _toysi_titles = {product_title_for(_p, True) for _p in prods if not _p.get("rt")} | {product_title_for(_p, False) for _p in oos_prods if not _p.get("rt")}
    _codes, _fail = assign_rt_title_codes(_rt_items, _toysi_titles)
    RT_TITLE_CODES.clear(); RT_TITLE_CODES.update(_codes)
    RT_TITLE_FAIL[:] = _fail
    if _rt_items:
        print(f"[build] RoyalToys title: {len(_rt_items)} карток, до {len(_codes)} додано артикул для унікальності, без унікального title лишилось {len(_fail)}", file=sys.stderr)
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
    print(f"[build] назви-капсом: нормалізовано {_TITLE_NORM_COUNT[0]} назв (feed_title_norm){' — ⚠ МОДУЛЬ НЕДОСТУПНИЙ' if _feed_normalize_title is None else ''})", file=sys.stderr)

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
    SEARCH_POPULAR[:] = [(c, f"category-{cat_slug[c]}.html") for c in cat_list if not category_hidden(c) and "уцінк" not in c.lower() and "уценк" not in c.lower()][:8]

    nav_list = [c for c in cat_list if not category_hidden(c)]                 # навігація (хаб, групи, чипи, «Схожі», фільтр): без NOINDEX_CATEGORIES
    nav_cats = {c: v for c, v in cats.items() if not category_hidden(c)}
    # крос-сел як ДОПОВНЕННЯ (add-on), НЕ замінник: до 4 ДЕШЕВШИХ товарів з ІНШОЇ
    # категорії. Стара логіка брала ту саму категорію за близькою ціною → на картці
    # виходили 4 копії того самого типу товару за тією ж ціною (замінники), що НЕ
    # піднімає чек (та сама ціна, та сама категорія). Тепер пропонуємо дешеві позиції
    # з інших категорій — класичний add-on до замовлення: додає ПОЗИЦІЮ → росте чек і
    # сукупний внесок. Пул обмежений найдешевшими в наявності (кеп), тож O(n·CAP), не O(n²).
    _ADDON_POOL_CAP = 80
    # Пул add-on НЕ бере уцінку, «розпродаж»-назви й копійчані позиції: «Уцінка. Браслет… 12 ₴» як
    # доповнення до дорогої іграшки виглядає сміттям (SMM 2026-10-06, наслідок PR #614 «каталог повний»).
    _addon_pool = sorted((p for p in prods if _addon_ok(p) and not p.get("rt")),
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

    # сторінки подарунків (SEO 15.11): план (добірки + наявність тексту) визначаємо ДО запису сторінок, щоб футер усіх сторінок знав, чи є хаб
    _gift_plan = gift_plan([p for p in prods if not p.get("rt")])
    GIFT_BUILT[:] = [(s, h, lb) for s, h, lb, _r, _i in _gift_plan]
    _brand_plan = brand_plan(prods)   # сторінки брендів (SEO 17.11) — за воротами SITE_BRAND_PAGES_LIVE=1; план (вже без підсторінок без батька) ДО запису сторінок, щоб футер усіх сторінок знав про хаб
    BRAND_BUILT[:] = [(s, h) for s, h, _par, _items, _t in _brand_plan]
    _brand_slugs = {s for s, _h in BRAND_BUILT}
    # статті про бренди — лише разом зі сторінками брендів: ворота + головний бренд статті (перший у BRAND_FOR_PORADY) справді збудовано (інакше тексти «у каталозі є…» неправдиві)
    _porady = {s: v for s, v in _load_porady().items() if s not in BRAND_FOR_PORADY or BRAND_FOR_PORADY[s][0] in _brand_slugs}
    GIFT_ARTICLES_BUILT[:] = [(s, _porady[s]["title"]) for s in PORADY_SLUGS if s in _porady]
    _coloring_plan = coloring_plan(prods)   # безкоштовні розмальовки (SEO 18.11): будуємо лише сторінки, для яких є текст і ≥COLORING_MIN_SHEETS аркушів (png+pdf у site/colorings/)
    _col_gone = sorted(fn for fn in COLORING_PAGE_FILES if os.path.isfile(os.path.join(OUT, fn)) and fn[:-5] not in {s for s, _h, _sh, _t in _coloring_plan})
    if _col_gone:   # хоч одна сторінка розмальовок була жива в попередній збірці, а тепер її нема в плані (аркуш/текст/файл/кількість зникли) → збірка її ПРИБЕРЕ: алерт (аудит #1707/#1711; сезонна сторінка не має зникати мовчки)
        try:
            from telegram_notify import send_throttled_alert
            send_throttled_alert("site_coloring_vanished", "⚠️ site-rebuild: сторінки розмальовок БУЛИ на сайті, а в цій збірці їх не збудовано: " + ", ".join(_col_gone) + " (аркуш/файл/текст/кількість аркушів). Сторінки прибрано. Причина — у логу збірки («coloring»).", cooldown_sec=6 * 3600)
        except Exception:  # noqa: BLE001 — алерт best-effort
            pass
    COLORING_BUILT[:] = [(s, h) for s, h, _sheets, _t in _coloring_plan]
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
    for p in shadows:   # тіні: сторінка жива (noindex, canonical на Toysi), без блоків схожих/крос-селу
        write_product(p, related=None, cat_slug=cat_slug, similar=[])
    # 2) сторінки категорій (з пагінацією) + 3) повний каталог — збираємо ВСІ записані сторінки
    paged = set()
    SITE_CATS.clear(); SITE_CATS.update(nav_cats)
    SITE_GROUPS[:] = site_groups(nav_cats)
    for g in SITE_GROUPS:
        g["slugs"] = cat_slug
    for c in cat_list:
        paged |= write_catalog(f"Каталог • {c}", rec_order(cats[c]), nav_list, cat_slug, f"category-{cat_slug[c]}.html", active=c)
    prods_rec = rec_order(prods)
    toysi_prods = [p for p in prods if not p.get("rt")]   # головна/каруселі/акції/спец-сторінки/add-on кошика — без RoyalToys (рішення власника ще не прийнято)
    paged |= write_catalog("Каталог іграшок", prods_rec, nav_list, cat_slug, "catalog.html", active=None)
    paged |= write_utsinka(toysi_prods, nav_list, cat_slug)
    paged |= write_aktsii(toysi_prods, nav_list, cat_slug)
    paged |= write_helovin(toysi_prods, nav_list, cat_slug)
    paged |= write_chip_pages(toysi_prods, nav_list, cat_slug)
    # 4) головна
    _cats_toysi = {c: [p for p in v if not p.get("rt")] for c, v in cats.items()}
    write_home(toysi_prods, _cats_toysi, [c for c in nav_list if _cats_toysi.get(c)], cat_slug)
    write_ig(toysi_prods)
    has_nabory = write_nabory(toysi_prods)
    gift_urls = write_gift_pages(toysi_prods, plan=_gift_plan) + write_porady(_porady) + write_brand_pages(_brand_plan) + write_coloring_pages(_coloring_plan, prods)
    # 5) кошик + checkout + сторінка подяки
    write_cart(_cart_addons(toysi_prods))
    write_thanks()
    write_review_page()
    write_404()
    # 5б) індекс усіх категорій (закриває orphan) + сторінки довіри (Про нас/Доставка/…)
    groups = SITE_GROUPS
    paged |= write_groups(groups, nav_cats, cat_slug)
    write_categories_index(nav_list, cat_slug, nav_cats, groups)
    trust = write_trust_pages()
    # 6) індекс пошуку
    # index.json — клієнтський пошук + фільтр/сорт каталогу. c=категорія, s=наявність(1/0).
    idx = index_entries(prods_rec) + [{**e, "dup": 1} for e in index_entries(shadows)]   # тіні — лише в повному index.json (сервер бере ціни); пошук/каталог їх не читають
    _write_text_atomic(os.path.join(OUT, "index.json"), json.dumps(idx, ensure_ascii=False))   # index.json читають кошик і пошук — запис атомарний
    # Сторінки (app.js) читають НЕ повний index.json, а два файли: index-t.json (Toysi, як було) і index-rt.json (RoyalToys, довантажується у фоні) —
    # 20.10: 9,9 тис. карток RoyalToys підняли index.json з 1,0 до 1,5 МБ gzip, і LCP каталогу/категорій у Lighthouse зріс з ≈3 до ≈12 с. Повний index.json лишається для site_order_api (ціни) і фідів.
    _write_text_atomic(os.path.join(OUT, "index-t.json"), json.dumps([x for x in idx if not x.get("rt") and not x.get("dup")], ensure_ascii=False))
    _write_text_atomic(os.path.join(OUT, "index-rt.json"), json.dumps([x for x in idx if x.get("rt") and not x.get("dup")], ensure_ascii=False))
    # 7) SEO: sitemap-індекс + сегменти + robots (lastmod = дата РЕАЛЬНОЇ зміни сторінки зі стану .htlastmod_state.json)
    static_urls = [("categories.html", "0.7", "weekly")] +([("nabory.html", "0.7", "weekly")] if has_nabory else []) + [(f, "0.4", "monthly") for f in trust] + gift_urls
    write_sitemap(prods, paged, static_urls, oos_prods=oos_prods, hidden_files={f"category-{cat_slug[c]}.html" for c in cat_list if category_hidden(c)} | {f for g in SITE_GROUPS for f in g.get("former", [])})   # сторінки-вказівники старих груп — поза sitemap
    write_robots()

    # 8) ПРИБИРАННЯ ЗАСТАРІЛИХ сторінок від попередніх білдів: build пише лише поточний вибір,
    # а старі product-/category-/catalog-*.html лишались у site/ і віддавались із застарілими цінами/сміттям
    # (реально знайдено 84 такі). Видаляємо ті, що не входять у поточний набір (пишемо УСІ актуальні
    # вище ДО цього кроку, тож видаляємо лише справжні залишки). `paged` містить усі сторінки
    # каталогу/категорій (вкл. пагіновані catalog_N/category-slug_N), тож застарілі сторінки
    # пагінації від більшого попереднього білда теж приберуться.
    valid = {product_file(p['id']) for p in prods} | {product_file(p['id']) for p in oos_prods} | {product_file(p['id']) for p in shadows} | paged
    removed = 0
    _gift_files = {u for u, _pr, _cf in gift_urls}
    for fn in os.listdir(OUT):
        if (fn.startswith(("product-", "category-", "catalog", "utsinka", "aktsii", "helovin", "halloween", "podibrka", "group-")) and fn.endswith(".html")
                and fn not in valid):
            try:
                os.remove(os.path.join(OUT, fn))
                removed += 1
            except OSError:
                pass
        elif (fn in GIFT_PAGE_FILES or fn in PORADY_FILES or fn in BRAND_PAGE_FILES or fn in COLORING_PAGE_FILES) and fn not in _gift_files:   # сторінка подарунків, якої ця збірка не будує (мало товарів/нема тексту), не лишається живою
            try:
                os.remove(os.path.join(OUT, fn))
                removed += 1
            except OSError:
                pass

    _remove_stale_tmp(OUT)

    _written_slugs = {m.group(1) for m in (re.fullmatch(r"category-(.+)\.html", f) for f in paged if not re.search(r"_\d+\.html$", f)) if m}
    _unmatched = sorted(set(_CATEGORY_TEXTS) - _written_slugs)
    if _unmatched:
        print(f"[build] ⚠ category_texts.json: slug без сторінки категорії (друкарська помилка? текст не з'явиться): {_unmatched[:10]}", file=sys.stderr)
    _alt_unmatched = sorted(set(_CATEGORY_RU_ALT) - _written_slugs)
    if _alt_unmatched:
        print(f"[build] ⚠ category_ru_alt.json: slug без сторінки категорії (друкарська помилка? рядок не з'явиться): {_alt_unmatched[:10]}", file=sys.stderr)
    _ru_alt_bad = ru_alt_proper_name_issues(_CATEGORY_RU_ALT, cats, cat_slug)
    if _ru_alt_bad:
        print(f"[build] ⚠ category_ru_alt.json: {len(_ru_alt_bad)} власних назв без опори в назвах товарів категорії (сторінка обіцяє чужий товар): "
              + "; ".join(f"{sl}: «{t}» у «{ph}» (товарів {n})" for sl, ph, t, n in _ru_alt_bad[:10]), file=sys.stderr)
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
    _lastmod_flush(keep=_current_html_files())
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

def _catalog_js_grid():
    """Лічильник «Знайдено…» і JS-сітка каталогу (керує app.js initCatalog); hidden до ініціалізації."""
    return (
      '<p id="cat-count" class="pagenote cat-count" hidden></p>'
      '<div id="cat-js" class="grid" hidden></div>'
      '<div id="cat-more-wrap" hidden><button id="cat-more" class="btn ghost">Показати ще</button></div>'
    )


def _catalog_controls(preset=None, group=None, own=None, groups_json=None, others=None, rtonly=False):
    """Панель сортування/фільтрів каталогу (керує app.js initCatalog з index.json).
    hidden до JS-ініціалізації — щоб без JS не показувати неробочі контроли."""
    return (
      f'<div id="cat-controls" class="cat-controls" hidden{(" data-cat=" + chr(34) + esc(preset) + chr(34)) if preset else ""}{(" data-group=" + chr(39) + esc(group) + chr(39)) if group else ""}{(" data-own=" + chr(34) + esc(own) + chr(34)) if own else ""}{(" data-groups=" + chr(39) + esc(groups_json) + chr(39)) if groups_json else ""}{(" data-others=" + chr(39) + esc(others) + chr(39)) if others else ""}{' data-rtonly="1"' if rtonly else ""}>'
        '<select id="cf-sort" aria-label="Сортування">'
          '<option value="rec">Рекомендовані</option>'
          '<option value="cheap">Спершу дешевші</option>'
          '<option value="dear">Спершу дорожчі</option>'
          '<option value="az">За назвою А–Я</option>'
        '</select>'
        '<select id="cf-cat" aria-label="Категорія" data-hide=' + chr(39) + json.dumps(sorted(NOINDEX_CATEGORIES_NAMES), ensure_ascii=False) + chr(39) + '><option value="">Усі категорії</option></select>'
        '<button id="cf-price-toggle" class="cf-price-toggle" type="button" aria-expanded="false" aria-controls="cf-price-panel">Ціна</button>'
        '<span id="cf-price-panel" class="cf-price"><input id="cf-min" type="number" inputmode="numeric" min="0" placeholder="ціна від" aria-label="Ціна від">'
          '<input id="cf-max" type="number" inputmode="numeric" min="0" placeholder="до" aria-label="Ціна до">'
        # Чекбокс «лише в наявності» прибрано (тестувальник 13.10): index.json лишає лише товари В НАЯВНОСТІ (s завжди 1), тож фільтр нічого не міняв. Повернути — коли в index.json з'являться позиції без залишку.
        '<button id="cf-reset" class="cf-reset" type="button">Скинути</button></span>'
      '</div>'
    )

# ── SEO-замовлення 2026-10-10 (п.2–3, фінальні шаблони — SEO_CHANNEL.md): title/description, хлібні крихти, розмітка Product/ItemList ──
def price_valid_until(today=None):
    """priceValidUntil (SEO 15.10): ОСТАННІЙ ДЕНЬ НАСТУПНОГО календарного місяця — змінюється раз на місяць, запас ≥ 1 міс. (було сьогодні+7: збій збірки → прострочене значення = попередження Merchant/rich results)."""
    from datetime import date
    t = today or date.today()
    y, m = (t.year + (t.month == 12), t.month % 12 + 1)      # наступний місяць
    ny, nm = (y + (m == 12), m % 12 + 1)                      # місяць після нього
    return date(ny, nm, 1) - timedelta(days=1)


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


PRODUCT_DUP_TITLES = set()   # title, що збігаються в кількох товарів (після обрізання до TITLE_MAX); заповнює build() — у них додається «№id» (SEO 18.10: 0 дублів title, зокрема серед OOS)


_BRAND_JUNK = frozenset({"-", "—", "–", "немає", "нема", "без бренду", "без бренда", "no brand", "no name", "noname", "не вказано", "інше", "інший", "other", "unknown", "n/a", "китай", "china", "україна", "ukraine"})


def _valid_gtin(code):
    """('gtin13', '4820…') для штрихкоду з 8/12/13/14 цифр і правильною контрольною цифрою (GS1); інакше None — не вигадуємо, не публікуємо сміття."""
    c = re.sub(r"\s", "", str(code or ""))
    if not re.fullmatch(r"\d{8}|\d{12,14}", c) or not any(ch != "0" for ch in c):
        return None
    digits = [int(x) for x in c]
    total = sum(d * (3 if i % 2 == 0 else 1) for i, d in enumerate(reversed(digits[:-1])))
    if (10 - total % 10) % 10 != digits[-1]:
        return None
    return ("gtin" + str(len(c)), c)


# ── Однакові <h1> карток (SEO 06.11: 591 група з ІДЕНТИЧНОЮ назвою, 1 519 карток — різні товари, яким Toysi дала одну назву; title розведено «№id», h1 — ні) ──
# Група = картки, чиї назви збігаються без регістру, розділових знаків, апострофів і латинсько-кириличних двійників («MIC»/«МІС»). Для групи шукаємо ОДНЕ поле з характеристик,
# ПОКАЗАНИХ на сторінці картки (_shown_specs — ті самі ≤6 рядків «<b>Колір:</b> …», що друкує write_product), яке дає СПРАВЖНЄ розрізнення (аудит 06.11):
#   • є в КОЖНОЇ картки групи, не узагальнене («різнобарвний», «мікс», «асорті», «інший»…), не повторює й не суперечить назві (колір при кольорі в назві, «120 ел.» у назві ≠ «150 ел.» у полі, розмір при розмірі в назві);
#   • значення попарно різні ПІСЛЯ нормалізації (регістр/пробіли/розділові; «ел./елементів/деталей/шт» — одне; виміри без порядку «21 x 30» = «30 x 21»; бренд без пробілів/дефісів; ведуча «1 »).
# Тоді h1 «{назва} — {поле}: {значення}» (≤ H1_MAX у всіх картках групи); інакше наступне поле; нема — « №id», як у title. Рішення залежить лише від складу групи (не від порядку).
# Змінюється ЛИШЕ <h1>: title, JSON-LD name, alt фото, крихти, index.json/пошук/кошик — сира назва. Картки поза групами — байт-у-байт як раніше.
PRODUCT_H1 = {}          # id → h1 (лише картки з груп однакових назв); заповнює build()
H1_MAX = 120
# (ключ характеристики, підпис у h1) — у порядку пріоритету. Не беремо: габарити/вагу В УПАКОВЦІ (шум для покупця) і «вік» (у Toysi «16 років»/«96 років» — двозначно: від чи до).
H1_ATTRS = (("колір", "колір"), ("мова", "мова"), ("габарити без упаковки", "розмір"), ("матеріал", "матеріал"), ("комплектація", "комплектація"), ("бренд", "бренд"))
_H1_CONFUSABLE = str.maketrans("аеорсхуікмнвт", "aeopcxyikmhbt")   # після casefold: кирилиця, що виглядає як латиниця
_H1_CTRL_RX = re.compile("[  ‪-‮⁦-⁩​-‏﻿]")   # розрив рядка/абзацу, bidi-керування, нульової ширини
_H1_GENERIC_RX = re.compile(r"(?:різно|багато)(?:барв|кольор)\w*|мікс\w*|mix|асорті\w*|в асортименті|assort\w*|multicolou?r\w*|інш(?:ий|а|е|і)|різн(?:ий|і|а|е)|не вказано|немає|нема|n a|unknown")
_H1_UNIT_RX = re.compile(r"(?<!\w)(?:ел|елем|елемент\w*|дет|детал\w*|шт|штук\w*|pcs)(?!\w)")
_H1_DIM_RX = re.compile(r"\d+(?:[.,]\d+)?\s*[xх×*]\s*\d+(?:[.,]\d+)?(?:\s*[xх×*]\s*\d+(?:[.,]\d+)?)*")
_H1_SIZE_IN_NAME_RX = re.compile(r"\d\s*(?:[xх×*]\s*\d|(?:см|мм|cm|mm)(?!\w))")
_H1_COLOR_RX = re.compile(r"(?<!\w)(?:червон|син(?:ій|я|є|і|ього|ьої|ім|ьому|ю)(?!\w)|жовт|зелен|рожев|фіолет|помаранч|оранжев|блакит|біл(?:ий|а|е|і|ого|ої|им|ою|ому)(?!\w)|чорн|"
                          r"сір(?:ий|а|е|і|ого|ої|им|ою|ому)(?!\w)|коричнев|бежев|бузков|бірюз|золот|срібл|салатов|малинов|бордов|голуб|мʼятн|м'ятн|лілов|персиков|корал|хакі|"
                          r"(?:red|blue|green|yellow|pink|black|white|purple|orange|grey|gray)(?!\w))")


def _shown_specs(parts):
    """Характеристики, які write_product показує на картці: рядки «<b>X:</b> Y» опису (після вступу), не більше 6."""
    return [_clean_desc(x) for x in parts[1:] if x.startswith("<b>") and "</b>" in x and ":" in x
            and not x.rstrip().endswith("</b>")][:6]


def _h1_key(s):
    """Ключ порівняння «на око»: без регістру, апострофів, розділових знаків і латинсько-кириличних двійників."""
    s = re.sub(r"[ʼ'’`]", "", unicodedata.normalize("NFKC", str(s or "")).casefold()).translate(_H1_CONFUSABLE)
    return " ".join(re.sub(r"[^\w]|_", " ", s).split())


def _h1_clean(v):
    """Значення для показу: без тегів, сутностей, керівних/невидимих символів і зайвих пробілів."""
    v = html.unescape(re.sub(r"<[^>]+>", " ", v or ""))
    v = "".join(" " if unicodedata.category(ch) == "Cc" else ch for ch in v)
    return " ".join(_H1_CTRL_RX.sub("", v).split()).strip(" :;,.")


def _h1_sem(v):
    """Смислова нормалізація значення (до транслітерації двійників): регістр, апострофи, розділові, одиниці «ел./деталей/шт», ведуча «1 »."""
    s = re.sub(r"[ʼ'’`]", "", unicodedata.normalize("NFKC", v).casefold())
    s = " ".join(re.sub(r"[^\w]|_", " ", s).split())
    s = re.sub(r"^1 ", "", _H1_UNIT_RX.sub("ел", s))
    return s


def _h1_vkey(label, v):
    """Ключ унікальності значення в групі: виміри — відсортовані числа (порядок і «x/х/×» не важать), бренд — без пробілів/дефісів, решта — _h1_sem + двійники."""
    m = _H1_DIM_RX.search(v.casefold())
    if label == "розмір" and m:
        nums = sorted(float(x.replace(",", ".")) for x in re.findall(r"\d+(?:[.,]\d+)?", m.group(0)))
        return "dim:" + "x".join(f"{n:g}" for n in nums)
    k = _h1_sem(v).translate(_H1_CONFUSABLE)
    return k.replace(" ", "") if label == "бренд" else k


_H1_TRANSLIT = str.maketrans({"а": "a", "б": "b", "в": "v", "г": "h", "ґ": "g", "д": "d", "е": "e", "є": "e", "ж": "zh", "з": "z", "и": "i", "і": "i", "ї": "i", "й": "i", "к": "k", "л": "l", "м": "m",
                              "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u", "ф": "f", "х": "h", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "sch", "ь": "", "ю": "iu", "я": "ia",
                              "c": "k", "y": "i", "w": "v", "x": "ks"})


def _h1_lev1(a, b):
    """Відстань Левенштейна ≤ 1 (одна заміна/вставка/видалення)."""
    if abs(len(a) - len(b)) > 1:
        return False
    i = 0
    while i < min(len(a), len(b)) and a[i] == b[i]:
        i += 1
    return a[i + 1:] == b[i + 1:] or a[i + 1:] == b[i:] or a[i:] == b[i + 1:]


def _h1_same(label, a, b):
    """Два значення — на ділі одне (несправжнє розрізнення)? Бренд: ще й транслітерація («Bamsic»/«Бамсик»), переставлені/одна зайва літера («Strateg»/«Starteg»)."""
    if _h1_vkey(label, a) == _h1_vkey(label, b):
        return True
    if label != "бренд":
        return False
    pa, pb = (_h1_sem(x).replace(" ", "").translate(_H1_TRANSLIT) for x in (a, b))
    return pa == pb or sorted(pa) == sorted(pb) or _h1_lev1(pa, pb)


def _h1_usable(label, v, name):
    """Значення годиться для h1 ЦІЄЇ картки: не узагальнене, не повторює назву й не суперечить їй."""
    sem = _h1_sem(v)
    if not sem or any(_H1_GENERIC_RX.fullmatch(_h1_sem(x)) for x in re.split(r"[,;/]| і | та ", v) if _h1_sem(x)):
        return False
    nsem = _h1_sem(name)
    if re.search(r"(?<!\w)" + re.escape(sem) + r"(?!\w)", nsem):
        return False                                                   # повторює назву («… (синій) — колір: синій»)
    if label == "колір" and _H1_COLOR_RX.search(name.casefold()):
        return False                                                   # колір уже в назві — інший колір у полі суперечив би їй
    if label == "розмір" and _H1_SIZE_IN_NAME_RX.search(name.casefold()):
        return False                                                   # розмір у назві («40х50 см», «7 см») — розмір у полі повторив би або суперечив
    n_name, n_val = set(re.findall(r"(\d+) ел(?!\w)", nsem)), set(re.findall(r"(\d+) ел(?!\w)", sem))
    if n_name and n_val and n_name != n_val:
        return False                                                   # «120 елементів» у назві, «150 елементів» у полі
    return True


def _h1_display(label, v):
    """«Синій, Зелений» → «синій, зелений» (кожен елемент переліку; абревіатури й бренд не чіпаємо)."""
    if label == "бренд":
        return v
    return ", ".join(x[0].lower() + x[1:] if len(x) > 1 and x[0].isupper() and x[1].islower() else x for x in v.split(", "))


def _h1_specs(p):
    """{ключ характеристики (casefold): значення} лише з характеристик, показаних на сторінці картки. Лише факти цієї картки."""
    out = {}
    parts = [x.strip() for x in re.split(r"<br\s*/?>", p.get("desc") or "") if x.strip()]
    for s in _shown_specs(parts):
        m = re.match(r"\s*<b>\s*([^<]+?)\s*</b>(.*)$", s, re.S)
        if not m:
            continue
        label = _h1_clean(m.group(1)).rstrip(":").strip().casefold()
        val = _h1_clean(m.group(2))
        if label and val and label not in out:
            out[label] = val
    return out


def assign_product_h1(items):
    """items: усі картки, що отримають сторінку (у наявності + без залишку; тіні — ні). Повертає ({id: h1}, статистика)."""
    groups = {}
    for p in items:
        k = _h1_key(p["name"])
        if k:
            groups.setdefault(k, []).append(p)
    h1s = {}
    st = {"groups": 0, "cards": 0, "attr_groups": 0, "attr_cards": 0, "id_cards": 0, "by": {}}
    for members in groups.values():
        if len(members) < 2:
            continue
        st["groups"] += 1
        st["cards"] += len(members)
        specs = [_h1_specs(p) for p in members]
        chosen = None
        for key, label in H1_ATTRS:
            vals = [s.get(key, "") for s in specs]
            if not all(vals) or not all(_h1_usable(label, v, p["name"]) for p, v in zip(members, vals)):
                continue
            if any(_h1_same(label, vals[i], vals[j]) for i in range(len(vals)) for j in range(i + 1, len(vals))):
                continue                                               # після нормалізації є збіги — розрізнення несправжнє
            cand = [f"{p['name']} — {label}: {_h1_display(label, v)}" for p, v in zip(members, vals)]
            if max(len(c) for c in cand) > H1_MAX:
                continue
            chosen = (label, cand)
            break
        if chosen:
            st["attr_groups"] += 1
            st["attr_cards"] += len(members)
            st["by"][chosen[0]] = st["by"].get(chosen[0], 0) + 1
            for p, c in zip(members, chosen[1]):
                h1s[p["id"]] = c
        else:
            st["id_cards"] += len(members)
            for p in members:
                h1s[p["id"]] = f"{p['name']} №{site_product_code(p['id']) if p.get('rt') else p['id']}"   # rt: код rt-… не друкуємо (як у title), лише «PT-R…»
    _seen = {}
    for p in items:
        _seen.setdefault(_h1_key(h1s.get(p["id"], p["name"])), []).append(p["id"])
    st["unresolved"] = sum(len(v) for v in _seen.values() if len(v) > 1)
    return h1s, st


def product_title(name, in_stock=True, uniq=None) -> str:
    """{назва} — купити | PlutusToys (без залишку: «— немає в наявності | PlutusToys»); якщо довше за 65 — обрізаємо НАЗВУ по межі слова + «…», суфікс лишається. uniq (id товару) → «… №id» перед суфіксом (унікальність title)."""
    name = (name or "").strip()
    suffix = _PROD_SUFFIX if in_stock else _PROD_SUFFIX_OOS
    tag = f" №{uniq}" if uniq else ""
    # ступінчасто (SEO 18.10: «прибрати «| PlutusToys» лише там, де інакше > 65»): повний суфікс → без бренду → без суфікса; назву не ріжемо, поки вона вміщується
    for suf in (suffix, suffix.split(" | ")[0], ""):
        if len(name) + len(tag) + len(suf) <= TITLE_MAX:
            return name + tag + suf
    room = TITLE_MAX - len(suffix) - len(tag) - 1
    cut = name[:room].rsplit(" ", 1)[0].rstrip(" ,.;:—–-") if " " in name[:room] else name[:room]
    return cut + "…" + tag + suffix


# ── Title карток RoyalToys (SEO 21.10, формула SEO) ─────────────────────────────────────────────────────────────────────────────────────────────────────
# title = {назва постачальника, обрізана ПО МЕЖІ СЛОВА так, щоб разом із суфіксом «— купити | PlutusToys» було ≤ 65 символів, БЕЗ «…» і БЕЗ внутрішнього коду rt-…}.
# Унікальність лише за потреби: після обрізання у групі однакових title до кінця назви додається артикул постачальника (vendorCode; префікса rt- там нема) — «… Qman 1418Q-6».
# Код rt-… у title не друкується НІКОЛИ (у H1/JSON-LD name лишається повна назва). Якщо унікальності досягти не вдалося (нема артикула / артикули однакові) — картка лишається з найкращим title
# і потрапляє в RT_TITLE_FAIL (збірка друкує число; список бачить SEO).
RT_TITLE_CODES = {}     # id → артикул, що додається до title (заповнює build())
RT_TITLE_FAIL = []      # id карток, чий title не став унікальним
_TITLE_TAIL_WORDS = re.compile(r"\s(?:для|та|і|й|з|із|зі|на|в|у|по|до|від|за|при|під|над|про|без|або|чи|а|the|and|or|with|for|of)$", re.I)


def _cut_title_words(name, room):
    """Назва ≤ room символів по межі слова: без «…», без висячих розділювачів/прийменників і без незакритих лапок/дужок."""
    name = re.sub(r"\s+", " ", (name or "")).strip()
    if room <= 0:
        return ""
    if len(name) <= room:
        return name
    cut = name[:room]
    if name[room] != " " and " " in cut:     # наступний символ — середина слова → відкидаємо недописане слово
        cut = cut.rsplit(" ", 1)[0]
    for _ in range(6):
        prev = cut
        cut = cut.rstrip(" ,.;:—–-/&+|")
        # незакриті лапки/дужки: прибираємо недописаний фрагмент (якщо лишається достатньо назви), інакше лише сам відкриваючий знак
        for o, c in (('"', '"'), ("«", "»"), ("(", ")"), ("“", "”")):
            opened = cut.count(o) - (cut.count(c) if o != c else 0) if o != c else cut.count('"') % 2
            if opened > 0:
                i = cut.rfind(o)
                head = cut[:i].rstrip(" ,.;:—–-/&+|")
                cut = head if len(head) >= 20 else (cut[:i] + cut[i + 1:]).strip()
        m = _TITLE_TAIL_WORDS.search(cut)
        if m:
            cut = cut[:m.start()]
        if cut == prev:
            break
    return cut.strip()


def rt_title(name, in_stock=True, code=None):
    suffix = _PROD_SUFFIX if in_stock else _PROD_SUFFIX_OOS
    code = (code or "").strip()
    if code:
        return _cut_title_words(name, TITLE_MAX - len(suffix) - len(code) - 1) + " " + code + suffix
    return _cut_title_words(name, TITLE_MAX - len(suffix)) + suffix


def assign_rt_title_codes(rt_items, taken_titles=()):
    """rt_items: [(p, in_stock)]. Повертає ({id: артикул}, [id картки без унікального title]). taken_titles — title Toysi-карток (rt не має збігатися й із ними)."""
    groups = {}
    for p, ins in rt_items:
        groups.setdefault(rt_title(p["name"], ins), []).append((p, ins))
    taken = set(taken_titles)
    codes, fail = {}, []
    for t, members in groups.items():
        if len(members) == 1 and t not in taken:
            continue
        seen = {}
        for p, ins in members:
            code = str(p.get("vendor_code") or "").strip()
            if re.match(r"(?i)rt-", code):
                code = ""
            if code:
                codes[p["id"]] = code
            seen.setdefault(rt_title(p["name"], ins, code or None), []).append(p["id"])
        for t2, ids in seen.items():
            if len(ids) > 1 or t2 in taken:
                fail.extend(ids)
    return codes, sorted(set(fail))


def product_title_for(p, in_stock=True) -> str:
    if p.get("rt"):   # RoyalToys: формула SEO 21.10 (без «…» і без коду rt-)
        return rt_title(p["name"], in_stock, RT_TITLE_CODES.get(p["id"]))
    t = product_title(p["name"], in_stock)
    return product_title(p["name"], in_stock, uniq=p["id"]) if t in PRODUCT_DUP_TITLES else t


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
        "priceValidUntil": ((_sale_until_of(p["id"]).date() if (SALE_ACTIVE and p.get("old_price") and _sale_until_of(p["id"])) else price_valid_until())).isoformat(),
        # shippingDetails без виміряної ставки не пишемо (shippingRate обов'язковий, вигадане число — розбіжність розмітки зі сторінкою); повернемо разом із цифрою.
        "hasMerchantReturnPolicy": {
            "@type": "MerchantReturnPolicy", "applicableCountry": "UA",
            "returnPolicyCategory": "https://schema.org/MerchantReturnFiniteReturnWindow",
            "merchantReturnDays": 14, "returnMethod": "https://schema.org/ReturnByMail",
            # SEO 14.10: ReturnShippingFees без returnShippingFeesAmount Google читав як «безкоштовно» (100% товарів у Merchant Center); тариф НП залежить від міста — суму не вказуємо, тож «фактична вартість — на покупці» (як на сторінці «Повернення» для товару належної якості)
            "returnFees": "https://schema.org/ReturnFeesCustomerResponsibility",
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
    # rt-картки (RoyalToys, лише передоплата) — після Toysi, доки власник не вирішить інакше (Код/Shopify-code 19.10): із cost вони мають внесок і інакше витіснили б Toysi з перших позицій
    return sorted(items, key=lambda p: (1 if p.get("rt") else 0, _rec_bucket(p), hashlib.sha1(f"{REC_SEED}:{p['id']}".encode()).hexdigest()))


_CATEGORY_TEXTS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "category_texts.json")
_CATEGORY_TEXTS = {}


# Запобіжник «без грошей у статичних текстах» (Консультант 24.10): ціни застарівають із фідом, а твердження «типово 259 ₴» при інших цифрах у фіді працює проти довіри.
# Ознака бінарна (знак валюти, «грн/гривня», «ціна/ціни/коштує»), порогів нема; «вартість» не ловимо (є значення «очки за літеру»). Лише попередження в stderr: текст усе одно показується.
_MONEY_RX = re.compile(r"₴|грн|гриве?н|\b(?:ціна|ціни|ціну|ціною|коштує|коштують)\b", re.I)


def _warn_money_words(label, texts):
    """Ключі {id|slug: текст}, де є слова про гроші; друкує попередження в stderr, повертає відсортований список ключів."""
    hits = sorted(k for k, v in texts.items() if _MONEY_RX.search(v))
    if hits:
        print(f"[build] ⚠ {label}: у тексті є гроші (₴/грн/«ціна»/«коштує») — вони застарівають із фідом, правило проєкту: без цін у статичних текстах; ключі: {hits[:10]} (усього {len(hits)})", file=sys.stderr)
    return hits


# Те саме правило на ВИХОДІ: що сторінка справді показує (тексти з файлів, згенерований текст брендової категорії, будь-який третій шлях). Дозволено лише ОБЧИСЛЕНЕ щозбірки
# речення «… від X до Y ₴.» (діапазон із даних, не застигле число) і службову фразу про сортування за ціною; решта грошей у видимому тексті — попередження в stderr один раз на ключ.
_RENDER_MONEY_OK_RX = re.compile(r"Ціни на [^.\n]{1,40} у нашому каталозі — від \d[\d\xa0 ]* до \d[\d\xa0 ]* ₴\.|відсортувати товари за ціною")
_MONEY_RENDER_SEEN = set()


def _warn_money_rendered(kind, key, text):
    """Попередження в stderr, якщо видимий текст (kind: «категорія»/«картка») має гроші поза дозволеним обчислюваним реченням. Повертає True, коли знайдено."""
    rest = _RENDER_MONEY_OK_RX.sub("", text or "")
    if not _MONEY_RX.search(rest):
        return False
    if (kind, key) not in _MONEY_RENDER_SEEN:
        _MONEY_RENDER_SEEN.add((kind, key))
        print(f"[build] ⚠ видимий текст ({kind} {key}): у ньому гроші (₴/грн/«ціна»/«коштує») поза обчислюваним діапазоном — застигле число застаріє з фідом, прибери з джерела", file=sys.stderr)
    return True


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
    _warn_money_words("category_texts.json", _CATEGORY_TEXTS)
    return _CATEGORY_TEXTS


# Не-зброя категорії, яких немає у вітрині ГОЛОВНОЇ (SMM: «перші 10 не вони»), але на СВОЇЙ сторінці рекомендація з них доречна (15.10: сторінка «Картини за номерами» лишалась із порожньою правою колонкою). Зброя/репліки залишаються виключеними.
CATEGORY_ASIDE_ALLOW_CATS = frozenset({"картини за номерами", "брелоки"})
CATEGORY_ASIDE_MIN_CONTRIB = 30.0   # внесок на замовлення, ₴: запасний поріг рекомендації в категорії


def _category_aside_html(cat_prods):
    """Права колонка біля вступного тексту категорії (власник 15.10: «права сторона повинна заповнитися рекомендацією товару або акцією»):
    є акційні товари цієї категорії («Знижка тижня», сайт показує лише чинну акцію) → найдешевший з них + посилання на всі знижки; інакше «Рекомендуємо» — один товар категорії з щоденною ротацією (home_pick: той самий відбір і правила, що у вітрині головної — ціна, внесок, без мілітарі/зброї)."""
    cat_prods = [p for p in cat_prods if not p.get("rt")]   # RoyalToys у «Рекомендуємо/Знижка тижня» біля вступу не показуємо (рішення власника ще нема)
    sale = [p for p in cat_prods if p.get("old_price") and p.get("stock", 0) > 0]
    if sale and SALE_ACTIVE:
        p = min(sale, key=lambda x: (x["price"], str(x["id"])))
        label = "Акції"
        more = '<a class="cat-aside-more" href="aktsii.html">Усі знижки →</a>'
    else:
        # спершу правила вітрини головної; у дешевих категоріях (внесок < 100 ₴, ціна < 150 ₴) вони нічого не дають і права колонка лишалась би порожньою (15.10, перевірка на проді) → пом'якшена драбина: ціна 50–1500, внесок ≥ 30 ₴ (поріг сезонних чипів); мілітарі/зброя/OOS/без фото відсіюються так само
        picks = home_pick(cat_prods, allow_cats=CATEGORY_ASIDE_ALLOW_CATS) or home_pick(cat_prods, pmin=50, pmax=1500, min_contrib=CATEGORY_ASIDE_MIN_CONTRIB, allow_cats=CATEGORY_ASIDE_ALLOW_CATS)
        if not picks:
            return ""
        p, label, more = picks[0], "Рекомендуємо", ""
    return f'<aside class="cat-aside" aria-label="{esc(label)}"><div class="cat-aside-lbl">{esc(label)}</div>{tile(p)}{more}</aside>'


# SEO 21.10 (пілот «посилена брендова категорія», варіант А): існуюча категорія бренду (НЕ новий URL) отримує унікальний текст 120–180 слів лише з наших даних —
# виробник, кількість моделей у наявності, діапазон деталей і вікові позначки ЛИШЕ з назв, ціни від/до. Без вигаданих дат, цифр, нагород. Текст рахується щозбірки (не застаріває зі складом);
# статичний текст із category_texts.json (SEO) для цього slug має пріоритет. Така ж категорія автоматично потрапляє в sitemap-priority і має CollectionPage-розмітку та посилання «Усі пазли …» на своїх картках.
# Клас Б (тверд. про БРЕНД, а не про товари; Консультант 24.10): «producer» — стійкий зовнішній факт, перенесення на товари заборонене (не «усі пазли тут польські»).
# Джерело «Trefl — польська компанія (Trefl S.A., пазли, настільні ігри, засн. 1985; зареєстрована на Kontenerowa, Гдиня)»: https://en.wikipedia.org/wiki/Trefl і https://www.trefl.com/en/ (звірено 24.10.2026).
# Поля «Країна виробник» на картках Trefl нема в більшості (вибірка 25: немає на 16, Польща на 5, Україна на 2, Китай на 2), тож текст не каже, де виготовлено конкретний пазл.
BRAND_CATEGORIES = {
    "pazly-trefl": {"brand": "Trefl", "producer": "польська компанія пазлів і настільних ігор", "noun": "пазли", "link_text": "Усі пазли Trefl",
                    # популярні сюжети (SEO 19.10: «Marvel, Холодне серце, Стіч, Міккі, Маквін»): згадуємо ЛИШЕ ті, що реально є в ≥ BRAND_THEME_MIN назвах моделей (підрахунок щозбірки, не вручну)
                    "themes": (("Marvel", r"marvel|марвел"), ("«Холодне серце»", r"холодне серце"), ("Стіч", r"стіч"), ("Міккі", r"мікк?і"), ("Маквін", r"маквін"),
                               ("«Щенячий патруль»", r"щенячий патруль"), ("Барбі", r"барбі"))},
}
BRAND_INTRO_MIN_ITEMS = 5      # менше моделей — тексту нема (нічого писати)
BRAND_THEME_MIN = 3           # сюжет згадуємо, лише коли він є в стількох назвах (і не менше)
_PIECES_RX = re.compile(r"(?<![\d.,])(\d{2,4})\s*ел\w*", re.I)    # «1000 елм.», «(60 ел)», «(2 x 70 елм.)», «2х200 елм.» → число деталей ОДНОГО пазла
_AGE_RX = re.compile(r"(?<![\w.,+])(\d{1,2})\+(?![\w+])")               # «3+», «6+» — вікова позначка в назві (впритул до плюса; «2в1 + Мемо» — не вік)


def brand_category_text(slug, items) -> str:
    """Унікальний вступ брендової категорії (див. BRAND_CATEGORIES). Лише Toysi-товари у наявності з цієї категорії; < BRAND_INTRO_MIN_ITEMS — порожньо."""
    cfg = BRAND_CATEGORIES.get(slug)
    items = [x for x in (items or []) if not x.get("rt") and x.get("stock", 0) > 0]
    if not cfg or len(items) < BRAND_INTRO_MIN_ITEMS:
        return ""
    n = len(items)
    pieces = sorted(int(m.group(1)) for x in items for m in [_PIECES_RX.search(html.unescape(x["name"]))] if m)
    ages = sorted(int(m.group(1)) for x in items for m in [_AGE_RX.search(html.unescape(x["name"]))] if m)
    pmin, pmax = min(x["price"] for x in items), max(x["price"] for x in items)
    brand, noun = cfg["brand"], cfg["noun"]
    p1 = f"{brand} — {cfg['producer']}. У цій категорії PlutusToys зібрано {noun} {brand}, які зараз є в наявності: {n} {_model_word(n)}."
    if pieces:
        p1 += f" За назвами моделей кількість деталей — від {pieces[0]} до {pieces[-1]}."
    if ages:
        p1 += f" У назвах зустрічаються вікові позначки — від {ages[0]}+ до {ages[-1]}+."
    names = [html.unescape(x["name"]).lower() for x in items]
    th = sorted(((sum(1 for nm in names if re.search(rx, nm)), label) for label, rx in cfg.get("themes", ())), reverse=True)
    th = [label for c, label in th if c >= BRAND_THEME_MIN][:5]
    if th:
        p1 += " У назвах моделей зустрічаються сюжети: " + ", ".join(th) + "."
    p2 = (f"Ціни на {noun} {brand} у нашому каталозі — від {_fmt_int(pmin)} до {_fmt_int(pmax)} ₴. Перед замовленням відкрийте картку товару: там є фото й повна назва моделі, "
          "а в категорії можна відсортувати товари за ціною.")
    p3 = ("Усі позиції на цій сторінці є в наявності на момент збірки сторінки; залишок може змінюватися, тому актуальний стан видно на картці товару. "
          "Відправляємо замовлення Новою Поштою по Україні, вартість доставки — за тарифами оператора. Умови оплати, доставки й повернення описано на сторінках «Доставка» та «Повернення».")
    return "\n\n".join((p1, p2, p3))


def _model_word(n):
    n10, n100 = n % 10, n % 100
    if n10 == 1 and n100 != 11:
        return "модель"
    if 2 <= n10 <= 4 and not 12 <= n100 <= 14:
        return "моделі"
    return "моделей"


def _category_intro_html(fname, cat_prods=None):
    """Вступний текст категорії (лише 1-ша сторінка; на пагінації не дублюємо). slug = ім'я файлу без «category-» і «.html». HTML у тексті екранується, абзаци — за порожнім рядком."""
    m = re.fullmatch(r"category-(.+)\.html", fname or "")
    text = _CATEGORY_TEXTS.get(m.group(1)) if m else None
    if not text and m and m.group(1) in BRAND_CATEGORIES:   # SEO 21.10: динамічний текст брендової категорії (із даних збірки)
        text = brand_category_text(m.group(1), cat_prods)
    alt = _CATEGORY_RU_ALT.get(m.group(1)) if m else None
    if not text and not alt:
        return ""
    _warn_money_rendered("категорії", m.group(1) if m else fname, text)
    paras = [p.strip() for p in re.split(r"\n\s*\n", (text or "").replace("\r\n", "\n")) if p.strip()]
    alt_html = f'<p class="cat-alt">Також шукають: {esc(alt)}</p>' if alt else ""   # SEO 19.10: російські синоніми запиту — ВИДИМИЙ рядок під H1 (не прихований)
    return '<div class="cat-intro">' + alt_html + "".join(f"<p>{esc(p)}</p>" for p in paras) + "</div>\n"


_CATEGORY_RU_ALT_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "category_ru_alt.json")
_CATEGORY_RU_ALT = {}


def _load_category_ru_alt():
    """site/category_ru_alt.json: {slug: "синонім 1, синонім 2, синонім 3"} від SEO (19.10) — рядок «Також шукають: …» під H1 категорії. Нема файлу — рядків нема; зламаний файл — ГОЛОСНО, збірка не падає."""
    global _CATEGORY_RU_ALT
    _CATEGORY_RU_ALT = {}
    try:
        with open(_CATEGORY_RU_ALT_FILE, encoding="utf-8-sig") as f:
            data = json.load(f)
    except FileNotFoundError:
        return _CATEGORY_RU_ALT
    except (OSError, ValueError, RecursionError) as e:
        print(f"[build] ⚠ category_ru_alt.json НЕ прочитано ({type(e).__name__}: {e}) — рядків «Також шукають» не буде", file=sys.stderr)
        return _CATEGORY_RU_ALT
    if not isinstance(data, dict):
        print(f"[build] ⚠ category_ru_alt.json — очікується об'єкт {{slug: рядок}}, отримано {type(data).__name__} — рядків не буде", file=sys.stderr)
        return _CATEGORY_RU_ALT
    _CATEGORY_RU_ALT = {str(k): re.sub(r"\s+", " ", v).strip() for k, v in data.items() if isinstance(v, str) and v.strip()}
    return _CATEGORY_RU_ALT


# Гейт «власні назви» для category_ru_alt.json (SEO 01.11, КОНТРАКТ_cat_alt_власні_назви.md): латинський токен або
# Слово-З-Великої усередині фрази мусить зустрічатись у назвах товарів ЦІЄЇ категорії (марка пишеться однаково
# обома мовами; інакше сторінка обіцяє чужий товар — так були хибні «конструктор Wader», «Bubble Pops»).
# Лише ПОПЕРЕДЖЕННЯ в stderr, збірку не валить. Загальні слова («погремушки») не карає — рядок для них і існує.
_RU_ALT_LATIN_OK = {"pop", "it", "ua", "3d", "led", "diy", "xl", "tv", "toys", "kids", "mini", "puzzle", "game", "games", "series",
                    "tech", "action", "figure", "baby", "block", "card", "gloves", "for", "and", "new", "set", "box", "art", "fun",
                    "soft", "water", "color", "colors", "como", "que", "isso", "the"}
_RU_ALT_ALLOW = {("pazly-castorland", "Касторленд")}  # транслітерація марки Castorland з НАЗВИ КАТЕГОРІЇ (SEO 31.10)
_RU_ALT_TR = {"а": "a", "б": "b", "в": "v", "г": "h", "д": "d", "е": "e", "ж": "zh", "з": "z", "и": "y", "і": "i", "й": "y", "к": "k",
              "л": "l", "м": "m", "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u", "ф": "f", "х": "kh", "ц": "ts",
              "ч": "ch", "ш": "sh", "щ": "shch", "ю": "iu", "я": "ia", "ї": "i", "є": "ie", "ё": "e", "ы": "y", "э": "e", "ъ": "", "ь": ""}


def _ru_alt_translit(w):
    return "".join(_RU_ALT_TR.get(c, c) for c in w.lower())


def ru_alt_proper_name_issues(alt, cats, cat_slug):
    """[(slug, фраза, токен, кількість товарів категорії)] — власні назви без опори в назвах товарів своєї категорії."""
    slug_cat = {v: k for k, v in cat_slug.items()}
    bad = []
    for slug, line in sorted(alt.items()):
        cname = slug_cat.get(slug)
        if cname is None:
            continue  # slug без сторінки — окреме попередження
        names = [str(p.get("name") or "").lower() for p in cats.get(cname, [])]
        pool = " | ".join(names)
        ctx = (slug + " " + cname).lower()
        for phrase in [x.strip() for x in line.split(",") if x.strip()]:
            latin = [w for w in re.findall(r"[A-Za-z][A-Za-z0-9'\-]{2,}", phrase) if w.lower() not in _RU_ALT_LATIN_OK]
            ws = phrase.split()
            proper = [re.sub(r"[^\wʼ'-]", "", w) for i, w in enumerate(ws) if i > 0 and re.match(r"^[А-ЯІЇЄҐA-Z][а-яіїєґa-z]{2,}", w)]
            for tok in set(latin + proper):
                if tok.lower() in pool or tok.lower() in ctx or _ru_alt_translit(tok)[:6] in _ru_alt_translit(ctx) or (slug, tok) in _RU_ALT_ALLOW:
                    continue
                bad.append((slug, phrase, tok, len(names)))
    return bad


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


def _product_text_key_ok(k):
    """Ключ тексту картки: id Toysi (лише ASCII-цифри), id RoyalToys `rt-…` (`_RT_ID_RX`, 31.10.2026 — Копірайтер, 11 207 rt-товарів без наших описів). fullmatch, тож юнікод-цифри й хвости не проходять."""
    return isinstance(k, str) and (re.fullmatch(r"[0-9]+", k) is not None or _RT_ID_RX.fullmatch(k) is not None or _KV_ID_RX.fullmatch(k) is not None)


def _load_product_texts():
    """site/product_texts.json: {"<toysi_id>": "абзац 1\\n\\nабзац 2"} (ключ також `rt-…` для RoyalToys). Не довіряємо даним: корінь — об'єкт; ключ — ASCII-цифри або rt-id; значення — непорожній рядок
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
    bad_id = [k for k in data if not _product_text_key_ok(k)]
    if bad_id:
        print(f"[build] ⚠ product_texts.json: id не з цифр і не rt-id, відкинуто: {bad_id[:10]}", file=sys.stderr)
    out = {k: v for k, v in data.items() if isinstance(v, str) and v.strip() and _product_text_key_ok(k)}
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
    _warn_money_words("product_texts.json", _PRODUCT_TEXTS)
    return _PRODUCT_TEXTS


def _product_text_first_para(pid):
    """Перший абзац тексту картки (plain) — для опису в JSON-LD там, де текст замінює опис постачальника. Нема тексту — порожньо."""
    text = _PRODUCT_TEXTS.get(str(pid)) or ""
    paras = [p.strip() for p in re.split(r"\n\s*\n", text.replace("\r\n", "\n")) if p.strip()]
    return paras[0] if paras else ""


# 12.11.2026 (SEO, варіант Б): сніпет (meta description) і JSON-LD description для Toysi/RT беруться з опису ПОСТАЧАЛЬНИКА (lead). Коли цей опис — заглушка «Бренд: …», коротший за 60 знаків або російський, а наш текст картки є — для сніпета і розмітки беремо перший абзац НАШОГО тексту. ЗА ВОРОТАМИ SITE_SNIPPET_OWN_TEXT=1 (вимкнено за замовчуванням → збірка як була).
# Нормальний український опис постачальника не чіпаємо (74 % наших текстів починаються з назви — у сніпеті вони дублювали б заголовок).
SNIPPET_OWN_TEXT = os.environ.get("SITE_SNIPPET_OWN_TEXT") == "1"
SNIPPET_SHORT_MIN = 60


_SECOND_PERSON_RX = re.compile(r"\b[а-яіїєґ']{1,}(?:єш|еш|иш|їш)(?:ся)?\b", re.I)   # дієслова 2-ї особи однини (SEO 12.11; основа ≥1 знак: ловить і «Даєш», «б'єш»; хибні «фреш»/«флеш» лише повертають сніпет до опису постачальника)


def snippet_use_own_text(supplier_plain, pid, name=""):
    """True, якщо для сніпета/JSON-LD треба взяти наш текст замість опису постачальника: ворота ввімкнено, наш текст є, а опис постачальника — «Бренд: …» / <60 знаків / російський. НЕ беремо наш текст, якщо його перший абзац містить дієслова 2-ї особи (шаблонні речення черги 2-ї особи не мають потрапляти у сніпет) або майже не додає до назви (коротший за назву + 40 знаків)."""
    first = _product_text_first_para(pid) if SNIPPET_OWN_TEXT else ""
    if not first or _SECOND_PERSON_RX.search(first) or len(" ".join(first.split())) < len(name or "") + 40:
        return False
    t = html.unescape(supplier_plain or "").strip()
    return bool(re.match(r"бренд\s*:", t, re.I)) or len(t) < SNIPPET_SHORT_MIN or bool(_RU_LETTERS_RE.search(t))


def _product_text_html(pid):
    """Блок тексту картки (plain text → esc(), абзаци за порожнім рядком). Нема тексту для id — порожньо (блоку нема)."""
    text = _PRODUCT_TEXTS.get(str(pid))
    if not text:
        return ""
    _warn_money_rendered("картки", str(pid), text)
    paras = [p.strip() for p in re.split(r"\n\s*\n", text.replace("\r\n", "\n")) if p.strip()]
    if not paras:
        return ""
    return '<div class="prod-text">' + "".join(f"<p>{esc(p)}</p>" for p in paras) + "</div>"


# ── Відгуки покупців (03.11.2026): бекенд на VPS віддає site/reviews.json ЛИШЕ з опублікованими (перевіреними) відгуками; контракт і межі — site/REVIEWS_CONTRACT.md ──
# Формат: {"_meta": {"exported_at": ISO, "count": N}, "<id Toysi>": [{"rating","text","author","date"}, …]} (ключі з «_» службові). Нема файлу — тихо порожньо.
# FAIL-CLOSED (SEO): файл нечитабельний / не об'єкт / нема _meta / _meta.count не ціле або ≠ кількості записів у файлі → ГОЛОСНО в stderr і ЖОДНОГО блоку/aggregateRating/review (збірка не падає).
# Розмітка = видиме: блок і JSON-LD (aggregateRating + review) рахуються з тих самих ≤REVIEWS_SHOW_MAX показаних записів (агрегату бекенд не передає); без відгуків картка байт-у-байт як була.
_REVIEWS_FILE = os.path.join(_HERE, "reviews.json")
_REVIEWS = {}
REVIEWS_SHOW_MAX = 10
REVIEW_TEXT_MIN, REVIEW_TEXT_MAX, REVIEW_AUTHOR_MAX = 3, 1500, 40
REVIEW_AUTHOR_DEFAULT = "Покупець"
REVIEWS_STALE_DAYS = 7
_REVIEW_BIDI_RX = re.compile("[\u202a-\u202e\u2066-\u2069\u200e\u200f\u061c]")   # керування напрямком (bidi): вирізаємо з text і author — інакше U+202E в імені перевертає дату в рядку відгуку (аудит-2)
_REVIEW_INVISIBLE_CATS = frozenset(("Cf", "Zs", "Zl", "Zp", "Cc", "Mn", "Me"))   # не рахуються як видимі символи (формат, пробіли, керівні, комбіновані знаки); з тексту їх НЕ вирізаємо — ZWJ-емодзі й прапори лишаються цілими
REVIEW_DATE_MIN = "2020-01-01"   # нижня межа дати відгуку (ISO-рядки порівнюються лексикографічно)


def _review_visible_len(s):
    """Кількість видимих символів: без категорій _REVIEW_INVISIBLE_CATS (U+200B, U+00AD, U+2061…, U+034F, пробіли, NBSP тощо)."""
    return sum(1 for ch in s if unicodedata.category(ch) not in _REVIEW_INVISIBLE_CATS)
_REVIEW_MONEY_RX = re.compile(_MONEY_RX.pattern + r"|доставк", re.I)   # гроші/доставка у відгуку — лише попередження (модерація бекенду), не блокуємо


def _review_clean(r, today=None):
    """Один запис відгуку → нормалізований dict або None (невалідний): bidi-символи з text/author вирізаються; rating int 1..5; text 3..1500 після trim, ≥3 ВИДИМИХ (_review_visible_len);
    author рядок ≤40 (без видимих символів/нема → «Покупець»); date YYYY-MM-DD (справжня дата, від REVIEW_DATE_MIN і не пізніше today — київська дата збірки)."""
    if not isinstance(r, dict):
        return None
    rating, text, author, date = r.get("rating"), r.get("text"), r.get("author"), r.get("date")
    if type(rating) is not int or not 1 <= rating <= 5:   # bool/float/рядок — ні
        return None
    if not isinstance(text, str):
        return None
    text = _REVIEW_BIDI_RX.sub("", text.replace("\r\n", "\n")).strip()
    if not REVIEW_TEXT_MIN <= len(text) <= REVIEW_TEXT_MAX or not _utf8_clean(text) or _review_visible_len(text) < REVIEW_TEXT_MIN:
        return None
    if author is None:
        author = ""
    if not isinstance(author, str):
        return None
    author = " ".join(_REVIEW_BIDI_RX.sub("", author).split())
    if len(author) > REVIEW_AUTHOR_MAX or not _utf8_clean(author):
        return None
    if not _review_visible_len(author):
        author = ""   # лише невидимі символи → «Покупець»
    if not isinstance(date, str) or not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", date):
        return None
    try:
        d = datetime.strptime(date, "%Y-%m-%d").date()
    except ValueError:
        return None
    if date < REVIEW_DATE_MIN or (today is not None and d > today):
        return None   # дата з майбутнього (datePublished не може бути завтрашнім) або до REVIEW_DATE_MIN — не публікуємо
    return {"rating": rating, "text": text, "author": author or REVIEW_AUTHOR_DEFAULT, "date": date}


def _load_reviews(now=None):
    """site/reviews.json → _REVIEWS {"<id>": [≤REVIEWS_SHOW_MAX валідних, від нових до старих]}. Fail-closed (див. вище); невалідні записи — пропуск з ОДНИМ зведеним попередженням."""
    global _REVIEWS
    _REVIEWS = {}
    off = "жодного відгуку й aggregateRating не публікуємо (fail-closed)"
    data = _read_json_loud(_REVIEWS_FILE, "reviews.json", off)
    if data is _MISSING:
        return _REVIEWS
    if not isinstance(data, dict):
        print(f"[build] ⚠ reviews.json — очікується об'єкт {{_meta, id: [відгуки]}}, отримано {type(data).__name__} — {off}", file=sys.stderr)
        return _REVIEWS
    meta = data.get("_meta")
    cnt = meta.get("count") if isinstance(meta, dict) else None
    if type(cnt) is not int or cnt < 0:
        print(f"[build] ⚠ reviews.json — нема _meta або _meta.count не ціле невід'ємне (файл неповний або не від бекенду) — {off}", file=sys.stderr)
        return _REVIEWS
    entries = {k: v for k, v in data.items() if not k.startswith("_")}
    actual = sum(len(v) if isinstance(v, list) else 1 for v in entries.values())   # валідні + невалідні; значення-не-масив рахується як 1 запис
    if actual != cnt:
        print(f"[build] ⚠ reviews.json — _meta.count={cnt}, а записів у файлі {actual} (файл обірваний або змінений не бекендом) — {off}", file=sys.stderr)
        return _REVIEWS
    exp = meta.get("exported_at")
    exp_dt = _aware_dt(exp.strip().replace("Z", "+00:00")) if isinstance(exp, str) else None
    now = now or datetime.now(timezone.utc)
    today = (now + timedelta(hours=_eu_dst_hours(now))).date()   # київська дата (як _kyiv_now, але від переданого now — для тестів)
    if exp_dt is None:
        print("[build] ⚠ reviews.json: _meta.exported_at нема, нечитабельне або без часового поясу — свіжість вивантаження невідома (відгуки показано)", file=sys.stderr)
    elif exp_dt - now > timedelta(days=1):
        print(f"[build] ⚠ reviews.json: _meta.exported_at у майбутньому (exported_at {exp}) — некоректний час вивантаження або годинник бекенду (відгуки показано)", file=sys.stderr)
    elif now - exp_dt > timedelta(days=REVIEWS_STALE_DAYS):
        print(f"[build] ⚠ reviews.json: вивантаження старше {REVIEWS_STALE_DAYS} діб (exported_at {exp}) — бекенд не оновлює файл? (відгуки показано)", file=sys.stderr)
    bad_keys, bad_recs, out = [], 0, {}
    for k, v in entries.items():
        if re.fullmatch(r"[1-9][0-9]*", k) is None or not isinstance(v, list):   # канонічний id Toysi: без ведучих нулів ("00101" не збігся б із жодною карткою)
            bad_keys.append(k)
            continue
        good = []
        for r in v:
            c = _review_clean(r, today)
            if c is None:
                bad_recs += 1
            else:
                good.append(c)
        if good:
            good.sort(key=lambda x: x["date"], reverse=True)   # від нових до старих; однакова дата — порядок бекенду (сортування стабільне)
            out[k] = good[:REVIEWS_SHOW_MAX]
    if bad_recs or bad_keys:
        print(f"[build] ⚠ reviews.json: пропущено невалідних записів {bad_recs} (rating 1..5, text {REVIEW_TEXT_MIN}..{REVIEW_TEXT_MAX}, author ≤{REVIEW_AUTHOR_MAX}, date YYYY-MM-DD)"
              + (f"; ключі не з цифр (або з ведучим нулем) чи значення не масив, відкинуто: {bad_keys[:10]}" if bad_keys else ""), file=sys.stderr)
    money = sorted({k for k, v in out.items() for r in v if _REVIEW_MONEY_RX.search(r["text"])}, key=_id_key)
    if money:
        print(f"[build] ⚠ reviews.json: у показаних відгуках є гроші/доставка (₴/грн/«ціна»/«доставк…») — показано як є, перевір модерацію; товари: {money[:10]} (усього {len(money)})", file=sys.stderr)
    _REVIEWS = out
    return _REVIEWS


def _review_avg(rv):
    """Середня оцінка з 1 знаком, округлення half-up, рядок «4.7» (для JSON-LD; на сторінці — з комою). Цілочисельно, без float-похибки."""
    s, n = sum(r["rating"] for r in rv), len(rv)
    q = (20 * s + n) // (2 * n)   # round_half_up(10·s/n)
    return f"{q // 10}.{q % 10}"


def _review_stars(n):
    return "★" * n + "☆" * (5 - n)


def _reviews_html(rv):
    """Блок «Відгуки покупців» на картці (лише ≥1 валідний відгук; інакше порожньо — розмітка картки як була). Усі тексти — esc()."""
    if not rv:
        return ""
    avg = _review_avg(rv)
    q = int(avg.replace(".", ""))
    n = len(rv)
    items = "".join(
        '<li class="rv" style="margin:0 0 12px">'
        f'<div class="rv-head"><span class="rv-stars" aria-hidden="true" style="color:var(--accent)">{_review_stars(r["rating"])}</span> '
        f'<span class="rv-num">{r["rating"]} з 5</span> · <b class="rv-author">{esc(r["author"])}</b> · '
        f'<time class="rv-date" datetime="{r["date"]}">{r["date"][8:10]}.{r["date"][5:7]}.{r["date"][:4]}</time></div>'
        f'<p class="rv-text" style="margin:4px 0 0">{esc(r["text"]).replace(chr(10), "<br>")}</p></li>'
        for r in rv)
    return ('<section class="desc reviews" id="reviews" aria-labelledby="reviews-h">'
            '<h2 id="reviews-h">Відгуки покупців</h2>'
            f'<p class="rv-sum"><span aria-hidden="true" style="color:var(--accent)">{_review_stars((q + 5) // 10)}</span> '
            f'<b>{avg.replace(".", ",")}</b> з 5 · {n} відгук{_plural(n)}</p>'
            f'<ul class="rv-list" style="list-style:none;padding:0;color:inherit">{items}</ul></section>')


# ── Перекриття категорії окремого Toysi-товару (SEO 23.10): у категоріях є чужі товари (аудит: «Металеві моделі» — чашки, шахи), а категорія береться з поля постачальника (`category_name`), тож перенесення робимо ДАНИМИ ──
# site/category_overrides.json {"<id Toysi, лише цифри>": "<ТОЧНА назва категорії, що вже є на сайті>"}; ключі з «_» службові. Застосовується під час збору картки: index.json, сторінки категорій, крихти, sitemap, noindex.
# Не довіряємо даним: id лише цифри (rt-картки не чіпаємо); ціль мусить бути назвою категорії, що вже має сторінку в ЦІЙ збірці (є хоч один товар у наявності: категорія лише з OOS нової сторінки не створює), і не з NOINDEX_CATEGORIES; інакше запис пропускається ГОЛОСНО, товар лишається у своїй категорії.
# Нових категорій цим файлом не створюємо (нова категорія = нова сторінка). Фіди Google/Meta/Bing і маркетплейси беруть категорію іншим шляхом і не змінюються.
# ВИНЯТОК із «нових категорій не створюємо» (рішення власника 28.10: ігри 18+ і 16+ — в ОКРЕМУ категорію): перелік назв, які ДОЗВОЛЕНО створити цим файлом (категорія з'являється, коли хоч один товар у наявності на неї вказує). Нова назва = рішення власника + рядок тут.
NEW_CATEGORIES_ALLOWED = ("Ігри 16+ та 18+",)
_CATEGORY_OVERRIDES_FILE = os.path.join(_HERE, "category_overrides.json")
_CATEGORY_OVERRIDES = {}


def _load_category_overrides(known_categories):
    global _CATEGORY_OVERRIDES
    _CATEGORY_OVERRIDES = {}
    data = _read_json_loud(_CATEGORY_OVERRIDES_FILE, "category_overrides.json", "перенесень товарів між категоріями не буде")
    if data is _MISSING:
        return _CATEGORY_OVERRIDES
    if not isinstance(data, dict):
        print(f"[build] ⚠ category_overrides.json — очікується об'єкт {{id: категорія}}, отримано {type(data).__name__} — перенесень не буде", file=sys.stderr)
        return _CATEGORY_OVERRIDES
    bad = []
    for k, v in data.items():
        if isinstance(k, str) and k.startswith("_"):
            continue
        if isinstance(k, str) and re.fullmatch(r"[0-9]{1,12}", k) and isinstance(v, str) and (v.strip() in known_categories or v.strip() in NEW_CATEGORIES_ALLOWED) and not category_hidden(v):
            _CATEGORY_OVERRIDES[k] = v.strip()
        else:
            bad.append(str(k)[:20])
    if bad:
        print(f"[build] ⚠ category_overrides.json: {len(bad)} записів відкинуто (id не з цифр, цілі нема серед категорій сайту або вона прихована; товар лишається у своїй категорії): {bad[:10]}", file=sys.stderr)
    return _CATEGORY_OVERRIDES


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
    by_slug = {sl: c for c, sl in cat_slug.items() if not category_hidden(c)}
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


UTSINKA_FILE = "utsinka.html"
UTSINKA_NOTICE = ('<div class="md-notice" role="note"><b>Уцінений товар:</b> має дефект або неповну комплектацію — це вказано в назві/описі. '
                  'Дефект, указаний у назві чи описі, є умовою продажу за зниженою ціною. '
                  'Перед покупкою прочитайте опис. <a href="returns.html">Умови повернення</a></div>')
# власник 16.10 (скетч підтверджено: «так»): на сторінці Уцінки примітка — у закріпленій панелі (назва + сортування + примітка не йдуть за прокруткою); повний текст на ПК, на телефоні — один рядок
UTSINKA_NOTICE_BAR = ('<div class="md-notice md-notice-bar" role="note"><span class="md-full"><b>Уцінений товар:</b> має дефект або неповну комплектацію — це вказано в назві/описі. '
                      'Дефект, указаний у назві чи описі, є умовою продажу за зниженою ціною. Перед покупкою прочитайте опис. </span>'
                      '<span class="md-short"><b>Уцінка:</b> дефект вказано в описі товару. </span><a href="returns.html">Умови повернення</a></div>')


def utsinka_pick(prods):
    """Сторінка /utsinka.html (SMM/власник 13.10): УСІ уцінені товари (категорія «Уцінка/Уценка» або «Уцінка.» в назві) у наявності, з фото і ВІДОМИМ внеском ≥ 0
    (збиткові на замовлення позиції не показуємо: власник — збитковість не планувалась); не репліки зброї. Сортування: ціна зростаюче (усі в наявності)."""
    out = [p for p in prods if _is_markdown_item(p) and p.get("photo") and (p.get("stock") or 0) > 0
           and p.get("contribution") is not None and p["contribution"] >= 0 and not is_home_excluded(p)]
    return sorted(out, key=lambda p: (p["price"], str(p["id"])))


def write_utsinka(prods, cat_list, cat_slug):
    """Пише /utsinka.html (+ пагінація як у каталозі). Повертає set імен записаних сторінок (порожній, якщо уцінених придатних нема)."""
    picked = utsinka_pick(prods)
    if not picked:
        print("[build] ⚠ utsinka: жодного придатного уціненого товару — /utsinka.html не пишемо", file=sys.stderr)
        return set()
    n = len(picked)
    desc = f"Уцінені іграшки PlutusToys: {_fmt_int(n)} товар{_plural(n)}, ціни від {picked[0]['price']} ₴. Дефект чи неповна комплектація вказані в назві й описі."
    return write_catalog("Уцінені іграшки", picked, cat_list, cat_slug, UTSINKA_FILE, active=None,
                         special={"h1": "Уцінені іграшки", "title": "Уцінені іграшки — знижені ціни | PlutusToys", "desc": desc, "notice": UTSINKA_NOTICE, "notice_bar": UTSINKA_NOTICE_BAR, "filters": True})


AKTSII_FILE = "aktsii.html"

# ── /helovin.html «Геловін» (власник 15.10: «це вкладка в Toysi, чому у нас немає цих товарів?» — вони в каталозі, але не зібрані: чип головної шукав «гелловін» (Toysi пише «Геловін»), не брав «маски/грим/помаду» і відсікав ціну < 100 ₴) ──
# Склад: id з вкладки Toysi (site/seasonal_halloween.json, 52 на 15.10) + товари в наявності, назва яких збігається з HELOVIN_RE. Вікно 1.10–2.11 за датою збірки; поза вікном сторінки нема (стара прибирається).
HELOVIN_FILE = "halloween.html"   # власник 16.10: єдина назва Halloween і в адресі (старий /helovin.html → 301, Apache); ім'я константи лишилось історичним
HELOVIN_JSON = os.path.join(os.path.dirname(os.path.abspath(__file__)), "seasonal_halloween.json")
HELOVIN_WINDOW = ((10, 1), (11, 2))
HELOVIN_MIN = 6
HELOVIN_RE = re.compile(r"ге?л+овін|хелловін|halloween|гарбуз|привид|скелет|павук|павуч|відьм|вампір|грим для|помада карнавальна|карнавальна маска|маска карнавальна", re.I)
# Хибні збіги за назвою (перевірка на проді 15.10: 49 із 102 «додаткових» — «Людина-павук», «Привид Києва», «Вампіріна», рюкзаки/пазли/подушки): не Геловін
HELOVIN_BAD = r"людин[аи]?[\s-]*павук|spider|марвел|marvel|привид\w*\s+києва|вампіріна|рюкзак|пазл|подушк|фотозона|супергер|funko|robo|пенал"


def helovin_pick(prods):
    """Товари для /halloween.html: спершу id з вкладки Toysi (її порядок), далі збіги за назвою; лише в наявності, з фото, не уцінка, не мілітарі/зброя. Ціни нижче порога вітрини (46–90 ₴) НЕ відсікаємо: це повний перелік, а не вітрина."""
    try:
        with open(HELOVIN_JSON, encoding="utf-8") as f:
            ids = [str(x) for x in (json.load(f).get("ids") or [])]
    except (OSError, ValueError, AttributeError):
        ids = []
    by = {p["id"]: p for p in prods}
    ok = lambda p: p.get("stock", 0) > 0 and p.get("photo") and not _is_markdown_item(p) and not is_home_excluded(p)   # noqa: E731
    out = [by[i] for i in ids if i in by and ok(by[i])]
    seen = {p["id"] for p in out}
    for p in prods:
        name = p.get("name") or ""
        if p["id"] in seen or not HELOVIN_RE.search(name) or re.search(HELOVIN_BAD, name, re.I) or HOME_SEASONAL_BAD_NAME.search(name) or HOME_SEASONAL_BAD_CAT.search(p.get("category") or "") or not ok(p):
            continue
        out.append(p)
    return out


def write_helovin(prods, cat_list=None, cat_slug=None):
    """Пише /halloween.html «Halloween» у вікні 1.10–2.11 (власник 16.10: шаблон як «Уцінка» — крупна назва + сортування серед товарів сторінки, пагінація); повертає set записаних сторінок (порожній — сторінки нема, стару прибере крок прибирання)."""
    if not seasonal_active(HELOVIN_WINDOW[0], HELOVIN_WINDOW[1]):
        return set()
    picks = helovin_pick(prods)
    if len(picks) < HELOVIN_MIN:
        return set()
    return write_catalog("Halloween", picks, cat_list or [], cat_slug or {}, HELOVIN_FILE, active=None,
                         special={"h1": "Halloween", "title": "Halloween: маски, грим, костюми, декор | PlutusToys",
                                  "desc": "Все для Halloween (Хелловін, Геловін) в PlutusToys: карнавальні маски, грим для обличчя, костюми й крила, світильники, павуки та декор. Є в наявності, доставка Новою Поштою.",
                                  "notice": '<p class="sale-lead">Маски, грим, костюми, декор і світильники до Halloween. Усе в наявності, відправляємо Новою Поштою по Україні.</p>', "filters": True, "list": "helovin"})


AKTSII_MIN = 1   # хоч одна придатна акційна картка; жодної (усі випали за перевірками) — сторінки й кнопки героя нема


def aktsii_pick(prods):
    """Картки «Знижки тижня» для /aktsii.html: товари з акційною ціною (old_price) у наявності; порядок — ціна зростаюче. Нема акції/нема придатних — []."""
    if not SALE_WEEK_ENABLED or not SALE_ACTIVE:
        return []
    out = sorted((p for p in prods if p.get("old_price") and p["stock"] > 0 and p.get("photo")), key=lambda p: (p["price"], str(p["id"])))
    return out if len(out) >= AKTSII_MIN else []


def write_aktsii(prods, cat_list=None, cat_slug=None):
    """Пише /aktsii.html (+ aktsii_2.html, aktsii_3.html …: PER_PAGE карток на сторінку, пагінація як у каталозі). Власник 16.10: заголовка не треба (H1 лише для структури/читачів екрана,
    sr-only), назв «Знижка тижня»/«Знижки −N%» нема; розмір знижки — бейдж «−N%» у правому верхньому куті картки (реальний % з цін). Події select_item «sale_week». Повертає set записаних сторінок."""
    picks = aktsii_pick(prods)
    if not picks:
        return set()
    n = len(picks)
    desc = f"Акції PlutusToys: {_fmt_int(n)} товар{_plural(n)} за акційними цінами, добірка оновлюється щодня. Стара ціна перекреслена, знижка вже в ціні."
    return write_catalog("Акції", picks, cat_list or [], cat_slug or {}, AKTSII_FILE, active=None,
                         special={"h1": "Акції", "h1_hidden": True, "title": "Акції | PlutusToys", "desc": desc, "notice": "", "list": "sale_week", "filters": True})


def _category_top_html(fname, cat_prods):
    """Вступний текст категорії зліва + (є текст) права колонка «Знижка тижня»/«Рекомендуємо»; на вузьких екранах колонка під текстом."""
    intro = _category_intro_html(fname, cat_prods)
    if not intro:
        return ""
    aside = _category_aside_html(cat_prods)
    return f'<div class="cat-top">{intro}{aside}</div>\n' if aside else intro


def write_catalog(title, prods, cat_list, cat_slug, fname, active, special=None):
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
        trail = [("Головна", "index.html")] + ([("Усі категорії", "catalog.html")] if active else []) + [(active or "Усі категорії", None)]
        if special:   # службова сторінка зі своїм H1/title/description (напр. /utsinka.html)
            full_t = special["title"] if k == 1 else special["title"].replace(" | PlutusToys", f" — сторінка {k} | PlutusToys")
            desc = (special["desc"] if k == 1 else f"Сторінка {k} з {pages}. " + special["desc"])[:DESC_MAX]
            trail = [("Головна", "index.html"), (special["h1"], None)]
        head = breadcrumb_ld(trail)
        head += _ld_script({"@context": "https://schema.org", "@type": "ItemList",
                            "itemListElement": [{"@type": "ListItem", "position": (k - 1) * PER_PAGE + i + 1,
                                                 "url": f"{SITE_URL}/{product_file(x['id'])}"} for i, x in enumerate(chunk)]})
        _bslug = (re.fullmatch(r"category-(.+)\.html", fname or "") or [None, None])[1]
        if active and not special and k == 1 and _bslug in BRAND_CATEGORIES:   # SEO 21.10: CollectionPage (BreadcrumbList і ItemList лишаються)
            head += _ld_script({"@context": "https://schema.org", "@type": "CollectionPage", "name": active, "url": f"{SITE_URL}/{fname}", "description": desc,
                                "about": {"@type": "Brand", "name": BRAND_CATEGORIES[_bslug]["brand"]}})
        if k > 1:
            head += f'<link rel="prev" href="{_page_fname(fname, k - 1)}">\n'
        if k < pages:
            head += f'<link rel="next" href="{_page_fname(fname, k + 1)}">\n'
        # Панель сорт/фільтр — лише на головному каталозі (стор.1). Працює клієнтом з index.json
        # (усі товари), тож фільтрує/сортує ВЕСЬ каталог, не лише сторінку. Прогресивне покращення:
        # без JS лишається статична пагінована сітка (SEO), контроли сховані (hidden) до ініціалізації.
        is_full = active is None and not special   # повний каталог (catalog.html і його сторінки)
        controls = _catalog_controls() if (is_full and k == 1) else ""
        static_grid = grid(chunk, eager_n=(4 if k == 1 else 0)) + _pager(fname, k, pages)
        if special and special.get("list"):   # /aktsii: select_item/view_item_list за data-list (initListTracking шукає #sale-list)
            static_grid = f'<div class="sale-list" id="sale-list" data-list="{special["list"]}">{static_grid}</div>'
        if controls:
            static_grid = f'<div id="cat-static">{static_grid}</div>'
        if is_full and k == 1 and SITE_GROUPS and controls:
            # власник 15.10 (схема з двох кроків): «Усі категорії» = сайдбар КАТЕГОРІЙ (групи Toysi, кожна веде на свою сторінку), назва окремим рядком, фільтри під нею (липкі), усі товари; слова «Каталог» і пункту «Усі товари» тут нема
            gj = json.dumps([{"n": g["display"], "s": [c for c, _n in g["subs"]], "o": g["own"] or ""} for g in SITE_GROUPS], ensure_ascii=False)
            side = "".join(f'<a class="sb-i sb-nav" href="{g["file"]}"><span>{esc(g["display"])}</span><em>{g["total"]}</em></a>' for g in SITE_GROUPS)
            trail1 = [("Головна", "index.html"), ("Усі категорії", None)]
            body = (
                crumbs_html(trail1) + '<div class="gp"><aside class="sb" aria-label="Категорії"><div class="sb-h">Категорії</div>' + side + '</aside><div class="gmain">'
                '<div class="cat-bar"><h1 class="page cat-h1">Усі категорії<span class="sr-only"> — каталог іграшок</span></h1>' + _catalog_controls(groups_json=gj) + catalog_mascot_html() + '</div>\n' +
                _catalog_js_grid() + static_grid + _catalog_cats_block(cat_list, cat_slug) + '</div></div>'
            )
            _write(cur, page(ptitle, body, extra_head=head, description=desc, canonical=cur, og_image=OG_IMAGE, title_full=full_t, noindex=bool(active and category_hidden(active))))
            written.add(cur)
            continue
        if special and special.get("filters") and k == 1:
            # власник 16.10 («не вистачає сортерів, збільши текст»): Уцінка/Акції — як каталог: крупна назва + сортування/категорія/ціна в закріпленій панелі; JS працює лише з id цієї сторінки
            ids_json = json.dumps([str(x["id"]) for x in prods]).replace("</", "<\\/")
            h1cls = "page cat-h1" + (" sr-only" if special.get("h1_hidden") else "")
            body = (
                crumbs_html(trail) +
                f'<div class="cat-bar"><h1 class="{h1cls}">{esc(special["h1"])}</h1>' + _catalog_controls() + (special.get("notice_bar") or "") + '</div>\n' +
                f'<script type="application/json" id="cat-ids">{ids_json}</script>\n' +
                ((special.get("notice") or "") if not special.get("notice_bar") else "") + _catalog_js_grid() + f'<div id="cat-static">{static_grid}</div>'
            )
            _write(cur, page(ptitle, body, extra_head=head, description=desc, canonical=cur, og_image=OG_IMAGE, title_full=full_t, noindex=bool(active and category_hidden(active))))
            written.add(cur)
            continue
        if is_full:
            # власник 15.10: «залиш тільки слово Каталог, в 1 лінію з сортуванням, сортування й категорії не мають зникати при прокрутці» → липка панель .cat-bar (H1 + контроли); крихти й «Сторінка 1 з N» на стор.1 не показуємо (BreadcrumbList лишається в <head>)
            body = (
                '<div class="cat-bar"><h1 class="page cat-h1">Каталог<span class="sr-only"> іграшок</span></h1>' + controls + (catalog_mascot_html() if controls else "") + '</div>\n' +
                (_catalog_js_grid() if controls else "") +
                (f'<p class="pagenote">Сторінка {k} з {pages}</p>\n' if k > 1 else "") +
                static_grid
            )
            _write(cur, page(ptitle, body, extra_head=head, description=desc, canonical=cur, og_image=OG_IMAGE, title_full=full_t, noindex=bool(active and category_hidden(active))))
            written.add(cur)
            continue
        if active and not special and k == 1:
            urls = json.dumps({c: f"category-{cat_slug[c]}.html" for c in cat_list}, ensure_ascii=False).replace("</", "<\\/")
            body = (
                crumbs_html(trail) +   # на сторінці категорії крихти лишаємо (навігація «Головна › Каталог › Категорія» + внутрішні посилання для SEO), «Сторінку 1 з N» прибрано
                f'<div class="cat-bar"><h1 class="page cat-h1">{esc(active)}</h1>' + _catalog_controls(preset=active, rtonly=bool(prods) and all(p.get("rt") for p in prods)) + catalog_mascot_html() + '</div>\n' +
                f'<script type="application/json" id="cat-urls">{urls}</script>\n' +
                chips(cat_list, cat_slug, active) +
                _category_top_html(fname, prods) +
                (COLORING_CAT_NOTICE if (active in COLORING_CATEGORIES and COLORING_BUILT) else "") +
                _catalog_js_grid() +
                f'<div id="cat-static">{static_grid}</div>' +
                _relcats_html(fname, cat_slug, active)
            )
            _write(cur, page(ptitle, body, extra_head=head, description=desc, canonical=cur, og_image=OG_IMAGE, title_full=full_t, noindex=bool(active and category_hidden(active))))
            written.add(cur)
            continue
        body = (
            crumbs_html(trail) +
            ("" if (special or active is None) else chips(cat_list, cat_slug, active)) +   # власник 14.10: у повному каталозі рядок чипів-категорій прибрано (є панель «Категорія» в сортуванні/фільтрах); на сторінках категорій чипи лишаються (навігація між категоріями)
            f'\n<h1 class="{"page sr-only" if (special and special.get("h1_hidden")) else "page"}">{esc(special["h1"] if special else (active or title))}</h1>\n' +
            (special["notice"] if special else "") +   # SEO 11.10: H1 категорії = лише назва («Каталог •» — у title/крихтах); повний каталог — «Каталог іграшок»
            (f'<p class="pagenote">Сторінка {k} з {pages}</p>\n' if pages > 1 else "") +
            (_category_intro_html(fname, prods) if (active and k == 1) else "") +
            controls +
            static_grid +
            (_relcats_html(fname, cat_slug, active) if (active and k == 1) else "")
        )
        _write(cur, page(ptitle, body, extra_head=head, description=desc, canonical=cur, og_image=OG_IMAGE, title_full=full_t, noindex=bool(active and category_hidden(active))))
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
                     ("stretch_yawn", 4400, 2, False), ("sneeze", 3500, 1, True), ("tailchase", 5976, 1, True), ("sleep", 4980, 1, True), ("rolljump", 4316, 1, True),
                     ("nod", 3486, 2, True, True),
                     # 4 нові сценки (SMM 16.10, власник: «усі сценки приймаються, ставте на сайт»): ms = фактична тривалість кадрів файлу; dip — кадр спокою відрізняється від poster. Вага: серце/стрибок 2, «думає»/подарунок 1 (відомі недоліки: «думає» наприкінці лисеня більший, gift — плями тіні)
                     ("heart", 2988, 2, True, True), ("think", 2988, 1, True, True), ("jump", 4150, 2, True, True), ("gift", 1660, 1, True, True),
                     ("wink", 4565, 2, True, True),   # «Підморгує» (SMM 16.10, 238 КБ, 55 кадрів)
                     ("shake", 4482, 1, True, True),
                     ("laugh", 4482, 1, True, True),
                     ("puff", 4482, 1, True, True),
                     ("lick", 4482, 2, True, True),
                     ("look", 3818, 1, True, True),
                     ("wag", 4482, 1, True, True),
                     ("gum", 4482, 2, True, True),
                     ("cool", 4482, 2, True, True),
                     ("fly", 4482, 2, True, True),
                     ("ice", 4482, 2, True, True),
                     ("duck", 4482, 2, True, True),    # «Гумова качка» (SMM 28.10, 305 КБ, 54 кадри; вага 2, dip; ліміт ваги рухів піднято до 8,0 МБ)
                     ("hedgehog", 4482, 2, True, True),    # «Їжак із коробки» (SMM 28.10, 288 КБ, 54 кадри; вага 2, dip)
                     ("ball", 4482, 1, True, True),    # «Червоний м'ячик» (SMM 28.10, 255 КБ, 54 кадри; вага 1, dip; лисеня без зміни масштабу)
                     ("paperplane", 4482, 1, True, True),    # «Паперовий літачок» (SMM 04.11, 250 КБ, 54 кадри; вага 1, dip)
                     ("dust", 4482, 2, True, True))   # «Пил із книжки» (dust, SMM 21.10, 245 КіБ, 54 кадри; вага 2; недолік джерела: кадр наближається до мордочки, нижній/правий край розчинено м'яко)   # «Морозиво» (ice, SMM 21.10, 245 КіБ, 54 кадри; вага 2: усе тіло з хвостом у кадрі; лисеня облизує величезне морозиво, крем на носі)   # «Окуляри» (cool, SMM 21.10, 247 КіБ; вага 2; недолік джерела: у середині кадр «наближається», нижній край розчинено) і «Муха» (fly, SMM 21.10, 240 КіБ; вага 2; недолік: кадр близько до мордочки, вуха місцями розмиті в русі)   # «Жуйка» (SMM 20.10, 240 КБ, 54 кадри; вага 2 — перша «сцена з історією»: надуває бульбашку, вона лопається, жуйка липне до носа; відомий недолік: після лопання кадр джерела збільшується, лисеня «ближче»)   # «Виляє хвостом» (SMM 20.10, 248 КБ, 54 кадри; вага 1: у джерела хвіст у кількох кадрах малюється по-різному, на кінці може виглядати відокремленим)   # «Озирається» (SMM 20.10, 232 КБ, 46 кадрів; вага 1: у джерела лисеня на ходу трохи змінює позу, кінець обрізано через слід водяного знака)   # «Облизує носа» (SMM 19.10, 247 КБ, 54 кадри; вага 2: найчистіший із нових — усе тіло й хвіст у кадрі, нічого не обрізано)   # «Надуває щоки» (SMM 19.10, 242 КБ, 54 кадри; вага 1: у джерела вирізано «другий хвіст», одна дрібна зелена цятка в одному кадрі, нижній/правий край розчинено м'яко)   # «Регоче» (SMM 19.10, 242 КБ, 54 кадри; вага 1: відомий недолік — нижня частина тіла обрізана краєм кадру, розчинена м'яким краєм, видно «бюст»)   # «Хитає головою (ні)» (SMM 19.10, 242 КБ, 54 кадри; вага 1: відомий недолік джерела — лисеня посередині руху трохи збільшується, вуха на кількох кадрах розмиті)   # «Киває» (SMM 16.10): 5-й елемент — dip: підміна poster↔кліп через «занурення» (кадр спокою відрізняється від спільного poster)
# Сезонний рух «Гелловін» (SMM 15.10): відьомська шапка, гарбуз; діє 15–31.10 за датою збірки (як чип «Хелловін»), вага 2. Його кадр спокою ≠ спільний poster (шапка, гарбуз), тому підміна poster↔кліп іде через «занурення» (data-dip: обгортка гасне на 0,17 с, підміна, повертається) —
# кросфейд дав би двійника крізь прозорий кліп. Вага рухів у репо ≤ 2,5 МБ: на час Гелловіну з ротації виходить слабкий tailchase (розмита пляма; рішення SMM), після 31.10 він повертається сам.
# (name, ms, weight, first_ok, dip, перший день (м, д), останній день, що замінює)
HERO_MASCOT_SEASONAL = (("halloween", 3320, 2, True, True, (10, 15), (10, 31), "tailchase"),
                        ("snowflake", 3818, 2, True, True, (10, 15), (12, 31), None))   # «Сніжинка» (SMM 15.10): червона шапка, ловить сніжинку; власник 15.10: «для чого так довго чекати? нехай зараз крутиться» — з дати деплою (15.10) до 31.12, а не з 25.11; tailchase НЕ витісняє (SMM: лишити в ротації); із Гелловіном 15–31.10 граються разом


def hero_mascot_html(hero_clips=None, lazy=False):
    """Анімований Плутус (poster + кліп, керує app.js initMascot за id hero-mascot/hero-mascot-clip): на головній у героя і (власник 20.10) у порожньому місці панелі каталогу/категорії. Один екземпляр на сторінку."""
    hero_clips = hero_clips if hero_clips is not None else hero_mascot_clips()
    lz = ' loading="lazy"' if lazy else ""
    return (
      f'<span class="mascot-wrap"><img class="mascot" id="hero-mascot" src="{asset_url(HERO_MASCOT_POSTER)}" alt="Плутус — маскот PlutusToys" width="130" height="100" decoding="async"{lz} '
      f'data-clips="{"|".join(esc(asset_url(f"assets/mascot/plutus_{c[0]}.webp")) for c in hero_clips)}" data-ms="{",".join(str(c[1]) for c in hero_clips)}" data-w="{",".join(str(c[2]) for c in hero_clips)}" data-nofirst="{",".join(str(i) for i, c in enumerate(hero_clips) if not c[3])}"'
      + (f' data-dip="{",".join(str(i) for i, c in enumerate(hero_clips) if c[4])}"' if any(c[4] for c in hero_clips) else "") + '>'
      '<img class="mascot mascot-clip" id="hero-mascot-clip" alt="" aria-hidden="true" width="130" height="100" decoding="async"></span>')


def catalog_mascot_html():
    """Плутус у порожньому місці панелі каталогу/категорії (власник 20.10: «анімувати як на головній, клікабельність та сама»); на вузьких екранах схований (CSS)."""
    return '<div class="cat-mascot">' + hero_mascot_html(lazy=True) + '</div>'


def hero_mascot_clips(today=None):
    """Рухи героя на ЗАРАЗ: базові 10 (HERO_MASCOT_CLIPS) + сезонні в межах дат (із витісненням слабшого). Кортеж: (name, ms, weight, first_ok, dip)."""
    clips = [(c[0], c[1], c[2], c[3], bool(len(c) > 4 and c[4])) for c in HERO_MASCOT_CLIPS]
    for n, ms, w, f, dip, first, last, replaces in HERO_MASCOT_SEASONAL:
        if seasonal_active(first, last, today):
            clips = [c for c in clips if c[0] != replaces] + [(n, ms, w, f, dip)]
    return clips


HOME_MIN_CONTRIB = 100.0           # внесок на замовлення, ₴ (рішення Консультанта: не ведемо вітрину позицією без маржі) — існуючий поріг «Новинок»
HOME_PICK_PRICE_MIN, HOME_PICK_PRICE_MAX = 150, 700    # ₴: імпульсна ціна (SMM): рамка, не мірило
HOME_PICK_COUNT, HOME_PICK_MAX_PER_CAT = 10, 2
# Категорії, яких у каруселі не буде: картини за номерами й брелоки (SMM: перші 10 не вони), зброя/репліки (як у фідах Google/Meta — перша вітрина не місце для спірного).
HOME_PICK_EXCLUDED_CATS = frozenset({"картини за номерами", "брелоки", "автомати і пістолети", "набори зі зброєю", "арбалети і луки", "зброя"})


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


def _eu_dst_hours(now_utc):
    """Київ без бази часових поясів: UTC+3 влітку (остання неділя березня 01:00 UTC … остання неділя жовтня 01:00 UTC), UTC+2 взимку."""
    def last_sunday(y, m):
        d = (datetime(y, m + 1, 1, tzinfo=timezone.utc) - timedelta(days=1))
        return d - timedelta(days=(d.weekday() + 1) % 7)
    y = now_utc.year
    start = last_sunday(y, 3).replace(hour=1)
    end = last_sunday(y, 10).replace(hour=1)
    return 3 if start <= now_utc < end else 2


def _kyiv_now():
    """Зараз за Києвом: Europe/Kyiv (зимовий час UTC+2 з 25.10, літній UTC+3), а якщо бази часових поясів нема (Windows без tzdata) — правило ЄС вручну (аудит 13.10: жорсткий UTC+3 зсував межі доби на годину взимку)."""
    now = datetime.now(timezone.utc)
    try:
        from zoneinfo import ZoneInfo
        return now.astimezone(ZoneInfo("Europe/Kyiv"))
    except Exception:  # noqa: BLE001 — немає tzdata
        return now + timedelta(hours=_eu_dst_hours(now))


def _env_pick_date():
    raw = os.environ.get("SITE_PICK_DATE", "").strip()
    if re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", raw):
        try:
            y, m, d = (int(x) for x in raw.split("-"))
            return datetime(y, m, d).date()
        except ValueError:
            return None   # «2026-02-30», «0000-01-01»: сміття в тестовій змінній не валить збірку
    return None


def _today():
    """Сьогодні за Києвом (з урахуванням зимового часу); env SITE_PICK_DATE=YYYY-MM-DD — для тестів і відтворення (той самий, що для ротації добірки)."""
    return _env_pick_date() or _kyiv_now().date()


def _pick_day():
    """Номер дня для щоденної ротації добірки (власник 13.10: «в каруселі крутяться одні й ті самі товари»). Київ, env SITE_PICK_DATE — лише для тестів.
    Добірка стабільна ПРОТЯГОМ доби (збірка кожні 2 год дає ту саму), а наступного дня — інша."""
    return _today().toordinal()


# Єдина політика «без мілітарі/зброї/книжок» для ВСІХ вітрин головної (home_pick усіх чипів і «Усі», «Популярне зараз», /utsinka, /aktsii; аудит 13.10: фільтри були різні — у «Усі» проходили «Танк Т-34», «Солдатики», «Снайпер гвинтівка з м'якими кулями»)
HOME_MILITARY_RE = re.compile(r"пістолет|зброя|зброї|військов|солдат|swat|бтр|автомат|гранат|армі|револьвер|рушниц|гвинтівк|кулемет|снайпер|дробовик|калаш|піхот|винищувач|\bтанк(?:и|а|у|ів|ом|ами|овий)?\b|\bппо\b|\bзсу\b", re.I)


# Водні бластери/пістолети («Водний автомат», «водний бластер») — водні іграшки, не зброя (SMM 13.10): скрізь відсіяні, КРІМ чипа «Активні ігри» (water_ok=True); решта військових слів і реплік зброї діє й там
HOME_WATER_RE = re.compile(r"водн|водян|бластер|water", re.I)
HOME_MILITARY_NOWATER_RE = re.compile(HOME_MILITARY_RE.pattern.replace("пістолет|", "").replace("автомат|", ""), re.I)


def is_home_water_toy(p) -> bool:
    return bool(HOME_WATER_RE.search(p.get("name") or ""))


def is_home_excluded(p, water_ok=False, allow_cats=frozenset()) -> bool:
    cat = (p.get("category") or "").strip().lower()
    name = p.get("name") or ""
    if water_ok and HOME_WATER_RE.search(name):
        return bool(HOME_MILITARY_NOWATER_RE.search(name) or is_weapon_replica(str(p["id"]), name))
    return bool((cat in HOME_PICK_EXCLUDED_CATS and cat not in allow_cats) or cat in NABORY_EXCLUDED_CATS or HOME_MILITARY_RE.search(name) or is_weapon_replica(str(p["id"]), name))


def home_pick(prods, pmin=None, pmax=None, min_contrib=None, extra_excluded_cats=frozenset(), allow_cats=frozenset(), name_re=None, bad_words=(), bad_cat_re=None, bad_name_re=None, alt_cat_re=None, water_ok=False, count=None, per_cat=None):
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
        water = water_ok and HOME_WATER_RE.search(name)   # водний бластер у чипі «Активні ігри» (SMM 13.10) — не зброя
        if (cat in HOME_PICK_EXCLUDED_CATS and cat not in allow_cats and not water) or cat in extra_excluded_cats or _is_markdown_item(p) or any(m in name.lower() for m in cp._BAD_NAME_MARKERS):
            continue
        if is_home_excluded(p, water_ok=water_ok, allow_cats=allow_cats):
            continue   # мілітарі/зброя/спецтехніка за назвою й категорією (єдина політика вітрин головної)
        if name_re is not None and not (name_re.search(name) or (alt_cat_re is not None and alt_cat_re.search(p.get("category") or ""))):
            continue   # тематичний набір (сезонні чипи): лише назви за ключовими словами
        if bad_words and not water and any(w in name.lower() for w in bad_words):
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
    orig = {c: list(v) for c, v in by.items()}   # смуга 25–75% — за ПОВНИМ списком категорії, а не за залишком після першої вибраної позиції (15.10: другий вибір опускався до 2-го з 8 за внеском)
    out, used = [], set()
    cnt_max, cat_max = (count or HOME_PICK_COUNT), (per_cat or HOME_PICK_MAX_PER_CAT)
    for _ in range(cat_max):
        for c in order:
            if len(out) >= cnt_max:
                break
            lst = [p for p in by[c] if _pick_key(p["name"]) not in used]
            if not lst:
                continue
            o = orig[c]
            lo0, hi0 = len(o) // 4, max(len(o) // 4 + 1, (3 * len(o)) // 4 + 1)
            ok = {id(x) for x in lst}
            band = [x for x in o[lo0:hi0] if id(x) in ok]
            if band:
                p = band[rng.randrange(len(band))]
            else:   # смуга порожня (вибрана/дубль за назвою) — як раніше, за залишком
                lo, hi = len(lst) // 4, max(len(lst) // 4 + 1, (3 * len(lst)) // 4 + 1)
                p = lst[rng.randrange(lo, min(hi, len(lst)))]
            used.add(_pick_key(p["name"]))
            out.append(p)
            by[c] = [x for x in by[c] if x is not p]
    rng.shuffle(out)
    return out


# Чипи над каруселлю головної (SMM 12.10): «До 300 ₴» (внесок ≥50), «300–700 ₴» (внесок ≥100, як основна добірка), «Набори» (відбір /nabory.html, перші 10 по колах груп). Набори — у <template> (інертні: фото не вантажаться,
# поки чип не натиснуто), у кожному ≤10 карток із бейджем залишку ≤3; перші картки НЕ eager. Ключі латиницею — у назвах подій home_chip_<key>.
HOME_CHIPS = (("nab", "Набори"),)
# Сезонні чипи (SMM 13.10; правила й підписи — її): показуються лише в своє вікно дат (за Києвом, на момент збірки; збірка кожні 2 год), для кожного — ключові слова в назві, stock ≥ 3 (як у плані SMM:
# build бере лише товари в наявності, окремого порога залишку нема), ціна 100–700, внесок ≥ 30; мілітарі/зброя (NABORY_BAD_WORDS) відсіюється. Менше HOME_SEASONAL_MIN карток — чип не показуємо.
# (key, назва чипа, підпис під чипами, ключові слова (regex, регістр ігнорується), перший день, останній день, додатковий regex виключень або None) — дні у вигляді (місяць, день).
HOME_SEASONAL = (
    ("hel", "Halloween", "Страшно весело — до Halloween", r"хелловін|ге?л+овін|halloween|привид|гарбуз|скелет|павук|павуч|вампір|відьм|грим|карнавальн", (10, 1), (10, 31), HELOVIN_BAD),
    ("ny", "Миколай і Новий рік", "Подарунки на Миколая і під ялинку", r"новорічн|ялинк|сніговик|санта|різдв|дід мороз|сніг|санки|зимов", (11, 25), (12, 31), None),
    # «Українське для дітей» (SMM 13.10): evergreen (весь рік). Додаткові виключення: війна/політика/обсценне, «фрази/гасла/слава» (невідомий зміст), англійська абетка, військові образи
    ("ukr", "Українське для дітей", "Своє, рідне", r"українськ|вишиван|патріот|тризуб|абетка", (1, 1), (12, 31), r"воєн|рус{1,2}ь?к|російськ|іди на|англійськ|фрази|гасл|слава|захисник|воїн|козак|зсу|герої|героям"),
)
# Інлайн-SVG іконки тематичних чипів (SMM 13.10: без картинок і емодзі, лише векторні; aria-hidden, текст чипа — окремо)
CHIP_ICONS = {
    "new": '<svg class="chip-ico ico-new" viewBox="0 0 24 24" width="18" height="18" aria-hidden="true" focusable="false"><path fill="currentColor" d="M12 2l2.2 6.3L21 9l-5 4.2 1.7 6.8L12 16.4 6.3 20l1.7-6.8L3 9l6.8-.7z"/></svg>',
    "hel": '<svg class="chip-ico ico-hel" viewBox="0 0 24 24" width="20" height="20" aria-hidden="true" focusable="false"><path d="M12 5.2c.2-1.2.9-2 2-2.4" fill="none" stroke="#7BC043" stroke-width="2" stroke-linecap="round"/><ellipse cx="12" cy="14" rx="9" ry="7.6" fill="#FF7A1A"/><path d="M8 11.4l2 1.8-2.2.2zM16 11.4l-2 1.8 2.2.2zM8.6 16.4q3.4 2.6 6.8 0l-.8 1.7-1.4-.9-1.2 1-1.2-1-1.4.9z" fill="#1B1030"/></svg>',
    "ny": '<svg class="chip-ico ico-ny" viewBox="0 0 24 24" width="20" height="20" aria-hidden="true" focusable="false"><g stroke="#fff" stroke-width="2" stroke-linecap="round"><path d="M12 2v20M3.3 7l17.4 10M3.3 17L20.7 7"/><path d="M9.5 3.6L12 6l2.5-2.4M9.5 20.4L12 18l2.5 2.4" fill="none" stroke-width="1.6"/></g></svg>',
    "pop": '<svg class="chip-ico ico-pop" viewBox="0 0 24 24" width="18" height="18" aria-hidden="true" focusable="false"><path fill="currentColor" d="M12.5 2c.6 3.6-2.8 4.8-4 8.2-.5 1.4-.2 2.7.5 3.7-1.7-.4-2.9-2-2.9-4 0 0-3 2.4-3 6.3A7.9 7.9 0 0 0 11.9 22 7.9 7.9 0 0 0 20 14.2c0-4.2-3.3-6.1-4.6-8.7-.6 1.2-.7 2.3-.6 3.2-1.8-1-3.1-3.4-2.3-6.7z"/></svg>',
    "bday": '<svg class="chip-ico ico-bday" viewBox="0 0 24 24" width="18" height="18" aria-hidden="true" focusable="false"><ellipse cx="12" cy="9" rx="5.6" ry="7" fill="currentColor"/><path fill="currentColor" d="M12 16.2l-2 2.6h4z"/><path d="M12 18.8c-1.2 1.3-1.2 2.6 0 3.2" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/></svg>',
    "school": '<svg class="chip-ico ico-school" viewBox="0 0 24 24" width="18" height="18" aria-hidden="true" focusable="false"><path d="M9 5.5V4.8A1.8 1.8 0 0 1 10.8 3h2.4A1.8 1.8 0 0 1 15 4.8v.7" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/><rect x="4.5" y="5.5" width="15" height="16" rx="4" fill="currentColor"/><rect x="8" y="14" width="8" height="5" rx="1.6" fill="#fff" opacity=".55"/></svg>',
    "duo": '<svg class="chip-ico ico-duo" viewBox="0 0 24 24" width="18" height="18" aria-hidden="true" focusable="false"><rect x="3" y="3" width="11.5" height="11.5" rx="2.4" fill="currentColor"/><rect x="9.5" y="9.5" width="11.5" height="11.5" rx="2.4" fill="currentColor" stroke="#fff" stroke-width="1.6"/></svg>',
    "quiet": '<svg class="chip-ico ico-quiet" viewBox="0 0 24 24" width="18" height="18" aria-hidden="true" focusable="false"><path fill="currentColor" d="M20 14.8A8.5 8.5 0 0 1 9.2 4 8.6 8.6 0 1 0 20 14.8z"/></svg>',
    "active": '<svg class="chip-ico ico-active" viewBox="0 0 24 24" width="18" height="18" aria-hidden="true" focusable="false"><path fill="currentColor" d="M13.5 2L5 13.4h5.6L9.5 22 19 10.2h-5.7z"/></svg>',
    "xmas": '<svg class="chip-ico ico-xmas" viewBox="0 0 24 24" width="18" height="18" aria-hidden="true" focusable="false"><path fill="currentColor" d="M12 2l4.6 6.2h-2.6L18 13.6h-3l3.6 4.9H5.4L9 13.6H6L10 8.2H7.4z"/><rect x="10.6" y="18.4" width="2.8" height="3.6" rx=".6" fill="currentColor"/></svg>',
    "ukr": '<svg class="chip-ico ico-ukr" viewBox="0 0 24 24" width="20" height="20" aria-hidden="true" focusable="false"><g fill="#FFD43B" stroke="#C99A00" stroke-width=".6"><ellipse cx="12" cy="4" rx="2" ry="3.2"/><ellipse cx="12" cy="20" rx="2" ry="3.2"/><ellipse cx="4" cy="12" rx="3.2" ry="2"/><ellipse cx="20" cy="12" rx="3.2" ry="2"/><ellipse cx="6.3" cy="6.3" rx="2" ry="3.2" transform="rotate(-45 6.3 6.3)"/><ellipse cx="17.7" cy="17.7" rx="2" ry="3.2" transform="rotate(-45 17.7 17.7)"/><ellipse cx="17.7" cy="6.3" rx="2" ry="3.2" transform="rotate(45 17.7 6.3)"/><ellipse cx="6.3" cy="17.7" rx="2" ry="3.2" transform="rotate(45 6.3 17.7)"/></g><circle cx="12" cy="12" r="4" fill="#5A3A12"/></svg>',
    "nab": '<svg class="chip-ico ico-nab" viewBox="0 0 24 24" width="20" height="20" aria-hidden="true" focusable="false"><path d="M12 12c-4-5-9-3-8 0s6 2 8 0zm0 0c4-5 9-3 8 0s-6 2-8 0z" fill="#E04E39"/><path d="M12 12l-3 8M12 12l3 8" stroke="#E8B04A" stroke-width="2.2" stroke-linecap="round"/><circle cx="12" cy="12" r="2.2" fill="#E8B04A"/></svg>',
}
HOME_SEASONAL_MIN = 6
CHIP_ICON_SALE_TAG = ('<svg class="btn-ico" viewBox="0 0 24 24" width="18" height="18" aria-hidden="true" focusable="false"><path fill="currentColor" d="M3 12.2V4.6A1.6 1.6 0 0 1 4.6 3h7.6l8.4 8.4a1.6 1.6 0 0 1 0 2.3l-6.9 6.9a1.6 1.6 0 0 1-2.3 0z"/><circle cx="8" cy="8" r="1.7" fill="#D93A3A"/></svg>')
CHIP_ICON_SALE = '<svg class="chip-ico ico-sale" viewBox="0 0 24 24" width="18" height="18" aria-hidden="true" focusable="false"><path fill="currentColor" d="M3 12.2V4.6A1.6 1.6 0 0 1 4.6 3h7.6l8.4 8.4a1.6 1.6 0 0 1 0 2.3l-6.9 6.9a1.6 1.6 0 0 1-2.3 0z"/><circle cx="8" cy="8" r="1.7" fill="#fff"/></svg>'   # іконка чипа-посилання «Уцінка» (це не набір, ключа в CHIP_ICONS нема)
# «Популярне зараз» — чип за ПОПИТОМ Google (власник 13.10: «чому не маркетинг за попитом»): список id від SEO у site/demand_featured.json
# {"date": "YYYY-MM-DD", "source": "Search Console 28d", "ids": ["..."], "categories": ["..."]}. Нема файлу / застарів (> DEMAND_MAX_AGE_DAYS) / менше DEMAND_MIN карток у наявності — чипа нема (не вигадуємо).
# Файл site/demand_featured.json від SEO: ПОТОЧНА версія date=2026-10-13 (Search Console, покази за 90 днів, 40 id; SEO оновлює щотижня; журнал змін і вимірів — SEO_ЖУРНАЛ_ЗМІН_І_ВИМІРІВ.md). Зрушення метрик відносити до дати зміни цього файлу (прохання SEO).
DEMAND_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "demand_featured.json")
DEMAND_MAX_IDS = 40
DEMAND_MAX_AGE_DAYS = 21
DEMAND_MIN = 6


def _read_demand_file(path, today=None, label="demand_featured.json"):
    """({'ids': [...], 'categories': [...], 'date': date|None, 'source': str}) або None. Нема файлу — None без шуму; є, але зламаний/застарілий — None ГОЛОСНО (stderr)."""
    try:
        with open(path, encoding="utf-8-sig") as f:
            raw = f.read()
    except FileNotFoundError:
        return None
    except (OSError, UnicodeDecodeError) as e:
        print(f"[build] ⚠ {label} НЕ прочитано ({type(e).__name__}: {e}) — чипа «Популярне зараз» нема", file=sys.stderr)
        return None
    try:
        data = json.loads(raw)
    except (ValueError, RecursionError) as e:
        print(f"[build] ⚠ {label} — невалідний JSON ({e}) — чипа нема", file=sys.stderr)
        return None
    if not isinstance(data, dict) or not isinstance(data.get("ids"), list):
        print("[build] ⚠ " + label + ': очікується {"date":..., "ids":[...]} — чипа нема', file=sys.stderr)
        return None
    dt = None
    m = re.fullmatch(r"([0-9]{4})-([0-9]{2})-([0-9]{2})", str(data.get("date") or "").strip())
    if m:
        try:
            dt = datetime(int(m.group(1)), int(m.group(2)), int(m.group(3))).date()
        except ValueError:
            dt = None
    if dt is None:
        print(f"[build] ⚠ {label}: нема коректної date (YYYY-MM-DD) — чипа нема (без дати свіжість невідома)", file=sys.stderr)
        return None
    if ((today or _today()) - dt).days > DEMAND_MAX_AGE_DAYS:
        print(f"[build] ⚠ {label} застарів ({dt}, > {DEMAND_MAX_AGE_DAYS} днів) — чипа нема", file=sys.stderr)
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


DEMAND_AUTO_NAME = "demand_featured_auto.json"
TOYSI_TOP_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "toysi_top.json")
TOYSI_TOP_MAX_AGE_DAYS = 3     # файл оновлюється двічі на добу (08:05/16:05); старіший за це — чип «Новинки» ховаємо (не показуємо застаріле)


def _load_toysi_top(today=None):
    """{"ids": [...], "date": date} з site/toysi_top.json (власник 16.10: «Новинка — створіть чіп і покладіть туди ТОП відвантажень»). ids = lists.shipments, далі week (без повторів, порядок Toysi).
    Нема файлу — None без шуму (чип прихований); зламаний/без дати/старіший за TOYSI_TOP_MAX_AGE_DAYS — None голосно."""
    try:
        with open(TOYSI_TOP_FILE, encoding="utf-8-sig") as f:
            data = json.loads(f.read())
    except FileNotFoundError:
        return None
    except (OSError, ValueError, UnicodeDecodeError, RecursionError) as e:
        print(f"[build] ⚠ toysi_top.json НЕ прочитано ({type(e).__name__}) — чипа «Новинки» нема", file=sys.stderr)
        return None
    lists = data.get("lists") if isinstance(data, dict) else None
    if not isinstance(lists, dict):
        print('[build] ⚠ toysi_top.json: очікується {"date":..., "lists": {"shipments": [...]}} — чипа «Новинки» нема', file=sys.stderr)
        return None
    raw = str(data.get("date") or str(data.get("fetched_at") or "")[:10]).strip()
    m = re.fullmatch(r"([0-9]{4})-([0-9]{2})-([0-9]{2})", raw)
    dt = None
    if m:
        try:
            dt = datetime(int(m.group(1)), int(m.group(2)), int(m.group(3))).date()
        except ValueError:
            dt = None
    if dt is None:
        print("[build] ⚠ toysi_top.json: нема коректної date/fetched_at — чипа «Новинки» нема", file=sys.stderr)
        return None
    if ((today or _today()) - dt).days > TOYSI_TOP_MAX_AGE_DAYS:
        print(f"[build] ⚠ toysi_top.json застарів ({dt}, > {TOYSI_TOP_MAX_AGE_DAYS} днів) — чипа «Новинки» нема", file=sys.stderr)
        return None
    ids = []
    for key in ("shipments", "week"):
        for x in (lists.get(key) or []):
            if isinstance(x, bool) or not isinstance(x, (str, int)):
                continue
            sx = str(x).strip()
            if sx and sx not in ids:
                ids.append(sx)
    return {"ids": ids, "date": dt} if ids else None


def _load_demand_featured(today=None):
    """Список попиту для «Популярне зараз»: ручний від SEO (demand_featured.json, у git) і автоматичний від Code (demand_featured_auto.json, щоденно з Search Console, лише на VPS, не в git) — береться ТОЙ,
    у якого свіжіша date (при рівності — ручний). Застарілий/зламаний файл ігнорується голосно; обидва непридатні — None (чипа нема, не вигадуємо)."""
    manual = _read_demand_file(DEMAND_FILE, today, "demand_featured.json")
    auto = _read_demand_file(os.path.join(os.path.dirname(DEMAND_FILE), DEMAND_AUTO_NAME), today, DEMAND_AUTO_NAME)
    cands = [c for c in (manual, auto) if c]
    if not cands:
        return None
    return max(cands, key=lambda c: (c["date"], c is manual))


def _demand_ok(prods, demand=None):
    """Придатні картки зі списку попиту (site/demand_featured.json) у порядку списку: у наявності, з фото, внесок ≥ 0, не уцінка/зброя/мілітарі/книги, без дублів «основи назви»."""
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
        if (p.get("category") or "").strip().lower() in HOME_CHIP_EXCLUDED_CATS or HOME_SEASONAL_BAD_NAME.search(name) or HOME_SEASONAL_BAD_CAT.search(p.get("category") or "") or is_home_excluded(p):
            continue   # не іграшки (косметика/побутова хімія, 13.10: «Green Pharmacy» у «Популярне зараз»), мілітарі/зброя (єдиний фільтр), книжки/манга
        if any(_pick_key(q["name"]) == _pick_key(name) for q in ok):
            continue   # одна «основа назви» — одна картка (кілька однакових Trefl-пазлів поспіль)
        ok.append(p)
    return ok


def demand_pick(prods, demand=None):
    """До HOME_PICK_COUNT карток із списку попиту: у наявності, внесок відомий і ≥ 0, не «Уцінка»/зброя/мілітарі; щодня інший зріз серед топ-списку (стабільний у добі)."""
    ok = _demand_ok(prods, demand)
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
HOME_SEASONAL_BAD_CAT = re.compile(r"книг|книж|манга|комікс|художн|літератур", re.I)
HOME_SEASONAL_BAD_NAME = re.compile(r"манга|роман|молотов|pok[eé]mon|покемон|колекційн|\bппо\b|\bзсу\b", re.I)   # слова на межі слова: «ППО»/«ЗСУ» (воєнна тематика) відсіюємо, «гіппопотам» — ні
HOME_SEASONAL_MIN_CONTRIB = 30.0


def seasonal_active(first, last, today=None):
    t = today or _today()
    return (first[0], first[1]) <= (t.month, t.day) <= (last[0], last[1])
# Не іграшки (аудит 13.10: перша картка «До 300 ₴» на живих даних — «Тренировочні трусики…» з категорії «Клейонки, килимки і підгузники») — з чипів прибираємо.
HOME_CHIP_EXCLUDED_CATS = frozenset({"клейонки, килимки і підгузники", "гігієнічні вироби", "догляд за тілом", "догляд за тілом та волоссям", "догляд за волоссям", "косметичні засоби",
                                     "засоби для прання", "засоби для миття посуду", "прання та миття посуду", "догляд за будинком", "new: побутова хімія", "батарейки"})   # точні назви з index.json (аудит #714); не іграшки


# Тематичні чипи (SMM/власник 13.10): «До 300 ₴» і «300–700 ₴» прибрано (це сортування, не чипи). Evergreen (first/last = None) і дата-вікно («До Різдва» 01.12–25.12): ключові слова за НАЗВОЮ
# (або категорією alt_cat), власні межі ціни й внеску; не більше 2 з категорії (як у добірці), щоденна ротація; без мілітарі/зброї/книжок/манги, у наявності; менше HOME_SEASONAL_MIN — чипа нема.
# (key, назва, підпис, regex назви, перший день, останній день, ціна від, ціна до, внесок від, regex категорії-альтернативи; чип ідуть ПЕРЕД «Популярне зараз», якщо є дати — «front»).
HOME_THEMED = (
    ("xmas", "До Різдва", "Різдвяний настрій", r"різдв|ялинк|янгол|вертеп|\bзірк[аиуі]\b|гірлянд|новорічн\w* прикрас|ялинков\w* прикрас", (12, 1), (12, 25), 50, 700, 0.0, None),
    ("bday", "День народження", "", r"набір|конструктор|лялька|м[ʼ'’‘´`]?яка іграшка|подарунк", None, None, 150, 700, 50.0, None),
    ("school", "У садок і школу", "", r"рюкзак|пенал|канцел|фломастер|олівц|розмальов|пластилін|навчальн|картки|абетка|лічба", None, None, 100, 600, 30.0, None),
    ("duo", "Для двох гравців", "", r"\bнастільна гра\b|\bкарткова гра\b|\bшахи\b|\bдоміно\b|\bлото\b|\bтвістер", None, None, 50, 600, 0.0, r"настільн"),
    ("quiet", "Тихі ігри", "", r"пазл|розмальов|пластилін|тісто|мозаїка|наліпк|бісер|малюв|творчість", None, None, 100, 600, 30.0, None),
    ("active", "Активні ігри", "", r"м[ʼ'’‘´`]?яч|скакалк|\bобруч(?:і|ів|ем)?\b(?!\s+для\s+волосся)|самокат|кегл|бадмінтон|боулінг|мильні бульбашки|літак|планер|санки|водний бластер", None, None, 50, 700, 0.0, None),
)


def themed_active(first, last):
    return first is None or seasonal_active(first, last)


def home_chip_sets(prods, count=None, per_cat=None):
    """{key: [товари]} для чипів; порожні набори не повертаємо (чип не показуємо)."""
    sets = {
        "nab": [p for _g, p in nabory_pick(prods)[:HOME_PICK_COUNT]],
    }
    if count:   # окрема сторінка чипа: увесь придатний список попиту (до count), а не добірка з 10
        okd = _demand_ok(prods)
        dp = okd[:count] if len(okd) >= DEMAND_MIN else []
    else:
        dp = demand_pick(prods)
    if dp:
        sets["pop"] = dp
    top = _load_toysi_top()
    if top:   # «Новинки» = ТОП відвантажень Toysi (за фактом переважно свіжі надходження; власник 16.10) — ті самі правила придатності, що й попит
        nl = _demand_ok(prods, {"ids": top["ids"]})
        if len(nl) >= DEMAND_MIN:
            sets["new"] = nl[:count] if count else nl[:HOME_PICK_COUNT]
    for key, _label, _sub, rx, first, last, extra_bad in HOME_SEASONAL:
        if seasonal_active(first, last):
            pk = home_pick(prods, pmin=100, pmax=700, min_contrib=HOME_SEASONAL_MIN_CONTRIB, extra_excluded_cats=HOME_CHIP_EXCLUDED_CATS,
                           name_re=re.compile(rx, re.I), bad_words=NABORY_BAD_WORDS,
                           bad_cat_re=HOME_SEASONAL_BAD_CAT, count=count, per_cat=per_cat,
                           bad_name_re=(re.compile(HOME_SEASONAL_BAD_NAME.pattern + "|" + extra_bad, re.I) if extra_bad else HOME_SEASONAL_BAD_NAME))
            if len(pk) >= HOME_SEASONAL_MIN:
                sets[key] = pk
    for key, _label, _sub, rx, first, last, pmin, pmax, mc, alt in HOME_THEMED:
        if themed_active(first, last):
            pk = home_pick(prods, pmin=pmin, pmax=pmax, min_contrib=mc, extra_excluded_cats=HOME_CHIP_EXCLUDED_CATS,
                           name_re=re.compile(rx, re.I), alt_cat_re=(re.compile(alt, re.I) if alt else None), water_ok=(key == "active"), bad_words=NABORY_BAD_WORDS,
                           bad_cat_re=HOME_SEASONAL_BAD_CAT, bad_name_re=HOME_SEASONAL_BAD_NAME, count=count, per_cat=per_cat)
            if len(pk) >= HOME_SEASONAL_MIN:
                sets[key] = pk
    return {k: v for k, v in sets.items() if v}


# власник 16.10 («погоджуюсь»): чипи, що дублюють категорії, з головної прибрано — лишаються тематичні/сезонні (Хелловін/Миколай, Українське, Популярне зараз, День народження, До Різдва) і посилання «Уцінка»
HOME_CHIPS_DROPPED = frozenset({"nab", "school", "duo", "quiet", "active"})
HOME_MORE_COUNT = 10        # власник 16.10: під «Усі товари →» — 10 випадкових карток (щодня інші), бажано з «Популярне зараз»


def home_more_picks(prods, exclude_ids, cats, cat_list):
    """Картки під посиланням «Усі товари →» (власник 16.10): випадкові (зерно від дати) з «Популярне зараз» (список попиту), без тих, що вже в каруселі; якщо їх мало —
    доповнюємо по 1 товару з топ-категорій з внеском ≥ HOME_MIN_CONTRIB (як була колишня секція «Більше іграшок»)."""
    pool = [p for p in _demand_ok(prods) if p["id"] not in exclude_ids]
    rng = random.Random(HOME_PICK_SEED * 11 + _pick_day() * 104729)
    rng.shuffle(pool)
    out = pool[:HOME_MORE_COUNT]
    if len(out) < HOME_MORE_COUNT:
        taken = set(exclude_ids) | {p["id"] for p in out}
        for c in cat_list[:30]:
            lst = [p for p in cats[c] if p["id"] not in taken and p.get("contribution") is not None and p["contribution"] >= HOME_MIN_CONTRIB]
            if lst:
                p = lst[len(lst) // 2]
                out.append(p)
                taken.add(p["id"])
            if len(out) >= HOME_MORE_COUNT:
                break
    return out


HOME_SALE_CAR_MIN = 4      # карусель з акційних товарів вмикається, коли придатних акційних карток ≥ стільки (інакше — звична «Добірка Плутуса»)
HOME_SALE_CAR_PER_CAT = 2  # не більше стількох з однієї категорії (інакше найдешевші 10 — одна категорія)


def home_sale_picks(prods):
    """Наповнення каруселі з акційних товарів (власник 16.10): придатні акційні картки (aktsii_pick), по ≤ 2 з категорії, до HOME_PICK_COUNT, у порядку ціни. Менше HOME_SALE_CAR_MIN — [] (карусель лишається звичайною)."""
    per, out = {}, []
    for p in aktsii_pick(prods):
        c = (p.get("category") or "").strip().lower()
        if per.get(c, 0) >= HOME_SALE_CAR_PER_CAT:
            continue
        per[c] = per.get(c, 0) + 1
        out.append(p)
        if len(out) >= HOME_PICK_COUNT:
            break
    return out if len(out) >= HOME_SALE_CAR_MIN else []


# власник 16.10: «коли нажимаєш на чіп — перекидає на окрему сторінку, як при Уцінці, зі своїми сортувальниками». Чип → файл сторінки (Хелловін — наявний /helovin.html).
CHIP_PAGE_FILES = {"ukr": "podibrka-ukrainske.html", "pop": "podibrka-populiarne.html", "bday": "podibrka-den-narodzhennia.html",
                   "ny": "podibrka-mykolai-novyi-rik.html", "xmas": "podibrka-do-rizdva.html", "hel": "halloween.html", "new": "podibrka-novynky.html"}
CHIP_PAGE_N = 72           # до 3 сторінок по PER_PAGE
CHIP_PAGE_PER_CAT = 4      # різноманітність: не більше стількох з однієї категорії


def chip_page_sets(prods):
    """{key: [товари]} для окремих сторінок чипів (без Хелловіну — його сторінка зі своїм складом): ті самі правила, що в чипів, але до CHIP_PAGE_N карток."""
    sets = home_chip_sets(prods, count=CHIP_PAGE_N, per_cat=CHIP_PAGE_PER_CAT)
    return {k: v for k, v in sets.items() if k in CHIP_PAGE_FILES and k != "hel" and k not in HOME_CHIPS_DROPPED}


def write_chip_pages(prods, cat_list=None, cat_slug=None):
    """Пише сторінки чипів (podibrka-*.html + _2/_3): шаблон «Уцінки» — крупна назва, сортування/категорія/ціна лише серед товарів сторінки, пагінація. Повертає set файлів."""
    written = set()
    sets = chip_page_sets(prods)
    defs = {k: (lb, sb) for k, lb, sb in home_chip_defs(sets)}
    for k, items in sets.items():
        label, sub = defs[k]
        n = len(items)
        desc = f"{label}: добірка іграшок PlutusToys — {_fmt_int(n)} товар{_plural(n)} у наявності. " + (sub.rstrip(".") + ". " if sub else "") + "Відправляємо Новою Поштою по Україні."
        written |= write_catalog(label, items, cat_list or [], cat_slug or {}, CHIP_PAGE_FILES[k], active=None,
                                 special={"h1": label, "title": f"{label} — іграшки | PlutusToys", "desc": desc, "notice": (GIFT_CHIP_NOTICE if (k == "ny" and GIFT_BUILT) else ""), "filters": True})
    return written


def home_chip_defs(chip_sets):
    """[(key, label, sub)] у порядку показу: активні сезонні (з наборами; «До Різдва» серед них) → «Популярне зараз» → «Набори» → вічнозелені тематичні."""
    seasonal = [(k, lb, sb) for k, lb, sb, _rx, f, l, _eb in HOME_SEASONAL if k in chip_sets]
    dated = [(k, lb, sb) for k, lb, sb, _rx, f, l, _p1, _p2, _mc, _ac in HOME_THEMED if f is not None and k in chip_sets]
    evergreen = [(k, lb, sb) for k, lb, sb, _rx, f, l, _p1, _p2, _mc, _ac in HOME_THEMED if f is None and k in chip_sets]
    pop = [("pop", "Популярне зараз", "Те, що зараз шукають найчастіше")] if "pop" in chip_sets else []
    pop += [("new", "Новинки", "Свіжі надходження: ТОП відвантажень Toysi")] if "new" in chip_sets else []
    return seasonal + dated + pop + [(k, t, "") for k, t in HOME_CHIPS if k in chip_sets] + evergreen




def write_home(prods, cats, cat_list, cat_slug):
    sale_picks = home_sale_picks(prods)
    picks = sale_picks or home_pick(prods)
    more = home_more_picks(prods, {p["id"] for p in picks}, cats, cat_list)
    carousel = ""
    if picks:
        # перші 3 слайди без lazy (мобільний: видно 1,5–2; SMM 11.10), перший — fetchpriority=high (кандидат на LCP); слайди 4–5 на широкому екрані JS переводить в eager (initHomeCarousel)
        chip_sets = {k: v for k, v in home_chip_sets(prods, count=CHIP_PAGE_N, per_cat=CHIP_PAGE_PER_CAT).items() if k in CHIP_PAGE_FILES and k not in HOME_CHIPS_DROPPED}
        helovin_on = seasonal_active(HELOVIN_WINDOW[0], HELOVIN_WINDOW[1]) and len(helovin_pick(prods)) >= HELOVIN_MIN
        if not helovin_on:
            chip_sets.pop("hel", None)   # чип «Halloween» веде на /halloween.html — нема сторінки, нема чипа
        slides = "".join(tile(p, cls="slide", eager=(i < 3), prio=(i == 0), badge=True) for i, p in enumerate(picks))
        chips_html = ""
        if chip_sets or utsinka_pick(prods):
            # власник 16.10: чип = посилання на окрему сторінку (як «Уцінка»); карусель чипами не перемикається
            chips_html = (
              '<div class="chips home-chips" id="home-chips" role="group" aria-label="Підбір іграшок">'
              + "".join(f'<a class="chip{" chip-theme" if k in CHIP_ICONS else ""}" data-set="{k}" href="{CHIP_PAGE_FILES[k]}">{CHIP_ICONS.get(k, "")}<span>{esc(t)}</span></a>' for k, t, sb in home_chip_defs(chip_sets))
              + (f'<a class="chip chip-theme chip-link chip-sale" href="utsinka.html">{CHIP_ICON_SALE}<span>Уцінка</span></a>' if utsinka_pick(prods) else "")
              + '</div>')
        car_title = "Акції" if sale_picks else "Добірка Плутуса"
        carousel = (   # власник 16.10: чипи — ПІД каруселлю; карусель — з акційних товарів (поки акції нема — звична «Добірка Плутуса»)
          f'<h2 class="sr-only">{car_title}</h2>'
          f'<div class="car" id="home-car" role="region" aria-label="{car_title}" data-list="{car_title}">'
            '<button class="arrow l" type="button" aria-label="Попередні товари">‹</button>'
            f'<div class="track">{slides}</div>'
            '<button class="arrow r" type="button" aria-label="Наступні товари">›</button>'
          '</div>'
          + chips_html
        )
    # кнопка героя (власник 13.10): «Акції» → /aktsii.html (є придатні уцінені товари), інакше «Усі іграшки»; «Уцінка» — чип-посилання над каруселлю
    if aktsii_pick(prods):
        hero_act = f'<a class="btn" href="catalog.html">Каталог товарів</a><a class="btn btn-sale" href="aktsii.html">{CHIP_ICON_SALE_TAG}<span>Акції</span></a>'
    else:
        hero_act = '<a class="btn" href="catalog.html">Каталог товарів</a>'
    hero_clips = hero_mascot_clips()
    body = (
      # компактний герой (SMM 09.10: смуга ≤180 px; Плутус ліворуч, заголовок + кнопка + чип доставки; без великого абзацу)
      # poster завжди під низом, кліп кросфейдом поверх (див. initMascot): обидва <img> із width/height — CLS 0
      '<div class="hero2">' + hero_mascot_html(hero_clips) +
      '<div class="txt"><h1>Іграшки, що радують</h1><p>Обираємо те, що радує дитину.</p></div>'
      '<div class="act">' + hero_act + '</div></div>'
      + carousel
      + '<div class="sec-title more-title"><a href="catalog.html">Усі товари →</a></div>' + (grid(more) if more else "")   # власник 16.10: замість «Більше іграшок» — посилання «Усі товари →» (вигляд як «Усі знижки →», зліва) і під ним 10 випадкових зі «Популярне зараз»
    )
    _write("index.html", page(
        "Іграшки з доставкою Новою Поштою", body, extra_head=home_jsonld() + (f'<link rel="preload" as="image" href="{esc(picks[0]["photo"])}" fetchpriority="high">' + chr(10) if (picks and picks[0].get("photo")) else ""),   # LCP головної (виміряно 18.10: 2,8 с mobile, елемент — перше фото каруселі з toysi.ua): починаємо тягти до розбору розмітки
        description="Дитячі іграшки, від яких світяться очі 🦊 Конструктори, ляльки, машинки, розвиваючі та творчі набори. Іграшки для дітей від 1 року, повернення 14 днів.",
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
    specs = _shown_specs(parts)   # ті самі рядки бере й h1 групи однакових назв (assign_product_h1)
    desc_html = f"<p>{lead}</p>" if lead else ""
    specs_html = ("<ul>" + "".join(f"<li>{s}</li>" for s in specs) + "</ul>") if specs else ""
    # Текст картки від SEO (site/product_texts.json): під описом Toysi, ПЕРЕД його характеристиками (якщо вони є — вони йдуть окремим блоком нижче), лише після блоку ціни/оплати/доставки,
    # тож перший екран мобільного (фото, ціна, бейджі, закріплена панель «У кошик») не зміщується. Нема тексту — розмітка рівно як була.
    prod_text_html = _product_text_html(p["id"])
    _rv = _REVIEWS.get(str(p["id"])) or []   # ≤REVIEWS_SHOW_MAX уже відібраних; ті самі записи — і блок, і JSON-LD
    if prod_text_html and specs_html:
        desc_block = (f'<div class="desc"><h2>Опис</h2>{desc_html}</div>' + prod_text_html + f'<div class="desc specs">{specs_html}</div>')
    else:
        desc_block = f'<div class="desc"><h2>Опис</h2>{desc_html}{specs_html}</div>' + prod_text_html
    # Головне фото = LCP картки: fetchpriority=high каже браузеру тягнути його першим (решта фото сторінки — loading=lazy).
    # width/height не ставимо: розмір оригіналу toysi.ua невідомий, а місце під кадр вже резервує `.photo img{aspect-ratio:1/1}` у styles.css.
    photo = (f'<img src="{esc(p["photo"])}" alt="{esc(p["name"])}" width="800" height="800" fetchpriority="high" decoding="async">'
             if p["photo"] else '<div class="ph">Фото готуємо</div>')
    avail = "У наявності" if p["stock"] > 0 else "Немає в наявності"
    oos = "" if p["stock"] > 0 else "oos"
    in_stock = p["stock"] > 0
    # Без залишку: чесний текст + шлях далі (категорія, якщо вона є в каталозі; інакше повний каталог). Кнопка «У кошик» вимкнена нижче.
    _oos_cat_href = f"category-{cat_slug[p['category']]}.html" if (cat_slug and p.get("category") in cat_slug) else "catalog.html"
    _phone_line = ""   # власник 14.10: рядок «Є питання? + телефон» на картці прибрано («не навантажуй»); контакти — у футері й на сторінці «Контакти»
    # Без залишку: НЕ обіцяємо оплату/доставку/повернення на сторінці з вимкненою кнопкою (аудит #675, Д2) — лише телефон.
    buy_info = (
        f'<p class="ret-line">{RETURN_LINE}</p>'
        '<div class="trust-badges">'
          '<span class="tb">✓ Оплата при отриманні</span>'
          '<a class="tb" href="returns.html">✓ Повернення 14 днів</a>'
          '<span class="tb">✓ Доставка Новою Поштою</span>'
          '<span class="tb">✓ Фіскальний чек</span>'
        '</div>' + _phone_line +
        '<div class="delivery"><span class="fox"><img class="mascot" src="assets/plutus_mascot_s.png" alt="Плутус" width="31" height="28" decoding="async"></span>'
        '<div><b>Доставка Новою Поштою</b> — ' + DELIVERY_COST + '; при накладеному платежі додається комісія НП: ' + DELIVERY_FEE_FORMULA_SHORT + '. Доставка сплачується при отриманні посилки. Замовлення до 12:00 йдуть того ж дня, '
        'далі 1–3 робочі дні.</div></div>'
    ) if in_stock else _phone_line
    if p.get("rt"):   # RoyalToys: лише передоплата
        buy_info = buy_info.replace("✓ Оплата при отриманні", "✓ Оплата карткою онлайн")
        buy_info = re.sub(r"; при накладеному платежі додається комісія НП:.*?Доставка сплачується при отриманні посилки\. ", ". Товар відправляється лише за передоплатою (оплата карткою онлайн). ", buy_info, flags=re.S)
    oos_note = ("" if in_stock else
                f'<div class="oos-note"><b>Зараз цього товару немає в наявності.</b> '
                f'<a href="{_oos_cat_href}">Подивитися схожі{(" в категорії «" + esc(p["category"]) + "»") if _oos_cat_href != "catalog.html" else " в каталозі"} →</a></div>')
    add = json.dumps({"id": p["id"], "name": p["name"], "price": p["price"], "photo": p["photo"]},
                     ensure_ascii=False)
    # OOS: кнопку деактивуємо (немає data-add) — не даємо покласти в кошик те, чого нема (баг рев'ю).
    buy_btn = (f"<button class=\"btn\" data-add='{esc(add)}'>У кошик</button>" if in_stock
               else '<button class="btn" disabled>Немає в наявності</button>')
    # Тінь (RoyalToys-дубль Toysi у наявності): сторінка жива лише як резерв (noindex + canonical), але купити тут не можна й ціну не показуємо — інакше той самий товар
    # на двох живих сторінках має дві ціни (тестувальник 21, 20.10). Покупця ведемо на сторінку Toysi.
    _shadow_twin = product_file(p["shadow_to"]) if (p.get("shadow") and p.get("shadow_to")) else ""   # shadow_to — id кінцевої живої картки (Toysi цифри / kv-<id>); product_file дає публічну адресу
    _buy_p = f'<div class="p">{p["price"]} ₴</div>'
    _shadow_note = ""    
    if _shadow_twin:
        buy_btn = f'<a class="btn" href="{_shadow_twin}">Перейти до товару</a>'
        _buy_p = ""    
        _shadow_note = f'<div class="oos-note shadow-note"><b>Цей товар продається на іншій сторінці.</b> <a href="{_shadow_twin}">Перейти до товару →</a></div>'
    _bslug_p = (cat_slug or {}).get(p.get("category"))   # брендова категорія → посилання «Усі пазли …» (SEO 21.10)
    body = (
      '<div class="pad-bar">'
      f'<div class="photo">{photo}</div>'
      '<div class="body">'
      f'<div class="cat">{esc(p["category"])}</div>'
      + (f'<a class="brandlink" href="category-{_bslug_p}.html">{esc(BRAND_CATEGORIES[_bslug_p]["link_text"])} →</a>' if _bslug_p in BRAND_CATEGORIES else '') +
            f'<h1 class="prod">{esc(PRODUCT_H1.get(p["id"], p["name"]))}</h1>'
      f'<div class="sku">Код товару: <b class="sku-code">{esc(site_product_code(p["id"]))}</b>'
      f' <button type="button" class="sku-copy" data-copy="{esc(site_product_code(p["id"]))}" aria-label="Копіювати код товару {esc(site_product_code(p["id"]))}">Копіювати</button><span class="sr-only sku-status" role="status" aria-live="polite"></span></div>'
      + (_shadow_note if _shadow_twin else (f'<div class="price-row"><div class="price sale-price">{p["price"]} ₴</div><s class="pr-old">{p["old_price"]} ₴</s>{sale_badge_html(p)}' if p.get("old_price") else f'<div class="price-row"><div class="price">{p["price"]} ₴</div>') +
      f'<div class="avail {oos}">{avail}</div></div>'
      + (sale_until_html(p["id"]) if p.get("old_price") else "") + oos_note + buy_info) +
      desc_block + _reviews_html(_rv) +
      '</div></div>'
      + (f'<section class="related"><h2>{esc(related_title)}</h2>{grid(related)}</section>' if related else "")
      + (f'<section class="related"><h2>{esc(similar_title)}</h2>{grid(similar)}</section>' if similar else "")
      + '<div class="buybar"><div class="buybar-inner">'
      f'{_buy_p}'
      f'{buy_btn}'
      '</div></div>'
    )
    # SEO: опис для сніпета (без HTML, ОБРІЗАНИЙ ПО СЛОВУ) + JSON-LD Product для Google Rich Results
    plain = re.sub(r"<[^>]+>", "", lead).strip()
    if snippet_use_own_text(plain, p["id"], p["name"]):
        plain = " ".join(_product_text_first_para(p["id"]).split())   # пробіли й переноси стиснуто (нема сирого перенесення в content); сніпет і JSON-LD: наш текст замість заглушки/короткого/російського опису постачальника (SEO, варіант Б; ворота SITE_SNIPPET_OWN_TEXT)
    meta_desc = product_description(p["name"], p["price"], "" if p.get("rt") else plain)   # rt: опис постачальника — анкетний («ТИП: …; ВІК: …»), у сніпет не годиться
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
        "sku": public_slug(p["id"]),   # RoyalToys при воротах — нейтральний slug
        "image": [p["photo"]] if p["photo"] else [],
        "description": _cut(plain, 500) or p["name"],
        "offers": {
            "@type": "Offer",
            "price": str(p["price"]), "priceCurrency": "UAH",
            "availability": "https://schema.org/InStock" if p["stock"] > 0 else "https://schema.org/OutOfStock",
            "url": f'{SITE_URL}/{product_file(p["id"])}',
            **offer_extras(p),
        },
    }
    if not brand:   # запасний бренд — поле vendor Toysi (SEO 18.10: brand у 65% карток); сміттєві значення не беремо, не вигадуємо
        _v = (p.get("vendor") or "").strip()
        if 2 <= len(_v) <= 60 and _v.lower() not in _BRAND_JUNK:
            brand = _v
    if brand:
        ld["brand"] = {"@type": "Brand", "name": brand}
    _gt = _valid_gtin(p.get("barcode"))
    if _gt:
        ld[_gt[0]] = _gt[1]
    if _rv:   # лише коли блок відгуків відрендерено на ЦІЙ сторінці (shippingDetails не ставимо — рішення власника)
        ld["aggregateRating"] = {"@type": "AggregateRating", "ratingValue": _review_avg(_rv), "reviewCount": str(len(_rv)), "bestRating": "5", "worstRating": "1"}
        ld["review"] = [{"@type": "Review", "author": {"@type": "Person", "name": r["author"]}, "datePublished": r["date"],
                         "reviewRating": {"@type": "Rating", "ratingValue": str(r["rating"]), "bestRating": "5"}, "reviewBody": r["text"]} for r in _rv]
    # безпечно в <script>: екрануємо КОЖЕН '<' у < (валідний JSON) — жоден HTML-вектор
    # (</script>, <!--, <script) не може вийти літерально, навіть із назви товару.
    # chr(92) = '\' — однозначно, без крихкого backslash-літерала.
    ld_json = json.dumps(ld, ensure_ascii=False).replace("<", chr(92) + "u003c")
    cat_href = f"category-{cat_slug[p['category']]}.html" if (cat_slug and p.get("category") in cat_slug) else None
    trail = [("Головна", "index.html")] + ([(p["category"], cat_href)] if cat_href else []) + [(p["name"], None)]
    extra = f'<script type="application/ld+json">{ld_json}</script>' + chr(10) + breadcrumb_ld(trail)
    if p.get("photo"):
        extra += f'<link rel="preload" as="image" href="{esc(p["photo"])}" fetchpriority="high">' + chr(10)   # LCP картки — головне фото
    body = crumbs_html(trail) + body
    _write(product_file(p['id']), page(
        p["name"], body, extra_head=extra, title_full=product_title_for(p, in_stock), description=meta_desc,
        canonical=(product_file(p["shadow_to"]) if p.get("shadow") and p.get("shadow_to") else product_file(p["id"])), og_image=p["photo"], og_type="product", noindex=bool(p.get("noindex"))))   # rt: хвиля не відкрита / тонкий опис

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


def _pay_radios():
    """Спосіб оплати (власник 13.10): коли онлайн-оплата ввімкнена (LIQPAY_LIVE) — «Оплата карткою онлайн» ПЕРША й обрана за замовчуванням, накладений платіж другий; інакше — як було (накладений обраний, слот тестового входу)."""
    cod = ('<label class="pay"><input type="radio" name="payment" value="cod"' + ("" if LIQPAY_LIVE else " checked") + '> '
           'Оплата при отриманні (накладений платіж на Новій Пошті)</label>')
    if LIQPAY_LIVE:
        card = ('<label class="pay"><input type="radio" name="payment" value="prepaid" checked> '
                'Оплата карткою онлайн ' + LIQPAY_LOGO_HTML.format(src=asset_url('assets/liqpay-logo.svg'), w=70, h=15) + '</label>')
        return card + cod
    # Прихований вхід для ТЕСТОВОЇ оплати (запит Коду 12.10): до карти LiqPay live покупці її не бачать; радіо розкривається лише з ?liqpay_test=1 (пам'ятається в sessionStorage, див. app.js).
    # Бекенд у sandbox не передає такі замовлення в Toysi й не фіскалізує, тож випадковий клік шкоди не завдасть; тексти для всіх покупців не змінюємо.
    # Радіо НЕ рендериться в HTML (аудит 13.10: hidden програвав `.field label.pay{display:flex}` — тестове радіо було видно всім): порожній слот, радіо створює JS лише з ?liqpay_test=1.
    return cod + '<div id="pay-test-slot"></div>'


def write_cart(addons=None):
    # двоколонково на десктопі (SMM 12.10: права половина була порожня): ліворуч кошик + форма, праворуч sticky-колонка (А «Ваше замовлення», Б довіра, В «Додайте до замовлення», Г Плутус);
    # на мобільному — одна колонка: підсумок над формою (як було), «Додайте до замовлення» над кнопкою «Оформити», довіра й Плутус нижче форми.
    addons_json = json.dumps(addons or [], ensure_ascii=False).replace("</", "<\\/")
    body = (
      '<h1 class="page">Кошик</h1>'
      '<div class="cart-grid"><div class="cart-main"><div id="cart-body"></div>'
      f'<p class="ret-line ret-cart">{RETURN_LINE}</p>'
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
          + _pay_radios() +
          '<div class="note rt-note" id="rt-pay-note" hidden></div>'
        '</div>'
        # Telegram-статус (запит Коду 20.10): галочка лише керує тим, НАСКІЛЬКИ помітна кнопка «Старт у Telegram» на сторінці подяки (автопідписка без натискання «Старт» у Telegram неможлива); на сервер вибір не йде
        '<label class="tgopt"><input type="checkbox" id="f-tg" name="tg" checked> <span>Хочу отримувати статус замовлення в Telegram</span></label>'
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
      # Telegram-статус: блок видно, коли номер замовлення відомий (sessionStorage або ?o= після LiqPay); галочка «статус у Telegram» вимкнена → клас is-calm (тихіше, але кнопка є)
      '<div class="thanks-tg" id="thanks-tg-wrap" hidden>'
        '<p class="thanks-tg-hint" id="thanks-tg-hint" data-calm="Передумали? Натисніть «Старт» у боті — і ми напишемо вам, коли замовлення відправлять.">Натисніть «Старт» у боті — і ми напишемо вам, коли замовлення відправлять.</p>'
        '<a class="btn" id="thanks-tg" href="https://t.me/plutustoys_support_bot" target="_blank" rel="noopener">Старт у Telegram</a>'
      '</div>'
      '<p style="margin-top:20px"><a class="btn ghost" href="/" style="display:inline-block;max-width:260px">На головну</a></p>'
      '</div>'
    )
    _write("thanks.html", page("Дякуємо за замовлення", body, noindex=True, defer_trackers=False))   # purchase — без відкладання (рішення Консультанта 12.10)

REVIEW_RATING_LABELS = ((5, "чудово"), (4, "добре"), (3, "нормально"), (2, "погано"), (1, "дуже погано"))
# Скрипт форми відгуку: посилання `review.html#t=<токен>` — токен лише у ФРАГМЕНТІ (не йде ні в Referer, ні в журнали сервера; аудит 04.11); одразу після зчитування фрагмент прибирається
# з адресного рядка (history.replaceState), далі токен лише в змінній (не в HTML, не в логах, не в аналітиці) і йде лише в ТІЛІ POST api/review/form і POST api/review. Назва товару — textContent. `?t=` не підтримується.
_REVIEW_JS = (
    '(function(){'
    'var d=document,st=d.getElementById("rv-state"),f=d.getElementById("review-form"),msg=d.getElementById("rv-msg"),btn=d.getElementById("rv-submit"),'
    'ta=d.getElementById("rv-text"),cnt=d.getElementById("rv-count"),au=d.getElementById("rv-author"),pn=d.getElementById("rv-product"),busy=false,t="";'
    'function clean(){try{history.replaceState(null,"",location.pathname);}catch(e){}}'
    'function say(s){f.hidden=true;st.textContent=s;st.hidden=false;}'
    'function bad(){say("Посилання недійсне або вже використане.");}'
    'function later(){say("Не вдалося відкрити форму відгуку. Спробуйте пізніше.");}'
    'function unlock(){busy=false;btn.disabled=false;btn.textContent="Надіслати відгук";}'
    'var old=/[?&]t=/.test(location.search),m=/^#(?:.*&)?t=([^&]*)/.exec(location.hash||"");'
    'try{t=m?decodeURIComponent(m[1]):"";}catch(e){t="";}'
    'clean();'   # токен ОДРАЗУ прибираємо з адресного рядка (і з історії): далі він лише в змінній t; перезавантаження → «недійсне посилання» (прийнятно, аудит-2)
    'if(old||!t||t.length>512){bad();return;}'   # старий формат ?t= НЕ підтримуємо: query йде в Referer і журнали сервера
    'ta.addEventListener("input",function(){cnt.textContent=ta.value.length+" / 1500";});'
    'fetch("api/review/form",{method:"POST",cache:"no-store",credentials:"omit",headers:{"Content-Type":"application/json"},body:JSON.stringify({t:t})}).then(function(r){'   # токен у тілі, не в query: не потрапляє в журнал Apache
      'if(r.status===404||r.status===410){bad();return;}'
      'if(!r.ok){later();return;}'
      'return r.json().then(function(j){'
        'var nm=j&&(j.product_name||j.name||j.title);'
        'if(nm){pn.textContent=String(nm);}else{d.getElementById("rv-product-line").hidden=true;}'
        'st.hidden=true;f.hidden=false;'
      '});'
    '}).catch(later);'
    'f.addEventListener("submit",function(e){'
      'e.preventDefault();if(busy)return;'
      'var sel=f.querySelector(\'input[name="rating"]:checked\'),text=ta.value.trim(),name=au.value.trim();'
      'msg.textContent="";'
      'if(!sel){msg.textContent="Оберіть оцінку від 1 до 5.";return;}'
      'if(text.length<3){msg.textContent="Напишіть кілька слів про товар (щонайменше 3 символи).";ta.focus();return;}'
      'if(text.length>1500){msg.textContent="Відгук задовгий: до 1500 символів.";ta.focus();return;}'
      'if(name.length>40){name=name.slice(0,40);}'
      'busy=true;btn.disabled=true;btn.textContent="Надсилаємо…";'
      'fetch("api/review",{method:"POST",cache:"no-store",credentials:"omit",headers:{"Content-Type":"application/json"},'
        'body:JSON.stringify({t:t,rating:+sel.value,text:text,author_name:name})}).then(function(r){'
        'if(r.ok){t="";say("Дякуємо! Відгук з’явиться після перевірки.");return;}'
        'if(r.status===404||r.status===410||r.status===409){bad();return;}'
        'if(r.status===429){msg.textContent="Забагато спроб. Зачекайте кілька хвилин і спробуйте ще раз.";}'
        'else if(r.status===400||r.status===422){msg.textContent="Перевірте оцінку й текст відгуку та спробуйте ще раз.";}'
        'else{msg.textContent="Не вдалося надіслати відгук. Спробуйте пізніше.";}'
        'unlock();'
      '}).catch(function(){msg.textContent="Немає зв’язку із сервером. Спробуйте пізніше.";unlock();});'
    '});'
    '})();'
)


def write_review_page():
    """review.html — форма відгуку за одноразовим посиланням (`review.html#t=<токен>`; токен видає й перевіряє бекенд VPS: POST api/review/form, POST api/review — див. site/REVIEWS_CONTRACT.md).
    noindex, поза sitemap (не в static_urls) і Disallow у robots.txt; БЕЗ аналітики (analytics=False): page_view/Pixel відправили б адресу з токеном третім сторонам."""
    radios = "".join(
        f'<label class="pay"><input type="radio" name="rating" value="{n}" required> <span aria-hidden="true" style="color:var(--accent)">{_review_stars(n)}</span> {n} з 5 — {lbl}</label>'
        for n, lbl in REVIEW_RATING_LABELS)
    body = (
      '<section class="doc review-page" id="review-page">'
      '<h1 class="page" style="margin-left:0;margin-right:0">Відгук про товар</h1>'
      '<p id="rv-state" role="status" aria-live="polite">Перевіряємо посилання…</p>'
      '<form id="review-form" hidden novalidate>'
        '<p id="rv-product-line">Товар: <b id="rv-product"></b></p>'
        '<fieldset class="field" style="border:0;padding:0;margin:12px 0;min-width:0">'
          '<legend style="font-size:13px;color:var(--muted);margin-bottom:6px;padding:0">Ваша оцінка</legend>' + radios +
        '</fieldset>'
        '<div class="field"><label for="rv-text">Ваш відгук</label>'
          '<textarea id="rv-text" name="text" rows="6" minlength="3" maxlength="1500" required placeholder="Що сподобалось, що ні, кому підійде"></textarea>'
          '<div class="note" id="rv-count">0 / 1500</div></div>'
        '<div class="field"><label for="rv-author">Ваше ім’я <span style="opacity:.6">(необовʼязково)</span></label>'
          '<input id="rv-author" name="author_name" maxlength="40" autocomplete="given-name"></div>'
        '<button class="btn" type="submit" id="rv-submit">Надіслати відгук</button>'
        '<div class="note" id="rv-msg" role="alert" aria-live="polite"></div>'
        '<p class="note">Відгук з’явиться на сторінці товару після перевірки.</p>'
      '</form>'
      '</section>'
      '<script>' + _REVIEW_JS + '</script>'
    )
    _write("review.html", page("Відгук про товар", body, description="Залиште відгук про товар, придбаний у PlutusToys.", noindex=True, analytics=False,
                               head_first='<meta name="referrer" content="no-referrer">\n'))   # жодного Referer з цієї сторінки, ПЕРШИМ у head — до stylesheet/іконок (аудити 04.11)

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


_IG_URL_RX = re.compile(r"https://www\.instagram\.com/plutustoys\.ua/(?:reel|p)/[A-Za-z0-9_-]{5,40}/?")


# Рілси, що пише АВТОМАТ публікації (reel_autopublish, VPS): <корінь репо>/ig_reels.json — ПОЗА site/ і поза git (як supplier_rt.json), щоб git pull не конфліктував з локальним записом.
# Формат той самий: {"reels": [{"date": "2026-10-21", "url": "https://www.instagram.com/plutustoys.ua/reel/<код>/", "title": "...", "ids": [...]}]}. Рілси з обох файлів зливаються (за url без дублів), найновіший першим.
IG_AUTO_FILE = os.environ.get("IG_REELS_FILE") or os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "ig_reels.json")


def _parse_ig_reels(path, label):
    """Рілси з одного файлу: список {ids,url,title,date,iso} або None (нема файлу/не той формат). Некоректний рілс (нема ids/чужий url) — голосно, інші лишаються."""
    try:
        with open(path, encoding="utf-8-sig") as f:
            data = json.loads(f.read())
    except (OSError, UnicodeDecodeError, ValueError, RecursionError):
        return None   # про помилки читання основного файлу голосно скаже _load_ig_featured (старий шлях)
    reels = data.get("reels") if isinstance(data, dict) else None
    if not isinstance(reels, list):
        return None
    out = []
    for r in reels[:12]:
        if not isinstance(r, dict) or not isinstance(r.get("ids"), list):
            print(f"[build] ⚠ {label}: рілс без списку ids — пропущено", file=sys.stderr)
            continue
        ids = []
        for x in r["ids"][:IG_MAX_IDS]:
            if isinstance(x, bool) or not isinstance(x, (str, int)):
                print(f"[build] ⚠ {label}: id {x!r} не рядок/число — пропущено", file=sys.stderr)
                continue
            sx = str(x).strip()
            if sx and sx not in ids:
                ids.append(sx)
        url = r.get("url") if isinstance(r.get("url"), str) else ""
        if url and not _IG_URL_RX.fullmatch(url):
            print(f"[build] ⚠ {label}: url {url[:60]!r} не з instagram.com/plutustoys.ua — посилання не показуємо", file=sys.stderr)
            url = ""
        d = str(r.get("date") or "")
        m = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", d)
        out.append({"ids": ids, "url": url, "title": re.sub(r"\s+", " ", str(r.get("title") or "")).strip()[:60], "date": f"{m.group(3)}.{m.group(2)}" if m else "", "iso": d if m else ""})
    return out


def _load_ig_reels():
    """Кілька рілсів на /ig.html (SMM 19.10; автозапис — 20.10): site/ig_featured.json (SMM) + <корінь>/ig_reels.json (автомат публікації) = {"reels": [{"date","url","title","ids"}]}.
    Зливаємо за url (запис автомата перемагає), найновіший першим, ≤6. Старий формат {"date","ids"} (один рілс) без жодного файлу з "reels" — повертаємо None: сторінка збирається як раніше."""
    main = _parse_ig_reels(IG_FEATURED_FILE, "ig_featured.json")
    auto = _parse_ig_reels(IG_AUTO_FILE, "ig_reels.json")
    if main is None and auto is None:
        return None
    merged, seen = [], set()
    for r in (auto or []) + (main or []):
        key = r["url"] or ("noUrl:" + ",".join(r["ids"]) + r["iso"])
        if key in seen:
            continue
        seen.add(key)
        merged.append(r)
    merged.sort(key=lambda r: r["iso"], reverse=True)   # найновіший першим (без дати — в кінець)
    return merged[:6]


def write_ig(prods):
    """Сторінка /ig.html: сітка з товарів ig_featured.json, ЛИШЕ в наявності (нема — не показуємо, у лог іде попередження). noindex (кампанійна сторінка), canonical без параметрів
    (utm не плодять дублів). Не в sitemap (noindex). Трекінг: view_item_list/select_item з item_list_name «ig_landing» (assets/app.js, initListTracking)."""
    by_id = {p["id"]: p for p in prods}
    reels = _load_ig_reels()
    if reels:   # кілька рілсів: секція на кожен, найновіший першим (SMM 19.10)
        secs, n_shown = [], 0
        for r in reels:
            shown_r = [by_id[i] for i in r["ids"] if i in by_id and by_id[i]["stock"] > 0][:IG_MAX_ITEMS]
            miss = [i for i in r["ids"] if i not in by_id or by_id[i]["stock"] <= 0]
            if miss:
                print(f"[build] ⚠ ig_featured.json ({r['title'] or r['date']}): нема в наявності/каталозі — не показано: {miss}", file=sys.stderr)
            if not shown_r:
                continue
            head = esc(r["title"] or "Відео")
            sub = f' <span class="ig-date">· відео від {esc(r["date"])}</span>' if r["date"] else ""
            watch = f'<p class="ig-watch"><a href="{esc(r["url"])}" target="_blank" rel="noopener">Дивитися в Instagram →</a></p>' if r["url"] else ""
            secs.append(f'<section class="ig-reel"><h2 class="sec-h">{head}{sub}</h2>{watch}' + grid(shown_r, eager_n=min(3, len(shown_r)) if not secs else 0, badge=True) + '</section>')
            n_shown += len(shown_r)
        if secs:
            body = (
                '<h1 class="page">З Instagram: що показали Плутус</h1>'
                '<p class="ig-lead">Ці товари з наших останніх відео.</p>'
                f'<div class="ig-list" id="ig-list" data-list="{IG_LIST_NAME}">' + "".join(secs) + '</div>'
                '<p class="ig-all"><a class="btn" href="catalog.html">Усі іграшки</a></p>'
            )
            _write("ig.html", page("З Instagram: що показали Плутус", body, canonical="ig.html", noindex=True,
                                   description="Товари з наших відео в Instagram — PlutusToys."))
            return
        print("[build] ⚠ ig_featured.json: жодного товару в наявності у жодному рілсі — /ig.html показує лише кнопку каталогу", file=sys.stderr)
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


# ── Сторінки подарунків (SEO 15.11.2026, рішення власника «Усі три разом»; ТЗ — SEO_метрики/2026-11-15/ТЗ_сторінки_подарунків_статті_рілси_2026-11-15.md) ──
# 12 сторінок хвилі 1 (/podarunok-<slug>.html) під запити «подарунок дитині на N років / до N грн / на Миколая / новорічний» + хаб /podarunky.html.
# Добірка 24–48 товарів у наявності РАХУЄТЬСЯ при кожній збірці; менше GIFT_MIN_ITEMS — сторінку не будуємо (голосне попередження). Вступ і питання-відповіді
# пише Копірайтер у site/gift_pages.json: {"<slug>": {"intro": "…", "faq": [{"q": "…", "a": "…"}]}}; без тексту (вступ + ≥ GIFT_FAQ_MIN питань) сторінку не будуємо.
# Вік беремо з ТЕКСТУ опису (окремого поля «вік» у Toysi нема): «від 3 років», «Вік: 3 роки», «3+», «3–5 років»; невизначений вік — товар у вікові сторінки не потрапляє.
GIFT_TEXTS_FILE = os.path.join(OUT, "gift_pages.json")
GIFT_MIN_ITEMS = 12
GIFT_SHOW_MAX = 36
GIFT_PER_CAT = 2
GIFT_LIST_NAME = "gift_page"
GIFT_MIN_CONTRIB = 0.0
# дитячі подарунки: категорії з «космет…» (накладні вії, бальзами для губ тощо) не показуємо (15.11, живий замір складу; перевіряє _gift_ok)
GIFT_POOL_MAX = 4 * GIFT_SHOW_MAX               # пул найвигідніших кандидатів, з якого вибираємо рівномірно за ціною
GIFT_BANDS = 6                                  # «На Миколая»/«Новорічний» (SEO 16.11): діапазон 100–700 ₴ ділимо на смуги по 100 ₴; у кожній GIFT_SHOW_MAX // GIFT_BANDS карток: найкращий за внеском у кожній під-смузі ціни
GIFT_AGE_PRICE_MAX = 2000                       # вікові сторінки: подарунок до 2 000 ₴ (без басейнів/ігрових комплексів за 4–5 тис.)
GIFT_FAQ_MIN, GIFT_FAQ_MAX = 3, 6
GIFT_HUB_FILE = "podarunky.html"
# (slug, H1 = запит, підпис, правило). Правило: pmin/pmax — ціна (₴); age=(lo, hi) — мінімальний вік з опису в РОКАХ (місяці = N/12), включно; ny=True — спершу назви з HOME_SEASONAL «ny» (при bands — першими в межах смуги; на сторінці порядок за ціною); bands=N — діапазон pmin..pmax ділиться на N цінових смуг, у кожній по одному найкращому за внеском у кожній під-смузі ціни (SEO 16.11).
GIFT_PAGES = (
    ("podarunok-na-mykolaia", "Подарунок на Миколая дитині", "На Миколая", {"pmin": 100, "pmax": 700, "bands": GIFT_BANDS}),
    ("novorichnyi-podarunok-dytyni", "Новорічний подарунок дитині", "На Новий рік", {"pmin": 100, "pmax": 700, "ny": True, "bands": GIFT_BANDS}),
    ("podarunok-do-100-hrn", "Подарунок дитині до 100 грн", "До 100 грн", {"pmin": 0, "pmax": 100}),
    ("podarunok-do-200-hrn", "Подарунок дитині до 200 грн", "До 200 грн", {"pmin": 0, "pmax": 200}),
    ("podarunok-do-300-hrn", "Подарунок дитині до 300 грн", "До 300 грн", {"pmin": 0, "pmax": 300}),
    ("podarunok-do-500-hrn", "Подарунок дитині до 500 грн", "До 500 грн", {"pmin": 0, "pmax": 500}),
    ("podarunok-na-1-rik", "Подарунок дитині на 1 рік", "На 1 рік", {"age": (0, 1)}),
    ("podarunok-na-2-roky", "Подарунок дитині на 2 роки", "На 2 роки", {"age": (1, 2)}),
    ("podarunok-na-3-roky", "Подарунок дитині на 3 роки", "На 3 роки", {"age": (1, 3)}),
    ("podarunok-na-5-rokiv", "Подарунок дитині на 5 років", "На 5 років", {"age": (3, 5)}),
    ("podarunok-na-7-rokiv", "Подарунок дитині на 7 років", "На 7 років", {"age": (5, 7)}),
    ("podarunok-na-10-rokiv", "Подарунок дитині на 10 років", "На 10 років", {"age": (7, 10)}),
)
GIFT_PAGE_FILES = frozenset(f"{s}.html" for s, _h, _l, _r in GIFT_PAGES) | {GIFT_HUB_FILE}
GIFT_CHIP_NOTICE = '<p class="sale-lead">Подарунки за віком і бюджетом: <a href="podarunky.html">усі добірки</a>.</p>'   # на сторінці чипа «Миколай і Новий рік» (SEO 15.11): посилання на хаб
GIFT_BUILT = []    # [(slug, h1, label)] побудованих сторінок поточної збірки; footer() і чип «Миколай і Новий рік» читають
# Шаблон SEO (перевірений 15.11) + виправлення за аудитом #1505: «год»/«г.» без закінчення не беремо (це години: «від 5 год»), лише «года»; «N+» — плюс упритул до цифри (не «3 + 1 в подарунок»); «Вік: 3 роки» без «від».
_GIFT_AGE_RXS = (
    re.compile(r"(?:від|от|з|с)\s*(\d{1,2})\s*(?:-х\s*)?(рок|років|р\.|лет|года|міс|мес)", re.I),
    re.compile(r"(?<![\d,.xх×])(1[0-8]|[0-9])\+(?!\s*(?:[0-9]|дет|елем|кольор|см|мм|шт|предм|фігур|варіант|ігор|рівн|слів|карт|аркуш|сторін|гр))", re.I),   # позначка «3+»: впритул, ≤18, далі не кількість («5+ кольорів», «20+ елементів», «3x3+ см» — не вік)
    re.compile(r"(\d{1,2})\s*[-–]\s*\d{1,2}\s*(?:рок|років|лет)", re.I),
    re.compile(r"(?:вік|возраст)\s*:?\s*(\d{1,2})\s*(?:-х\s*)?(рок|років|роки|лет|года|міс|мес)", re.I),
)


def gift_min_age(desc):
    """Мінімальний рекомендований вік у РОКАХ з тексту опису (None — вік не визначено): перший за позицією збіг із _GIFT_AGE_RXS; місяці → N/12."""
    txt = re.sub(r"<[^>]+>", " ", html.unescape(desc or ""))
    best = None
    for rx in _GIFT_AGE_RXS:
        m = rx.search(txt)
        if m and (best is None or m.start() < best.start()):
            best = m
    if best is None:
        return None
    n = int(best.group(1))
    unit = (best.group(2) if best.re.groups >= 2 else "") or ""
    return n / 12.0 if unit.lower() in ("міс", "мес") else float(n)


def _gift_ok(p):
    """Спільні виключення (як у HOME_SEASONAL): у наявності, фото, внесок відомий, не уцінка/зброя/мілітарі/книжки/манга/не-іграшки."""
    name, cat = p.get("name") or "", p.get("category") or ""
    c = p.get("contribution")
    if (p.get("stock") or 0) <= 0 or not p.get("photo") or c is None or c < GIFT_MIN_CONTRIB:
        return False
    if _is_markdown_item(p) or any(m in name.lower() for m in cp._BAD_NAME_MARKERS) or is_weapon_replica(str(p["id"]), name) or any(w in name.lower() for w in NABORY_BAD_WORDS):
        return False
    if "космет" in cat.strip().lower() or cat.strip().lower() in HOME_CHIP_EXCLUDED_CATS or HOME_SEASONAL_BAD_NAME.search(name) or HOME_SEASONAL_BAD_CAT.search(cat) or is_home_excluded(p):
        return False
    return True


GIFT_MAX_AGE = 12   # на сторінках без вікового правила (ціна/сезон) картки з відомим мінімальним віком > 12 («14+», «18+») не показуємо (аудит #1505)


def gift_pool(prods):
    """[(товар, мінімальний вік у роках | None)] придатних для сторінок подарунків (спільні виключення, ціна > 0, не RT) — рахується ОДИН раз на збірку."""
    return [(p, gift_min_age(p.get("desc"))) for p in prods if not p.get("rt") and (p.get("price") or 0) > 0 and _gift_ok(p)]


def _gift_spread(items, n):
    """n позицій із items, РІВНОМІРНО за ціною (від найдешевшого до найдорожчого), порядок — за зростанням ціни; items ≤ n повертає як є (відсортовано за ціною)."""
    items = sorted(items, key=lambda p: (p.get("price") or 0, str(p["id"])))
    if n <= 0:
        return []
    if len(items) <= n:
        return items
    if n == 1:
        return items[:1]
    idx = sorted({round(i * (len(items) - 1) / (n - 1)) for i in range(n)})
    return [items[i] for i in idx]


def gift_pick(prods, rule, pool=None):
    """Добірка сторінки подарунків за правилом: з пулу найвигідніших (≤GIFT_POOL_MAX; ≤GIFT_PER_CAT з категорії, без дублів «основи назви») беремо ≤GIFT_SHOW_MAX РІВНОМІРНО за ціною (щоб були і дешеві, і дорожчі); новорічна — спершу «ny»-назви. Правило з bands (Миколай, новорічна) — по цінових смугах (_gift_pick_bands). pool — gift_pool(prods), якщо вже пораховано."""
    ny_rx = None
    if rule.get("ny"):
        ny_rx = next((re.compile(rx, re.I) for k, _l, _s, rx, _f, _la, _eb in HOME_SEASONAL if k == "ny"), None)
    pool = gift_pool(prods) if pool is None else pool
    out = _gift_pick_pool(pool, rule, ny_rx, True)
    if "age" in rule and len(out) < GIFT_MIN_ITEMS:   # стеля ціни не має ховати вікову сторінку (за < 12 — повторюємо без стелі)
        out = _gift_pick_pool(pool, rule, ny_rx, False)
    return out


def _gift_pick_bands(cand, rule, ny_rx=None):
    """Цінові смуги (SEO 16.11): [pmin, pmax] ділимо на rule["bands"] рівних смуг (остання включає pmax), у кожній — GIFT_SHOW_MAX // bands місць.
    cand уже відсортовано: «ny»-назви першими, далі за внеском спадно; ≤GIFT_PER_CAT з категорії на ВСЮ сторінку, без дублів «основи назви» (take()).
    У смузі: 1) «ny»-назви (≤ місць); 2) решта місць — по ОДНОМУ найкращому за внеском у кожній з рівних під-смуг ціни (смуга / кількість решти місць, ≈17 ₴),
    бо внесок росте разом із ціною: вибір «найкращі за внеском» у цілій смузі липне до її верху (на живому було 199×6, 299×6…), а під-смуги дають ціни по всій смузі;
    3) під-смуга без кандидатів — місце добирається найкращим із решти смуги; 4) якщо смуга порожня/мала — решту сторінки добираємо найкращими кандидатами залишку.
    Результат — за зростанням ціни."""
    n_b = int(rule["bands"])
    lo, hi = rule.get("pmin", 0), rule["pmax"]
    width = (hi - lo) / n_b
    per_band = max(1, GIFT_SHOW_MAX // n_b)
    out, per_cat, keys, used = [], {}, set(), set()

    def take(p):
        cat, k = (p.get("category") or "").strip().lower(), _pick_key(p["name"])
        if per_cat.get(cat, 0) >= GIFT_PER_CAT or k in keys:
            return False
        per_cat[cat] = per_cat.get(cat, 0) + 1
        keys.add(k)
        used.add(str(p["id"]))
        out.append(p)
        return True

    for b in range(n_b):
        blo, bhi = lo + b * width, lo + (b + 1) * width
        in_band = [p for p in cand if (blo <= p["price"] < bhi or (b == n_b - 1 and p["price"] == hi)) and str(p["id"]) not in used]
        got = 0
        if ny_rx:
            for p in in_band:
                if got >= per_band:
                    break
                if ny_rx.search(p.get("name") or "") and take(p):
                    got += 1
        n_sub = per_band - got
        if n_sub > 0:
            sw = (bhi - blo) / n_sub
            for s in range(n_sub):
                slo, shi = blo + s * sw, blo + (s + 1) * sw
                for p in in_band:
                    pr = p["price"]
                    if str(p["id"]) in used or not (slo <= pr < shi or (s == n_sub - 1 and b == n_b - 1 and pr == hi)):
                        continue
                    if take(p):
                        got += 1
                        break
        for p in in_band:   # під-смуга без кандидатів: місце — найкращому з решти смуги
            if got >= per_band:
                break
            if str(p["id"]) not in used and take(p):
                got += 1
    for p in cand:   # смуга без кандидатів не лишає порожніх місць на сторінці
        if len(out) >= GIFT_SHOW_MAX:
            break
        if str(p["id"]) not in used:
            take(p)
    return sorted(out[:GIFT_SHOW_MAX], key=lambda p: (p.get("price") or 0, str(p["id"])))


def _gift_pick_pool(pool, rule, ny_rx, price_cap):
    cand = []
    for p, a in pool:
        if "pmax" in rule and not (rule.get("pmin", 0) <= p["price"] <= rule["pmax"]):
            continue
        if "age" in rule:
            if a is None or not (rule["age"][0] <= a <= rule["age"][1]) or (price_cap and p["price"] > GIFT_AGE_PRICE_MAX):
                continue
        elif a is not None and a > GIFT_MAX_AGE:
            continue
        cand.append(p)
    cand.sort(key=lambda p: (0 if (ny_rx and ny_rx.search(p.get("name") or "")) else 1, -(p.get("contribution") or 0), str(p["id"])))
    if rule.get("bands"):
        return _gift_pick_bands(cand, rule, ny_rx)
    out, per_cat, keys = [], {}, set()
    for p in cand:
        cat, k = (p.get("category") or "").strip().lower(), _pick_key(p["name"])
        if per_cat.get(cat, 0) >= GIFT_PER_CAT or k in keys:
            continue
        per_cat[cat] = per_cat.get(cat, 0) + 1
        keys.add(k)
        out.append(p)
        if len(out) >= GIFT_POOL_MAX:
            break
    if ny_rx:
        ny = [p for p in out if ny_rx.search(p.get("name") or "")]
        rest = [p for p in out if not ny_rx.search(p.get("name") or "")]
        first = _gift_spread(ny, GIFT_SHOW_MAX)
        return first + _gift_spread(rest, GIFT_SHOW_MAX - len(first))
    return _gift_spread(out, GIFT_SHOW_MAX)


def _load_gift_texts(path=None):
    """site/gift_pages.json → {slug: {"intro": str, "faq": [(q, a), …]}}. Кривий запис пропускаємо ГОЛОСНО (сторінка без тексту не будується)."""
    path = path or GIFT_TEXTS_FILE
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, encoding="utf-8") as fh:
            raw = json.load(fh)
    except (OSError, ValueError) as e:
        print(f"[build] ⚠ gift_pages.json не читається ({e}) — сторінки подарунків не будуються", file=sys.stderr)
        return {}
    if not isinstance(raw, dict):
        print("[build] ⚠ gift_pages.json: корінь не об'єкт {slug: {intro, faq}} — сторінки подарунків не будуються", file=sys.stderr)
        return {}
    out = {}
    for slug, v in raw.items():
        if slug.startswith("_") or not isinstance(v, dict):
            continue
        intro = v.get("intro")
        _faq = v.get("faq")
        faq = [(x["q"].strip(), x["a"].strip()) for x in (_faq if isinstance(_faq, list) else []) if isinstance(x, dict) and isinstance(x.get("q"), str) and isinstance(x.get("a"), str) and x["q"].strip() and x["a"].strip()]
        if not isinstance(intro, str) or not intro.strip() or len(faq) < GIFT_FAQ_MIN:
            print(f"[build] ⚠ gift_pages.json[{slug}]: потрібні рядок intro і ≥{GIFT_FAQ_MIN} пар q/a — запис пропущено", file=sys.stderr)
            continue
        out[slug] = {"intro": intro.strip(), "faq": faq[:GIFT_FAQ_MAX]}
    return out


def _gift_paras(text):
    return "".join(f"<p>{esc(x.strip())}</p>" for x in re.split(r"\n\s*\n", text) if x.strip())


def gift_plan(prods, texts=None):
    """[(slug, h1, label, rule, items)] сторінок, які МОЖНА побудувати (є текст і ≥GIFT_MIN_ITEMS товарів); решту — голосне попередження в журнал збірки."""
    texts = _load_gift_texts() if texts is None else texts
    plan, pool = [], gift_pool(prods)
    for slug, h1, label, rule in GIFT_PAGES:
        if slug not in texts:
            print(f"[build] ⚠ gift: для {slug} нема тексту в gift_pages.json — сторінку не будуємо", file=sys.stderr)
            continue
        items = gift_pick(prods, rule, pool)
        if len(items) < GIFT_MIN_ITEMS:
            print(f"[build] ⚠ gift: {slug} — лише {len(items)} товарів за правилом (< {GIFT_MIN_ITEMS}) — сторінку не будуємо", file=sys.stderr)
            continue
        plan.append((slug, h1, label, rule, items))
    return plan


def write_gift_pages(prods, texts=None, plan=None):
    """Пише сторінки подарунків і хаб; повертає [(файл, пріоритет, частота)] для sitemap. H1 = запит; вступ і питання-відповіді — з gift_pages.json (видимі, збігаються з FAQPage)."""
    texts = _load_gift_texts() if texts is None else texts
    plan = gift_plan(prods, texts) if plan is None else plan
    GIFT_BUILT[:] = [(s, h, lb) for s, h, lb, _r, _i in plan]
    if not plan:
        return []
    written = []
    for slug, h1, label, rule, items in plan:
        t = texts[slug]
        fname = f"{slug}.html"
        trail = [("Головна", "index.html"), ("Подарунки", GIFT_HUB_FILE), (h1, None)]
        links = "".join(f'<li><a href="{s}.html">{esc(h)}</a></li>' for s, h, _lb in GIFT_BUILT if s != slug)
        faq_html = "".join(f"<h3>{esc(q)}</h3><p>{esc(a)}</p>" for q, a in t["faq"])
        arts = [(s_, t_) for s_, t_ in GIFT_ARTICLES_BUILT if s_ in PORADY_FOR_GIFT.get(slug, ())]
        art_html = ('<nav class="gift-links" aria-label="Статті-поради"><h2>Поради</h2><ul>' + "".join(f'<li><a href="{s_}.html">{esc(t_)}</a></li>' for s_, t_ in arts) + '</ul></nav>') if arts else ""
        body = (
            crumbs_html(trail) +
            f'<h1 class="page">{esc(h1)}</h1>'
            f'<div class="gift-intro">{_gift_paras(t["intro"])}</div>'
            f'<div class="gift-list" id="gift-list" data-list="{GIFT_LIST_NAME}">' + grid(items, eager_n=min(3, len(items)), badge=True) + '</div>'
            f'<section class="gift-faq"><h2>Питання й відповіді</h2>{faq_html}</section>' + art_html +
            f'<nav class="gift-links" aria-label="Інші добірки подарунків"><h2>Інші добірки подарунків</h2><ul>{links}</ul>'
            f'<p><a href="{GIFT_HUB_FILE}">Усі добірки подарунків</a> · <a href="catalog.html">Увесь каталог</a></p></nav>'
        )
        head = breadcrumb_ld(trail)
        head += _ld_script({"@context": "https://schema.org", "@type": "ItemList", "name": h1,
                            "itemListElement": [{"@type": "ListItem", "position": i + 1, "url": f"{SITE_URL}/{product_file(x['id'])}"} for i, x in enumerate(items)]})
        head += _ld_script({"@context": "https://schema.org", "@type": "FAQPage",
                            "mainEntity": [{"@type": "Question", "name": q, "acceptedAnswer": {"@type": "Answer", "text": a}} for q, a in t["faq"]]})
        first = re.split(r"(?<=[.!?])\s+", re.sub(r"\s+", " ", t["intro"]).strip())
        desc = _cut(" ".join(first[:2]), DESC_MAX) or h1
        _write(fname, page(h1, body, extra_head=head, description=desc, canonical=fname, og_image=OG_IMAGE, title_full=f"{h1} — ідеї та добірка | PlutusToys"))
        written.append((fname, "0.6", "weekly"))
    trail = [("Головна", "index.html"), ("Подарунки", None)]
    lis = "".join(f'<li><a href="{s}.html">{esc(h)}</a></li>' for s, h, _lb in GIFT_BUILT)
    _kinds = ([] if not any(s in ("podarunok-na-mykolaia", "novorichnyi-podarunok-dytyni") for s, _h, _lb in GIFT_BUILT) else ["на Миколая та Новий рік"]) + (["за ціною"] if any("-hrn" in s for s, _h, _lb in GIFT_BUILT) else []) + (["за віком, який рекомендує виробник"] if any(s.startswith("podarunok-na-") and s.endswith(("-rik", "-roky", "-rokiv")) for s, _h, _lb in GIFT_BUILT) else [])
    hub_kinds = ", ".join(_kinds) or "подарунки дитині"
    hub_body = (crumbs_html(trail) + '<h1 class="page">Подарунки дитині: добірки за віком і бюджетом</h1>'
                f'<p class="gift-hub-lead">Добірки іграшок у наявності: {esc(hub_kinds)}.</p>' +
                (f'<p class="gift-hub-lead"><a href="{COLORING_HUB_FILE}">Безкоштовні розмальовки з Плутусом для друку</a></p>' if COLORING_BUILT else "") +
                f'<nav class="gift-links" aria-label="Добірки подарунків"><ul>{lis}</ul></nav>'
                + (('<nav class="gift-links" aria-label="Статті-поради"><h2>Поради</h2><ul>' + "".join(f'<li><a href="{s_}.html">{esc(t_)}</a></li>' for s_, t_ in GIFT_ARTICLES_BUILT) + '</ul></nav>') if GIFT_ARTICLES_BUILT else ""))
    head = breadcrumb_ld(trail) + _ld_script({"@context": "https://schema.org", "@type": "ItemList", "name": "Подарунки дитині",
                                              "itemListElement": [{"@type": "ListItem", "position": i + 1, "name": h, "url": f"{SITE_URL}/{s}.html"} for i, (s, h, _lb) in enumerate(GIFT_BUILT)]})
    _write(GIFT_HUB_FILE, page("Подарунки дитині", hub_body, extra_head=head, description=_cut(f"Добірки подарунків для дітей: {hub_kinds}. Усе в наявності, доставка Новою Поштою.", DESC_MAX),
                               canonical=GIFT_HUB_FILE, og_image=OG_IMAGE, title_full="Подарунки дитині — добірки за віком і бюджетом | PlutusToys"))
    written.append((GIFT_HUB_FILE, "0.7", "weekly"))
    return written


# ── Статті-поради (SEO 15.11, ТЗ розд. 2): /porady-<slug>.html — підзаголовки-питання, коротка відповідь на початку кожного розділу, Article (+FAQPage) ──
# Тексти пише Копірайтер у site/porady.json: {"porady-<slug>": {"title", "published": "РРРР-ММ-ДД", "lead", "sections": [{"h", "short", "text"}], "faq": [{"q", "a"}]}}.
# Дозволені лише slug-и з PORADY_SLUGS (хвиля 1); решта — голосне попередження. Дата публікації — з файлу (dateModified = datePublished: не вигадуємо «свіжість»); автор — PlutusToys.
PORADY_SLUGS = ("porady-shcho-podaruvaty-na-mykolaia", "porady-podarunok-na-5-rokiv", "porady-yak-vybraty-nastilnu-hru", "porady-funko-pop-bitty-pop-klipsa", "porady-baby-born-za-vikom")
PORADY_FILE = os.path.join(OUT, "porady.json")
PORADY_FILES = frozenset(f"{s}.html" for s in PORADY_SLUGS)
PORADY_MIN_SECTIONS = 3
GIFT_ARTICLES_BUILT = []    # [(slug, title)] статей, які побудовано в цій збірці (сторінки подарунків і хаб лінкують лише на них)
# які статті лінкує яка сторінка подарунків (і, зворотно, на які сторінки подарунків веде стаття)
PORADY_FOR_GIFT = {
    "podarunok-na-mykolaia": ("porady-shcho-podaruvaty-na-mykolaia", "porady-podarunok-na-5-rokiv"),
    "novorichnyi-podarunok-dytyni": ("porady-shcho-podaruvaty-na-mykolaia", "porady-podarunok-na-5-rokiv"),
    "podarunok-do-100-hrn": ("porady-shcho-podaruvaty-na-mykolaia",),
    "podarunok-do-200-hrn": ("porady-shcho-podaruvaty-na-mykolaia",),
    "podarunok-do-300-hrn": ("porady-shcho-podaruvaty-na-mykolaia",),
    "podarunok-do-500-hrn": ("porady-shcho-podaruvaty-na-mykolaia",),
    "podarunok-na-1-rik": ("porady-shcho-podaruvaty-na-mykolaia",),
    "podarunok-na-2-roky": ("porady-shcho-podaruvaty-na-mykolaia",),
    "podarunok-na-3-roky": ("porady-shcho-podaruvaty-na-mykolaia", "porady-yak-vybraty-nastilnu-hru"),
    "podarunok-na-5-rokiv": ("porady-podarunok-na-5-rokiv", "porady-shcho-podaruvaty-na-mykolaia", "porady-yak-vybraty-nastilnu-hru"),
    "podarunok-na-7-rokiv": ("porady-shcho-podaruvaty-na-mykolaia", "porady-yak-vybraty-nastilnu-hru"),
    "podarunok-na-10-rokiv": ("porady-shcho-podaruvaty-na-mykolaia", "porady-yak-vybraty-nastilnu-hru"),
}   # зворотно: на які сторінки подарунків веде стаття (лише побудовані); статті лінкують і одна одну («Інші поради»)


def _load_porady(path=None):
    """site/porady.json → {slug: {title, published, lead, sections[(h, short, text)], faq[(q, a)]}}. Кривий запис пропускаємо ГОЛОСНО (стаття не будується); збірка не падає."""
    path = path or PORADY_FILE
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, encoding="utf-8") as fh:
            raw = json.load(fh)
    except (OSError, ValueError) as e:
        print(f"[build] ⚠ porady.json не читається ({e}) — статті-поради не будуються", file=sys.stderr)
        return {}
    if not isinstance(raw, dict):
        print("[build] ⚠ porady.json: корінь не об'єкт {slug: {…}} — статті-поради не будуються", file=sys.stderr)
        return {}
    out = {}
    for slug, v in raw.items():
        if slug.startswith("_"):
            continue
        if slug not in PORADY_SLUGS or not isinstance(v, dict):
            print(f"[build] ⚠ porady.json[{slug}]: невідомий slug або не об'єкт — пропущено (дозволені: {', '.join(PORADY_SLUGS)})", file=sys.stderr)
            continue
        title, pub, lead = v.get("title"), v.get("published"), v.get("lead")
        secs = [(x["h"].strip(), x["short"].strip(), (x.get("text") or "").strip()) for x in (v.get("sections") if isinstance(v.get("sections"), list) else [])
                if isinstance(x, dict) and isinstance(x.get("h"), str) and isinstance(x.get("short"), str) and x["h"].strip() and x["short"].strip() and isinstance(x.get("text") or "", str)]
        faq = [(x["q"].strip(), x["a"].strip()) for x in (v.get("faq") if isinstance(v.get("faq"), list) else []) if isinstance(x, dict) and isinstance(x.get("q"), str) and isinstance(x.get("a"), str) and x["q"].strip() and x["a"].strip()]
        _n_secs = len(v.get("sections") if isinstance(v.get("sections"), list) else []); _n_faq = len(v.get("faq") if isinstance(v.get("faq"), list) else [])
        if len(secs) < _n_secs or len(faq) < _n_faq:
            print(f"[build] ⚠ porady.json[{slug}]: відкинуто розділів {_n_secs - len(secs)} і пар q/a {_n_faq - len(faq)} (потрібні рядки h+short / q+a) — перевірте файл", file=sys.stderr)
        _pub_ok = False
        if isinstance(pub, str) and re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", pub):
            try:
                datetime.strptime(pub, "%Y-%m-%d")
                _pub_ok = True
            except ValueError:
                pass
        if not (isinstance(title, str) and title.strip() and isinstance(lead, str) and lead.strip() and _pub_ok and len(secs) >= PORADY_MIN_SECTIONS):
            print(f"[build] ⚠ porady.json[{slug}]: потрібні title, lead, published РРРР-ММ-ДД і ≥{PORADY_MIN_SECTIONS} розділів h/short — запис пропущено", file=sys.stderr)
            continue
        out[slug] = {"title": title.strip(), "published": pub, "lead": lead.strip(), "sections": secs, "faq": faq}
    return out


def _porady_date_ua(iso):
    y, m, d = iso.split("-")
    return f"{d}.{m}.{y}"


def write_porady(porady):
    """Пише статті-поради; повертає [(файл, пріоритет, частота)] для sitemap. Видимі розділи й FAQ = розмітка (Article, FAQPage, BreadcrumbList); посилання на сторінки подарунків — лише побудовані."""
    GIFT_ARTICLES_BUILT[:] = [(s, porady[s]["title"]) for s in PORADY_SLUGS if s in porady]
    written = []
    built_gifts = {s: h for s, h, _lb in GIFT_BUILT}
    for slug in PORADY_SLUGS:
        if slug not in porady:
            continue
        a = porady[slug]
        fname = f"{slug}.html"
        trail = [("Головна", "index.html")] + ([("Подарунки", GIFT_HUB_FILE)] if GIFT_BUILT else []) + [(a["title"], None)]
        secs_html = "".join(f'<section class="porady-sec"><h2>{esc(h)}</h2><p class="porady-short"><strong>{esc(short)}</strong></p>{_gift_paras(text)}</section>' for h, short, text in a["sections"])
        faq_html = ("<section class=\"gift-faq\"><h2>Часті запитання</h2>" + "".join(f"<h3>{esc(q)}</h3><p>{esc(an)}</p>" for q, an in a["faq"]) + "</section>") if a["faq"] else ""
        rel = [(g, built_gifts[g]) for g, arts in PORADY_FOR_GIFT.items() if slug in arts and g in built_gifts]
        _bl = {s: h for s, h in BRAND_BUILT}
        rel += [(b_, _bl[b_]) for b_ in BRAND_FOR_PORADY.get(slug, ()) if b_ in _bl]   # статті про бренди лінкують на побудовані сторінки брендів
        rel_links = "".join(f'<li><a href="{g}.html">{esc(h)}</a></li>' for g, h in rel)
        rel_html = ('<nav class="gift-links" aria-label="Добірки товарів за темою">' + (f'<h2>Добірки товарів за темою</h2><ul>{rel_links}</ul>' if rel else "")
                    + (f'<p><a href="{GIFT_HUB_FILE}">Усі добірки подарунків</a> · <a href="catalog.html">Увесь каталог</a></p>' if GIFT_BUILT else '<p><a href="catalog.html">Увесь каталог</a></p>') + '</nav>')
        other = [(s_, t_) for s_, t_ in GIFT_ARTICLES_BUILT if s_ != slug]
        other_html = ('<nav class="gift-links" aria-label="Інші поради"><h2>Інші поради</h2><ul>' + "".join(f'<li><a href="{s_}.html">{esc(t_)}</a></li>' for s_, t_ in other) + '</ul></nav>') if other else ""
        body = (crumbs_html(trail) + f'<article class="porady"><h1 class="page">{esc(a["title"])}</h1>'
                f'<p class="porady-by">PlutusToys · {_porady_date_ua(a["published"])}</p>'
                f'<div class="gift-intro">{_gift_paras(a["lead"])}</div>{secs_html}{faq_html}</article>{rel_html}{other_html}')
        head = breadcrumb_ld(trail)
        head += _ld_script({"@context": "https://schema.org", "@type": "Article", "headline": a["title"], "inLanguage": "uk", "datePublished": a["published"], "dateModified": a["published"],
                            "author": {"@type": "Organization", "name": "PlutusToys", "url": f"{SITE_URL}/"},
                            "publisher": {"@type": "Organization", "name": "PlutusToys", "url": f"{SITE_URL}/"},
                            "mainEntityOfPage": f"{SITE_URL}/{fname}", "description": _cut(a["lead"], DESC_MAX)})
        if a["faq"]:
            head += _ld_script({"@context": "https://schema.org", "@type": "FAQPage",
                                "mainEntity": [{"@type": "Question", "name": q, "acceptedAnswer": {"@type": "Answer", "text": an}} for q, an in a["faq"]]})
        first = re.split(r"(?<=[.!?])\s+", re.sub(r"\s+", " ", a["lead"]).strip())
        desc = _cut(" ".join(first[:2]), DESC_MAX) or a["title"]
        _write(fname, page(a["title"], body, extra_head=head, description=desc, canonical=fname, og_image=OG_IMAGE, og_type="article", title_full=(f'{a["title"]} | PlutusToys' if len(a["title"]) + 13 <= TITLE_MAX else a["title"])))
        written.append((fname, "0.6", "monthly"))
    return written


# ── Сторінки брендів (SEO 17.11.2026, ТЗ «сторінки брендів KIDDISVIT», рішення власника «Так, усе») ────────────────────────────────────────────────────────────────────────────────────
# 6 сторінок брендів (/brend-<slug>.html) + 6 підсторінок Funko (за шляхом категорій KIDDISVIT) + хаб /brendy.html. Добірка РАХУЄТЬСЯ при кожній збірці по ВСЬОМУ каталогу (Toysi + RT + kv; у наявності; не тіні);
# менше BRAND_MIN_ITEMS — сторінку не будуємо (голосне попередження). Вступ і питання-відповіді — site/brand_pages.json ({"<slug>": {"h1", "intro", "faq": [{"q","a"}]}}, пише Копірайтер); без тексту сторінку не будуємо.
# Сторінки будуються ЛИШЕ за воротами SITE_BRAND_PAGES_LIVE=1 (вимкнено за замовчуванням: SEO — запуск після SITE_KV_LIVE=1 і тіні дублів). Заборонені слова в текстах («оригінал/офіційний/ліцензійний/дистриб'ютор», ім'я постачальника) — збірка їх не пропускає.
BRAND_PAGES_LIVE = os.environ.get("SITE_BRAND_PAGES_LIVE") == "1"
BRAND_TEXTS_FILE = os.path.join(OUT, "brand_pages.json")
BRAND_HUB_FILE = "brendy.html"
BRAND_MIN_ITEMS = 12
BRAND_SHOW_MAX = 48
BRAND_FAQ_MIN, BRAND_FAQ_MAX = 3, 6
BRAND_LIST_NAME = "brand_page"
_BRAND_BAD_TEXT_RX = re.compile(r"оригінал|офіційн|ліцензійн|дистриб|kiddi\s*svit|кіддісвіт|\bkvu?-[0-9]|<|>", re.I)
BRAND_BUILT = []    # [(slug, h1)] побудованих сторінок поточної збірки; footer() і статті читають
BRAND_FOR_PORADY = {"porady-funko-pop-bitty-pop-klipsa": ("brend-funko-pop", "brend-funko-pop-filmy-serialy", "brend-funko-pop-multfilmy", "brend-funko-pop-anime", "brend-funko-pop-igry", "brend-funko-pop-harry-potter", "brend-funko-pop-marvel"),
                    "porady-baby-born-za-vikom": ("brend-baby-born",)}


def _brand_hay(p):
    return f'{p.get("name") or ""} {p.get("vendor") or ""}'.casefold()


_BR_FUNKO = re.compile(r"(?<![a-z0-9])funko(?![a-z])|фанко")
_BR_BORN = re.compile(r"baby[\s\-]*born|бе(?:бі|би|бі)[\s\-]*борн")
_BR_BRATZ = re.compile(r"(?<![a-z0-9])bratz(?![a-z0-9])|(?<![а-яіїєґ])братц(?![а-яіїєґ])")
_BR_SQUISH = re.compile(r"squishmal+ow|сквішмел+оу|сквишмел+оу")
_BR_RAINBOW = re.compile(r"rainbow[\s\-]*high(?![a-z0-9])|рейнбоу[\s\-]*хай")
_BR_LOL = re.compile(r"(?<![a-zа-яіїєґ0-9])(?:l\.\s?o\.\s?l|л\.\s?о\.\s?л)(?![a-zа-яіїєґ0-9])|(?<![a-zа-яіїєґ0-9])(?:lol|лол)[\s\-]*(?:surprise|сюрприз)")
# H1 «Ляльки …»: у добірці лише ляльки й супутні товари, без пазлів/картин за номерами/розмальовок/творчості (аудит #1706: у Rainbow High було 3 ляльки з 15)
_BR_NOT_DOLL_CAT = re.compile(r"пазл|картин|розмальов|мозаїк|пластилін|тісто|творч|фарб|наліп|книж|канцтовар|зошит|блокнот|алмазн|вишиван|розпис|гравюр", re.I)


def _doll_only(fn):
    return lambda p: fn(p) and not _BR_NOT_DOLL_CAT.search(str(p.get("category") or ""))


def _is_funko(p):
    return bool(_BR_FUNKO.search(_brand_hay(p)))


# (slug, батьківська сторінка або None, правило-предикат). Підсторінки Funko — лише картки Funko (H1 «Funko Pop …») з потрібним шляхом KIDDISVIT (поле src_path; Toysi/RT без шляху — лише на головній сторінці Funko).
BRAND_PAGES = (
    ("brend-funko-pop", None, _is_funko),
    ("brend-baby-born", None, _doll_only(lambda p: bool(_BR_BORN.search(_brand_hay(p))))),
    ("brend-bratz", None, _doll_only(lambda p: bool(_BR_BRATZ.search(_brand_hay(p))))),
    ("brend-squishmallows", None, lambda p: bool(_BR_SQUISH.search(_brand_hay(p)))),
    ("brend-rainbow-high", None, _doll_only(lambda p: bool(_BR_RAINBOW.search(_brand_hay(p))))),
    ("brend-lol-surprise", None, _doll_only(lambda p: bool(_BR_LOL.search(_brand_hay(p))))),
    ("brend-funko-pop-filmy-serialy", "brend-funko-pop", lambda p: _is_funko(p) and p.get("src_path") == "Funko > Герої фільмів та серіалів"),
    ("brend-funko-pop-multfilmy", "brend-funko-pop", lambda p: _is_funko(p) and p.get("src_path") == "Funko > Герої мультфільмів"),
    ("brend-funko-pop-anime", "brend-funko-pop", lambda p: _is_funko(p) and p.get("src_path") == "Ліцензійні герої > Герої Аніме"),
    ("brend-funko-pop-igry", "brend-funko-pop", lambda p: _is_funko(p) and p.get("src_path") == "Funko > Герої ігор"),
    ("brend-funko-pop-harry-potter", "brend-funko-pop", lambda p: _is_funko(p) and p.get("src_path") == "Funko > Гаррі Поттер"),
    ("brend-funko-pop-marvel", "brend-funko-pop", lambda p: _is_funko(p) and p.get("src_path") == "Хлопчикам > Ліцензійні герої > Marvel"),
)
BRAND_PAGE_FILES = frozenset(f"{s}.html" for s, _par, _fn in BRAND_PAGES) | {BRAND_HUB_FILE}


def _load_brand_texts(path=None):
    """site/brand_pages.json → {slug: {"h1", "intro", "faq": [(q, a), …]}}. Кривий запис, заборонені слова/теги — пропускаємо ГОЛОСНО (сторінка без тексту не будується)."""
    path = path or BRAND_TEXTS_FILE
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, encoding="utf-8-sig") as fh:
            raw = json.load(fh)
    except (OSError, ValueError) as e:
        print(f"[build] ⚠ brand_pages.json не читається ({e}) — сторінки брендів не будуються", file=sys.stderr)
        return {}
    if not isinstance(raw, dict):
        print("[build] ⚠ brand_pages.json: корінь не об'єкт {slug: {h1, intro, faq}} — сторінки брендів не будуються", file=sys.stderr)
        return {}
    out = {}
    for slug, v in raw.items():
        if slug.startswith("_") or not isinstance(v, dict):
            continue
        h1, intro, _faq = v.get("h1"), v.get("intro"), v.get("faq")
        faq = [(x["q"].strip(), x["a"].strip()) for x in (_faq if isinstance(_faq, list) else []) if isinstance(x, dict) and isinstance(x.get("q"), str) and isinstance(x.get("a"), str) and x["q"].strip() and x["a"].strip()]
        if not isinstance(h1, str) or not h1.strip() or not isinstance(intro, str) or not intro.strip() or len(faq) < BRAND_FAQ_MIN:
            print(f"[build] ⚠ brand_pages.json[{slug}]: потрібні рядки h1, intro і ≥{BRAND_FAQ_MIN} пар q/a — запис пропущено", file=sys.stderr)
            continue
        if _BRAND_BAD_TEXT_RX.search(" ".join([h1, intro] + [q + " " + a for q, a in faq])):
            print(f"[build] ⚠ brand_pages.json[{slug}]: заборонене слово («оригінал/офіційний/ліцензійний/дистриб'ютор», ім'я постачальника) чи «<» «>» — запис пропущено", file=sys.stderr)
            continue
        out[slug] = {"h1": h1.strip(), "intro": intro.strip(), "faq": faq[:BRAND_FAQ_MAX]}
    return out


def _brand_ok(p):
    return (p.get("stock") or 0) > 0 and bool(p.get("photo")) and (p.get("price") or 0) > 0 and not p.get("shadow") and not _is_markdown_item(p) and not is_weapon_replica(str(p["id"]), p.get("name") or "")


def brand_plan(prods, texts=None):
    """[(slug, h1, parent, items)] сторінок брендів, які МОЖНА побудувати (ворота відкриті, є текст, ≥BRAND_MIN_ITEMS товарів у наявності). Порядок добірки: за внеском спадно (невідомий — в кінець), потім ціна."""
    if not BRAND_PAGES_LIVE:
        return []
    texts = _load_brand_texts() if texts is None else texts
    pool = [p for p in prods if _brand_ok(p)]
    plan = []
    for slug, parent, fn in BRAND_PAGES:
        if slug not in texts:
            print(f"[build] ⚠ brand: для {slug} нема тексту в brand_pages.json — сторінку не будуємо", file=sys.stderr)
            continue
        items = [p for p in pool if fn(p)]
        if len(items) < BRAND_MIN_ITEMS:
            print(f"[build] ⚠ brand: {slug} — лише {len(items)} товарів за правилом (< {BRAND_MIN_ITEMS}) — сторінку не будуємо", file=sys.stderr)
            continue
        items.sort(key=lambda p: (-(p["contribution"] if p.get("contribution") is not None else -1e9), -(p.get("price") or 0), str(p["id"])))
        plan.append((slug, texts[slug]["h1"], parent, items[:BRAND_SHOW_MAX], texts[slug]))
    _have = {x[0] for x in plan}
    for x in [x for x in plan if x[2] is not None and x[2] not in _have]:   # підсторінка без збудованого батька (нема тексту/мало товарів) — не будуємо, інакше крихти ведуть у нікуди
        print(f"[build] ⚠ brand: {x[0]} — головна сторінка {x[2]} не збудована — підсторінку не будуємо", file=sys.stderr)
    plan = [x for x in plan if x[2] is None or x[2] in _have]
    return plan


def write_brand_pages(plan):
    """Пише сторінки брендів і хаб; повертає [(файл, пріоритет, частота)] для sitemap. Підсторінка Funko будується лише разом із головною сторінкою Funko (інакше хлібні крихти ведуть у нікуди)."""
    if not plan:
        return []
    written = []
    h1_of = {s: h for s, h, _par, _it, _t in plan}
    for slug, h1, parent, items, t in plan:
        fname = f"{slug}.html"
        trail = [("Головна", "index.html"), ("Бренди", BRAND_HUB_FILE)] + ([(h1_of[parent], f"{parent}.html")] if parent else []) + [(h1, None)]
        sib = [(s_, h_) for s_, h_, par_, _i, _t in plan if s_ != slug and par_ is None]
        sub = [(s_, h_) for s_, h_, par_, _i, _t in plan if par_ == slug]
        links = "".join(f'<li><a href="{s_}.html">{esc(h_)}</a></li>' for s_, h_ in sub + sib)
        arts = [(a_, t_) for a_, t_ in GIFT_ARTICLES_BUILT if slug in BRAND_FOR_PORADY.get(a_, ())]
        art_html = ('<nav class="gift-links" aria-label="Статті-поради"><h2>Поради</h2><ul>' + "".join(f'<li><a href="{a_}.html">{esc(t_)}</a></li>' for a_, t_ in arts) + '</ul></nav>') if arts else ""
        faq_html = "".join(f"<h3>{esc(q)}</h3><p>{esc(a)}</p>" for q, a in t["faq"])
        body = (
            crumbs_html(trail) +
            f'<h1 class="page">{esc(h1)}</h1>'
            f'<div class="gift-intro">{_gift_paras(t["intro"])}</div>'
            f'<div class="gift-list" id="gift-list" data-list="{BRAND_LIST_NAME}">' + grid(items, eager_n=min(3, len(items)), badge=True) + '</div>'
            f'<section class="gift-faq"><h2>Питання й відповіді</h2>{faq_html}</section>' + art_html +
            f'<nav class="gift-links" aria-label="Інші бренди"><h2>Інші бренди та добірки</h2><ul>{links}</ul>'
            f'<p><a href="{BRAND_HUB_FILE}">Усі бренди</a> · <a href="catalog.html">Увесь каталог</a></p></nav>'
        )
        head = breadcrumb_ld(trail)
        head += _ld_script({"@context": "https://schema.org", "@type": "ItemList", "name": h1,
                            "itemListElement": [{"@type": "ListItem", "position": i + 1, "url": f"{SITE_URL}/{product_file(x['id'])}"} for i, x in enumerate(items)]})
        head += _ld_script({"@context": "https://schema.org", "@type": "FAQPage",
                            "mainEntity": [{"@type": "Question", "name": q, "acceptedAnswer": {"@type": "Answer", "text": a}} for q, a in t["faq"]]})
        first = re.split(r"(?<=[.!?])\s+", re.sub(r"\s+", " ", t["intro"]).strip())
        desc = _cut(" ".join(first[:2]), DESC_MAX) or h1
        _write(fname, page(h1, body, extra_head=head, description=desc, canonical=fname, og_image=OG_IMAGE, title_full=(f"{h1} — купити в Україні | PlutusToys" if len(h1) + 29 <= TITLE_MAX else f"{h1} | PlutusToys")))
        written.append((fname, "0.6", "weekly"))
    top = [(s, h) for s, h, par, _i, _t in plan if par is None]
    trail = [("Головна", "index.html"), ("Бренди", None)]
    subs = {s: [(s_, h_) for s_, h_, par_, _i, _t in plan if par_ == s] for s, _h in top}
    lis = "".join(f'<li><a href="{s}.html">{esc(h)}</a>' + (('<ul>' + "".join(f'<li><a href="{s2}.html">{esc(h2)}</a></li>' for s2, h2 in subs[s]) + '</ul>') if subs[s] else "") + '</li>' for s, h in top)
    hub_body = (crumbs_html(trail) + '<h1 class="page">Бренди іграшок у PlutusToys</h1>'
                '<p class="gift-hub-lead">Добірки товарів у наявності за брендами: фігурки, ляльки, м\'які іграшки.</p>'
                f'<nav class="gift-links" aria-label="Бренди"><ul>{lis}</ul></nav>'
                + (('<nav class="gift-links" aria-label="Статті-поради"><h2>Поради</h2><ul>' + "".join(f'<li><a href="{s_}.html">{esc(t_)}</a></li>' for s_, t_ in GIFT_ARTICLES_BUILT if s_ in BRAND_FOR_PORADY) + '</ul></nav>') if any(s_ in BRAND_FOR_PORADY for s_, _t in GIFT_ARTICLES_BUILT) else ""))
    head = breadcrumb_ld(trail) + _ld_script({"@context": "https://schema.org", "@type": "ItemList", "name": "Бренди іграшок",
                                              "itemListElement": [{"@type": "ListItem", "position": i + 1, "name": h, "url": f"{SITE_URL}/{s}.html"} for i, (s, h) in enumerate(top)]})
    _write(BRAND_HUB_FILE, page("Бренди іграшок", hub_body, extra_head=head, description=_cut("Добірки іграшок за брендами в наявності: " + ", ".join(h for _s, h in top[:6]) + ". Доставка Новою Поштою.", DESC_MAX),
                                canonical=BRAND_HUB_FILE, og_image=OG_IMAGE, title_full="Бренди іграшок — добірки в наявності | PlutusToys"))
    written.append((BRAND_HUB_FILE, "0.7", "weekly"))
    return written


# ── Безкоштовні розмальовки з Плутусом (SEO 18.11.2026, рішення власника «Так, робимо») ─────────────────────────────────────────────────────────────────────────────────────────────────
# Хаб /rozmalovky.html + 3 добірки (/rozmalovky-novorichni|-dlia-malyukiv|-dlia-ditei.html). Аркуші (SMM): PNG 1240×1754 (прев'ю) і PDF A4 у site/colorings/<id>.png|.pdf, перелік і alt — site/rozmalovky.json:
# {"<slug>": {"sheets": [{"id": "plutus-yalynka-rozmalovka", "title": "…", "alt": "Розмальовка: …"}]}}; тексти (вступ, 3 питання) — site/rozmalovky_texts.json ({"<slug>": {"h1", "intro", "faq": [{"q","a"}]}}, пише Копірайтер).
# Сторінка будується, лише коли є текст І ≥COLORING_MIN_SHEETS аркушів з обома файлами (інакше голосне попередження); хаб — коли збудовано хоч одну добірку. Нижче — 8–12 живих розмальовок-книжок і наборів (оновлюються щозбірки).
COLORING_MANIFEST_FILE = os.path.join(OUT, "rozmalovky.json")
COLORING_TEXTS_FILE = os.path.join(OUT, "rozmalovky_texts.json")
COLORING_DIR = "colorings"
COLORING_HUB_FILE = "rozmalovky.html"
COLORING_MIN_SHEETS = 3
COLORING_PRODUCTS_MIN, COLORING_PRODUCTS_MAX = 4, 12
COLORING_FAQ_MIN, COLORING_FAQ_MAX = 3, 6
COLORING_CATEGORIES = ("Класичні розмальовки", "Водні розмальовки", "Незвичайні розмальовки", "Розмальовки та розпис")
COLORING_SUBPAGES = ("rozmalovky-novorichni", "rozmalovky-dlia-malyukiv", "rozmalovky-dlia-ditei")
COLORING_PAGE_FILES = frozenset(f"{s}.html" for s in COLORING_SUBPAGES) | {COLORING_HUB_FILE}
COLORING_BUILT = []   # [(slug, h1)] збудованих сторінок поточної збірки (хаб — slug "rozmalovky"); footer() і категорії розмальовок читають
COLORING_CAT_NOTICE = '<p class="sale-lead">Безкоштовні розмальовки з Плутусом для друку: <a href="rozmalovky.html">завантажити й роздрукувати</a>.</p>'
_COLORING_ID_RX = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
_COLORING_BAD_TEXT_RX = re.compile(r"розвива|розвит|користь|корисн|навчи|тренує|тренуван|disney|дісней|peppa|пепп|<|>", re.I)   # без обіцянок розвитку/користі і чужих марок (ТЗ SEO 18.11); ім'я постачальника — _kv_leaks


_COLORING_LAT2CYR = str.maketrans({"a": "а", "e": "е", "o": "о", "p": "р", "c": "с", "x": "х", "i": "і", "y": "у", "k": "к", "m": "м", "t": "т", "h": "н", "b": "в"})   # латинські двійники кирилиці («рoзвиває» з латинською o)


def _coloring_bad(s):
    n = unicodedata.normalize("NFKC", "".join(ch for ch in str(s) if unicodedata.category(ch) != "Cf")).casefold()
    return bool(_COLORING_BAD_TEXT_RX.search(n)) or bool(_COLORING_BAD_TEXT_RX.search(n.translate(_COLORING_LAT2CYR))) or _kv_leaks(s)


def _png_size(path):
    """(ширина, висота) з заголовка PNG або None (не PNG / обірваний файл): розмітка ImageObject має відповідати справжньому файлу (аудит #1707)."""
    try:
        with open(path, "rb") as fh:
            head = fh.read(24)
    except OSError:
        return None
    if len(head) < 24 or head[:8] != b"\x89PNG\r\n\x1a\n" or head[12:16] != b"IHDR":
        return None
    w, h = int.from_bytes(head[16:20], "big"), int.from_bytes(head[20:24], "big")
    return (w, h) if 0 < w <= 20000 and 0 < h <= 20000 else None


def _coloring_site_dir():
    return os.path.join(OUT, COLORING_DIR)


def _load_coloring(path_m=None, path_t=None):
    """(manifest {slug: [sheet…]}, texts {slug: {h1, intro, faq}}). Аркуш — лише з існуючими PNG і PDF; кривий запис/відсутній файл/заборонені слова — ГОЛОСНО, запис пропускаємо."""
    path_m, path_t = path_m or COLORING_MANIFEST_FILE, path_t or COLORING_TEXTS_FILE
    man, texts = {}, {}
    try:
        with open(path_m, encoding="utf-8-sig") as fh:
            raw = json.load(fh)
    except FileNotFoundError:
        raw = {}
    except (OSError, ValueError) as e:
        print(f"[build] ⚠ rozmalovky.json не читається ({e}) — сторінки розмальовок не будуються", file=sys.stderr)
        raw = {}
    for slug, v in (raw.items() if isinstance(raw, dict) else []):
        if slug.startswith("_") or not isinstance(v, dict) or not isinstance(v.get("sheets"), list):
            continue
        ok = []
        for s in v["sheets"]:
            sid = s.get("id") if isinstance(s, dict) else None
            if not isinstance(sid, str) or not _COLORING_ID_RX.fullmatch(sid) or not str(s.get("alt") or "").strip() or not str(s.get("title") or "").strip():
                print(f"[build] ⚠ rozmalovky.json[{slug}]: аркуш без коректних id/title/alt пропущено ({str(sid)[:40]})", file=sys.stderr)
                continue
            if _coloring_bad(s["title"]) or _coloring_bad(s["alt"]):
                print(f"[build] ⚠ rozmalovky.json[{slug}]: у title/alt аркуша {sid} заборонене слово (обіцянка розвитку/користі, чужа марка, ім'я постачальника, <>) — аркуш пропущено", file=sys.stderr)
                continue
            if any(x["id"] == sid for x in ok):
                print(f"[build] ⚠ rozmalovky.json[{slug}]: аркуш {sid} повторюється — дубль пропущено", file=sys.stderr)
                continue
            if not (os.path.isfile(os.path.join(_coloring_site_dir(), sid + ".png")) and os.path.isfile(os.path.join(_coloring_site_dir(), sid + ".pdf"))):
                print(f"[build] ⚠ rozmalovky.json[{slug}]: нема site/{COLORING_DIR}/{sid}.png або .pdf — аркуш пропущено", file=sys.stderr)
                continue
            _sz = _png_size(os.path.join(_coloring_site_dir(), sid + ".png"))
            if _sz is None:
                print(f"[build] ⚠ rozmalovky.json[{slug}]: {sid}.png не є коректним PNG — аркуш пропущено", file=sys.stderr)
                continue
            ok.append({"id": sid, "title": str(s["title"]).strip(), "alt": str(s["alt"]).strip(), "w": _sz[0], "h": _sz[1]})
        man[slug] = ok
    try:
        with open(path_t, encoding="utf-8-sig") as fh:
            rawt = json.load(fh)
    except FileNotFoundError:
        rawt = {}
    except (OSError, ValueError) as e:
        print(f"[build] ⚠ rozmalovky_texts.json не читається ({e}) — сторінки розмальовок не будуються", file=sys.stderr)
        rawt = {}
    for slug, v in (rawt.items() if isinstance(rawt, dict) else []):
        if slug.startswith("_") or not isinstance(v, dict):
            continue
        h1, intro, _faq = v.get("h1"), v.get("intro"), v.get("faq")
        faq = [(x["q"].strip(), x["a"].strip()) for x in (_faq if isinstance(_faq, list) else []) if isinstance(x, dict) and isinstance(x.get("q"), str) and isinstance(x.get("a"), str) and x["q"].strip() and x["a"].strip()]
        if not isinstance(h1, str) or not h1.strip() or not isinstance(intro, str) or not intro.strip() or len(faq) < COLORING_FAQ_MIN:
            print(f"[build] ⚠ rozmalovky_texts.json[{slug}]: потрібні h1, intro і ≥{COLORING_FAQ_MIN} пар q/a — запис пропущено", file=sys.stderr)
            continue
        if _coloring_bad(" ".join([h1, intro] + [q + " " + a for q, a in faq])):
            print(f"[build] ⚠ rozmalovky_texts.json[{slug}]: заборонене слово (обіцянка розвитку/користі, чужа марка), ім'я постачальника чи «<» «>» — запис пропущено", file=sys.stderr)
            continue
        texts[slug] = {"h1": h1.strip(), "intro": intro.strip(), "faq": faq[:COLORING_FAQ_MAX], "standalone": v.get("standalone") is True}   # standalone: текст без згадок інших добірок («три добірки», «є добірка для…») — дозволяє будувати розділ, коли готові не всі добірки
    return man, texts


_COLORING_NUM_RX = re.compile(r"(\d+)\s+аркуш")
_COLORING_MANY_RX = re.compile(r"(?:три|3|дві|2|всі|усі|кілька|декілька)\s+(?:окрем\w+\s+)?добірк|(?:чотири|п'ять|інш\w+|окрем\w+)\s+добірк", re.I)   # згадка інших добірок («зібрані в три добірки»)


_COLORING_MENTION_RX = {"rozmalovky-novorichni": re.compile(r"новорічн|різдв|миколая"), "rozmalovky-dlia-malyukiv": re.compile(r"малюк|3\s*[–-]\s*5\s*рок"), "rozmalovky-dlia-ditei": re.compile(r"6\s*[–-]\s*9\s*рок|старш\w+ діт")}


def _coloring_alltext(tx):
    return " ".join([tx["intro"]] + [q + " " + a for q, a in tx["faq"]])


def coloring_plan(prods, loaded=None):
    """[(slug, h1, sheets, text)] сторінок, які МОЖНА побудувати: добірки (≥COLORING_MIN_SHEETS аркушів + текст) і хаб (є текст і хоч одна добірка)."""
    man, texts = _load_coloring() if loaded is None else loaded
    plan = []
    for slug in COLORING_SUBPAGES:
        if slug not in texts:
            if man.get(slug):
                print(f"[build] ⚠ coloring: для {slug} нема тексту в rozmalovky_texts.json — сторінку не будуємо", file=sys.stderr)
            continue
        sheets = man.get(slug) or []
        if not sheets and slug not in man:
            continue   # аркушів для цієї добірки ще нема — тихо (SMM не віддав): попередження в кожній збірці було б шумом
        if len(sheets) < COLORING_MIN_SHEETS:
            print(f"[build] ⚠ coloring: {slug} — лише {len(sheets)} аркушів із файлами (< {COLORING_MIN_SHEETS}) — сторінку не будуємо", file=sys.stderr)
            continue
        _m = next((m_ for m_ in _COLORING_NUM_RX.finditer(_coloring_alltext(texts[slug])) if int(m_.group(1)) != len(sheets)), None)   # текст (intro+FAQ) називає кількість аркушів («8 аркушів») — кожна згадка має збігатися з фактичною (інакше сторінка бреше)
        if _m:
            print(f"[build] ⚠ coloring: {slug} — у тексті «{_m.group(0)}», а аркушів із файлами {len(sheets)} — сторінку не будуємо (виправте текст або додайте аркуші)", file=sys.stderr)
            continue
        plan.append((slug, texts[slug]["h1"], sheets, texts[slug]))
    hub_slug = COLORING_HUB_FILE[:-5]
    if not plan:
        return []
    if hub_slug not in texts:
        print(f"[build] ⚠ coloring: хаб {hub_slug} без тексту — розмальовки не будуємо", file=sys.stderr)
        return []
    if len(plan) < len(COLORING_SUBPAGES):   # SEO 18.11 (варіант «а»): новорічна не чекає інших добірок — хаб будується з тими, що є, але ЛИШЕ з текстами без посилань на відсутні добірки (прапорець standalone + механічна перевірка)
        _built = {s for s, _h, _sh, _t in plan}
        _gone_rx = [_COLORING_MENTION_RX[s] for s in COLORING_SUBPAGES if s not in _built]   # згадки відсутніх добірок
        _bad = []
        for s, _h, _sh, tx in plan + [(hub_slug, "", [], texts[hub_slug])]:
            _all_t = _coloring_alltext(tx).lower()
            if not tx.get("standalone") or _COLORING_MANY_RX.search(_all_t) or any(rx_.search(_all_t) for rx_ in _gone_rx):
                _bad.append(s)
        if _bad:
            print(f"[build] ⚠ coloring: збудовано лише {len(plan)} з {len(COLORING_SUBPAGES)} добірок, а тексти {', '.join(_bad)} не позначені standalone або згадують інші добірки («три добірки», назви відсутніх) — розмальовки не будуємо", file=sys.stderr)
            return []
    _seen, _all = set(), []
    for _s, _h, shs, _t in plan:
        for sh in shs:
            if sh["id"] not in _seen:
                _seen.add(sh["id"])
                _all.append(sh)
    _mh = next((m_ for m_ in _COLORING_NUM_RX.finditer(_coloring_alltext(texts[hub_slug])) if int(m_.group(1)) != len(_all)), None)   # хаб: число аркушів у тексті = усі унікальні аркуші
    if _mh:
        print(f"[build] ⚠ coloring: хаб — у тексті «{_mh.group(0)}», а аркушів {len(_all)} — розмальовки не будуємо", file=sys.stderr)
        return []
    plan.append((hub_slug, texts[hub_slug]["h1"], _all, texts[hub_slug]))
    return plan


def _coloring_products(prods):
    pool = [p for p in prods if (p.get("category") or "") in COLORING_CATEGORIES and (p.get("stock") or 0) > 0 and p.get("photo") and (p.get("price") or 0) > 0 and not p.get("shadow")
            and not p.get("noindex") and not _is_markdown_item(p)]   # не уцінка і не noindex-картки постачальників (лінки з посадкової на noindex — тонка цінність)
    pool.sort(key=lambda p: (-(p["contribution"] if p.get("contribution") is not None else -1e9), -(p.get("price") or 0), str(p["id"])))
    out, keys = [], set()
    for p in pool:
        k = _pick_key(p["name"])
        if k in keys:
            continue
        keys.add(k)
        out.append(p)
        if len(out) >= COLORING_PRODUCTS_MAX:
            break
    return out


def write_coloring_pages(plan, prods):
    """Пише хаб і добірки розмальовок; повертає [(файл, пріоритет, частота)] для sitemap. Аркуш = прев'ю <img alt> + «Завантажити PDF» (без реєстрації/форм); розмітка ImageObject на кожне прев'ю, BreadcrumbList, FAQPage."""
    if not plan:
        return []
    items = _coloring_products(prods)
    written = []
    hub_slug = COLORING_HUB_FILE[:-5]
    subs = [(s, h) for s, h, _sh, _t in plan if s != hub_slug]
    for slug, h1, sheets, t in plan:
        is_hub = slug == hub_slug
        fname = f"{slug}.html"
        trail = [("Головна", "index.html")] + ([] if is_hub else [("Безкоштовні розмальовки", COLORING_HUB_FILE)]) + [(h1, None)]
        figs = "".join(
            f'<figure class="coloring-sheet"><a href="{COLORING_DIR}/{sh["id"]}.pdf" type="application/pdf" download><img src="{COLORING_DIR}/{sh["id"]}.png" alt="{esc(sh["alt"])}" width="310" height="{max(1, round(310 * sh["h"] / sh["w"]))}"'
            + ('' if i == 0 else ' loading="lazy"') + ' decoding="async"></a>'
            f'<figcaption>{esc(sh["title"])}</figcaption><a class="btn coloring-dl" href="{COLORING_DIR}/{sh["id"]}.pdf" type="application/pdf" download>Завантажити PDF</a></figure>' for i, sh in enumerate(sheets))
        links = "".join(f'<li><a href="{s_}.html">{esc(h_)}</a></li>' for s_, h_ in subs if s_ != slug)
        faq_html = "".join(f"<h3>{esc(q)}</h3><p>{esc(a)}</p>" for q, a in t["faq"])
        shop_html = (f'<section class="coloring-shop"><h2>Розмальовки-книжки й набори</h2><div class="gift-list" id="gift-list" data-list="coloring_shop">' + grid(items, eager_n=0, badge=True) + '</div></section>') if len(items) >= COLORING_PRODUCTS_MIN else ""
        body = (
            crumbs_html(trail) +
            f'<h1 class="page">{esc(h1)}</h1>'
            f'<div class="gift-intro">{_gift_paras(t["intro"])}</div>'
            f'<div class="coloring-grid">{figs}</div>' + shop_html +
            f'<section class="gift-faq"><h2>Питання й відповіді</h2>{faq_html}</section>'
            f'<nav class="gift-links" aria-label="Інші розмальовки">' + (f'<h2>Інші розмальовки</h2><ul>{links}</ul>' if links else '')
            + (f'<p><a href="{COLORING_HUB_FILE}">Усі безкоштовні розмальовки</a> · <a href="catalog.html">Увесь каталог</a></p>' if not is_hub else '<p><a href="catalog.html">Увесь каталог</a></p>') + '</nav>'
        )
        head = breadcrumb_ld(trail)
        for sh in sheets:
            head += _ld_script({"@context": "https://schema.org", "@type": "ImageObject", "contentUrl": f"{SITE_URL}/{COLORING_DIR}/{sh['id']}.png", "url": f"{SITE_URL}/{COLORING_DIR}/{sh['id']}.png",
                                "name": sh["title"], "caption": sh["alt"], "description": sh["alt"], "encodingFormat": "image/png", "width": sh["w"], "height": sh["h"],
                                "creator": {"@type": "Organization", "name": "PlutusToys", "url": f"{SITE_URL}/"}})
        head += _ld_script({"@context": "https://schema.org", "@type": "FAQPage", "mainEntity": [{"@type": "Question", "name": q, "acceptedAnswer": {"@type": "Answer", "text": a}} for q, a in t["faq"]]})
        first = re.split(r"(?<=[.!?])\s+", re.sub(r"\s+", " ", t["intro"]).strip())
        desc = _cut(" ".join(first[:2]), DESC_MAX) or h1
        _write(fname, page(h1, body, extra_head=head, description=desc, canonical=fname, og_image=f"{SITE_URL}/{COLORING_DIR}/{sheets[0]['id']}.png", title_full=(f"{h1} | PlutusToys" if len(h1) + 13 <= TITLE_MAX else h1)))
        written.append((fname, "0.7" if is_hub else "0.6", "weekly"))
    return written


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

# ── Групи категорій за розкладкою Toysi (власник 15.10: «у Toysi гарна розкладка за категоріями, чому ми нею не користуємось?») ──
# Фід Toysi має ієрархію (<category parentId>): 24 групи → підкатегорії. Раніше брали лише назви листків (плоский список із 287). Тепер: сторінки груп group-<slug>.html (сайдбар підкатегорій зліва, фільтри під назвою, липкі),
# «Усі категорії» = лише групи. Розкладка — site/category_groups.json (знято з фіду 15.10; name = Toysi, display = підпис на сайті, hide = не показувати: Уцінка й Геловін мають власні сторінки). Підкатегорія без групи не губиться: її плитка на «Усіх категоріях» у «Окремих розділах».
CATEGORY_GROUPS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "category_groups.json")
GROUP_MIN_PRODUCTS = 6
GROUP_SUB_MIN = 20     # власник 15.10: «таку щільну розбивку по підкатегоріям хто зробив? ущільніть» — підкатегорії, де менше ніж стільки товарів, зводимо в одну «Інше» (на сайті з наявності їх 135 із 284 мають < 20)
GROUP_SUB_MAX = 12     # і не більше стільки окремих підкатегорій у сайдбарі групи (решта — «Інше»)


def load_category_groups():
    try:
        with open(CATEGORY_GROUPS_FILE, encoding="utf-8") as f:
            raw = json.load(f)
    except (OSError, ValueError):
        return []
    out = []
    for g in (raw.get("groups") if isinstance(raw, dict) else None) or []:
        if isinstance(g, dict) and isinstance(g.get("name"), str) and isinstance(g.get("subs"), list) and not g.get("hide"):
            out.append({"name": g["name"], "display": str(g.get("display") or g["name"]), "subs": [s for s in g["subs"] if isinstance(s, str)],
                        "former": [x for x in (g.get("former_files") or []) if isinstance(x, str) and re.fullmatch(r"group-[a-z0-9-]+\.html", x)],
                        "non_toy": g.get("non_toy") is True})
    return out


def site_groups(cats):
    """[{name, display, file, subs:[(категорія, к-сть)], total}] лише для груп, у яких на сайті ≥ GROUP_MIN_PRODUCTS товарів; підкатегорії — за кількістю товарів (спадання)."""
    out, used = [], set()
    for g in load_category_groups():
        own = g["name"] if g["name"] in cats else None   # товари, віднесені прямо до кореня групи (у Toysi так буває: «Канцтовари» 17 шт.) -> остання підкатегорія «Інше»
        subs = sorted(((c, len(cats[c])) for c in dict.fromkeys(g["subs"]) if c in cats and c != own), key=lambda x: (-x[1], x[0]))
        if own:
            subs.append((own, len(cats[own])))
        total = sum(n for _, n in subs)
        if not subs or total < GROUP_MIN_PRODUCTS:
            continue
        base = f"group-{slugify(g['display'])}"
        slug, k = base, 1
        while slug in used:
            k += 1
            slug = f"{base}-{k}"
        used.add(slug)
        main = [(c, n) for c, n in subs if c != own and n >= GROUP_SUB_MIN][:GROUP_SUB_MAX]
        _m = {c for c, _n in main}
        other = [(c, n) for c, n in subs if c not in _m]
        out.append({"name": g["name"], "display": g["display"], "file": f"{slug}.html", "subs": subs, "total": total, "own": own, "main": main, "other": other, "former": g.get("former", []), "non_toy": g.get("non_toy") is True})
    _live = {g["file"] for g in out}
    for g in out:   # former_files не може вказувати на ЖИВУ сторінку (власну чи чужої групи): вказівник перезаписав би її / вибив із sitemap
        g["former"] = [f for f in g["former"] if f not in _live]
    out.sort(key=lambda g: -g["total"])
    return out


SITE_CATS = {}      # категорії навігації поточної збірки {назва: товари} (заповнює build(); потрібні блоку категорій внизу /catalog.html)
SITE_GROUPS = []   # групи поточної збірки (заповнює build() до запису сторінок; потрібні й каталогу, і сторінкам груп)


def catalog_tree_sidebar(groups, total):
    """Сайдбар повного каталогу (власник 15.10: «і скетч з підкатегоріями» — каталог теж із сайдбаром): «Усі товари» + групи Toysi; активна група розкриває підкатегорії. Усі пункти — справжні посилання (група → її сторінка, підкатегорія → сторінка категорії), JS фільтрує на місці."""
    parts = [f'<a class="sb-i all on" href="catalog.html" data-g="-1" data-sub=""><span>Усі товари</span><em>{total}</em></a>']
    for i, g in enumerate(groups):
        subs = "".join(
            f'<a class="sb-i sb-sub" href="category-{g["slugs"][c]}.html" data-g="{i}" data-sub="{esc(c)}"><span>{"Інше" if c == g["own"] else esc(c)}</span><em>{n}</em></a>' for c, n in g["subs"])
        parts.append(f'<div class="sb-grp" data-g="{i}"><a class="sb-i sb-gh" href="{g["file"]}" data-g="{i}" data-sub=""><span>{esc(g["display"])}</span><em>{g["total"]}</em></a><div class="sb-subs" hidden>{subs}</div></div>')
    return '<aside class="sb" aria-label="Категорії"><div class="sb-h">Категорії</div>' + "".join(parts) + '</aside>'


def write_groups(groups, cats, cat_slug):
    """Сторінки груп: H1 + фільтри під ним (липкі) + сайдбар підкатегорій (клік фільтрує на місці, а href веде на сторінку підкатегорії — для краулера) + сітка (JS з index.json; статична перша сторінка для SEO). Повертає set імен."""
    written = set()
    for g in groups:
        items = rec_order([p for c, _n in g["subs"] for p in cats[c]])
        side = "".join(
            f'<a class="sb-i" href="category-{cat_slug[c]}.html" data-sub="{esc(c)}"><span>{esc(c)}</span><em>{n}</em></a>' for c, n in g["main"])
        n_other = sum(n for _c, n in g["other"])
        if g["other"]:   # дрібні підкатегорії зведено в «Інше» (клік фільтрує на місці); їхні сторінки лишаються доступними краулеру через приховані посилання
            side += (f'<a class="sb-i" href="{g["file"]}" data-sub="__other"><span>Інше</span><em>{n_other}</em></a>'
                     '<div class="sb-hid" hidden>' + "".join(f'<a href="category-{cat_slug[c]}.html">{esc(c)}</a>' for c, _n in g["other"]) + '</div>')
        sidebar = (f'<aside class="sb" aria-label="Підкатегорії">'
                   f'<a class="sb-i all on" href="{g["file"]}" data-sub=""><span>Усі товари</span><em>{g["total"]}</em></a>{side}</aside>')
        trail = [("Головна", "index.html"), ("Усі категорії", "catalog.html"), (g["display"], None)]
        subs_json = json.dumps([c for c, _n in g["subs"]], ensure_ascii=False)
        body = (
            crumbs_html(trail) +
            '<div class="gp">' + sidebar + '<div class="gmain">'
            f'<div class="cat-bar"><h1 class="page cat-h1">{esc(g["display"])}</h1>' + _catalog_controls(group=subs_json, own=g["own"], others=json.dumps([c for c, _n in g["other"]], ensure_ascii=False) if g["other"] else None, rtonly=bool(items) and all(p.get("rt") for p in items)) + '</div>\n' +
            _catalog_js_grid() +
            f'<div id="cat-static">{grid(items[:PER_PAGE], eager_n=4)}</div>'
            '</div></div>'
        )
        head = breadcrumb_ld(trail) + _ld_script({"@context": "https://schema.org", "@type": "ItemList", "itemListElement": [
            {"@type": "ListItem", "position": i + 1, "url": f"{SITE_URL}/{product_file(x['id'])}"} for i, x in enumerate(items[:PER_PAGE])]})
        n_sub = len(g["subs"])
        desc = f"{g['display']} у PlutusToys: {g['total']} товарів у {n_sub} підкатегоріях. Є в наявності, доставка Новою Поштою по Україні."[:DESC_MAX]
        _write(g["file"], page(g["display"], body, extra_head=head, description=desc, canonical=g["file"], og_image=OG_IMAGE, title_full=(f"{g['display']} — в наявності | PlutusToys" if g.get("non_toy") else f"{g['display']} — іграшки в наявності | PlutusToys")))
        written.add(g["file"])
        for old in g.get("former", []):   # група перейменована (змінився slug): стара адреса не 404, а сторінка-вказівник → нова (noindex, canonical на нову, refresh 0 с)
            if old != g["file"]:
                _write(old, page(g["display"], f'<div class="gp"><p>Розділ перейменовано: <a href="{g["file"]}">{esc(g["display"])}</a>.</p></div>',
                                 extra_head=f'<meta http-equiv="refresh" content="0; url={g["file"]}">', description=g["display"], canonical=g["file"], noindex=True,
                                 title_full=f"{g['display']} | PlutusToys"))
                written.add(old)
    return written


def _cat_hub_sections(cat_list, cat_slug, cats, groups):
    """Секції переліку категорій: [(назва групи, файл групи | None, [(категорія, кількість товарів)])] — групи за порядком, потім «Окремі розділи» (категорії поза видимими групами, без Уцінки). Спільне для хаба /categories.html і блоку внизу /catalog.html."""
    in_group = set()
    sections = []
    for g in groups:
        in_group.update(c for c, _n in g["subs"])
        subs = [(c, n) for c, n in g["subs"] if c in cat_slug]
        if subs:
            sections.append((g["display"], g["file"], subs))
    rest = sorted(((c, len(cats[c])) for c in cat_list if c not in in_group and c in cat_slug and c in cats and "уцінк" not in c.lower() and "уценк" not in c.lower()), key=lambda x: (-x[1], x[0]))
    if rest:
        sections.append(("Окремі розділи", None, rest))
    return sections


def _cat_hub_html(sections, cat_slug):
    return "".join(
        '<section class="chub-g"><h2 class="chub-h">' + (f'<a href="{f}">{esc(t)}</a>' if f else esc(t)) + '</h2><ul class="chub-l">'
        + "".join(f'<li><a href="category-{cat_slug[c]}.html">{esc(c)}</a> <span>{n}</span></li>' for c, n in subs) + '</ul></section>'
        for t, f, subs in sections)


def _catalog_cats_block(cat_list, cat_slug):
    """Блок «Категорії товарів» унизу /catalog.html (SEO 22.10: Googlebot робить на /catalog.html 9 782 запити за 14 діб, а на /category- лише 0,1 %; Консультант 22.10 схвалив варіант Б — футер і головну не чіпаємо, власник 15.10 і 19.10): ті самі видимі текстові посилання, що й хаб."""
    sections = _cat_hub_sections(cat_list, cat_slug, SITE_CATS, SITE_GROUPS)
    if not sections:
        return ""
    total = sum(len(s[2]) for s in sections)
    return f'<section class="chub catblock" aria-label="Категорії товарів"><h2 class="catblock-h">Категорії товарів ({total})</h2>' + _cat_hub_html(sections, cat_slug) + '</section>'


def write_categories_index(cat_list, cat_slug, cats, groups=None):
    """Сторінка «Усі категорії» — закриває проблему orphan-категорій (усі 259 доступні
    з одного індексу + футера, а не лише з sitemap). Повертає ім'я файлу."""
    if groups:   # SEO 19.10: ХАБ — усі категорії одним списком ВИДИМИХ текстових посилань (Googlebot ходить на /category- ≈0,1% запитів, 28 з 87 перевірених категорій Google не знає); посилання з футера кожної сторінки
        sections = _cat_hub_sections(cat_list, cat_slug, cats, groups)
        blocks = _cat_hub_html(sections, cat_slug)
        total = sum(len(s[2]) for s in sections)
        trail = [("Головна", "index.html"), ("Усі категорії товарів", None)]
        body = (crumbs_html(trail) + '<div class="chub"><h1 class="page">Усі категорії товарів</h1>'
                f'<p class="pagenote">{total} категорій у {len(sections)} розділах — оберіть потрібну.</p>' + blocks + '</div>')
        desc = f"Усі {total} категорій іграшок PlutusToys одним списком: конструктори, ляльки, машинки, настільні ігри, творчість та інші. Доставка Новою Поштою по Україні."[:DESC_MAX]
        _write("categories.html", page("Усі категорії товарів", body, extra_head=breadcrumb_ld(trail), description=desc, canonical="categories.html", og_image=OG_IMAGE,
                                       title_full="Усі категорії товарів — каталог PlutusToys"))
        return "categories.html"
    if False:   # (стара сторінка з плитками груп — не використовується) (без списків підкатегорій); категорії поза видимими групами (Уцінка, Геловін, нові) — «Окремі розділи», щоб жодна не лишилась без посилання
        in_group = {c for g in groups for c, _n in g["subs"]}
        gtiles = "".join(
            f'<a class="cattile" href="{g["file"]}"><div class="emo">{cat_emoji(g["name"])}</div><div class="t">{esc(g["display"])}</div>'
            f'<div class="cnt">{g["total"]} товар{_plural(g["total"])}</div></a>' for g in groups)
        rest = [c for c in cat_list if c not in in_group]
        rtiles = "".join(
            f'<a class="cattile" href="category-{cat_slug[c]}.html"><div class="emo">{cat_emoji(c)}</div><div class="t">{esc(c)}</div>'
            f'<div class="cnt">{len(cats[c])} товар{_plural(len(cats[c]))}</div></a>' for c in rest)
        body = (
            '<h1 class="page">Усі категорії</h1>\n'
            f'<p class="pagenote">{len(groups)} груп іграшок</p>\n'
            f'<div class="catgrid">{gtiles}</div>' +
            (f'<h2 class="sec-h">Окремі розділи</h2><div class="catgrid">{rtiles}</div>' if rest else "")
        )
        desc = (f"Усі групи іграшок PlutusToys: {', '.join(g['display'] for g in groups[:4])} та інші. Доставка Новою Поштою по всій Україні.")[:DESC_MAX]
        _write("categories.html", page("Усі категорії", body, description=desc, canonical="categories.html", og_image=OG_IMAGE))
        return "categories.html"
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
<p>Вартість доставки Новою Поштою — <b>за тарифами оператора</b>; точна сума залежить від міста, ваги та розмірів посилки й називається Новою Поштою. При накладеному платежі Нова Пошта додає комісію: <b>20 ₴ + 2% від суми замовлення</b>. Якщо замовлення відправляється з різних складів, ви отримаєте кілька посилок, і доставка рахується за тарифом оператора за кожну. Доставку оплачує одержувач при отриманні посилки.</p>
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
<p>Для виконання вашого замовлення: підтвердження, передача на комплектацію та відправлення, зв'язок щодо замовлення, повернення чи обміну, бухгалтерський і податковий облік, як того вимагає закон. Окремо — щоб розуміти, з якої реклами прийшло замовлення (розділ «Мітки реклами» нижче).</p>
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
<h2>Мітки реклами: з якого оголошення ви прийшли</h2>
<p>Щоб розуміти, з якої реклами прийшло замовлення, сайт запам'ятовує у вашому браузері, у локальному сховищі сайту (localStorage), відомості про ваш <b>перший захід</b>:</p>
<ul>
<li>рекламні мітки з адреси, за якою ви перейшли: gclid, gbraid, wbraid, fbclid, msclkid та utm_source, utm_medium, utm_campaign, utm_term, utm_content — якщо вони там були;</li>
<li>шлях сторінки сайту, на яку ви потрапили (без параметрів після знака «?»);</li>
<li>доменне ім'я сайту, з якого ви прийшли (наприклад, google.com; без адреси сторінки), якщо це не наш сайт.</li>
</ul>
<p>Ми враховуємо ці відомості протягом 30 діб від першого заходу; доки цей строк не минув, пізніші заходи їх не змінюють, а після нього запис оновлюється під час наступного заходу. Якщо за цей час ви оформите замовлення, відомості надсилаються нам разом із ним і зберігаються у записі вашого замовлення. Без замовлення збережений запис із браузера не надсилається.</p>
<p>Ми не збираємо в цих відомостях ваше ім'я, телефон, адресу чи пошту, і сайт не додає їх до міток. Значення міток формує посилання, за яким ви прийшли; рекламні мітки (зокрема ідентифікатори кліку) у записі замовлення лежать поряд із вашими контактними даними. Самі мітки в адресі сторінки можуть бачити сервіси аналітики й реклами, якщо вони ввімкнені (див. «Cookie та аналітика»).</p>
<p>Окремого перемикача відмови на сайті немає. Збережений запис можна видалити, очистивши дані сайту plutustoys.com.ua в налаштуваннях браузера (разом із ним зникне й кошик); під час наступного заходу сайт збереже новий «перший захід». Якщо заборонити сайту використовувати локальне сховище, ці відомості не зберігатимуться, але кошик теж не працюватиме, і замовити на сайті не вийде. Попросити видалити такі відомості із запису вашого замовлення можна за контактами з розділу «Ваші права».</p>
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

# ── Правдивий lastmod і Last-Modified (SEO 15.10.2026: усі 28 595 URL мали одну дату — Google такому lastmod не довіряє; файли переписувались щозбірки → і Last-Modified/304 жили до наступної збірки) ──
# Стан .htlastmod_state.json у каталозі сайту (.ht* Apache не віддає назовні): {файл: {"h": sha1 відрендереного HTML БЕЗ плаваючих полів, "t": епоха останньої РЕАЛЬНОЇ зміни}}.
# «Плаваючі» поля, що самі по собі змінюються при кожній збірці/добі й НЕ є зміною сторінки: priceValidUntil (сьогодні+7), версії асетів ?v=<hex8> і глобальний «хром» (див. нижче). Їх файл отримує (щоб на диску був актуальний HTML), але lastmod від них не рухається.
# Після запису mtime файла = t, тож Apache віддає Last-Modified = дата реальної зміни й відповідає 304 на If-Modified-Since між збірками.
LASTMOD_STATE_FILE = ".htlastmod_state.json"
# + «хром» сторінки, що залежить від рейтингу категорій УСЬОГО каталогу, а не від самої сторінки: популярні категорії в оверлеї пошуку (sr-pill) і рядок чипів-категорій (div.chips) — інакше будь-яка зміна ціни десь у каталозі пересувала б lastmod УСІХ 28 тис. сторінок.
_LASTMOD_FLOAT_RX = re.compile(r'"priceValidUntil":\s*"\d{4}-\d{2}-\d{2}"|\?v=[0-9a-f]{8}|<a class="sr-pill" href="[^"]*">[^<]*</a>|<div class="chips">.*?</div>|<nav class="fcats".*?</nav>|<a href="categories\.html">Усі категорії товарів</a> · |<div class="cat-mascot">.*?</div>', re.S)   # + перелік категорій у футері (глобальний «хром»: топ-30 за кількістю рухається від наявності — це не зміна сторінки)
# На КАРТЦІ товару додатково не рахуємо сітки інших товарів («Додайте до замовлення», «Схожі товари» — підбір залежить від стану всього каталогу): lastmod картки = зміна ЇЇ власних даних (назва, ціна, наявність, фото, текст), а не чужих.
_LASTMOD_GRID_RX = re.compile(r'<h2>[^<]*</h2><div class="grid">(?:\s*<a class="card".*?</a>)*\s*</div>', re.S)
# На сторінці категорії не рахуємо права колонка «Рекомендуємо»/«Знижка тижня» (ротація щодня, акція щотижня — це не зміна самої сторінки).
_LASTMOD_ASIDE_RX = re.compile(r'<aside class="cat-aside".*?</aside>', re.S)
_lm = {"out": None, "state": {}}


def _lm_state():
    if _lm["out"] != OUT:
        _lm["out"] = OUT
        try:
            with open(os.path.join(OUT, LASTMOD_STATE_FILE), encoding="utf-8") as f:
                raw = json.load(f)
        except (OSError, ValueError):
            raw = {}
        raw = raw if isinstance(raw, dict) else {}
        _lm["state"] = {k: v for k, v in raw.items() if isinstance(v, dict) and isinstance(v.get("h"), str) and isinstance(v.get("t"), (int, float))}
    return _lm["state"]


def _lastmod_reset():
    _lm["out"] = None   # новий build() перечитує стан з диска


def _current_html_files():
    try:
        return {fn for fn in os.listdir(OUT) if fn.endswith(".html")}
    except OSError:
        return set()


def _lastmod_flush(keep):
    st = _lm_state()
    for fn in [k for k in st if k not in keep]:
        del st[fn]   # сторінки, яких уже нема, зі стану прибираємо
    try:
        tmp = os.path.join(OUT, LASTMOD_STATE_FILE + ".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(st, f, separators=(",", ":"))
        os.replace(tmp, os.path.join(OUT, LASTMOD_STATE_FILE))
    except OSError as e:
        print(f"[build] ⚠ стан lastmod не записано ({e}) — наступна збірка вважатиме всі сторінки зміненими", file=sys.stderr)


def _lastmod_date(fname):
    ent = _lm_state().get(fname)
    t = ent["t"] if ent else time.time()
    return time.strftime("%Y-%m-%d", time.gmtime(t))


_STALE_TMP_RX = re.compile(r"\.[^/]+\.tmp[0-9]+")


def _remove_stale_tmp(out, max_age=600):
    """Прибирає залишки тимчасових файлів атомарного запису від перерваної збірки (kill/живлення): лише старші за max_age секунд — тимчасовий файл паралельної збірки (timer + ручна) не чіпаємо."""
    n = 0
    for fn in os.listdir(out):
        if _STALE_TMP_RX.fullmatch(fn):
            try:
                if time.time() - os.path.getmtime(os.path.join(out, fn)) > max_age:
                    os.remove(os.path.join(out, fn))
                    n += 1
            except OSError:
                pass
    return n


def _write_text_atomic(path, content):
    """Запис файлу збірки АТОМАРНО (SEO 10.11.2026: open(path, "w") обнуляє файл ще до запису, і Apache у цю мить віддає порожню/обірвану сторінку з кодом 200).
    Пишемо в сусідній тимчасовий файл і os.replace — читач бачить або старий файл цілим, або новий цілим. Збій запису/заміни: тимчасовий файл прибираємо, старий лишається."""
    tmp = os.path.join(os.path.dirname(path), "." + os.path.basename(path) + f".tmp{os.getpid()}")
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(content)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def _write(fname, content):
    path = os.path.join(OUT, fname)
    if fname.endswith(".html"):
        st = _lm_state()
        masked = _LASTMOD_FLOAT_RX.sub("", content)
        if fname.startswith("product-"):
            masked = _LASTMOD_GRID_RX.sub("", masked)
        elif fname.startswith("category-"):
            masked = _LASTMOD_ASIDE_RX.sub("", masked)
        h = hashlib.sha1(masked.encode("utf-8")).hexdigest()
        ent = st.get(fname)
        t = ent["t"] if (ent and ent["h"] == h) else time.time()
        try:
            with open(path, encoding="utf-8") as f:
                same = f.read() == content
        except OSError:
            same = False
        if not same:
            _write_text_atomic(path, content)
        try:
            os.utime(path, (t, t))
        except OSError:
            pass
        st[fname] = {"h": h, "t": t}
        return
    _write_text_atomic(path, content)


SITEMAP_PRIORITY_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sitemap_priority.json")
PROM_GROUP_CATEGORY_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "prom_group_to_category.json")
SITEMAP_STALE_FILES = ("sitemap-dropt.xml", "sitemap-more.xml")   # Dropt прибрано з сайту (16.11.2026): його сегмент не будуємо, файли від попередніх збірок прибираємо
SITEMAP_SEGMENTS = ("priority", "products", "oos", "categories", "static", "rt", "k")   # rt — окремий сегмент RoyalToys (SEO 19.10: міряти rt- vs Toysi окремо; НЕ в priority)


def _sitemap_priority():
    """(id товарів, файли категорій) для sitemap-priority.xml: картки з текстами (product_texts.json), demand_featured, топ за показами GSC (sitemap_priority.json від SEO) і категорії з текстами + цільові категорії старих груп Prom."""
    ids = set(_PRODUCT_TEXTS)
    cats = {f"category-{slug}.html" for slug in _CATEGORY_TEXTS}
    cats |= {f"category-{slug}.html" for slug in BRAND_CATEGORIES}   # SEO 21.10: посилена брендова категорія — у priority (міряємо окремо)
    for path, key in ((DEMAND_FILE, "ids"), (SITEMAP_PRIORITY_FILE, "product_ids")):
        try:
            with open(path, encoding="utf-8") as f:
                ids |= {str(x) for x in (json.load(f).get(key) or [])}
        except (OSError, ValueError, AttributeError):
            pass
    try:
        with open(PROM_GROUP_CATEGORY_FILE, encoding="utf-8") as f:
            for v in json.load(f).values():
                slug = (v or {}).get("category") if isinstance(v, dict) else None
                if isinstance(slug, str) and re.fullmatch(r"[a-z0-9-]+", slug):
                    cats.add(f"category-{slug}.html")
    except (OSError, ValueError):
        pass
    return ids, cats


def write_sitemap(prods, paged, static_urls=None, oos_prods=(), hidden_files=frozenset()):
    """sitemap.xml = ІНДЕКС п'яти сегментів (SEO 15.10.2026: GSC рахує виявлені/проіндексовані по кожному sitemap окремо — швидкий вимір): priority / products (в наявності) / oos / categories / static.
    Кожен URL рівно в одному сегменті. lastmod — дата реальної зміни сторінки (див. _write). paged = усі сторінки каталогу/категорій; стор.≥2 не потрапляють у sitemap."""
    pri_ids, pri_cats = _sitemap_priority()
    exist = {p["id"] for p in prods} | {p["id"] for p in oos_prods}
    seg = {k: [] for k in SITEMAP_SEGMENTS}
    # головна — КОРІНЬ '/', а не '/index.html': збігається з canonical головної (page()),
    # інакше конфліктний сигнал каноніку (loc {SITE_URL}/{u} з u='' дає {SITE_URL}/).
    seg["priority"].append(("", "index.html", "1.0", "daily"))
    seg["static"] += [(u, u, pr, cf) for u, pr, cf in (static_urls or [])]   # categories.html + сторінки довіри
    for fn in sorted(paged):
        if fn in hidden_files:
            continue   # NOINDEX_CATEGORIES: сторінка категорії поза sitemap (SEO 22.10)
        if re.search(r"_\d+\.html$", fn):
            continue   # SEO 11.10.2026: у sitemap лише 1-ша сторінка категорії/каталогу; пагінація лишається доступною через посилання, canonical і rel=prev/next
        if fn in pri_cats:
            seg["priority"].append((fn, fn, "0.7", "weekly"))
        elif fn == "catalog.html":
            seg["categories"].append((fn, fn, "0.9", "daily"))
        else:                                             # page-1 категорії/каталогу
            seg["categories"].append((fn, fn, "0.7", "weekly"))
    for p in prods:
        f = product_file(p['id'])
        if product_hidden(p):
            continue   # NOINDEX_CATEGORIES (True): товар noindex і поза sitemap
        if p.get("rt"):
            if not p.get("noindex"):
                seg["k" if p.get("kv") else "rt"].append((f, f, "0.5", "weekly"))   # noindex-картки в sitemap не потрапляють
            continue
        (seg["priority"] if p["id"] in pri_ids else seg["products"]).append((f, f, "0.6", "weekly"))
    # без залишку: адреса жива (OutOfStock), лишається в sitemap — інакше Google знову побачить 404 на вже проіндексованому; пріоритет нижчий
    for p in oos_prods:
        f = product_file(p['id'])
        if product_hidden(p):
            continue
        if p.get("rt"):
            continue   # rt без залишку: сторінка жива, у sitemap не додаємо
        (seg["priority"] if p["id"] in pri_ids else seg["oos"]).append((f, f, "0.6" if p["id"] in pri_ids else "0.3", "weekly" if p["id"] in pri_ids else "monthly"))
    index_items = []
    for _legacy in SITEMAP_STALE_FILES:
        try:
            os.remove(os.path.join(OUT, _legacy))
        except OSError:
            pass
    for name in SITEMAP_SEGMENTS:
        fname = f"sitemap-{name}.xml"
        urls = seg[name]
        if not urls:
            try:
                os.remove(os.path.join(OUT, fname))   # порожній сегмент не лишаємо від попередньої збірки
            except OSError:
                pass
            continue
        dates = [_lastmod_date(src) for _u, src, _pr, _cf in urls]
        items = "\n".join(
            f"  <url><loc>{SITE_URL}/{u}</loc><lastmod>{d}</lastmod><changefreq>{cf}</changefreq><priority>{pr}</priority></url>"
            for (u, _src, pr, cf), d in zip(urls, dates)
        )
        _write(fname, '<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n' + items + "\n</urlset>\n")
        index_items.append(f"  <sitemap><loc>{SITE_URL}/{fname}</loc><lastmod>{max(dates)}</lastmod></sitemap>")
    _write("sitemap.xml",
           '<?xml version="1.0" encoding="UTF-8"?>\n'
           '<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
           + "\n".join(index_items) + "\n</sitemapindex>\n")


def write_robots():
    _write("robots.txt",
           "User-agent: *\n"
           "Allow: /\n"
           "Disallow: /cart.html\n"
           "Disallow: /thanks.html\n"
           "Disallow: /review.html\n"
           f"Sitemap: {SITE_URL}/sitemap.xml\n")


if __name__ == "__main__":
    build()

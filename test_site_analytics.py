# -*- coding: utf-8 -*-
"""SMM P1.1 (2026-10-06): GA4 + Meta Pixel керуються змінними середовища; без ID у HTML нічого нема; ID валідується
(потрапляє в <script>); app.js має події view_item/add_to_cart/begin_checkout/purchase і не шле без конфігу."""
import importlib, os, sys, subprocess
from pathlib import Path
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
HERE = Path(__file__).parent
sys.path.insert(0, str(HERE / "site"))
F = []
def chk(n, c):
    print(f"[{'OK ' if c else 'FAIL'}] {n}")
    if not c: F.append(n)

def load(**env):
    for k in ("SITE_GA4_ID", "SITE_META_PIXEL_ID", "SITE_GADS_ID"):
        os.environ.pop(k, None)
    os.environ.update(env)
    import build_site
    return importlib.reload(build_site)

b = load()
chk("без ID: analytics_head() порожній і сторінка без PT_ANALYTICS/gtag/fbq",
    b.analytics_head() == "" and "PT_ANALYTICS" not in b.page("t", "<p>x</p>") and "gtag" not in b.page("t", "x"))
b = load(SITE_GA4_ID="G-ABC123XYZ9", SITE_META_PIXEL_ID="1234567890123")
h = b.analytics_head()
chk("з ID: gtag.js + config і fbq init + PageView", "googletagmanager.com/gtag/js?id=G-ABC123XYZ9" in h and "gtag('config','G-ABC123XYZ9')" in h
    and "fbq('init','1234567890123')" in h and "fbq('track','PageView')" in h)
chk("з ID: PT_ANALYTICS у head кожної сторінки", 'window.PT_ANALYTICS={"ga4": "G-ABC123XYZ9", "fb": "1234567890123"}' in b.page("t", "x"))
b = load(SITE_GA4_ID='G-AAAAAA");alert(1);//', SITE_META_PIXEL_ID="12ab")
chk("зламаний/ін'єкційний ID відкидається (нічого не вставляється)", b.GA4_ID == "" and b.META_PIXEL_ID == "" and b.analytics_head() == "")
b = load(SITE_META_PIXEL_ID="١٢٣٤٥٦٧٨٩٠")
chk("юнікод-цифри в ID піксела відкидаються (аудит #626)", b.META_PIXEL_ID == "")
b = load(SITE_META_PIXEL_ID="1234567890123")
chk("Meta: Automatic Advanced Matching вимкнено (autoConfig=false) до init", "fbq('set','autoConfig',false,'1234567890123')" in b.analytics_head()
    and b.analytics_head().index("autoConfig") < b.analytics_head().index("fbq('init'"))
b = load(SITE_GA4_ID="G-ABC123XYZ9")
chk("лише GA4: без fbq", "fbq" not in b.analytics_head() and "gtag" in b.analytics_head())
load()

js = (HERE / "site" / "assets" / "app.js").read_text(encoding="utf-8")
for ev in ("view_item", "add_to_cart", "begin_checkout", "purchase"):
    chk(f"app.js: подія {ev}", f'"{ev}"' in js)
chk("app.js: purchase один раз на замовлення (pt_purchase_sent)", "pt_purchase_sent" in js)
chk("app.js: для prepaid позиції замовлення НЕ зберігаються (purchase лише COD)", 'if(payment!=="prepaid"){' in js and 'removeItem("pt_last_order_items")' in js)
chk("app.js: без window.PT_ANALYTICS нічого не шле (cfg.ga4/cfg.fb умови)", "cfg.ga4 && window.gtag" in js and "cfg.fb && window.fbq" in js)
try:
    r = subprocess.run(["node", "--check", str(HERE / "site" / "assets" / "app.js")], capture_output=True, text=True, timeout=30)
    chk("app.js: синтаксис (node --check)", r.returncode == 0)
except FileNotFoundError:
    print("[SKIP] node відсутній — синтаксис JS не перевірено")
# --- Google Ads (AW) на тому ж gtag.js (власник 21.10.2026, SEO) ---
b = load(SITE_GA4_ID="G-ABC123XYZ9")
hd, hn = b.analytics_head(defer=True), b.analytics_head(defer=False)
chk("Google Ads: за замовчуванням AW-18528978608 додається поруч із GA4 у ОБОХ режимах (відкладений і thanks)", b.GADS_ID == "AW-18528978608"
    and "gtag('config','G-ABC123XYZ9');gtag('config','AW-18528978608');" in hd and "gtag('config','G-ABC123XYZ9');gtag('config','AW-18528978608');" in hn)
chk("Google Ads: gtag.js вантажиться ОДИН раз і лише з id GA4 (AW у src немає, другого <script src=gtag/js> нема)", hn.count("googletagmanager.com/gtag/js") == 1 and "gtag/js?id=G-ABC123XYZ9" in hn and "gtag/js?id=AW" not in hn + hd
    and hd.count("gtag/js") == 1)
chk("Google Ads: window.PT_ANALYTICS і умови app.js (purchase через cfg.ga4) без змін", 'window.PT_ANALYTICS={"ga4": "G-ABC123XYZ9", "fb": ""}' in hd and "cfg.ga4 && window.gtag" in (HERE / "site" / "assets" / "app.js").read_text(encoding="utf-8"))
b = load()
chk("Google Ads: без GA4 (сайт без аналітики) AW НЕ додається — сторінка як раніше", b.GADS_ID == "" and b.analytics_head() == "" and "gtag" not in b.page("t", "x"))
b = load(SITE_GA4_ID="G-ABC123XYZ9", SITE_GADS_ID="")
chk("Google Ads: SITE_GADS_ID=\"\" вимикає (лише GA4)", b.GADS_ID == "" and "AW-" not in b.analytics_head() and "gtag('config','G-ABC123XYZ9')" in b.analytics_head())
for bad in ('AW-1");alert(1);//', "AW-12", "AW-abc12345678", "aw-18528978608", "AW-١٢٣٤٥٦٧٨٩٠", "G-ABC123XYZ9"):
    b = load(SITE_GA4_ID="G-ABC123XYZ9", SITE_GADS_ID=bad)
    chk(f"Google Ads: некоректний id {bad[:20]!r} відкидається, у HTML не потрапляє", b.GADS_ID == "" and "AW-" not in b.analytics_head() and "alert" not in b.analytics_head())
b = load(SITE_GA4_ID="G-ABC123XYZ9", SITE_GADS_ID="AW-99999999999")
chk("Google Ads: інший валідний AW-id через env підставляється", "gtag('config','AW-99999999999')" in b.analytics_head())
load()

print(f"\n{'❌ ПРОВАЛЕНО: ' + str(F) if F else '✅ аналітика сайту — усі перевірки коректні.'}")
sys.exit(1 if F else 0)

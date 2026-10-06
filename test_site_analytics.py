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
    for k in ("SITE_GA4_ID", "SITE_META_PIXEL_ID"):
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
b = load(SITE_GA4_ID="G-ABC123XYZ9")
chk("лише GA4: без fbq", "fbq" not in b.analytics_head() and "gtag" in b.analytics_head())
load()

js = (HERE / "site" / "assets" / "app.js").read_text(encoding="utf-8")
for ev in ("view_item", "add_to_cart", "begin_checkout", "purchase"):
    chk(f"app.js: подія {ev}", f'"{ev}"' in js)
chk("app.js: purchase один раз на замовлення (pt_purchase_sent)", "pt_purchase_sent" in js)
chk("app.js: без window.PT_ANALYTICS нічого не шле (cfg.ga4/cfg.fb умови)", "cfg.ga4 && window.gtag" in js and "cfg.fb && window.fbq" in js)
try:
    r = subprocess.run(["node", "--check", str(HERE / "site" / "assets" / "app.js")], capture_output=True, text=True, timeout=30)
    chk("app.js: синтаксис (node --check)", r.returncode == 0)
except FileNotFoundError:
    print("[SKIP] node відсутній — синтаксис JS не перевірено")
print(f"\n{'❌ ПРОВАЛЕНО: ' + str(F) if F else '✅ аналітика сайту — усі перевірки коректні.'}")
sys.exit(1 if F else 0)

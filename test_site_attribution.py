#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_site_attribution.py — 11.11.2026 (Код, PR #1233; SEO): джерело замовлення. app.js зберігає first-touch мітки (gclid/gbraid/wbraid/fbclid/msclkid/utm_*) + landing + хост зовнішнього реферера у localStorage на 30 днів і шле їх як `attribution` у POST /api/order.
Пастки (SEO): (1) gclid приходить на першу сторінку, замовлення — на cart.html, тож читаємо збережене, а не location.search у момент оформлення; (2) друга сторінка без параметрів НЕ затирає збережене (first-touch, пусте не пишемо).
Тест виконує блок ATTR-BEGIN…ATTR-END з app.js у node з підміною window/document/localStorage; без мережі."""
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).parent
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
F = []


def chk(n, c):
    print(f"[{'OK ' if c else 'FAIL'}] {n}")
    if not c:
        F.append(n)


app = (HERE / "site" / "assets" / "app.js").read_text(encoding="utf-8")
m = re.search(r"/\* ATTR-BEGIN \*/(.*?)/\* ATTR-END \*/", app, re.S)
chk("блок ATTR-BEGIN…ATTR-END є в app.js", bool(m))
block = m.group(1) if m else ""

HARNESS = r"""
const store = {};
let now = 1700000000000;
const realNow = Date.now; Date.now = () => now;
global.localStorage = { getItem: k => (k in store ? store[k] : null), setItem: (k, v) => { store[k] = String(v); } };
function visit(url, referrer) {
  const u = new URL(url);
  global.window = { location: { search: u.search, pathname: u.pathname, hostname: u.hostname } };
  global.document = { referrer: referrer || "" };
  const run = new Function("window", "document", "localStorage", BLOCK + "; return {attrRead: attrRead};");
  return run(global.window, global.document, global.localStorage);
}
const out = {};
let r;
r = visit("https://plutustoys.com.ua/?gclid=TEST123&utm_source=google&utm_medium=cpc", "");
out.first = r.attrRead();
r = visit("https://plutustoys.com.ua/product-5.html", "https://plutustoys.com.ua/");
out.second = r.attrRead();
r = visit("https://plutustoys.com.ua/cart.html", "https://plutustoys.com.ua/product-5.html");
out.cart = r.attrRead();
r = visit("https://plutustoys.com.ua/?gclid=OTHER", "https://www.google.com/");
out.notOverwritten = r.attrRead();
now += 31 * 24 * 3600 * 1000;
r = visit("https://plutustoys.com.ua/x.html", "");
out.expired = r.attrRead();
delete store["pt_attr_v1"];
r = visit("https://plutustoys.com.ua/?fbclid=FB1&gclid=&utm_term=%D0%BC%D0%B0%D1%88%D0%B8%D0%BD%D0%BA%D0%B0", "https://l.facebook.com/l.php?u=secret&x=1");
out.afterExpiry = r.attrRead();
delete store["pt_attr_v1"];
r = visit("https://plutustoys.com.ua/", "");
out.noMarks = r.attrRead();
delete store["pt_attr_v1"];
r = visit("https://plutustoys.com.ua/product-7.html", "https://www.google.com/search?q=secret");
out.organic = r.attrRead();
delete store["pt_attr_v1"];
r = visit("https://plutustoys.com.ua/?utm_source=" + "x".repeat(500), "");
out.long = r.attrRead();
delete store["pt_attr_v1"];
r = visit("https://plutustoys.com.ua/?foo=1&q=2", "");
out.unknown = r.attrRead();
delete store["pt_attr_v1"];
store["pt_attr_v1"] = "{не json";
r = visit("https://plutustoys.com.ua/?gclid=AFTERBAD", "");
out.badJson = r.attrRead();
delete store["pt_attr_v1"];
r = visit("https://www.plutustoys.com.ua/a.html", "https://plutustoys.com.ua/");
out.ownWww = r.attrRead();
console.log(JSON.stringify(out));
"""

with tempfile.TemporaryDirectory() as td:
    js = Path(td) / "t.js"
    js.write_text("const BLOCK = " + json.dumps(block) + ";\n" + HARNESS, encoding="utf-8")
    try:
        r = subprocess.run(["node", str(js)], capture_output=True, text=True, timeout=30, encoding="utf-8")
        ok = r.returncode == 0
        o = json.loads(r.stdout.strip().splitlines()[-1]) if ok and r.stdout.strip() else {}
        if not ok:
            print(r.stderr[:500])
    except FileNotFoundError:
        print("[SKIP] node відсутній — поведінку JS не перевірено")
        o = None

if o is not None:
    chk("перша сторінка з gclid+utm: збережено gclid, utm_source, utm_medium, landing='/'", o.get("first") == {"gclid": "TEST123", "utm_source": "google", "utm_medium": "cpc", "landing": "/"})
    chk("ПАСТКА 2: друга сторінка без параметрів не затирає збережене", o.get("second") == o.get("first"))
    chk("ПАСТКА 1: на cart.html (без параметрів у URL) збережене gclid доступне для замовлення", (o.get("cart") or {}).get("gclid") == "TEST123")
    chk("first-touch: новий gclid на наступному заході НЕ перезаписує перший", (o.get("notOverwritten") or {}).get("gclid") == "TEST123")
    chk("після ТТЛ (31 доба) старе збережене не повертається; новий перший дотик = поточна сторінка без міток", o.get("expired") == {"landing": "/x.html"})
    chk("після ТТЛ новий перший дотик: fbclid + utm_term (кирилиця розкодована), пустий gclid= не пишемо, реферер — лише хост без шляху/query",
        o.get("afterExpiry") == {"fbclid": "FB1", "utm_term": "машинка", "landing": "/", "referrer": "l.facebook.com"})
    chk("прямий перший захід без міток і без реферера: зберігається лише landing (відрізняємо «органіка/прямий» від «збір не спрацював»)", o.get("noMarks") == {"landing": "/"})
    chk("органічний перехід (зовнішній реферер без міток): зберігаємо лише хост, query пошуку не потрапляє", o.get("organic") == {"landing": "/product-7.html", "referrer": "www.google.com"})
    chk("значення обрізається до 200 знаків", len((o.get("long") or {}).get("utm_source", "")) == 200)
    chk("невідомі параметри (foo, q) ігноруються; лишається лише landing", o.get("unknown") == {"landing": "/"})
    chk("зіпсований JSON у сховищі не ламає збір: новий дотик записується", (o.get("badJson") or {}).get("gclid") == "AFTERBAD")
    chk("власний хост (з www і без) не вважається зовнішнім реферером: лише landing", o.get("ownWww") == {"landing": "/a.html"})

chk("payload.attribution додається лише коли є збережене (attrRead), після expected_total", "var attr=attrRead(); if(attr){ payload.attribution=attr; }" in app and app.index("payload.expected_total=Math.round(shownTotal)") < app.index("payload.attribution=attr"))
chk("збір не лежить усередині обробника сабміту: attrCapture() викликається при завантаженні сторінки", re.search(r"^  attrCapture\(\);\s*$", app, re.M) is not None)
chk("персональні поля (name/phone/email) не потрапляють у блок збору", not re.search(r"f-name|f-phone|f-email", block))

print()
if F:
    print(f"FAILED ({len(F)}):")
    for n in F:
        print("  -", n)
    sys.exit(1)
print("ALL OK")

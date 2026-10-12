#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_site_defer_trackers.py — 12.10.2026, рішення Консультанта: відкласти Pixel/gtag (Lighthouse mobile TBT 1,4–3,3 с майже весь від трекерів), КРІМ сторінки «Дякуємо» (там `purchase`).
Повний build() із заданими GA4/Pixel ID + node (vm) виконує інлайн-скрипти <head>:
  • усі сторінки КРІМ thanks.html: у статичному HTML НЕМАЄ <script async src=gtag/js…> і нічого не вантажить fbevents.js; стаби gtag/fbq (черга) є одразу — події app.js не губляться;
  • gtag.js і fbevents.js підвантажуються РІВНО один раз: через 3 с після load або на першу взаємодію (pointerdown/keydown/scroll/touchstart), що раніше; повторні тригери нічого не додають;
  • сторінка ще вантажиться (readyState=loading) — таймера до load нема;
  • події, викликані ДО підвантаження (gtag('event',…), fbq('track',…)), лишаються в черзі (dataLayer / fbq.queue);
  • thanks.html — БЕЗ відкладання (статичні скрипти як раніше); лише GA4 / лише Pixel / без ID — збірка не падає, зайвого не додається.
Без node частина про поведінку падає ЯВНО."""
import contextlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).parent
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
os.environ.setdefault("AUDIT_NO_TELEGRAM", "1")
for k in ("SITE_LIQPAY_LIVE", "SITE_SELLER_TAXID"):
    os.environ.pop(k, None)
sys.path.insert(0, str(HERE / "site"))
sys.path.insert(0, str(HERE))
import build_site as bs  # noqa: E402

F = []


def chk(n, c):
    print(f"[{'OK ' if c else 'FAIL'}] {n}")
    if not c:
        F.append(n)


def item(pid, name, category="Картини за номерами", price=300.0):
    return {"id": str(pid), "name": name, "vendor": "T", "price": price, "stock": 5, "category_name": category, "category_id": "10",
            "pictures": [f"https://toysi.ua/p/enl_{pid}.jpg"], "description": "Опис товару достатньої довжини для картки сайту, понад сорок символів.",
            "params": {}, "url": f"https://toysi.ua/{pid}"}


catalog = {str(i): item(i, f"Товар {i}", price=250 + i) for i in range(1, 30)}
import site_photo_probe  # noqa: E402
site_photo_probe.run = lambda idx: None


def build_with(ga4, fb):
    bs.GA4_ID, bs.META_PIXEL_ID = ga4, fb
    out = Path(tempfile.mkdtemp())
    bs.OUT = str(out)
    bs.tp.fetch_toysi_catalog = lambda: dict(catalog)
    bs.tp.assert_catalog_size_sane = lambda c, *a, **k: None
    with contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(io.StringIO()):
        bs.build()
    return out


GA, FB = "G-TEST12345", "1234567890"
out = build_with(GA, FB)
pages = ["index.html", "product-5.html", "cart.html", "catalog.html", "category-kartyny-za-nomeramy.html", "about.html", "ig.html", "404.html"]
for f in pages:
    h = (out / f).read_text(encoding="utf-8")
    chk(f"{f}: у статичному HTML НЕМАЄ <script async src=…gtag/js> і fbevents.js як src (скрипти підвантажуються з відкладенням)", not re.search(r'<script[^>]+src="https://(www\.googletagmanager\.com|connect\.facebook\.net)', h))
    chk(f"{f}: стаби gtag/fbq і window.PT_ANALYTICS є ОДРАЗУ в <head>", "function gtag()" in h and "n.queue=[]" in h and "window.PT_ANALYTICS=" in h)
thanks = (out / "thanks.html").read_text(encoding="utf-8")
chk("thanks.html — БЕЗ відкладання: статичний <script async src=gtag/js?id=…> і Pixel-сніпет із fbevents.js (purchase не втрачаємо)", f'<script async src="https://www.googletagmanager.com/gtag/js?id={GA}"></script>' in thanks and "connect.facebook.net/en_US/fbevents.js" in thanks and "setTimeout(load,3000)" not in thanks)
home = (out / "index.html").read_text(encoding="utf-8")
chk("відкладені сторінки: завантажувач (3 с після load + взаємодія) є, id GA4/Pixel — у ньому", "setTimeout(load,3000)" in home and f"gtag/js?id={GA}" in home and "fbevents.js" in home and "pointerdown" in home)

node = shutil.which("node")
if not node:
    chk("node доступний (без нього поведінку не перевірити)", False)
else:
    harness = r'''
const fs=require("fs"), vm=require("vm");
const html=fs.readFileSync(process.argv[2],"utf8"); const scenario=process.argv[3];
const head=html.slice(0, html.indexOf("</head>"));
const scripts=[...head.matchAll(/<script>([\s\S]*?)<\/script>/g)].map(m=>m[1]);
const appended=[]; const timers=[]; const winH={};
const doc={readyState: scenario==="loading"?"loading":"complete", createElement(t){ return {tag:t, async:false, src:""}; }, head:{appendChild(s){ appended.push(s.src); }}, getElementsByTagName(){ return [{parentNode:{insertBefore(s){ appended.push(s.src); }}}]; }};
const ctx={document:doc, console, setTimeout:function(f,ms){ timers.push({f,ms,done:false}); return timers.length; }, addEventListener(t,f,o){ (winH[t]=winH[t]||[]).push({f,o}); }, Date};
ctx.window=ctx; ctx.self=ctx;
vm.createContext(ctx);
scripts.forEach(s=>vm.runInContext(s,ctx));
const out={nScripts:scripts.length, appended0:appended.length, timers0:timers.map(t=>t.ms), listeners:Object.keys(winH).sort()};
out.dataLayer=(ctx.dataLayer||[]).map(a=>Array.from(a)[0]); out.dlTypes=(ctx.dataLayer||[]).map(a=>Object.prototype.toString.call(a)); out.fbqQueue=(ctx.fbq&&ctx.fbq.queue||[]).map(a=>Array.from(a).slice(0,2)); out.hasGtag=typeof ctx.gtag; out.hasFbq=typeof ctx.fbq;
const fireTimers=()=>timers.filter(t=>!t.done).forEach(t=>{ t.done=true; t.f(); });
if(scenario==="timer"){ fireTimers(); out.afterTimer=appended.slice(); fireTimers(); (winH.pointerdown||[]).forEach(h=>h.f()); out.afterMore=appended.length; }
else if(scenario==="interaction"){ (winH.pointerdown||[]).forEach(h=>h.f()); out.afterInteraction=appended.slice(); (winH.keydown||[]).forEach(h=>h.f()); fireTimers(); out.afterMore=appended.length; out.once=(winH.scroll||[]).every(h=>h.o&&h.o.once===true&&h.o.passive===true); }
else if(scenario==="loading"){ out.beforeLoad=timers.length; (winH.load||[]).forEach(h=>h.f()); out.afterLoad=timers.map(t=>t.ms); fireTimers(); out.afterTimer=appended.length; }
else if(scenario==="events"){ ctx.gtag("event","add_to_cart",{value:1}); ctx.fbq("track","AddToCart",{value:1}); out.dataLayerAfter=(ctx.dataLayer||[]).map(a=>Array.from(a)[0]); out.fbqQueueAfter=(ctx.fbq.queue||[]).map(a=>Array.from(a).slice(0,2)); out.appendedStill=appended.length; }
console.log(JSON.stringify(out));
'''
    tmp = Path(tempfile.mkdtemp())
    hf = tmp / "h.js"
    hf.write_text(harness, encoding="utf-8")

    def run(page_file, sc):
        r = subprocess.run([node, str(hf), str(page_file), sc], capture_output=True, text=True, encoding="utf-8", timeout=60)
        try:
            return json.loads(r.stdout.strip().splitlines()[-1])
        except Exception:
            print("   stdout:", r.stdout[-300:], "stderr:", r.stderr[:900])
            return None

    i = run(out / "index.html", "idle")
    chk("після виконання head: нічого не підвантажено; один таймер на 3000 мс; слухачі pointerdown/keydown/scroll/touchstart/", i is not None and i["appended0"] == 0 and i["timers0"] == [3000] and {"pointerdown", "keydown", "scroll", "touchstart"} <= set(i["listeners"]))
    chk("dataLayer: записи — справжні Arguments-об'єкти (gtag.js не читає масиви; мутація dataLayer.push(Array.from(arguments)) ламає GA — аудит #693 M1)", i is not None and i["dlTypes"] == ["[object Arguments]", "[object Arguments]"])
    chk("стаби: gtag і fbq — функції; у черзі gtag js+config, fbq set+init+track(PageView)", i is not None and i["hasGtag"] == "function" and i["hasFbq"] == "function" and i["dataLayer"] == ["js", "config"] and i["fbqQueue"] == [["set", "autoConfig"], ["init", FB], ["track", "PageView"]])
    t = run(out / "index.html", "timer")
    chk("через 3 с після load: РІВНО два скрипти (gtag.js з id і fbevents.js); повторні тригери нічого не додають", t is not None and t["afterTimer"] == [f"https://www.googletagmanager.com/gtag/js?id={GA}", "https://connect.facebook.net/en_US/fbevents.js"] and t["afterMore"] == 2)
    n = run(out / "index.html", "interaction")
    chk("перша взаємодія (pointerdown) — одразу ті самі два скрипти; keydown/таймер потім не дублюють; слухачі once+passive", n is not None and len(n["afterInteraction"]) == 2 and n["afterMore"] == 2 and n["once"] is True)
    ld = run(out / "index.html", "loading")
    chk("сторінка ще вантажиться: таймера ДО load нема; після load — 3000 мс → два скрипти", ld is not None and ld["beforeLoad"] == 0 and ld["afterLoad"] == [3000] and ld["afterTimer"] == 2)
    ev = run(out / "index.html", "events")
    chk("події ДО підвантаження (gtag('event'), fbq('track')) лишаються в черзі, нічого не вантажать", ev is not None and ev["dataLayerAfter"] == ["js", "config", "event"] and ev["fbqQueueAfter"][-1] == ["track", "AddToCart"] and ev["appendedStill"] == 0)
    tn = run(out / "thanks.html", "idle")
    chk("thanks.html у node: Pixel-сніпет одразу вставляє fbevents.js, таймер відкладання відсутній", tn is not None and tn["timers0"] == [] and tn["appended0"] == 1)

# варіанти конфігурації
for ga, fb, label in ((GA, "", "лише GA4"), ("", FB, "лише Pixel"), ("", "", "без ID")):
    o = build_with(ga, fb)
    h = (o / "index.html").read_text(encoding="utf-8")
    t = (o / "thanks.html").read_text(encoding="utf-8")
    ok = True
    if ga:
        ok = ok and f"gtag/js?id={ga}" in h and "fbevents" not in h and "gtag/js" in t
    if fb:
        ok = ok and "fbevents.js" in h and "gtag/js" not in h and "fbevents.js" in t
    if not ga and not fb:
        ok = ok and "PT_ANALYTICS" not in h and "setTimeout(load" not in h
    chk(f"{label}: збірка проходить, у відкладеному завантажувачі лише потрібне", ok)
bs.GA4_ID, bs.META_PIXEL_ID = GA, FB

print()
if F:
    print(f"FAILED ({len(F)}):")
    for n in F:
        print("  -", n)
    sys.exit(1)
print("ALL OK")

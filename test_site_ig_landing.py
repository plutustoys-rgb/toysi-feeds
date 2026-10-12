#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_site_ig_landing.py — 12.10.2026, замовлення SMM: посадкова /ig.html для bio-посилання Instagram (у підписах IG посилання не клікабельні).
Перевіряє повний build() на синтетичному каталозі (мережа не потрібна):
  • /ig.html: H1, «Ці товари з нашого останнього відео», сітка РІВНО з товарів site/ig_featured.json у заданому порядку, ЛИШЕ в наявності (нема — не показуємо + попередження в stderr),
    ≤6 карток, дублі прибрано, id-числа приймаються; кнопка «Усі іграшки» → catalog.html; noindex,follow; canonical без параметрів (utm не плодять дублів); НЕ в sitemap;
  • нема файлу / битий JSON / не той формат / BOM — сторінка збирається («скоро з'явиться»), причина голосно в stderr, збірка не падає;
  • футер: посилання на Instagram/Facebook несуть utm_source=site&utm_medium=footer&utm_campaign=social_link; у JSON-LD sameAs — чисті адреси;
  • node: view_item_list рівно один раз з item_list_name «ig_landing», select_item лише на клік; без #ig-list нічого не шлеться й не падає.
Без node частина про події падає ЯВНО."""
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


def item(pid, name, category="Картини за номерами", price=300.0, stock=5):
    return {"id": str(pid), "name": name, "vendor": "T", "price": price, "stock": stock, "category_name": category, "category_id": "10",
            "pictures": [f"https://toysi.ua/p/enl_{pid}.jpg"], "description": "Опис товару достатньої довжини для картки сайту, понад сорок символів.",
            "params": {}, "url": f"https://toysi.ua/{pid}"}


catalog = {}
for i in range(1, 41):
    catalog[str(i)] = item(i, f"Товар {i}", price=250 + i)
catalog["900"] = item(900, "Закінчився", stock=0)
catalog["901"] = item(901, 'Назва з <b>тегом</b> & "лапками"', price=410)
import site_photo_probe  # noqa: E402
site_photo_probe.run = lambda idx: None


def build_with(content, name="ig.json", binary=False):
    """Збирає сайт з підміненим ig_featured.json; повертає (тека, stderr)."""
    out = Path(tempfile.mkdtemp())
    bs.OUT = str(out)
    bs.tp.fetch_toysi_catalog = lambda: dict(catalog)
    bs.tp.assert_catalog_size_sane = lambda c, *a, **k: None
    f = out / name
    if content is not None:
        (f.write_bytes if binary else f.write_text)(content, **({} if binary else {"encoding": "utf-8"}))
    bs.IG_FEATURED_FILE = str(f)
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        bs.build()
    return out, err.getvalue()


def cards(html):
    box = re.search(r'<div class="ig-list" id="ig-list" data-list="ig_landing">(.*?)</div>\s*<p class="ig-all">', html, re.S)
    return re.findall(r'href="product-(\d+)\.html"', box.group(1)) if box else None


# 1) штатний випадок: порядок, лише в наявності, дублі, ≤6, числа
payload = {"date": "2026-10-12", "ids": ["7", 3, "900", "3", "999999", "901", "12", "15", "20", "25", "30"]}
out, err = build_with(json.dumps(payload))
ig = (out / "ig.html").read_text(encoding="utf-8")
chk("/ig.html збирається; H1 «З Instagram: що показали Плутус»", '<h1 class="page">З Instagram: що показали Плутус</h1>' in ig)
chk("підзаголовок «Ці товари з нашого останнього відео» і кнопка «Усі іграшки» → catalog.html", "Ці товари з нашого останнього відео" in ig and re.search(r'<a class="btn" href="catalog.html">Усі іграшки</a>', ig) is not None)
c = cards(ig)
chk("сітка: рівно товари з JSON у заданому порядку (7, 3, 901, 12, 15, 20), без дубля «3», без OOS «900» і неіснуючого, максимум 6", c == ["7", "3", "901", "12", "15", "20"])
chk("HTML у назві товару екранується (жодного літерального <b>)", "<b>тегом</b>" not in ig and "&lt;b&gt;тегом&lt;/b&gt;" in ig)
chk("нема в наявності / нема в каталозі → попередження в stderr з id", "900" in err and "999999" in err and "не показано" in err)
chk("noindex,follow (кампанійна сторінка, не для пошуку)", '<meta name="robots" content="noindex,follow">' in ig)
chk("canonical БЕЗ параметрів → utm не плодять дублів", '<link rel="canonical" href="https://plutustoys.com.ua/ig.html">' in ig and "utm" not in re.search(r'<link rel="canonical"[^>]*>', ig).group(0))
sm = (out / "sitemap.xml").read_text(encoding="utf-8")
chk("/ig.html НЕ в sitemap (noindex)", "ig.html" not in sm)
chk("перші картки без lazy (LCP), решта lazy", ig.count('loading="lazy"') >= 1 and 'fetchpriority="high"' in ig)

# 2) відсутній / поламаний файл
for label, content, binary, expect in [
    ("нема файлу", None, False, ""),
    ("битий JSON", "{not json", False, "невалідний JSON"),
    ("корінь-список", '["1","2"]', False, "очікується"),
    ("ids не список", '{"ids": "1,2"}', False, "очікується"),
    ("порожній ids", '{"date":"x","ids":[]}', False, ""),
]:
    o, e = build_with(content, binary=binary)
    pg = (o / "ig.html").read_text(encoding="utf-8")
    chk(f"{label}: сторінка збирається («скоро з’явиться» + кнопка каталогу), сітки нема", "скоро з" in pg and 'id="ig-list"' not in pg and "Усі іграшки" in pg)
    if expect:
        chk(f"{label}: причина ГОЛОСНО в stderr", expect in e and "ig_featured.json" in e)
o, e = build_with(b"\xef\xbb\xbf" + json.dumps({"ids": ["5"]}).encode("utf-8"), binary=True)
chk("BOM (Блокнот/Windows) читається нормально", cards((o / "ig.html").read_text(encoding="utf-8")) == ["5"] and e.count("⚠ ig_featured") == 0)
o, e = build_with(json.dumps({"ids": [True, None, ["x"], "8"]}))
chk("не рядкові/не числові id (bool/null/список) пропускаються голосно, валідний лишається", cards((o / "ig.html").read_text(encoding="utf-8")) == ["8"] and "пропущено" in e)
o, e = build_with(json.dumps({"ids": ["900"]}))
chk("усі товари не в наявності → без сітки, лише кнопка каталогу, попередження «жодного товару в наявності»", 'id="ig-list"' not in (o / "ig.html").read_text(encoding="utf-8") and "жодного товару" in e)

# 3) футер: utm на соцмережі, sameAs чисті
home = (out / "index.html").read_text(encoding="utf-8")
fsoc = re.search(r'<p class="fsoc">(.*?)</p>', home, re.S).group(1)
hrefs = re.findall(r'href="([^"]+)"', fsoc)
chk("футер: Instagram і Facebook з utm_source=site&utm_medium=footer&utm_campaign=social_link", len(hrefs) == 2
    and all("utm_source=site&utm_medium=footer&utm_campaign=social_link" in h for h in hrefs))
chk("футер: у Facebook-посилання (вже має ?id=…) utm додано через «&», а не другий «?»", [h for h in hrefs if "facebook" in h][0].count("?") == 1)
chk("JSON-LD sameAs — чисті адреси без utm", '"sameAs"' in home and "utm_" not in re.search(r'"sameAs":\s*\[[^\]]*\]', home).group(0))
chk("футер із utm є на ВСІХ типах сторінок (картка товару, кошик, ig)", all("utm_source=site&utm_medium=footer" in (out / f).read_text(encoding="utf-8") for f in ("product-5.html", "cart.html", "ig.html")))

# 4) події в node
node = shutil.which("node")
if not node:
    chk("node доступний (без нього події не перевірити)", False)
else:
    harness = r'''
const fs=require("fs"), vm=require("vm");
const code=fs.readFileSync(process.argv[2],"utf8"); const scenario=process.argv[3];
function store(){ const m={}; return {getItem:k=>(k in m?m[k]:null), setItem:(k,v)=>{m[k]=String(v)}, removeItem:k=>{delete m[k]}}; }
function mkEl(o){ const h={}; return Object.assign({h, style:{}, classList:{add(){},remove(){},toggle(){},contains(){return false}}, textContent:"", disabled:false,
  addEventListener(t,f){(h[t]=h[t]||[]).push(f)}, getAttribute(k){return (o.attrs||{})[k]||null}, querySelector(){return null}, querySelectorAll(){return []}, closest(){return null}}, o); }
(async()=>{
  const gt=[], fb=[]; const N=3;
  const cardsEl=Array.from({length:N},(_,k)=>k+1).map(i=>{ const nm=mkEl({textContent:"Товар "+i}), pr=mkEl({textContent:(100*i)+" ₴"}); const a=mkEl({attrs:{href:"product-"+(10+i)+".html"}});
    a.querySelector=s=>s===".nm"?nm:(s===".pr"?pr:null); a.closest=s=>s==="a.card"?a:null; return a; });
  const box=mkEl({attrs:{"data-list":"ig_landing"}}); box.querySelectorAll=s=>s==="a.card"?cardsEl:[];
  const ready=[]; let ioCb=null;
  const doc={addEventListener(t,f){ if(t==="DOMContentLoaded") ready.push(f); }, getElementById(id){ return (id==="ig-list" && scenario!=="nobox")?box:null; }, querySelector(){return null}, querySelectorAll(){return []}, body:{addEventListener(){}}, createElement(){return {style:{}}}};
  const ctx={document:doc, localStorage:store(), sessionStorage:store(), console, fetch:()=>Promise.reject(), navigator:{}, location:{pathname:"/ig.html",href:"/ig.html"}, setTimeout, clearTimeout, setInterval:function(){return 1;}, addEventListener(){}};
  ctx.window=ctx; ctx.self=ctx; ctx.matchMedia=function(){ return {matches:false}; };
  ctx.PT_ANALYTICS={ga4:"G-T", fb:"1"}; ctx.gtag=function(){gt.push(Array.from(arguments))}; ctx.fbq=function(){fb.push(Array.from(arguments))};
  if(scenario==="io"){ ctx.IntersectionObserver=function(cb){ ioCb=cb; this.observe=function(){}; this.disconnect=function(){}; }; }
  vm.createContext(ctx); vm.runInContext(code,ctx);
  let threw=null; try{ ready.forEach(f=>f()); }catch(e){ threw=String(e); }
  const out={threw};
  if(scenario==="io"){
    out.before=gt.filter(e=>e[1]==="view_item_list").length;
    ioCb([{isIntersecting:false}]); out.afterNo=gt.filter(e=>e[1]==="view_item_list").length;
    ioCb([{isIntersecting:true}]); ioCb([{isIntersecting:true}]); out.vil=gt.filter(e=>e[1]==="view_item_list");
  } else { out.vil=gt.filter(e=>e[1]==="view_item_list"); }
  if(scenario!=="nobox"){ (box.h.click||[]).forEach(f=>f({target:cardsEl[1]})); }
  out.sel=gt.filter(e=>e[1]==="select_item"); out.fb=fb.length;
  console.log(JSON.stringify(out));
})();
'''
    tmp = Path(tempfile.mkdtemp())
    hf = tmp / "h.js"
    hf.write_text(harness, encoding="utf-8")
    app = str(HERE / "site" / "assets" / "app.js")

    def run(sc):
        r = subprocess.run([node, str(hf), app, sc], capture_output=True, text=True, encoding="utf-8", timeout=60)
        try:
            return json.loads(r.stdout.strip().splitlines()[-1])
        except Exception:
            print("   stdout:", r.stdout[-300:], "stderr:", r.stderr[:900])
            return None

    a = run("noio")
    chk("без IntersectionObserver: view_item_list одразу, РІВНО один, item_list_name «ig_landing», 3 позиції", a is not None and a["threw"] is None and len(a["vil"]) == 1
        and a["vil"][0][2]["item_list_name"] == "ig_landing" and len(a["vil"][0][2]["items"]) == 3)
    chk("select_item лише на клік: рівно один, item_list_name «ig_landing», це 2-га картка (item_id 12, index 1)", a is not None and len(a["sel"]) == 1
        and a["sel"][0][2]["item_list_name"] == "ig_landing" and a["sel"][0][2]["items"][0]["item_id"] == "12" and a["sel"][0][2]["items"][0]["index"] == 1)
    io = run("io")
    chk("з IntersectionObserver: до появи/поза екраном — нічого; побачили → один view_item_list (повтори не множать)", io is not None and io["before"] == 0 and io["afterNo"] == 0 and len(io["vil"]) == 1)
    nb = run("nobox")
    chk("на сторінці БЕЗ #ig-list (інші сторінки) жодних подій і нічого не падає", nb is not None and nb["threw"] is None and nb["vil"] == [] and nb["sel"] == [])

print()
if F:
    print(f"FAILED ({len(F)}):")
    for n in F:
        print("  -", n)
    sys.exit(1)
print("ALL OK")

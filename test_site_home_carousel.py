#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_site_home_carousel.py — головна 11.10.2026 (SMM 09.10, рішення власника «герой крінж, зверху карусель»): компактний герой + карусель «Добірка Плутуса».
Інваріанти відбору home_pick(): у наявності, ціна 150–700 ₴, внесок ≥ HOME_MIN_CONTRIB, не «Уцінка», не картини за номерами/брелоки/зброя, ≤2 з категорії, ≤10 позицій,
порядок за внеском спадно (детермінований). Розмітка: герой без великого абзацу, карусель зі стрілками, перші 3 зображення без lazy, «Новинок» нема («Більше іграшок»), без повторів
із каруселлю; без підходящих товарів карусель не рендериться. Поведінка app.js (node/vm): стрілки скролять, view_item_list рівно один раз із позиціями каруселі, select_item по кліку,
phone_click по tel:, social_click по IG/FB у футері; без ID нічого не шлеться. Мережа не потрібна."""
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
import build_site as bs  # noqa: E402

F = []


def chk(n, c):
    print(f"[{'OK ' if c else 'FAIL'}] {n}")
    if not c:
        F.append(n)


def prod(i, cat="Ляльки", price=300, contrib=150.0, stock=5, photo=True, name=None):
    return {"id": str(i), "name": name or f"Іграшка {i}", "price": price, "category": cat, "photo": f"https://toysi.ua/p/{i}.jpg" if photo else "",
            "stock": stock, "desc": "", "contribution": contrib}


# ── відбір
good = [prod(i, cat=f"Кат{i % 7}", contrib=100 + i) for i in range(1, 41)]       # 40 прохідних у 7 категоріях
good += [prod(800 + i, cat="Домінуюча", contrib=1000 + i) for i in range(6)]       # 6 найвищих внесків В ОДНІЙ категорії — ліміт «≤2 з категорії» має спрацювати
bad = [
    prod(901, price=149, contrib=500), prod(902, price=701, contrib=500),               # поза діапазоном ціни
    prod(903, contrib=99.99), prod(904, contrib=None),                                  # внесок
    prod(905, cat="Уцінка", contrib=900), prod(906, cat="Картини за номерами", contrib=900), prod(907, cat="Брелоки", contrib=900),
    prod(908, cat="Автомати і пістолети", contrib=900), prod(909, cat="Зброя", contrib=900),
    prod(910, photo=False, contrib=900), prod(911, stock=0, contrib=900),
]
pick = bs.home_pick(good + bad)
ids = [p["id"] for p in pick]
chk("відбір: ≤10 позицій", len(pick) == 10)
chk("відбір: домінуюча категорія дала рівно 2 (а не 6)", sum(1 for p in pick if p["category"] == "Домінуюча") == 2)
chk("відбір: жодної з відсіяних (ціна, внесок, Уцінка, картини/брелоки/зброя, без фото, без залишку)", not any(p["id"].startswith("9") for p in pick))
chk("відбір: не більше 2 з категорії", max(sum(1 for p in pick if p["category"] == c) for c in {p["category"] for p in pick}) <= 2)
chk("відбір: ціна 150–700 і внесок ≥ HOME_MIN_CONTRIB для кожної", all(150 <= p["price"] <= 700 and p["contribution"] >= bs.HOME_MIN_CONTRIB for p in pick))
chk("відбір: порядок за внеском спадно", [p["contribution"] for p in pick] == sorted((p["contribution"] for p in pick), reverse=True))
chk("відбір: детермінований (той самий вхід у іншому порядку → той самий результат)", [p["id"] for p in bs.home_pick(list(reversed(good + bad)))] == ids)
chk("відбір: межі ціни включні (150 і 700 проходять)", {p["id"] for p in bs.home_pick([prod(1, price=150, cat="A"), prod(2, price=700, cat="B")])} == {"1", "2"})
chk("відбір: порожній вхід / нічого прохідного → []", bs.home_pick([]) == [] and bs.home_pick(bad) == [])
chk("відбір: категорії без урахування регістру/пробілів («  Брелоки »)", bs.home_pick([prod(1, cat="  БРЕЛОКИ ")]) == [])

# ── розмітка головної
cats = {}
for p in good + bad:
    cats.setdefault(p["category"], []).append(p)
for c in cats:
    cats[c].sort(key=lambda p: -(p["contribution"] or -1e9))
cat_list = sorted(cats)
cat_slug = {c: bs.slugify(c) for c in cat_list}
tmp = Path(tempfile.mkdtemp())
bs.OUT = str(tmp)
bs.write_home(good + bad, cats, cat_list, cat_slug)
h = (tmp / "index.html").read_text(encoding="utf-8")
chk("герой: компактний .hero2 (ніяких старих .hero/.fox, hero-note)", 'class="hero2"' in h and 'class="hero"' not in h and "hero-note" not in h)
chk("герой: h1 «Іграшки, що радують», кнопка на каталог, чип доставки з оплатою", "<h1>Іграшки, що радують</h1>" in h and 'class="btn" href="catalog.html">Обрати іграшку' in h and 'class="chip">🚚 Нова Пошта · оплата при отриманні' in h)
chk("герой: підзаголовок ≤100 символів", len(re.search(r'<div class="txt"><h1>.*?</h1><p>(.*?)</p>', h).group(1)) <= 100)
chk("карусель: секція «Добірка Плутуса», #home-car, 2 стрілки-кнопки з aria-label", 'id="home-car"' in h and "<h2>Добірка Плутуса</h2>" in h and h.count('class="arrow') == 2 and h.count("aria-label=\"Попередні товари\"") == 1)
slides = re.findall(r'<a class="card slide" href="product-(\d+)\.html">', h)
chk("карусель: рівно 10 слайдів у порядку відбору", slides == ids)
imgs = re.findall(r'<a class="card slide".*?<img ([^>]*)>', h, re.S)
chk("карусель: перші 3 зображення без loading=lazy, решта — lazy", all("lazy" not in x for x in imgs[:3]) and all("lazy" in x for x in imgs[3:]))
chk("«Новинок» нема, є «Більше іграшок»", "Новинки" not in h and "<h2>Більше іграшок</h2>" in h)
tail = h.split("<h2>Більше іграшок</h2>", 1)[1] if "<h2>Більше іграшок</h2>" in h else ""
more = re.findall(r'href="product-(\d+)\.html"', tail)
chk("«Більше іграшок»: без повторів із каруселлю", not (set(more) & set(ids)))
chk("в усій розмітці немає «Знижок»/«Хітів продажів» (немає старої ціни й власних продажів)", "Знижки" not in h and "Хіти продажів" not in h)
bs.write_home(bad, {c: [p for p in v if p in bad] for c, v in cats.items() if any(p in bad for p in v)}, [c for c in cat_list if any(p in bad for p in cats[c])], cat_slug)
h2 = (tmp / "index.html").read_text(encoding="utf-8")
chk("без підходящих товарів карусель НЕ рендериться (герой і категорії є)", 'id="home-car"' not in h2 and 'class="hero2"' in h2)
css = (HERE / "site" / "assets" / "styles.css").read_text(encoding="utf-8")
chk("CSS: .hero2, .car .track (scroll-snap), мобільне правило ≤600px ховає абзац і стрілки",
    ".hero2{" in css and "scroll-snap-type:x mandatory" in css and "@media(max-width:600px){" in css and ".hero2 p{display:none}" in css and ".car .arrow{display:none}" in css)
chk("CSS: без автопрокрутки/анімації каруселі (animation/autoplay відсутні в .car)", not re.search(r"\.car[^{}]*\{[^}]*animation", css))

# ── поведінка app.js
node = shutil.which("node")
if not node:
    chk("node доступний (без нього поведінку не перевірити)", False)
else:
    harness = r'''
const fs=require("fs"), vm=require("vm");
const code=fs.readFileSync(process.argv[2],"utf8"); const scenario=process.argv[3];
function store(){ const m={}; return {getItem:k=>(k in m?m[k]:null), setItem:(k,v)=>{m[k]=String(v)}, removeItem:k=>{delete m[k]}}; }
function mkEl(o){ const h={}; const e=Object.assign({h, style:{}, classList:{add(){},remove(){},toggle(){}}, textContent:"", disabled:false, scrollLeft:0, clientWidth:600, scrollWidth:2400,
  addEventListener(t,f){(h[t]=h[t]||[]).push(f)}, getAttribute(k){return (o.attrs||{})[k]||null}, querySelector(){return null}, querySelectorAll(){return []}, closest(){return null}, scrollByCalls:[]}, o);
  e.scrollBy=function(a){ e.scrollByCalls.push(a); }; return e; }
(async()=>{
  const gt=[], fb=[]; const doc_h={};
  const cards=[1,2,3].map(i=>{ const nm=mkEl({textContent:"Товар "+i}), pr=mkEl({textContent:(100*i)+" ₴"});
    const a=mkEl({attrs:{href:"product-"+(10+i)+".html"}}); a.querySelector=s=>s===".nm"?nm:(s===".pr"?pr:null); a.closest=s=>s==="a.card"?a:null; return a; });
  const left=mkEl({}), right=mkEl({}), track=mkEl({});
  const car=mkEl({attrs:{"data-list":"Добірка Плутуса"}});
  car.querySelector=s=>s===".track"?track:(s===".arrow.l"?left:(s===".arrow.r"?right:null));
  car.querySelectorAll=s=>s==="a.card"?cards:[];
  const ready=[]; let ioCb=null; let ioCount=0;
  const doc={addEventListener(t,f){ if(t==="DOMContentLoaded") ready.push(f); else (doc_h[t]=doc_h[t]||[]).push(f); },
    getElementById(id){ return id==="home-car"?car:null; }, querySelector(){return null}, querySelectorAll(){return []}, body:{addEventListener(){}}, createElement(){return {style:{}}}};
  const ctx={document:doc, localStorage:store(), sessionStorage:store(), console, fetch:()=>Promise.reject(), navigator:{}, location:{pathname:"/", href:"/"}, setTimeout, clearTimeout};
  ctx.window=ctx; ctx.self=ctx;
  if(scenario!=="noid"){ ctx.PT_ANALYTICS={ga4:"G-T", fb:"1"}; ctx.gtag=function(){gt.push(Array.from(arguments))}; ctx.fbq=function(){fb.push(Array.from(arguments))}; }
  if(scenario==="io"){ ctx.IntersectionObserver=function(cb){ ioCb=cb; ioCount++; this.observe=function(){}; this.disconnect=function(){}; }; }
  vm.createContext(ctx); vm.runInContext(code,ctx); ready.forEach(f=>f());
  const out={};
  if(scenario==="io"){
    out.before=gt.filter(e=>e[1]==="view_item_list").length;
    ioCb([{isIntersecting:false}]); out.afterNo=gt.filter(e=>e[1]==="view_item_list").length;
    ioCb([{isIntersecting:true}]); ioCb([{isIntersecting:true}]); out.afterYes=gt.filter(e=>e[1]==="view_item_list").length;
  } else { out.vil=gt.filter(e=>e[1]==="view_item_list"); }
  right.h.click.forEach(f=>f()); left.h.click.forEach(f=>f());
  out.scroll=track.scrollByCalls;
  car.h.click.forEach(f=>f({target:cards[1]}));
  out.select=gt.filter(e=>e[1]==="select_item");
  const mkA=(href,ins)=>{ const a=mkEl({attrs:{href}}); a.closest=s=>s==="a[href]"?a:(s===".fsoc"&&ins?{}:null); return a; };
  (doc_h.click||[]).forEach(f=>f({target:mkA("tel:+380730150815",false)}));
  (doc_h.click||[]).forEach(f=>f({target:mkA("https://www.instagram.com/plutustoys.ua/",true)}));
  (doc_h.click||[]).forEach(f=>f({target:mkA("https://www.facebook.com/profile.php?id=1",true)}));
  (doc_h.click||[]).forEach(f=>f({target:mkA("catalog.html",false)}));
  out.phone=gt.filter(e=>e[1]==="phone_click"); out.social=gt.filter(e=>e[1]==="social_click"); out.fb=fb;
  out.total=gt.length;
  console.log(JSON.stringify(out));
})();
'''
    hf = tmp / "h.js"
    hf.write_text(harness, encoding="utf-8")
    app = str(HERE / "site" / "assets" / "app.js")

    def run(sc):
        r = subprocess.run([node, str(hf), app, sc], capture_output=True, text=True, encoding="utf-8", timeout=60)
        try:
            return json.loads(r.stdout.strip().splitlines()[-1])
        except Exception:
            print("   stdout:", r.stdout[-300:], "stderr:", r.stderr[:700])
            return None

    a = run("plain")
    chk("без IntersectionObserver: view_item_list шлеться одразу, РІВНО один раз, з 3 позиціями каруселі", a is not None and len(a["vil"]) == 1 and len(a["vil"][0][2]["items"]) == 3)
    if a and a["vil"]:
        prm = a["vil"][0][2]
        chk("view_item_list: item_list_id=home_pick, item_list_name=«Добірка Плутуса», items з item_id/item_name/price/index", prm["item_list_id"] == "home_pick" and prm["item_list_name"] == "Добірка Плутуса"
            and prm["items"][0] == {"item_id": "11", "item_name": "Товар 1", "price": 100, "index": 0, "item_list_name": "Добірка Плутуса"})
    chk("стрілки: › скролить вправо, ‹ вліво (smooth, ≥160 px)", a is not None and len(a["scroll"]) == 2 and a["scroll"][0]["left"] > 0 > a["scroll"][1]["left"] and a["scroll"][0]["behavior"] == "smooth")
    chk("select_item по кліку на 2-й слайд: item_id=12, index=1, список «Добірка Плутуса»", a is not None and len(a["select"]) == 1 and a["select"][0][2]["items"][0]["item_id"] == "12" and a["select"][0][2]["items"][0]["index"] == 1)
    chk("phone_click по tel: (+ Meta Contact); звичайне посилання подій не дає", a is not None and len(a["phone"]) == 1 and a["phone"][0][2]["link_url"].startswith("tel:") and any(x[:2] == ["track", "Contact"] for x in a["fb"]))
    chk("social_click: instagram і facebook (+ Meta trackCustom SocialClick); лише з .fsoc",
        a is not None and [e[2]["network"] for e in a["social"]] == ["instagram", "facebook"] and sum(1 for x in a["fb"] if x[:2] == ["trackCustom", "SocialClick"]) == 2)
    io = run("io")
    chk("з IntersectionObserver: до появи на екрані подій нема; видимість → рівно ОДИН view_item_list (повторні спрацювання не дублюють)", io is not None and io["before"] == 0 and io["afterNo"] == 0 and io["afterYes"] == 1)
    n = run("noid")
    chk("без PT_ANALYTICS (нема ID) жодна подія не шлеться і нічого не падає (стрілки працюють)", n is not None and n["total"] == 0 and len(n["scroll"]) == 2)

print(f"\n{'❌ ПРОВАЛЕНО: ' + str(F) if F else '✅ головна (герой + карусель) — усі перевірки коректні.'}")
sys.exit(1 if F else 0)

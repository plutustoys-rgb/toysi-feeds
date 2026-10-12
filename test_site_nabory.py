#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_site_nabory.py — 12.10.2026 (SMM): /nabory.html «Набори, які займуть дитину надовго» + кнопка «Набори в подарунок» у герої.
Перевіряє: відбір (у назві «набір», в наявності, фото, 150–700 ₴, внесок ≥ 50, не «Уцінка», не зброя), групи/чипи, round-robin, ≤1 на «основу назви», ліміти, бейдж залишку 1–3 (badge=True),
розмітка (data-g, data-list, data-limit), чипи сховані без JS, SEO (title/description/canonical, sitemap), аналітика (node: view_item_list з item_list_id «nabory_page», ≤24 видимих; select_item; чип
ховає картки), без обіцянок безкоштовної доставки. Без node частина про поведінку падає ЯВНО."""
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


_n = [0]


def P(name, cat, price=300, contrib=80, stock=5, photo="https://x/p.jpg"):
    _n[0] += 1
    return {"id": str(1000 + _n[0]), "name": name, "category": cat, "price": price, "contribution": contrib, "stock": stock, "photo": photo}


prods = []
# 40 різних основ назв у «Творчість» (для ліміту 30), по 3 в інших групах, сміття, що має відсіятись
for i in range(40):
    prods.append(P(f"Набір для творчості Модель{chr(1040 + i % 30)}{i} \"Серія{i}\"", "Інша творчість"))
for cat in ("Тісто для ліплення і пластилін", "Наукові ігри", "Кухні і посуд", "Прикраси", "Набори для фокусів", "Лікарські набори"):
    for i in range(3):
        prods.append(P(f"Набір {cat} \"Лінійка{i}{cat[:3]}\"", cat))
for i in range(30):
    prods.append(P(f"Набір пластикових машинок \"Парк{i}\"", "Пластикові машинки"))      # «other» — ліміт 24
junk = {
    "без слова набір": P("Конструктор великий", "Прикраси"),
    "не в наявності": P("Набір не в наявності", "Прикраси", stock=0),
    "дешевий": P("Набір дешевий", "Прикраси", price=149),
    "дорогий": P("Набір дорогий", "Прикраси", price=701),
    "низький внесок": P("Набір без внеску", "Прикраси", contrib=49.9),
    "нема внеску": P("Набір без розрахунку", "Прикраси", contrib=None),
    "без фото": P("Набір без фото", "Прикраси", photo=""),
    "уцінка": P("Набір уцінений", "Уцінка"),
    "зброя": P("Набір зі зброєю", "Автомати і пістолети"),
    "набір зі зброєю": P("Набір пістолетів", "Набори зі зброєю"),
}
prods += list(junk.values())
low1 = P("Набір з одним залишком \"Рідкісний\"", "Прикраси", stock=1)
prods.append(low1)

picked = bs.nabory_pick(prods)
ids = {p["id"] for _g, p in picked}
chk("відсіяно все зайве: без «набір» у назві, не в наявності, <150/>700 ₴, внесок <50 або None, без фото, «Уцінка», зброя", all(j["id"] not in ids for j in junk.values()))
from collections import Counter  # noqa: E402
cnt = Counter(g for g, _p in picked)
chk(f"ліміти: творчість ≤30 (є {cnt['tvorchist']}), інші машинки лише в «other» ≤24 (є {cnt['other']})", cnt["tvorchist"] == bs.NABORY_PER_GROUP and cnt["other"] == bs.NABORY_OTHER_MAX)
chk("кожна група з товарами представлена (7 груп + other)", set(cnt) == {k for k, _t, _c in bs.NABORY_GROUPS} | {"other"})
chk("round-robin: перші 8 карток — по одній із різних груп (перший екран різноманітний)", len({g for g, _p in picked[:8]}) == 8)
chk("≤1 картка на «основу назви» в групі (дублі лінійки відсікаються)", len({(g, bs._pick_key(p["name"])) for g, p in picked}) == len(picked))
chk("детерміновано: дві збірки дають той самий порядок (фіксоване зерно)", [p["id"] for _g, p in bs.nabory_pick(prods)] == [p["id"] for _g, p in picked])
chk("порожній вибір → порожній список (сторінку не пишемо)", bs.nabory_pick([junk["уцінка"]]) == [])

out = Path(tempfile.mkdtemp())
bs.OUT = str(out)
ok = bs.write_nabory(prods)
html = (out / "nabory.html").read_text(encoding="utf-8")
chk("write_nabory пише сторінку", ok is True)
chk("H1 і підзаголовок за текстом SMM", "<h1 class=\"page\">Набори, які займуть дитину надовго</h1>" in html and "Готові набори — усе потрібне в комплекті." in html)
chk("чипи: «Усі» + 7 груп, кнопки з data-g/aria-pressed, 'Усі' активний", html.count('class="chip') >= 8 and 'data-g="all" aria-pressed="true"' in html and all(f'data-g="{k}"' in html for k, _t, _c in bs.NABORY_GROUPS)
    and all(t in html for _k, t, _c in bs.NABORY_GROUPS))
chk("картки несуть data-g, group «other» без чипа", html.count('<a class="card" data-g=') == len(picked) and 'data-g="other"' in html and 'data-g="other" aria-pressed' not in html)
chk("список: id=nabory-list, data-list=nabory_page, data-limit=24; бейдж залишку лише на stock 1–3 (badge=True)", 'id="nabory-list" data-list="nabory_page" data-limit="24"' in html and "Залишилось 1 шт" in html and "Залишилось 5 шт" not in html)
chk("перші 3 картки без lazy, перша fetchpriority=high (LCP)", html.count('fetchpriority="high"') == 1)
chk("SEO: canonical nabory.html, title з назвою, description без обіцянки безкоштовної доставки, не noindex", 'rel="canonical" href="https://plutustoys.com.ua/nabory.html"' in html and "noindex" not in html and "<title>Набори, які займуть дитину надовго" in html and "безкоштовн" not in html.lower())
_css = (HERE / "site/assets/styles.css").read_text(encoding="utf-8")
chk("чипи: місце зарезервовано до JS (visibility, без CLS), .js — visible; без JS ховає <noscript><style>; решта карток видима", ".nb-chips{visibility:hidden}" in _css and ".nb-chips.js{visibility:visible}" in _css and "<noscript><style>.nb-chips{display:none}</style></noscript>" in html)
_app = (HERE / "site/assets/app.js").read_text(encoding="utf-8")
chk("view_item_list: IO спостерігає ПЕРШУ картку (не весь список 11–23 тис. px, інакше поріг 0.1 не досягається — аудит #706)", "io.observe(cards[0] || box);" in _app and "io.observe(box);" not in _app[_app.index("function initListTracking"):_app.index("function initNaboryChips")])
chk("мілітарі/зброя за назвою відсіюються (пістолет, військов, солдат, БТР…) і категорії солдатики/військова техніка", all(bs.nabory_pick([P(n, "Інша творчість")]) == [] for n in ("Набір військового з пістолетом", "Набір «Військова техніка»", "Набір солдатиків", "Набір героя, механічна зброя", "Набір БТР")) and bs.nabory_pick([P("Набір фігурок", "Солдатики")]) == [] and len(bs.nabory_pick([P("Набір для творчості", "Інша творчість")])) == 1)
chk("герой: мобільний рядок обгорнутий (flex-wrap), «Усі іграшки» окремим рядком — не вилазить за край на 320 px", "flex-wrap:wrap;align-items:center;gap:6px 10px" in _css and ".hero2 .hero-all{flex:0 0 100%;order:3" in _css)

# герой головної
src = (HERE / "site" / "build_site.py").read_text(encoding="utf-8")
chk("герой: «Набори в подарунок» → nabory.html, під нею «Усі іграшки» → catalog.html (SMM)", '<a class="btn" href="nabory.html">Набори в подарунок</a><a class="hero-all" href="catalog.html">Усі іграшки</a>' in src and "Обрати іграшку" not in src)
chk("sitemap: nabory.html додається лише коли сторінка записана", '[("nabory.html", "0.7", "weekly")] if has_nabory else []' in src and "has_nabory = write_nabory(prods)" in src)

# node: аналітика й чипи
node = shutil.which("node")
if not node:
    chk("node доступний (без нього поведінку не перевірити)", False)
else:
    harness = r'''
const fs=require("fs"), vm=require("vm");
const code=fs.readFileSync(process.argv[2],"utf8");
function store(){ const m={}; return {getItem:k=>(k in m?m[k]:null), setItem:(k,v)=>{m[k]=String(v)}, removeItem:k=>{delete m[k]}}; }
function mkEl(o){ const h={}; const cls=new Set(); const attrs=Object.assign({}, o.attrs||{});
  const e=Object.assign({h, hidden:false, style:{}, classList:{add(c){cls.add(c)},remove(c){cls.delete(c)},toggle(c,on){ if(on===undefined) on=!cls.has(c); on?cls.add(c):cls.delete(c); },contains(c){return cls.has(c)}}, textContent:"", _cls:cls, _attrs:attrs,
    addEventListener(t,f){(h[t]=h[t]||[]).push(f)}, getAttribute(k){return k in attrs?attrs[k]:null}, setAttribute(k,v){attrs[k]=String(v)}, querySelector(){return null}, querySelectorAll(){return []}, closest(){return null}}, o); return e; }
function fire(el,t,ev){ (el.h[t]||[]).forEach(f=>f(ev||{})); }
(async()=>{
  const N=40, gs=["tvorchist","lipl","nauka","kuhni"];
  const cards=Array.from({length:N},(_,i)=>{ const nm=mkEl({textContent:"Набір "+i}), pr=mkEl({textContent:(200+i)+" ₴"});
    const a=mkEl({attrs:{href:"product-"+(10+i)+".html","data-g":gs[i%4]}}); a.querySelector=s=>s===".nm"?nm:(s===".pr"?pr:null); a.closest=s=>s==="a.card"?a:null; return a; });
  const box=mkEl({attrs:{"data-list":"nabory_page","data-limit":"24"}}); box.querySelectorAll=s=>s==="a.card"?cards:[];
  const btn=(g)=>{ const b=mkEl({attrs:{"data-g":g}}); b.closest=s=>s==="button[data-g]"?b:null; return b; };
  const bAll=btn("all"), bL=btn("lipl"); const bar=mkEl({}); bar.querySelectorAll=s=>s==="button[data-g]"?[bAll,bL]:[];
  const ready=[]; const gt=[]; const ioCbs=[];
  const doc={hidden:false, addEventListener(t,f){ if(t==="DOMContentLoaded") ready.push(f); }, getElementById(id){ return id==="nabory-list"?box:(id==="nb-chips"?bar:null); }, querySelector(){return null}, querySelectorAll(){return []}, body:{addEventListener(){},appendChild(){}}, createElement(){return {style:{},appendChild(){}}}};
  const ctx={document:doc, localStorage:store(), sessionStorage:store(), console, fetch:()=>Promise.reject(), navigator:{}, location:{pathname:"/nabory.html",href:"/nabory.html"}, setTimeout, clearTimeout, Date, setInterval:()=>1, addEventListener(){}};
  ctx.window=ctx; ctx.self=ctx; ctx.matchMedia=()=>({matches:false});
  ctx.PT_ANALYTICS={ga4:"G-T", fb:"1"}; ctx.gtag=function(){gt.push(Array.from(arguments))}; ctx.fbq=function(){};
  ctx.IntersectionObserver=function(cb){ ioCbs.push(cb); this.observe=function(){}; this.disconnect=function(){}; };
  vm.createContext(ctx); vm.runInContext(code,ctx);
  let threw=null; try{ ready.forEach(f=>f()); }catch(e){ threw=String(e); }
  const lists=()=>gt.filter(e=>e[1]==="view_item_list");
  ioCbs.forEach(cb=>cb([{isIntersecting:true}])); ioCbs.forEach(cb=>cb([{isIntersecting:true}]));
  const l1=lists().map(e=>({id:e[2].item_list_id, name:e[2].item_list_name, n:e[2].items.length, first:e[2].items[0]&&e[2].items[0].item_name}));
  // чип «lipl» ховає все, крім data-g=lipl; «all» повертає
  fire(bar,"click",{target:bL}); const visL=cards.filter(a=>!a.hidden).length; const allLipl=cards.filter(a=>!a.hidden).every(a=>a.getAttribute("data-g")==="lipl");
  const pressed=[bAll._attrs["aria-pressed"], bL._attrs["aria-pressed"]], active=[bAll._cls.has("active"), bL._cls.has("active")];
  fire(bar,"click",{target:bAll}); const visAll=cards.filter(a=>!a.hidden).length;
  fire(box,"click",{target:cards[5]});
  const sel=gt.filter(e=>e[1]==="select_item").map(e=>({id:e[2].item_list_id, name:e[2].item_list_name, n:e[2].items.length}));
  console.log(JSON.stringify({threw, l1, visL, allLipl, pressed, active, visAll, sel}));
})();
'''
    tmp = Path(tempfile.mkdtemp())
    hf = tmp / "h.js"
    hf.write_text(harness, encoding="utf-8")
    r = subprocess.run([node, str(hf), str(HERE / "site" / "assets" / "app.js")], capture_output=True, text=True, encoding="utf-8", timeout=60)
    try:
        o = json.loads(r.stdout.strip().splitlines()[-1])
    except Exception:
        o = None
        print("stdout:", r.stdout[-400:], "stderr:", r.stderr[:600])
    chk("node: скрипт не падає", o is not None and o["threw"] is None)
    if o:
        chk("view_item_list рівно один раз, item_list_id=item_list_name=nabory_page, не більше data-limit=24 карток", len(o["l1"]) == 1 and o["l1"][0]["id"] == "nabory_page" and o["l1"][0]["name"] == "nabory_page" and o["l1"][0]["n"] == 24)
        chk("чип «Ліплення» лишає видимими лише картки своєї групи (10 із 40), aria-pressed/active перемикаються", o["visL"] == 10 and o["allLipl"] and o["pressed"] == ["false", "true"] and o["active"] == [False, True])
        chk("чип «Усі» повертає всі 40", o["visAll"] == 40)
        chk("select_item на клік: item_list_name nabory_page, одна позиція", o["sel"] == [{"id": "nabory_page", "name": "nabory_page", "n": 1}])

print()
if F:
    print(f"FAILED ({len(F)}):")
    for n in F:
        print("  -", n)
    sys.exit(1)
print("ALL OK")

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_site_home_carousel.py — головна 11.10.2026 (SMM 09.10, рішення власника «герой крінж, зверху карусель»; правки SMM 11.10 і аудит #678): компактний герой + карусель «Добірка Плутуса».
Відбір home_pick(): у наявності, фото, ціна 150–700 ₴ (межі включні), внесок ≥ HOME_MIN_CONTRIB (100.0 включно), не «Уцінка»/«пошкоджено…» (і за категорією, і за НАЗВОЮ), не
картини за номерами/брелоки/зброя-категорії, не репліка зброї за назвою (weapon_policy), НЕ БІЛЬШЕ 1 картки на «основу назви» (дедуплікація), ≤2 з категорії, ≤10, round-robin по категоріях,
з кожної — позиція з МЕДІАННИМ внеском (а не найвищим: той упирається в стелю 700 ₴), перемішування з фіксованим зерном (стабільне й не залежить від порядку входу).
Розмітка: компактний герой, карусель (role=region, <button>-стрілки), перші 5 зображень без lazy і fetchpriority=high на першому, «Більше іграшок» без повторів. CSS: чип не nowrap, мобільна
розкладка героя до 700 px, стрілки лише з класом .js. Поведінка app.js (node/vm): стрілки, допуск ±6 px для disabled (scrollLeft≈2,4 у спокої), resize/orientationchange, prefers-reduced-motion,
клас .js, null-guard на сторінках без каруселі, view_item_list рівно раз, select_item, phone_click, social_click; без ID нічого. weapon_policy ≡ generate_google_feed.is_weapon_replica (корпус)."""
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


def prod(i, cat="Ляльки", price=300, contrib=150.0, stock=5, photo=True, name=None):
    return {"id": str(i), "name": name or f"Унікальна іграшка {chr(1072 + i % 26)}{i}", "price": price, "category": cat,
            "photo": f"https://toysi.ua/p/{i}.jpg" if photo else "", "stock": stock, "desc": "", "contribution": contrib}


# ── ключ основи назви
k = bs._pick_key
chk("ключ: колір у дужках і префікс «Мʼяка іграшка» не різнять: «Мʼяка іграшка \"Кролик, що дихає\" (ЗЕЛЕНИЙ)» ≡ «(ЖОВТИЙ)»", k('Мʼяка іграшка "Кролик, що дихає" (ЗЕЛЕНИЙ)') == k('Мʼяка іграшка "Кролик, що дихає" (ЖОВТИЙ)'))
chk("ключ: бренд латиницею: «Набір для творчості Sekoia Picamania» ≡ «Набір для рукоділля Sekoia Kraftopia»", k("Набір для творчості Sekoia Picamania: Мої ласощі") == k("Набір для рукоділля Sekoia Kraftopia"))
chk("ключ: без лапок/латиниці — перші 2 значущі слова: «Рюкзак дитячий універсальний» ≡ «Рюкзак універсальний, 26 см»", k("Рюкзак дитячий універсальний") == k("Рюкзак універсальний 26 см"))
chk("ключ: різні лінійки різняться: «\"Русалонька\"» ≠ «\"Попелюшка\"»", k('Лялька "Русалонька"') != k('Лялька "Попелюшка"'))
chk("ключ: порожня/дивна назва не падає", isinstance(k(""), str) and isinstance(k(None), str) and isinstance(k("123 456"), str))

# ── відбір
cats7 = [f"Кат{c}" for c in range(7)]
good = []
for c, cat in enumerate(cats7):
    for j in range(8):                                     # у кожній категорії 8 позицій із різними внесками й різними назвами
        good.append(prod(100 * c + j, cat=cat, price=200 + 50 * j, contrib=100.0 + 10 * j + c, name=f"Товар{cat}Варіант{j}Лінійка{c}{j}"))
dom = [prod(800 + i, cat="Домінуюча", price=690 + i, contrib=1000.0 + i, name=f"Домінант{i}Модель{i}") for i in range(6)]   # 6 найвищих внесків В ОДНІЙ категорії
dups = [prod(850 + i, cat="Дубль", price=600, contrib=2000.0 + i, name=f'Мʼяка іграшка "Кролик, що дихає" ({col})') for i, col in enumerate(["ЗЕЛЕНИЙ", "ЖОВТИЙ", "РОЖЕВИЙ"])]
bad = [
    prod(901, price=149, contrib=500, cat="Х1"), prod(902, price=701, contrib=500, cat="Х2"),            # поза діапазоном ціни
    prod(903, contrib=99.99, cat="Х3"), prod(904, contrib=None, cat="Х4"),                               # внесок
    prod(905, cat="Уцінка", contrib=900), prod(906, cat="Картини за номерами", contrib=900), prod(907, cat="Брелоки", contrib=900),
    prod(908, cat="Автомати і пістолети", contrib=900), prod(909, cat="Зброя", contrib=900),
    prod(910, photo=False, contrib=900, cat="Х5"), prod(911, stock=0, contrib=900, cat="Х6"),
    prod(912, cat="Х7", contrib=900, name="Лялька (Уцінка) пошкоджена упаковка"), prod(913, cat="Х8", contrib=900, name="Розпродаж Лялька"),
    prod(914, cat="Х9", contrib=900, name="Автомат іграшковий з кульками 6 мм"), prod(915, cat="Х10", contrib=900, name="Іграшка не працює"),
]
allp = good + dom + dups + bad
pick = bs.home_pick(allp)
ids = [p["id"] for p in pick]
chk("відбір: ≤10 позицій і рівно 10 при достатньому виборі", len(pick) == 10)
chk("відбір: жодної з відсіяних (ціна, внесок, Уцінка/«пошкоджено»/«розпродаж»/«не працює» за назвою, картини/брелоки/зброя-категорії, репліка за назвою, без фото, без залишку)",
    not any(int(p["id"]) >= 900 for p in pick))
chk("відбір: домінуюча категорія дала ≤2 (а не 6)", sum(1 for p in pick if p["category"] == "Домінуюча") <= 2)
chk("відбір: не більше 2 з категорії", max(sum(1 for p in pick if p["category"] == c) for c in {p["category"] for p in pick}) <= 2)
chk("відбір: дедуплікація — із трьох «Кролик, що дихає» не більше однієї", sum(1 for p in pick if "Кролик" in p["name"]) <= 1)
keys = [bs._pick_key(p["name"]) for p in pick]
chk("відбір: усі ключі основи назви різні", len(set(keys)) == len(keys))
chk("відбір: ціна 150–700 і внесок ≥ HOME_MIN_CONTRIB для кожної", all(150 <= p["price"] <= 700 and p["contribution"] >= bs.HOME_MIN_CONTRIB for p in pick))
chk("відбір: детермінований і НЕ залежить від порядку входу (перевернутий вхід → той самий список у тому самому порядку)", [p["id"] for p in bs.home_pick(list(reversed(allp)))] == ids)
chk("відбір: другий виклик дає той самий результат (стабільне зерно)", [p["id"] for p in bs.home_pick(allp)] == ids)
medians = {p["id"] for c in cats7 for p in [sorted([x for x in good if x["category"] == c], key=lambda x: (-x["contribution"], x["id"]))[4]]}
top1 = {sorted([x for x in good if x["category"] == c], key=lambda x: (-x["contribution"], x["id"]))[0]["id"] for c in cats7}
def _band_ok(c):
    lst = sorted([x for x in good if x["category"] == c], key=lambda x: (-x["contribution"], x["id"]))
    got = [i for i, x in enumerate(lst) if x["id"] in set(ids)]
    return all(len(lst) // 4 <= i <= (3 * len(lst)) // 4 for i in got)
chk("відбір: з категорії береться позиція із СЕРЕДНЬОЇ смуги за внеском (25–75%, щоденна ротація 13.10; раніше строго медіана), найвищих із 7 категорій нема", all(_band_ok(c) for c in cats7) and not (top1 & set(ids)))
chk("відбір: ціновий розкид — не всі біля стелі (макс−мін ≥ 100 ₴ на синтетиці з різними цінами)", max(p["price"] for p in pick) - min(p["price"] for p in pick) >= 100)
chk("відбір: внесок рівно 100.0 і ціни 150/700 включно проходять", {p["id"] for p in bs.home_pick([prod(1, price=150, cat="A", contrib=100.0, name="Альфа"), prod(2, price=700, cat="B", contrib=100.0, name="Бета")])} == {"1", "2"})
chk("відбір: порожній вхід / нічого прохідного → []", bs.home_pick([]) == [] and bs.home_pick(bad) == [])
chk("відбір: категорії без урахування регістру/пробілів («  БРЕЛОКИ ») і None-категорія не падають", bs.home_pick([prod(1, cat="  БРЕЛОКИ ")]) == [] and isinstance(bs.home_pick([dict(prod(2), category=None)]), list))
chk("відбір: stock=None не падає (трактується як без залишку)", bs.home_pick([dict(prod(3), stock=None)]) == [])
tie = [prod(1, cat="A", contrib=150.0, name="Однаковий внесок Альфа"), prod(2, cat="B", contrib=150.0, name="Однаковий внесок Бета"), prod(3, cat="C", contrib=150.0, name="Однаковий внесок Гамма")]
chk("відбір: однакові внески — порядок детермінований за id (тай-брейк) незалежно від входу", [p["id"] for p in bs.home_pick(tie)] == [p["id"] for p in bs.home_pick(list(reversed(tie)))])

# ── додаткові відбір-інваріанти (аудит #678, раунд 2)
big2 = [prod(2000 + i, cat="Одна", price=300, contrib=150.0 + i, name=f"Ключ{chr(1072 + i)}Один{i}Тест") for i in range(30)] +        [prod(3000 + i, cat="Друга", price=300, contrib=150.0 + i, name=f"Інший{chr(1072 + i)}Два{i}Тест") for i in range(30)]
chk("відбір: дві категорії по 30 позицій → рівно 4 (≤2 з кожної), а не 10", len(bs.home_pick(big2)) == 4)
chk("відбір: категорія None поряд з рядковою при однаковому внеску НЕ падає", len(bs.home_pick([prod(1, cat="A", contrib=150.0, name="Альфа"), dict(prod(2, cat=None, contrib=150.0, name="Бета"))])) == 2)
seq = [p["id"] for p in bs.home_pick(allp)]
chk("відбір: порядок у каруселі ПЕРЕМІШАНИЙ (не збігається з порядком round-robin за внеском)", seq != [p["id"] for p in sorted(bs.home_pick(allp), key=lambda p: -p["contribution"])])
chk("ключ: дужки відкидаються, апостроф нормалізується (мʼяка/м'яка), 1 слово ≠ 2 слова: «Рюкзак» ≠ «Рюкзак шкільний»? (2 значущі слова беруться)",
    k("Рюкзак (рожевий)") == k("Рюкзак (синій)") and k("М'яка іграшка Зайчик") == k("Мʼяка іграшка Зайчик") and k("Рюкзак шкільний") != k("Рюкзак туристичний"))
chk("ключ: загальні англійські слова (Super/Mini/Play) НЕ бренд: «Super Mario» ≠ «Super Wings»", k("Super Mario фігурка") != k("Super Wings літак"))

# ── weapon_policy ≡ generate_google_feed.is_weapon_replica
try:
    import generate_google_feed as gg
    import weapon_policy as wp
    corpus = ["Помпова рушниця з патронами", "Автомат іграшковий з кульками 6 мм", "Водяний пістолет великий", "Піна для ванни", "Пістолет страйкбол металеві кульки", "Лялька Барбі",
              "Револьвер пістони", "Карабін брелок", "Мильні бульбашки пістолет", "Автомат на батарейках", "Гвинтівка 6мм кульки", "Снайпер набір поролон"]
    ok = all(gg.is_weapon_replica("1", t) == wp.is_weapon_replica("1", t) for t in corpus) and all(gg.is_weapon_replica(i, "x") == wp.is_weapon_replica(i, "x") for i in ("276019", "1"))
    chk("weapon_policy.is_weapon_replica ≡ generate_google_feed.is_weapon_replica (корпус назв + ID)", ok)
    chk("weapon_policy: водяний пістолет із кульками НЕ репліка; числовий ID у списку розпізнається (str(id))", not wp.is_weapon_replica("1", "Водяний пістолет з кульками") and wp.is_weapon_replica(276019, "x"))
    chk("списки слів/ID збігаються (дрейф копії ловиться тут)", set(gg._WEAPON_REPLICA_IDS) == set(wp.WEAPON_REPLICA_IDS) and tuple(gg._WEAPON_WORDS) == tuple(wp.WEAPON_WORDS)
        and tuple(gg._WATER_WORDS) == tuple(wp.WATER_WORDS) and tuple(gg._PROJECTILE_WORDS) == tuple(wp.PROJECTILE_WORDS))
except Exception as e:  # noqa: BLE001
    chk(f"weapon_policy ≡ feed (імпорт generate_google_feed: {type(e).__name__}: {e})", False)

# ── розмітка головної
cats = {}
for p in allp:
    cats.setdefault(p["category"], []).append(p)
for c in cats:
    cats[c].sort(key=lambda p: -(p["contribution"] or -1e9))
cat_list = sorted(cats)
cat_slug = {c: bs.slugify(c) for c in cat_list}
tmp = Path(tempfile.mkdtemp())
bs.OUT = str(tmp)
bs.write_home(allp, cats, cat_list, cat_slug)
h = (tmp / "index.html").read_text(encoding="utf-8")
chk("герой: компактний .hero2 (ніяких старих .hero/.fox, hero-note)", 'class="hero2"' in h and 'class="hero"' not in h and "hero-note" not in h)
chk("герой: h1 «Іграшки, що радують», кнопка «Нехай Плутус вибере» (#plutus-pick, пул у data-pool) + «Усі іграшки» → каталог (SMM 13.10); «Набори» і рядка про доставку/оплату в героєві нема", "<h1>Іграшки, що радують</h1>" in h and 'id="plutus-pick" href="catalog.html" data-pool="' in h and '>Нехай Плутус вибере</a><a class="hero-all" href="catalog.html">Усі іграшки</a>' in h and "Нова Пошта ·" not in h and 'href="nabory.html">Набори в подарунок' not in h)
chk("герой: підзаголовок ≤100 символів", len(re.search(r'<div class="txt"><h1>.*?</h1><p>(.*?)</p>', h).group(1)) <= 100)
chk("герой: підзаголовок «Не знаєте, що обрати? Нехай Плутус підкаже» (SMM 13.10), фрази про «маму спокійною» ніде на головній нема", re.search(r'<div class="txt"><h1>.*?</h1><p>(.*?)</p>', h).group(1) == "Не знаєте, що обрати? Нехай Плутус підкаже" and "спокійною" not in h)
chk("карусель: секція «Добірка Плутуса», #home-car з role=region і aria-label, 2 стрілки — <button type=button> з aria-label",
    'id="home-car" role="region" aria-label="Добірка Плутуса"' in h and "<h2>Добірка Плутуса</h2>" in h and h.count('<button class="arrow') == 2 and h.count('type="button" aria-label=') == 2)
h_main = re.sub(r'<template id="car-set-.*?</template>', '', h, flags=re.S)   # чипи (SMM 12.10): набори в <template> — не слайди основної каруселі
slides = re.findall(r'<a class="card slide" href="product-(\d+)\.html">', h_main)
chk("карусель: рівно 10 слайдів у порядку відбору", slides == ids)
imgs = re.findall(r'<a class="card slide".*?<img ([^>]*)>', h_main, re.S)
chk("карусель: перші 3 зображення без loading=lazy, решта — lazy (4–5 на широкому екрані переводить JS)", all("lazy" not in x for x in imgs[:3]) and all("lazy" in x for x in imgs[3:]))
chk("карусель: першому слайду fetchpriority=high (кандидат на LCP), іншим — ні", 'fetchpriority="high"' in imgs[0] and sum(1 for x in imgs if "fetchpriority" in x) == 1)
chk("«Новинок» нема, є «Більше іграшок»", "Новинки" not in h and "<h2>Більше іграшок</h2>" in h)
tail = h.split("<h2>Більше іграшок</h2>", 1)[1] if "<h2>Більше іграшок</h2>" in h else ""
more = re.findall(r'href="product-(\d+)\.html"', tail)
chk("«Більше іграшок»: без повторів із каруселлю", not (set(more) & set(ids)))
chk("в усій розмітці немає «Знижок»/«Хітів продажів» (немає старої ціни й власних продажів)", "Знижки" not in h and "Хіти продажів" not in h)
bs.write_home(bad, {c: [p for p in v if p in bad] for c, v in cats.items() if any(p in bad for p in v)}, [c for c in cat_list if any(p in bad for p in cats[c])], cat_slug)
h2 = (tmp / "index.html").read_text(encoding="utf-8")
chk("без підходящих товарів карусель НЕ рендериться (герой і категорії є)", 'id="home-car"' not in h2 and 'class="hero2"' in h2)
css = (HERE / "site" / "assets" / "styles.css").read_text(encoding="utf-8")
chk("CSS: .hero2, .car .track (scroll-snap)", ".hero2{" in css and "scroll-snap-type:x mandatory" in css)
chk("CSS: мобільна розкладка героя до 700 px (абзац ховається, .act на всю ширину), стрілки/слайди — до 600 px",
    "@media(max-width:700px){" in css and ".hero2 p{display:none}" in css and ".hero2 .act{flex:0 0 100%" in css and ".car .arrow{display:none}" in css and ".car .slide{flex-basis:44%}" in css)
chk("CSS: рядок доставки в героєві прибрано (нема .hero2 .chip)", ".hero2 .chip" not in css)
chk("CSS: стрілки без JS приховано (.car:not(.js) .arrow{display:none})", ".car:not(.js) .arrow{display:none}" in css)
chk("CSS: без автопрокрутки/анімації каруселі", not re.search(r"\.car[^{}]*\{[^}]*animation", css))

# ── поведінка app.js
node = shutil.which("node")
if not node:
    chk("node доступний (без нього поведінку не перевірити)", False)
else:
    harness = r'''
const fs=require("fs"), vm=require("vm");
const code=fs.readFileSync(process.argv[2],"utf8"); const scenario=process.argv[3];
function store(){ const m={}; return {getItem:k=>(k in m?m[k]:null), setItem:(k,v)=>{m[k]=String(v)}, removeItem:k=>{delete m[k]}}; }
function mkEl(o){ const h={}; const cls=new Set(); const e=Object.assign({h, style:{}, classList:{add(c){cls.add(c)},remove(c){cls.delete(c)},toggle(){},contains(c){return cls.has(c)}}, textContent:"", disabled:false, scrollLeft:0, clientWidth:600, scrollWidth:2400,
  addEventListener(t,f){(h[t]=h[t]||[]).push(f)}, getAttribute(k){return (o.attrs||{})[k]||null}, querySelector(){return null}, querySelectorAll(){return []}, closest(){return null}, scrollByCalls:[], cls}, o);
  e.scrollBy=function(a){ e.scrollByCalls.push(a); }; return e; }
(async()=>{
  const gt=[], fb=[]; const doc_h={}, win_h={};
  const NC=(scenario==="wide"||scenario==="narrow6")?6:3; const cards=Array.from({length:NC},(_,k)=>k+1).map(i=>{ const nm=mkEl({textContent:"Товар "+i}), pr=mkEl({textContent:(100*i)+" ₴"});
    const im=mkEl({}); im.loading="lazy"; const a=mkEl({attrs:{href:"product-"+(10+i)+".html"}}); a.img=im; a.querySelector=s=>s===".nm"?nm:(s===".pr"?pr:(s==="img"?im:null)); a.closest=s=>s==="a.card"?a:null; return a; });
  const left=mkEl({}), right=mkEl({}), track=mkEl({});
  const car=mkEl({attrs: scenario==="nodatalist"?{}:{"data-list":"Добірка Плутуса"}});
  car.querySelector=s=>s===".track"?track:(s===".arrow.l"?left:(s===".arrow.r"?right:null));
  car.querySelectorAll=s=>s==="a.card"?cards:[];
  const ready=[]; let ioCb=null;
  const doc={addEventListener(t,f){ if(t==="DOMContentLoaded") ready.push(f); else (doc_h[t]=doc_h[t]||[]).push(f); },
    getElementById(id){ return (id==="home-car" && scenario!=="nocar")?car:null; }, querySelector(){return null}, querySelectorAll(){return []}, body:{addEventListener(){}}, createElement(){return {style:{}}}};
  const ctx={document:doc, localStorage:store(), sessionStorage:store(), console, fetch:()=>Promise.reject(), navigator:{}, location:{pathname:"/", href:"/"}, setTimeout, clearTimeout, setInterval:function(){ return 1; },
    addEventListener(t,f){ (win_h[t]=win_h[t]||[]).push(f); }};
  ctx.window=ctx; ctx.self=ctx;
  ctx.matchMedia=function(q){ return {matches: (scenario==="reduce" && /reduce/.test(q)) || (scenario==="wide" && /min-width: 700px/.test(q))}; };
  if(scenario!=="noid"){ ctx.PT_ANALYTICS={ga4:"G-T", fb:"1"}; ctx.gtag=function(){gt.push(Array.from(arguments))}; ctx.fbq=function(){fb.push(Array.from(arguments))}; }
  if(scenario==="noid_gtag_only"){ ctx.PT_ANALYTICS=undefined; ctx.gtag=function(){gt.push(Array.from(arguments))}; ctx.fbq=function(){fb.push(Array.from(arguments))}; }
  if(scenario==="io"){ ctx.IntersectionObserver=function(cb){ ioCb=cb; this.observe=function(){}; this.disconnect=function(){}; }; }
  vm.createContext(ctx); vm.runInContext(code,ctx);
  let threw=null; try{ ready.forEach(f=>f()); }catch(e){ threw=String(e); }
  const out={threw};
  if(scenario==="io"){
    out.before=gt.filter(e=>e[1]==="view_item_list").length;
    ioCb([{isIntersecting:false}]); out.afterNo=gt.filter(e=>e[1]==="view_item_list").length;
    ioCb([{isIntersecting:true}]); ioCb([{isIntersecting:true}]); out.afterYes=gt.filter(e=>e[1]==="view_item_list").length;
  } else { out.vil=gt.filter(e=>e[1]==="view_item_list"); }
  if(scenario==="nocar"){
    const mkA=(href)=>{ const a=mkEl({attrs:{href}}); a.closest=s=>s==="a[href]"?a:null; return a; };
    (doc_h.click||[]).forEach(f=>f({target:mkA("tel:+380730150815")}));
    out.phone=gt.filter(e=>e[1]==="phone_click").length; console.log(JSON.stringify(out)); return;
  }
  out.jsClass=car.cls.has("js");
  out.imgLoading=cards.map(c=>c.img.loading);
  // sync(): стан спокою (scrollLeft 2.4) → ліва disabled; після прокрутки — активна; край справа — права disabled
  const scrollH=track.h.scroll||[];
  track.scrollLeft=2.4; scrollH.forEach(f=>f()); out.leftRest=left.disabled; out.rightRest=right.disabled;
  track.scrollLeft=300; scrollH.forEach(f=>f()); out.leftMid=left.disabled; out.rightMid=right.disabled;
  track.scrollLeft=2400-600-3; scrollH.forEach(f=>f()); out.rightEnd=right.disabled;
  track.scrollLeft=300; track.clientWidth=300; (win_h.resize||[]).forEach(f=>f()); out.resizeSync=(win_h.resize||[]).length>0 && (win_h.orientationchange||[]).length>0;
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
    chk("клас .js ставиться на карусель (стрілки з'являються лише з працюючим JS)", a is not None and a["jsClass"] is True)
    chk("стрілки: у стані спокою (scrollLeft≈2,4) ліва disabled, права активна", a is not None and a["leftRest"] is True and a["rightRest"] is False)
    chk("стрілки: посередині обидві активні; на правому краї права disabled (допуск ±6 px)", a is not None and a["leftMid"] is False and a["rightMid"] is False and a["rightEnd"] is True)
    chk("resize і orientationchange перераховують стан стрілок", a is not None and a["resizeSync"] is True)
    chk("стрілки: › скролить вправо, ‹ вліво (smooth, ≥160 px)", a is not None and len(a["scroll"]) == 2 and a["scroll"][0]["left"] > 0 > a["scroll"][1]["left"] and a["scroll"][0]["behavior"] == "smooth")
    chk("select_item по кліку на 2-й слайд: item_id=12, index=1, список «Добірка Плутуса»", a is not None and len(a["select"]) == 1 and a["select"][0][2]["items"][0]["item_id"] == "12" and a["select"][0][2]["items"][0]["index"] == 1)
    chk("phone_click по tel: (+ Meta Contact); звичайне посилання подій не дає", a is not None and len(a["phone"]) == 1 and a["phone"][0][2]["link_url"].startswith("tel:") and any(x[:2] == ["track", "Contact"] for x in a["fb"]))
    chk("social_click: instagram і facebook (+ Meta trackCustom SocialClick); лише з .fsoc",
        a is not None and [e[2]["network"] for e in a["social"]] == ["instagram", "facebook"] and sum(1 for x in a["fb"] if x[:2] == ["trackCustom", "SocialClick"]) == 2)
    n6 = run("narrow6")
    chk("мобільний (matchMedia не wide): усі слайди лишаються lazy (4–5 НЕ переводяться в eager — економія ваги до першого свайпу)", n6 is not None and n6["imgLoading"] == ["lazy"] * 6)
    wd = run("wide")
    chk("широкий екран (≥700 px): JS переводить рівно слайди 4 і 5 в eager, решта lazy", wd is not None and wd["threw"] is None and wd["imgLoading"] == ["lazy", "lazy", "lazy", "eager", "eager", "lazy"])
    rd = run("reduce")
    chk("prefers-reduced-motion: reduce → скрол без плавності (behavior=auto)", rd is not None and all(c["behavior"] == "auto" for c in rd["scroll"]) and len(rd["scroll"]) == 2)
    dl = run("nodatalist")
    chk("без data-list береться назва за замовчуванням «Добірка Плутуса»", dl is not None and dl["vil"] and dl["vil"][0][2]["item_list_name"] == "Добірка Плутуса")
    io = run("io")
    chk("з IntersectionObserver: до появи на екрані подій нема; видимість → рівно ОДИН view_item_list (повторні спрацювання не дублюють)", io is not None and io["before"] == 0 and io["afterNo"] == 0 and io["afterYes"] == 1)
    nc = run("nocar")
    chk("сторінка БЕЗ каруселі (#home-car нема): жодного винятку, а initContactTracking далі працює (phone_click)", nc is not None and nc["threw"] is None and nc["phone"] == 1)
    n = run("noid")
    chk("без PT_ANALYTICS (нема ID) жодна подія не шлеться і нічого не падає (стрілки працюють)", n is not None and n["total"] == 0 and len(n["scroll"]) == 2)
    g = run("noid_gtag_only")
    chk("gtag є, але PT_ANALYTICS нема (cfg.ga4 порожній) → подій нема", g is not None and g["total"] == 0 and g["fb"] == [])

print(f"\n{'❌ ПРОВАЛЕНО: ' + str(F) if F else '✅ головна (герой + карусель) — усі перевірки коректні.'}")
sys.exit(1 if F else 0)

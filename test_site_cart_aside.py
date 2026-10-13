#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_site_cart_aside.py — 12.10.2026, SMM: на десктопі права половина кошика була порожня → двоколонково: sticky-колонка (А «Ваше замовлення», Б довіра, В «Додайте до замовлення», Г Плутус).
Повний build() на синтетичному каталозі + node (vm) для app.js:
  • розмітка: .cart-grid > .cart-main (кошик, форма) + aside#cart-aside; на мобільному «Додайте до замовлення» всередині форми НАД кнопкою «Оформити»; Плутус статичний; довіра без нових обіцянок;
  • пул add-on у cart.html: ≤12, лише в наявності, ціна ≥ порогу, без «Уцінки»/«розпродажу», по одному з категорії, детерміновано, JSON не ламає HTML (</script>);
  • CSS: десктоп ≥960 — дві колонки, sticky, мобільні елементи сховано; мобільний — колонка нижче, десктопний підсумок і add-on сховано;
  • node: render — «Ваше замовлення» (мініатюри, кількість × ціна, товари/разом), 3 add-on БЕЗ тих, що вже в кошику; клік «+ Додати» → кошик оновлено, add_to_cart з item_list_name «cart_addon», add-on перерахувано; порожній кошик → колонка прихована;
    динамічний рядок про відправку (до/після 12:00 за Києвом) без нових обіцянок поза політикою («зазвичай»).
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


def item(pid, name, category, price=300.0, stock=5):
    return {"id": str(pid), "name": name, "vendor": "T", "price": price, "stock": stock, "category_name": category, "category_id": "10",
            "pictures": [f"https://toysi.ua/p/enl_{pid}.jpg"], "description": "Опис товару достатньої довжини для картки сайту, понад сорок символів.",
            "params": {}, "url": f"https://toysi.ua/{pid}"}


catalog = {}
pid = 1
for c in range(20):                                           # 20 категорій × 3 товари: 100…, ціни різні
    for j in range(3):
        catalog[str(pid)] = item(pid, f"Іграшка {c}-{j}", f"Кат{c:02d}", price=100 + c * 10 + j)
        pid += 1
catalog["900"] = item(900, "Уцінка. Браслет", "Уцінка", price=500)           # уцінка — не add-on
catalog["901"] = item(901, "Копійка", "Дешеві", price=20)                    # нижче порогу
catalog["902"] = item(902, 'Назва </script><b>x</b> "лапки"', "Небезпечна", price=450)   # екранування в JSON
out = Path(tempfile.mkdtemp())
bs.OUT = str(out)
bs.tp.fetch_toysi_catalog = lambda: dict(catalog)
bs.tp.assert_catalog_size_sane = lambda c, *a, **k: None
import site_photo_probe  # noqa: E402
site_photo_probe.run = lambda idx: None
with contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(io.StringIO()):
    bs.build()
cart = (out / "cart.html").read_text(encoding="utf-8")

chk("кошик: .cart-grid із .cart-main та aside#cart-aside", '<div class="cart-grid"><div class="cart-main">' in cart and '<aside class="cart-aside" id="cart-aside"' in cart)
form = re.search(r'<form id="checkout-form">(.*?)</form>', cart, re.S).group(1)
chk("мобільний: «Додайте до замовлення» (addon-m) ВСЕРЕДИНІ форми і РАНІШЕ кнопки «Оформити»", 'class="addon-box addon-m" data-addon' in form and form.index("addon-m") < form.index('id="checkout-submit"'))
aside = re.search(r'<aside class="cart-aside".*?</aside>', cart, re.S).group(0)
chk("колонка: «Ваше замовлення» (А), довіра (Б), add-on (В), Плутус (Г)", all(x in aside for x in ('id="aside-order"', 'class="aside-card trust-card"', 'addon-box addon-d', 'class="aside-card pack-card"')))
chk("довіра: фіскальний чек, повернення 14 днів (посилання на returns.html), оплата при отриманні, рядок про 12:00 — без нових обіцянок («зазвичай»)", "Фіскальний чек" in aside and 'href="returns.html"' in aside and "Повернення протягом 14 днів" in aside
    and "Оплата при отриманні" in aside and 'id="ship-today">🚚 Замовлення до 12:00 зазвичай відправляємо того ж дня' in aside and "знижк" not in re.sub(r'<div class="row discount-row" id="aside-discount-row".*?</div>', "", aside, flags=re.S).lower() and "безкоштовн" not in aside.lower())   # динамічний прихований рядок «Знижка» (UI промокоду) не рахується — це не обіцянка, а відповідь сервера
chk("Плутус: статичний ≈80 px із підписом «Плутус пакує вашу посилку»", 'class="mascot" src="assets/plutus_mascot_s.png" alt="Плутус" width="80" height="73"' in aside and "Плутус пакує вашу посилку" in aside)
chk("підсумок доставки в колонці — «орієнтовно 70–90 ₴ до 2 кг» І застереження (важчі посилки, комісія НП за накладений) — десктопний #cart-summary сховано, тож примітка має бути в колонці (аудит #691 F2)", "орієнтовно 70–90 ₴ до 2 кг" in aside and "для важчих посилок сума вища" in aside and "комісія за накладений платіж: 20 ₴ + 2% від суми замовлення" in aside and 'id="aside-delivery"' in aside)
chk("aside aria-label не вводить в оману на мобільному («Інформація про замовлення»)", 'aria-label="Інформація про замовлення"' in aside)

m = re.search(r'<script type="application/json" id="cart-addons">(.*?)</script>', cart, re.S)
pool = json.loads(m.group(1)) if m else None
chk("пул add-on: JSON у cart.html розбирається, ≤12, поля id/name/price/photo", pool is not None and 0 < len(pool) <= 12 and all(set(x) == {"id", "name", "price", "photo"} for x in pool))
byid = {x["id"]: x for x in pool or []}
cats = [catalog[x["id"]]["category_name"] for x in pool or []]
chk("пул: лише в наявності, ціна ≥ порогу, без «Уцінки» і «Копійки», по одному з категорії", all(catalog[i]["stock"] > 0 and catalog[i]["price"] >= bs.ADDON_MIN_PRICE for i in byid) and "900" not in byid and "901" not in byid and len(set(cats)) == len(cats))
chk("пул: у порядку спадання ціни (цінніші add-on першими), детерміновано (дві збірки однакові)", [x["price"] for x in pool] == sorted((x["price"] for x in pool), reverse=True))
chk("назва з </script> не ламає HTML: у JSON екрановано «<\/script>», сирого «</script><b>» у cart.html нема", "<\/script>" in cart and "</script><b>" not in cart)
out2 = Path(tempfile.mkdtemp())
bs.OUT = str(out2)
with contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(io.StringIO()):
    bs.build()
chk("детермінізм: повторна збірка дає той самий пул", re.search(r'id="cart-addons">(.*?)</script>', (out2 / "cart.html").read_text(encoding="utf-8"), re.S).group(1) == m.group(1))
chk("write_cart() без аргументу (старі виклики/тести) не падає, пул порожній", "[]</script>" in (lambda: (bs.write_cart(), (out2 / "cart.html").read_text(encoding="utf-8"))[1])())

css = (HERE / "site" / "assets" / "styles.css").read_text(encoding="utf-8")
chk("CSS: ≥960 px — дві колонки (1fr + 380px), sticky-колонка; десктопний підсумок і мобільний add-on сховано", re.search(r"@media \(min-width:960px\)\{[^@]*\.cart-grid\{display:grid;grid-template-columns:minmax\(0,1fr\) 380px", css) is not None
    and "position:sticky;top:84px" in css and re.search(r"\.addon-m,#cart-summary\{display:none !important\}", css) is not None)
chk("CSS: на мобільному «Ваше замовлення» і десктопний add-on за замовчуванням сховані (#aside-order,.addon-d{display:none})", "#aside-order,.addon-d{display:none}" in css)
chk("CSS: колонка вища за вікно прокручується всередині (max-height + overflow-y), список позицій обмежено — add-on і Плутус досяжні (аудит #691 F3)", re.search(r"\.cart-aside\{position:sticky;top:84px;max-height:calc\(100vh - 100px\);overflow-y:auto", css) is not None and "#aside-items{max-height:220px;overflow-y:auto}" in css)

node = shutil.which("node")
if not node:
    chk("node доступний (без нього поведінку не перевірити)", False)
else:
    harness = r'''
const fs=require("fs"), vm=require("vm");
const code=fs.readFileSync(process.argv[2],"utf8"); const scenario=process.argv[3]; const poolJson=fs.readFileSync(process.argv[4],"utf8");
function store(){ const m={}; return {getItem:k=>(k in m?m[k]:null), setItem:(k,v)=>{m[k]=String(v)}, removeItem:k=>{delete m[k]}, _m:m}; }
function el(id){ const h={}; return {id,value:"",textContent:"",_inner:"",style:{},classList:{add(){},remove(){},toggle(){},contains(){return false}},addEventListener(t,f){(h[t]=h[t]||[]).push(f)},h,
  set innerHTML(v){ this._inner=v; }, get innerHTML(){ return this._inner; }, querySelectorAll(){return []}, querySelector(){return null}, closest(){return null}, getAttribute(){return null}, setAttribute(){}, focus(){}}; }
(async()=>{
  const ls=store(), ss=store(); const gt=[];
  const cartItems={"1":{id:"1",name:"Картина",price:468,photo:"https://x/1.jpg",qty:2},"2":{id:"2",name:"Дрібничка <b>",price:150,photo:"https://x/2.jpg",qty:1}};
  if(scenario!=="empty") ls.setItem("pt_cart_v1", JSON.stringify(scenario==="inpool"?Object.assign({}, cartItems, {"__": null}) && cartItems : cartItems));
  const E={}; ["cart-body","cart-summary","checkout-form","cart-aside","aside-items","aside-goods","aside-total","ship-today","cart-badge","sum-goods","sum-delivery","sum-total","aside-delivery"].forEach(i=>E[i]=el(i));
  E["ship-today"].textContent="🚚 Замовлення до 12:00 зазвичай відправляємо того ж дня"; E["cart-addons"]=el("cart-addons"); E["cart-addons"].textContent=poolJson;
  const boxes=[el("m"), el("d")];
  const docH={}; const ready=[];
  const doc={addEventListener(t,f){ if(t==="DOMContentLoaded") ready.push(f); }, getElementById(id){return E[id]||null}, querySelector(){return null}, querySelectorAll(sel){ return sel==="[data-addon]"?boxes:[]; },
    body:{addEventListener(t,f){ (docH[t]=docH[t]||[]).push(f); },appendChild(){}}, createElement(){return {style:{},appendChild(){}}}};
  const HM={morning:[9,30], evening:[15,30], noon:[12,5], weekend:[9,30]}[scenario]||[9,30];
  const day = scenario==="weekend" ? 17 : 12;                 // 12.10.2026 — понеділок; 17.10.2026 — субота
  const FIXED=new Date(Date.UTC(2026,9,day,HM[0]-3,HM[1],0));      // Київ = UTC+3 у жовтні 2026
  class FD extends Date{ constructor(...a){ super(...(a.length?a:[FIXED.getTime()])); } static now(){ return FIXED.getTime(); } }
  const ctx={document:doc, localStorage:ls, sessionStorage:ss, console, fetch:()=>Promise.reject(), navigator:{}, location:{pathname:"/cart.html",href:"/cart.html"}, setTimeout, clearTimeout, setInterval:function(){return 1;}, addEventListener(){}, Date:FD, Intl};
  ctx.window=ctx; ctx.self=ctx; ctx.matchMedia=function(){ return {matches:false}; };
  ctx.PT_ANALYTICS={ga4:"G-T", fb:"1"}; ctx.gtag=function(){gt.push(Array.from(arguments))}; ctx.fbq=function(){};
  vm.createContext(ctx); vm.runInContext(code,ctx);
  let threw=null; try{ ready.forEach(f=>f()); }catch(e){ threw=String(e); }
  const out={threw, aside:E["cart-aside"].style.display, items:E["aside-items"].innerHTML, goods:E["aside-goods"].textContent, deliv:E["aside-delivery"].textContent, sumDeliv:E["sum-delivery"].textContent, total:E["aside-total"].textContent, ship:E["ship-today"].textContent,
    boxM:boxes[0].innerHTML, boxD:boxes[1].innerHTML};
  if(scenario==="click"){
    const btn={getAttribute:k=>k==="data-add"?JSON.stringify({id:"5",name:"Add",price:90,photo:"https://x/5.jpg"}):(k==="data-list"?"cart_addon":null)};
    const target={closest:s=>s==="[data-add]"?btn:null};
    (docH.click||[]).forEach(f=>f({target, preventDefault(){}}));
    out.cart=JSON.parse(ls.getItem("pt_cart_v1")); out.ev=gt.filter(e=>e[1]==="add_to_cart"); out.boxAfter=boxes[1].innerHTML; out.goodsAfter=E["aside-goods"].textContent; out.delivAfter=E["aside-delivery"].textContent;
  }
  console.log(JSON.stringify(out));
})();
'''
    tmp = Path(tempfile.mkdtemp())
    hf = tmp / "h.js"
    hf.write_text(harness, encoding="utf-8")
    pf = tmp / "pool.json"
    pool_test = [{"id": str(i), "name": f"Add{i}", "price": 100 + i, "photo": f"https://x/{i}.jpg"} for i in (1, 2, 3, 4, 5, 6)]     # 1 і 2 уже в кошику
    pool_test[3]["name"] = "М'яка іграшка \"ведмедик\" a' onmouseover='x'"          # апостроф/лапки/ін'єкція атрибута
    pf.write_text(json.dumps(pool_test, ensure_ascii=False), encoding="utf-8")
    app = str(HERE / "site" / "assets" / "app.js")

    def run(sc):
        r = subprocess.run([node, str(hf), app, sc, str(pf)], capture_output=True, text=True, encoding="utf-8", timeout=60)
        try:
            return json.loads(r.stdout.strip().splitlines()[-1])
        except Exception:
            print("   stdout:", r.stdout[-300:], "stderr:", r.stderr[:900])
            return None

    a = run("morning")
    chk("render: колонка видима; «Ваше замовлення» — мініатюри, «2 × 468 ₴», «1 × 150 ₴», екранована назва", a is not None and a["threw"] is None and a["aside"] == ""
        and "2 × 468 ₴" in a["items"] and "1 × 150 ₴" in a["items"] and "&lt;b&gt;" in a["items"] and "<b>" not in a["items"])
    chk("render: товари і «Разом за товари» = 468×2+150 = 1086 ₴", a is not None and a["goods"] == "1086 ₴" and a["total"] == "1086 ₴")
    chk("доставка в колонці рахується від суми: 1086 ₴ → 70+5,43+20+21,72 … 90+… = «орієнтовно 117–137 ₴» (аудит #699 F1: renderAside писав aside-delivery, тест цього не бачив)", a is not None and a["deliv"] == "орієнтовно 117–137 ₴")
    adds = re.findall(r'data-add=', a["boxD"]) if a else []
    chk("add-on: РІВНО 3 позиції, жодної з уже наявних у кошику (1, 2), порядок пулу (3, 4, 5); в обох контейнерах (десктоп і мобільний)", a is not None and len(adds) == 3
        and all(x in a["boxD"] for x in ("Add3", "ведмедик", "Add5")) and "Add1" not in a["boxD"] and "Add2" not in a["boxD"] and a["boxM"] == a["boxD"])
    chk("add-on: кнопка «+ Додати» з data-list=cart_addon та JSON товару", a is not None and 'data-list="cart_addon"' in a["boxD"] and "+ Додати" in a["boxD"])
    STATIC = "🚚 Замовлення до 12:00 зазвичай відправляємо того ж дня"
    chk("будній день до 12:00 за Києвом: «Замовте до 12:00 — зазвичай відправимо сьогодні»", a is not None and a["ship"] == "🚚 Замовте до 12:00 — зазвичай відправимо сьогодні")
    ev = run("evening")
    chk("після 12:00: лишається СТАТИЧНИЙ текст політики (нової обіцянки «наступного робочого дня» немає — аудит #691)", ev is not None and ev["ship"] == "🚚 Замовлення до 12:00 зазвичай відправляємо того ж дня" and "наступного" not in ev["ship"])
    nn = run("noon")
    chk("межа: 12:05 вже НЕ «до 12:00» → статичний текст (мутація hh<=12 червоніє)", nn is not None and nn["ship"] == "🚚 Замовлення до 12:00 зазвичай відправляємо того ж дня")
    we = run("weekend")
    chk("субота 9:30 за Києвом → статичний текст (не обіцяємо «сьогодні» у вихідні)", we is not None and we["ship"] == "🚚 Замовлення до 12:00 зазвичай відправляємо того ж дня")
    chk("апостроф у назві add-on екранується (&#39;), сирого «М'яка» в атрибуті нема (аудит #691 F1: ламало кнопку й давало ін'єкцію)", a is not None and "&#39;" in a["boxD"] and "М'яка" not in a["boxD"])
    chk("кнопка «+ Додати» має aria-label з назвою товару", a is not None and 'aria-label="Додати до замовлення: Add3"' in a["boxD"])
    ck = run("click")
    chk("клік «+ Додати»: доставка в колонці перерахована (1176 ₴ → «орієнтовно 119–139 ₴»)", ck is not None and ck["delivAfter"] == "орієнтовно 119–139 ₴")
    chk("клік «+ Додати»: товар у кошику (qty 1), рівно ОДИН add_to_cart з item_list_name «cart_addon», підсумок оновлено (1086+90=1176), доданий зникає з add-on",
        ck is not None and ck["cart"]["5"]["qty"] == 1 and len(ck["ev"]) == 1 and ck["ev"][0][2]["items"][0]["item_list_name"] == "cart_addon" and ck["goodsAfter"] == "1176 ₴" and "Add5" not in ck["boxAfter"] and "Add6" in ck["boxAfter"])
    em = run("empty")
    chk("порожній кошик: права колонка прихована, add-on не показано", em is not None and em["aside"] == "none")

print()
if F:
    print(f"FAILED ({len(F)}):")
    for n in F:
        print("  -", n)
    sys.exit(1)
print("ALL OK")

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_site_purchase_event.py — 11.10.2026, перед тестовим замовленням власника: події `purchase` (GA4) / `Purchase` (Meta) наживо перевірити НЕ можна без справжнього
замовлення (тестового режиму немає), тому весь шлях перевіряється в node (vm зі стабами DOM/gtag/fbq/fetch/sessionStorage):
  A) cart.html: відправка форми COD → POST api/order → у sessionStorage лежать pt_last_order і pt_last_order_items, кошик очищено, перехід на thanks.html;
     prepaid → позиції НЕ зберігаються (purchase лише для COD, аудит #626);
  B) thanks.html: рівно ОДИН gtag('event','purchase', {transaction_id, currency:'UAH', value, items}) і ОДИН fbq('track','Purchase', …); повторне відкриття (reload) —
     нічого не шле вдруге (pt_purchase_sent);
  C) без window.PT_ANALYTICS (нема ID) жодна подія не шлеться і нічого не падає.
Без node тест падає ЯВНО."""
import json
import shutil
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


node = shutil.which("node")
if not node:
    chk("node доступний (без нього поведінку не перевірити)", False)
else:
    harness = r'''
const fs=require("fs"), vm=require("vm");
const code=fs.readFileSync(process.argv[2],"utf8");
const scenario=process.argv[3];
function store(){ const m={}; return {getItem:k=>(k in m?m[k]:null), setItem:(k,v)=>{m[k]=String(v)}, removeItem:k=>{delete m[k]}, _m:m}; }
function el(id){ const h={}; return {id,value:"",textContent:"",disabled:false,classList:{add(){},remove(){},toggle(){}},style:{},addEventListener(t,f){(h[t]=h[t]||[]).push(f)},h,
  querySelectorAll(){return []},closest(){return null},setAttribute(){},getAttribute(){return null}}; }
async function run(opts){
  const ls=opts.ls||store(), ss=opts.ss||store();
  const E={};
  const ready=[];
  const gt=[], fb=[];
  const loc={href:"cart.html"};
  const els=opts.elements||[];
  els.forEach(id=>{E[id]=el(id)});
  const doc={addEventListener(t,f){ if(t==="DOMContentLoaded") ready.push(f); }, getElementById(id){return E[id]||null}, querySelector(sel){
      if(sel==='input[name="payment"]:checked') return opts.payment?{value:opts.payment}:null; return null; },
    querySelectorAll(){return []}, body:{addEventListener(){},appendChild(){}}, createElement(){return {style:{},appendChild(){},submit(){}}}};
  const fetchStub=function(url, init){ return Promise.resolve({ok:true, json(){return Promise.resolve({order_id:"PT-TEST-1", liqpay:opts.liqpay||null})}}); };
  const ctx={document:doc,localStorage:ls,sessionStorage:ss,console,fetch:fetchStub,navigator:{},location:loc,setTimeout,clearTimeout};
  const win=ctx; ctx.window=ctx; ctx.self=ctx;      // як у браузері: window === глобальний об'єкт (app.js звертається до PT без window.)
  if(opts.analytics!==false){ ctx.PT_ANALYTICS={ga4:"G-TEST", fb:"123"}; ctx.gtag=function(){gt.push(Array.from(arguments))}; ctx.fbq=function(){fb.push(Array.from(arguments))}; }
  vm.createContext(ctx); vm.runInContext(code,ctx);
  ready.forEach(f=>f());
  return {E, ls, ss, gt, fb, loc, win};
}
(async()=>{
  const cartItem={"235193":{id:"235193",name:"Картина за номерами",price:468,photo:"x",qty:2}};
  if(scenario==="submit_cod"){
    const ls=store(); ls.setItem("pt_cart_v1", JSON.stringify(cartItem));
    const r=await run({ls, elements:["checkout-form","checkout-msg","checkout-submit","f-name","f-phone","f-email","f-city","f-warehouse","cart-badge"], payment:"cod"});
    r.E["f-name"].value="Іван Петренко"; r.E["f-phone"].value="0501234567"; const form=r.E["checkout-form"]; (form.h.submit||[]).forEach(f=>f({preventDefault(){}}));
    await new Promise(x=>setTimeout(x,50));
    console.log(JSON.stringify({loc:r.loc.href, order:r.ss.getItem("pt_last_order"), items:JSON.parse(r.ss.getItem("pt_last_order_items")||"null"), cart:ls.getItem("pt_cart_v1"), tg_ss:r.ss.getItem("pt_tg_opt")}));
  } else if(scenario==="submit_cod_tgoff"){
    const ls=store(); ls.setItem("pt_cart_v1", JSON.stringify(cartItem));
    const r=await run({ls, elements:["checkout-form","checkout-msg","checkout-submit","f-name","f-phone","f-email","f-city","f-warehouse","cart-badge","f-tg"], payment:"cod"});
    r.E["f-tg"].checked=false;
    r.E["f-name"].value="Іван Петренко"; r.E["f-phone"].value="0501234567"; const form=r.E["checkout-form"]; (form.h.submit||[]).forEach(f=>f({preventDefault(){}}));
    await new Promise(x=>setTimeout(x,50));
    console.log(JSON.stringify({loc:r.loc.href, tg_ss:r.ss.getItem("pt_tg_opt"), tg_ls:r.ls.getItem("pt_tg_opt")}));
  } else if(scenario==="submit_cod_tgon"){
    const ls=store(); ls.setItem("pt_cart_v1", JSON.stringify(cartItem));
    const r=await run({ls, elements:["checkout-form","checkout-msg","checkout-submit","f-name","f-phone","f-email","f-city","f-warehouse","cart-badge","f-tg"], payment:"cod"});
    r.E["f-tg"].checked=true;
    r.E["f-name"].value="Іван Петренко"; r.E["f-phone"].value="0501234567"; const form=r.E["checkout-form"]; (form.h.submit||[]).forEach(f=>f({preventDefault(){}}));
    await new Promise(x=>setTimeout(x,50));
    console.log(JSON.stringify({loc:r.loc.href, tg_ss:r.ss.getItem("pt_tg_opt"), tg_ls:r.ls.getItem("pt_tg_opt")}));
  } else if(scenario==="submit_prepaid"){
    const ls=store(); ls.setItem("pt_cart_v1", JSON.stringify(cartItem));
    const ss0=store(); ss0.setItem("pt_last_order_items", JSON.stringify([{id:"1",name:"Старе COD-замовлення",price:100,qty:1}]));   // у тій самій вкладці раніше було COD
    const r=await run({ls, ss:ss0, elements:["checkout-form","checkout-msg","checkout-submit","f-name","f-phone","f-email","f-city","f-warehouse","cart-badge"], payment:"prepaid", liqpay:{data:"d",signature:"s",action_url:"https://www.liqpay.ua/api/3/checkout"}});
    r.E["f-name"].value="Іван Петренко"; r.E["f-phone"].value="0501234567"; const form=r.E["checkout-form"]; (form.h.submit||[]).forEach(f=>f({preventDefault(){}}));
    await new Promise(x=>setTimeout(x,50));
    console.log(JSON.stringify({order:r.ss.getItem("pt_last_order"), items:r.ss.getItem("pt_last_order_items")}));
  } else if(scenario==="thanks" || scenario==="thanks_noid"){
    const ss=store(); ss.setItem("pt_last_order","PT-TEST-1");
    ss.setItem("pt_last_order_items", JSON.stringify([{id:"235193",name:"Картина за номерами",price:468,qty:2},{id:"7",name:"Дрібничка",price:50,qty:1}]));
    const o=scenario==="thanks"?{ss, elements:["thanks-oid"]}:{ss, elements:["thanks-oid"], analytics:false};
    const r1=await run(o);
    const r2=await run({ss, elements:["thanks-oid"], ls:r1.ls});     // «reload»: той самий sessionStorage
    console.log(JSON.stringify({gt1:r1.gt, fb1:r1.fb, gt2:r2.gt, fb2:r2.fb, oid:r1.E["thanks-oid"].textContent, sent:ss.getItem("pt_purchase_sent")}));
  }
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

    a = run("submit_cod")
    chk("COD: після відповіді сервера → перехід на thanks.html", a is not None and a["loc"] == "thanks.html")
    chk("COD: у sessionStorage pt_last_order = PT-TEST-1", a is not None and a["order"] == "PT-TEST-1")
    chk("COD: позиції замовлення збережено для purchase (id, name, price, qty)", a is not None and a["items"] == [{"id": "235193", "name": "Картина за номерами", "price": 468, "qty": 2}])
    chk("COD: кошик очищено", a is not None and json.loads(a["cart"] or "{}") == {})
    g0 = run("submit_cod_tgoff")
    chk("Telegram-галочка ВИМКНЕНА → pt_tg_opt=0 у sessionStorage і localStorage, перехід на thanks.html не зламано", g0 is not None and g0["tg_ss"] == "0" and g0["tg_ls"] == "0" and g0["loc"] == "thanks.html")
    g1 = run("submit_cod_tgon")
    chk("Telegram-галочка УВІМКНЕНА → pt_tg_opt=1", g1 is not None and g1["tg_ss"] == "1" and g1["tg_ls"] == "1")
    chk("COD без елемента галочки (старі сторінки) → pt_tg_opt=1 (за замовчуванням увімкнено)", a is not None and a.get("tg_ss") == "1")
    p = run("submit_prepaid")
    chk("prepaid: СТАРІ позиції попереднього COD-замовлення у вкладці стерто (інакше purchase піде за неоплаченим prepaid)", p is not None and p["order"] == "PT-TEST-1" and p["items"] is None)
    t = run("thanks")
    gt = [e for e in (t or {}).get("gt1", []) if e[:2] == ["event", "purchase"]]
    chk("thanks: рівно ОДИН gtag('event','purchase')", t is not None and len(gt) == 1)
    if gt:
        prm = gt[0][2]
        chk("GA4 purchase: transaction_id = номер замовлення, currency UAH, value = 468×2+50 = 986", prm.get("transaction_id") == "PT-TEST-1" and prm.get("currency") == "UAH" and prm.get("value") == 986)
        chk("GA4 purchase: items із item_id/item_name/price/quantity", prm.get("items") == [{"item_id": "235193", "item_name": "Картина за номерами", "price": 468, "quantity": 2},
                                                                                 {"item_id": "7", "item_name": "Дрібничка", "price": 50, "quantity": 1}])
    fbp = [e for e in (t or {}).get("fb1", []) if e[:2] == ["track", "Purchase"]]
    chk("Meta: рівно ОДИН fbq('track','Purchase') зі значенням 986 UAH і content_ids", len(fbp) == 1 and fbp[0][2].get("value") == 986 and fbp[0][2].get("currency") == "UAH"
        and fbp[0][2].get("content_ids") == ["235193", "7"] and fbp[0][2].get("num_items") == 3)
    chk("thanks: номер замовлення показано на сторінці", t is not None and t["oid"] == "PT-TEST-1")
    chk("thanks: повторне відкриття (reload) НЕ шле purchase вдруге (pt_purchase_sent)", t is not None and not [e for e in t["gt2"] if e[:2] == ["event", "purchase"]]
        and not [e for e in t["fb2"] if e[:2] == ["track", "Purchase"]] and t["sent"] == "PT-TEST-1")
    n = run("thanks_noid")
    chk("без PT_ANALYTICS (нема ID) нічого не шлеться і нічого не падає", n is not None and n["gt1"] == [] and n["fb1"] == [])

print()
if F:
    print(f"FAILED ({len(F)}):")
    for n in F:
        print("  -", n)
    sys.exit(1)
print("ALL OK")

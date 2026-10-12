#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_site_checkout_address.py — 12.10.2026, «усі варіанти заповнення адреси» (запит власника): поведінка форми оформлення в node (vm, КЕРОВАНИЙ fetch довідника НП).
Захист: місто, вписане руками, коли довідник НП показав міста / ще не відповів, не йде на сервер без CityRef/області (посилка могла б поїхати в однойменне село: Бородянський 906224962,
«Південне»→«Южное» EVA 8-081747967). Виходи: довідник мертвий / мовчить >6 с / повернув порожнє або не-масив / кнопка «Мого міста немає в списку».
Гонки (аудит #682): reset ref — НЕГАЙНО на input (не в debounce); запізнілі й невпорядковані відповіді ігноруються (citySeq); відповідь після вибору міста не перемальовує й не блокує;
клік повз прибрав випадайку → при блокованому submit список повертається; submit у вікні debounce — блок (pending), не fail-open.
Без node тест падає ЯВНО."""
import json
import re
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
function el(id){ const h={}; const e={id,value:"",textContent:"",disabled:false,placeholder:"",classList:{add(){},remove(){},toggle(){}},style:{},
  addEventListener(t,f){(h[t]=h[t]||[]).push(f)},h, _inner:"", focus(){ e._focused=true; },
  querySelectorAll(sel){ if(sel!==".opt") return []; const idx=[...e._inner.matchAll(/data-i="(-?\d+)"/g)].map(m=>m[1]); const out=[];
    idx.forEach(v=>{ const hh={}; out.push({getAttribute:()=>v, addEventListener(t,f){hh[t]=f}, _h:hh}); }); e._opts=out; return out; },
  closest(){return null},setAttribute(){},getAttribute(){return null}};
  Object.defineProperty(e,"innerHTML",{get(){return e._inner}, set(v){e._inner=v; e._opts=null;}}); return e; }
const CITIES=[{name:"Іванівка",area:"Харківська",ref:"aaaaaaaa-1111-2222-3333-444444444444"},{name:"Іванівка (Чугуївський р-н)",area:"Харківська",ref:"bbbbbbbb-1111-2222-3333-444444444444"}];
async function run(mode){
  const ls=store(), ss=store(); ls.setItem("pt_cart_v1", JSON.stringify({"235193":{id:"235193",name:"Картина",price:468,photo:"x",qty:1}}));
  const E={}, ready=[], posts=[], pend=[], docH={}, timers=[], timerMs=[];
  ["checkout-form","checkout-msg","checkout-submit","f-name","f-phone","f-email","f-city","f-warehouse","ac-city","ac-warehouse","cart-badge"].forEach(id=>{E[id]=el(id)});
  const doc={addEventListener(t,f){ if(t==="DOMContentLoaded") ready.push(f); else (docH[t]=docH[t]||[]).push(f); }, getElementById(id){return E[id]||null}, querySelector(sel){
      if(sel==='input[name="payment"]:checked') return {value:"cod"}; return null; },
    querySelectorAll(){return []}, body:{addEventListener(){},appendChild(){}}, createElement(){return {style:{},appendChild(){},submit(){}}}};
  const fetchStub=function(url, init){
    if(String(url).indexOf("api/np/city")===0){
      const q=decodeURIComponent(String(url).split("q=")[1]);
      if(mode==="dead") return Promise.reject(new Error("net"));
      if(mode==="empty") return Promise.resolve({ok:true, json(){return Promise.resolve({cities:[]})}});
      if(mode==="nonarray") return Promise.resolve({ok:true, json(){return Promise.resolve({cities:"abc"})}});
      if(mode==="list") return Promise.resolve({ok:true, json(){return Promise.resolve({cities:CITIES})}});
      return new Promise((res,rej)=>{ pend.push({q, res:(c)=>res({ok:true, json(){return Promise.resolve({cities:c})}}), rej}); });     // controlled
    }
    if(String(url).indexOf("api/order")===0){ posts.push(JSON.parse(init.body)); return Promise.resolve({ok:true, json(){return Promise.resolve({order_id:"PT-TEST-1", liqpay:null})}}); }
    return Promise.resolve({ok:true, json(){return Promise.resolve({warehouses:[]})}});
  };
  // таймаут pending (6 с) не чекаємо реально: довгі таймери збираємо й запускаємо вручну
  const st=function(f,ms){ if(ms>=5000){ timers.push(f); timerMs.push(ms); return 0; } return setTimeout(f,ms); };
  const ctx={document:doc,localStorage:ls,sessionStorage:ss,console,fetch:fetchStub,navigator:{},location:{href:"cart.html"},setTimeout:st,clearTimeout};
  ctx.window=ctx; ctx.self=ctx;
  vm.createContext(ctx); vm.runInContext(code,ctx);
  ready.forEach(f=>f());
  return {E, posts, pend, docH, timers, timerMs};
}
const sleep=ms=>new Promise(x=>setTimeout(x,ms));
function fire(el,t,ev){ (el.h[t]||[]).forEach(f=>f(ev||{})); }
async function typeCity(r, text, wait){ r.E["f-city"].value=text; fire(r.E["f-city"],"input"); await sleep(wait===undefined?320:wait); }
function fill(r, wh){ r.E["f-name"].value="  Іван Петренко "; r.E["f-phone"].value="0501234567"; r.E["f-warehouse"].value=wh||"відділення 1"; }
async function submit(r){ fire(r.E["checkout-form"],"submit",{preventDefault(){}}); await sleep(60); }
function opts(r){ return r.E["ac-city"]._opts||r.E["ac-city"].querySelectorAll(".opt"); }
function pick(r,i){ opts(r)[i]._h.mousedown({preventDefault(){}}); }
const state=r=>({posts:r.posts.length, msg:r.E["checkout-msg"].textContent, list:r.E["ac-city"].innerHTML.length>0, btn:r.E["checkout-submit"].disabled});
(async()=>{
  let r, o;
  if(scenario==="listed_not_picked"){
    r=await run("list"); await typeCity(r,"Іванівка"); fill(r); await submit(r); o=state(r); o.focus=!!r.E["f-city"]._focused; o.hasExtra=r.E["ac-city"].innerHTML.indexOf("Мого міста немає")>=0;
  } else if(scenario==="click_away"){
    r=await run("list"); await typeCity(r,"Іванівка"); const before=r.E["ac-city"].innerHTML.length>0;
    (r.docH.click||[]).forEach(f=>f({target:{closest(){return null}}}));       // клік повз поле міста
    const cleared=r.E["ac-city"].innerHTML.length===0;
    fill(r); await submit(r); o=state(r); o.before=before; o.cleared=cleared;
  } else if(scenario==="picked"){
    r=await run("list"); await typeCity(r,"Іванівка"); pick(r,0); fill(r,"Відділення №1"); await submit(r); o={posts:r.posts};
  } else if(scenario==="dead"){
    r=await run("dead"); await typeCity(r,"Іванівка"); fill(r); await submit(r); o={posts:r.posts};
  } else if(scenario==="empty"){
    r=await run("empty"); await typeCity(r,"Ксямлвпр"); fill(r); await submit(r); o={posts:r.posts};
  } else if(scenario==="nonarray"){
    r=await run("nonarray"); await typeCity(r,"Іванівка"); fill(r); await submit(r); o={posts:r.posts};
  } else if(scenario==="picked_then_edited_instantly"){
    r=await run("list"); await typeCity(r,"Іванівка"); pick(r,1); fill(r);
    r.E["f-city"].value="Тернопіль"; fire(r.E["f-city"],"input");         // БЕЗ очікування debounce: ref іншого міста має скинутись одразу
    await submit(r); o={posts:r.posts.length, msg:r.E["checkout-msg"].textContent};
  } else if(scenario==="submit_in_debounce"){
    r=await run("list"); r.E["f-city"].value="Іванівка"; fire(r.E["f-city"],"input"); fill(r); await submit(r);       // submit раніше за відповідь довідника
    o={posts:r.posts.length, msg:r.E["checkout-msg"].textContent};
  } else if(scenario==="none_button"){
    r=await run("list"); await typeCity(r,"Іванівка"); const n=opts(r).length; pick(r,n-1); fill(r); await submit(r);       // остання опція = «Мого міста немає в списку»
    o={n, posts:r.posts};
  } else if(scenario==="pending_timeout"){
    r=await run("controlled"); await typeCity(r,"Іванівка"); fill(r); await submit(r); const blocked=r.posts.length===0;
    r.timers.forEach(f=>f());               // довідник мовчить >6 с → вважаємо недоступним
    await submit(r); o={blocked, posts:r.posts, ms:r.timerMs[r.timerMs.length-1]};
  } else if(scenario==="out_of_order"){
    r=await run("controlled"); await typeCity(r,"Кс"); await typeCity(r,"Ксямлвпр");
    r.pend[1].res([]); await sleep(20);              // нова відповідь (порожня) першою
    r.pend[0].res(CITIES); await sleep(20);           // запізніла стара — має бути проігнорована
    fill(r); await submit(r); o={posts:r.posts.length, list:r.E["ac-city"].innerHTML.length>0};
  } else if(scenario==="late_after_pick"){
    r=await run("controlled"); await typeCity(r,"Іва"); r.pend[0].res(CITIES); await sleep(20);
    r.E["f-city"].value="Іван"; fire(r.E["f-city"],"input"); await sleep(320);       // новий ввід → lookup #2 у польоті; стара випадайка ще в DOM
    pick(r,0);                                                                          // тап по старому списку: ref обрано
    r.pend[1].res(CITIES); await sleep(20);                                             // запізніла відповідь #2 НЕ має ні блокувати, ні перемальовувати
    fill(r,"Відділення №1"); await submit(r); o={posts:r.posts, list:r.E["ac-city"].innerHTML.length>0};
  } else if(scenario==="list_then_shorten"){
    r=await run("list"); await typeCity(r,"Іванівка"); await typeCity(r,"І"); fill(r); await submit(r); o={posts:r.posts.length};       // список був, текст скоротили до 1 символа — залиплого блоку нема
  } else if(scenario==="one_char"){
    r=await run("list"); await typeCity(r,"І"); fill(r); await submit(r); o={posts:r.posts.length};
  } else if(scenario==="nothing_typed"){
    r=await run("list"); fill(r); await submit(r); o={posts:r.posts.length};
  }
  console.log(JSON.stringify(o));
  process.exit(0);
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

    a = run("listed_not_picked")
    chk("S1 довідник показав міста, жодне не обране → на сервер НІЧОГО не йде", a is not None and a["posts"] == 0)
    chk("S1 …пояснення «Оберіть своє місто зі списку», кнопка знову активна, фокус на місті", a is not None and "зі списку" in a["msg"] and a["btn"] is False and a["focus"] is True)
    chk("S1 …у списку є вихід «Мого міста немає в списку»", a is not None and a["hasExtra"] is True)
    c = run("click_away")
    chk("S2 клік повз прибирає випадайку, але заблокований submit ПОВЕРТАЄ список (покупець не лишається без підказок)", c is not None and c["before"] is True and c["cleared"] is True and c["posts"] == 0 and c["list"] is True)
    b = run("picked")
    chk("S3 місто обране зі списку → рівно ОДИН POST з np_city_ref і np_city_area (Харківська)", b is not None and len(b["posts"]) == 1
        and b["posts"][0]["np_city_ref"].startswith("aaaaaaaa") and b["posts"][0]["np_city_area"] == "Харківська")
    if b and b["posts"]:
        p = b["posts"][0]
        chk("S3 payload: ім'я обрізане, телефон як ввели, місто/відділення, items з id і qty", p["name"] == "Іван Петренко" and p["phone"] == "0501234567"
            and p["city_name"] == "Іванівка" and p["warehouse_name"] == "Відділення №1" and p["items"] == [{"id": "235193", "qty": 1}] and p["payment_method"] == "cod")
    d = run("dead")
    chk("S4 довідник НП недоступний → ручне місто ЙДЕ (форма не в глухому куті), np_city_ref порожній", d is not None and len(d["posts"]) == 1 and d["posts"][0]["np_city_ref"] == "")
    e = run("empty")
    chk("S5 довідник відповів ПОРОЖНІМ списком (міста в ньому нема) → ручне місто йде", e is not None and len(e["posts"]) == 1)
    na = run("nonarray")
    chk("S5b довідник повернув не-масив (cities:\"abc\") → як порожній, ручне місто йде, без винятку", na is not None and len(na["posts"]) == 1)
    f = run("picked_then_edited_instantly")
    chk("S6 обрав місто, одразу відредагував текст і submit БЕЗ очікування debounce → застарілий ref не їде (блок), а не payload «Тернопіль» з ref Іванівки", f is not None and f["posts"] == 0 and "зі списку" in f["msg"])
    sd = run("submit_in_debounce")
    chk("S7 submit у вікні debounce (відповіді довідника ще нема) → блок, а не fail-open", sd is not None and sd["posts"] == 0 and "зі списку" in sd["msg"])
    nb = run("none_button")
    chk("S8 «Мого міста немає в списку» → submit дозволено, np_city_ref порожній", nb is not None and len(nb["posts"]) == 1 and nb["posts"][0]["np_city_ref"] == "" and nb["n"] == 3)
    pt = run("pending_timeout")
    chk("S9 довідник мовчить: спершу блок, після таймауту (вважаємо недоступним) — форма йде далі; таймаут 3–10 с, а не вічність", pt is not None and pt["blocked"] is True and len(pt["posts"]) == 1 and 3000 <= pt["ms"] <= 10000)
    oo = run("out_of_order")
    chk("S10 невпорядковані відповіді: запізніла стара («Кс») після нової (порожньої) ігнорується — ні списку, ні блоку", oo is not None and oo["posts"] == 1 and oo["list"] is False)
    la = run("late_after_pick")
    chk("S11 запізніла відповідь ПІСЛЯ вибору міста не перемальовує випадайку і не блокує; у payload правильний ref", la is not None and len(la["posts"]) == 1
        and la["posts"][0]["np_city_ref"].startswith("aaaaaaaa") and la["list"] is False)
    ls = run("list_then_shorten")
    chk("S11b список був, текст скоротили до 1 символа → залиплого блоку нема (прапорець списку скинуто на кожен ввід)", ls is not None and ls["posts"] == 1)
    oc = run("one_char")
    chk("S12 один символ у місті → пошуку нема, pending не висить, submit не блокується фронтом", oc is not None and oc["posts"] == 1)
    nt = run("nothing_typed")
    chk("S13 поле міста не торкались → фронт не блокує (порожнє місто відсікає required/сервер)", nt is not None and nt["posts"] == 1)

# сервер: ліміт довідника і поведінка коми (повернуто до master: санація ламала «(Область обл., Район р-н)» і «Київ, Оболонь»)
sys.path.insert(0, str(HERE))
src_api = (HERE / "site_order_api.py").read_text(encoding="utf-8")
chk("сервер: автокомпліт міст просить limit=50 (у «Іванівка» 37 збігів, з 8 покупець свого села не бачить)", "search_cities((q.get(\"q\") or [\"\"])[0], limit=50)" in src_api)
import site_order_api as so  # noqa: E402
import order_router as orr  # noqa: E402
so.price_map = lambda: {"100": {"price": 250, "name": "Тест"}}
base = {"items": [{"id": "100", "qty": 1}], "name": "Іван Петренко", "phone": "0501234567", "city_name": "Київ, Оболонь", "warehouse_name": "відділення 5", "payment_method": "cod"}
o, _ = so.build_order(base)
chk("сервер: «Київ, Оболонь» → np_branch як у master (місто «Київ», «Оболонь» у адресі) — коми НЕ склеюємо в назву міста", o["np_branch"].startswith("Київ, Оболонь, ") and orr.parse_np_branch(o["np_branch"])[0] == "Київ")
o3, _ = so.build_order(dict(base, city_name="Троїцьке (Одеська обл., Біляївський р-н)"))
chk("сервер: кома ВСЕРЕДИНІ дужок («Троїцьке (Одеська обл., Біляївський р-н)») зберігається → область для area_hint не губиться", orr.parse_np_branch(o3["np_branch"])[2] == "Одеська")
o2, _ = so.build_order(dict(base, city_name="  Львів  "))
chk("сервер: пробіли по краях міста обрізані", o2["np_branch"].startswith("Львів,"))

# форма: підказки покупцю
src = (HERE / "site" / "build_site.py").read_text(encoding="utf-8")
chk("форма: під полем імені підказка «ім'я, потім прізвище — без по батькові»", "без по батькові" in src)
chk("форма: підказка під містом просить обрати зі списку, ручний ввід — лише якщо довідник мертвий", "Оберіть своє місто зі списку підказок" in src and "Довідник не працює" in src)

print()
if F:
    print(f"FAILED ({len(F)}):")
    for n in F:
        print("  -", n)
    sys.exit(1)
print("ALL OK")

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_site_checkout_address.py — 12.10.2026, «усі варіанти заповнення адреси» (запит власника): поведінка форми оформлення в node (vm зі стабами DOM/fetch).
Що ловить: місто, вписане руками, коли довідник НП ПОКАЗАВ міста, не має йти на сервер без CityRef/області (посилка могла б поїхати в однойменне село:
інциденти Бородянський 906224962, «Південне»→«Южное» EVA 8-081747967). Якщо довідник мертвий або міста в ньому нема — ручний ввід лишається дозволеним.
Сценарії: S1 міста показано, не обрано → submit ЗАБЛОКОВАНО, на сервер нічого; S2 обрано зі списку → payload має np_city_ref+np_city_area;
S3 довідник віддав помилку → ручне місто йде; S4 довідник віддав порожній список → ручне місто йде; S5 обрав, потім відредагував місто → знову блокується
(застарілий ref іншого міста не лишається); S6 payload: усі поля (name/phone/city_name/warehouse_name/np_*), обрізані пробіли.
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
  querySelectorAll(sel){ // .opt з innerHTML (renderAc) → фейкові елементи з mousedown
    if(sel!==".opt") return []; const n=(e._inner.match(/class="opt"/g)||[]).length; const out=[];
    for(let i=0;i<n;i++){ const hh={}; out.push({getAttribute:()=>String(i), addEventListener(t,f){hh[t]=f}, _h:hh}); } e._opts=out; return out; },
  closest(){return null},setAttribute(){},getAttribute(){return null}};
  Object.defineProperty(e,"innerHTML",{get(){return e._inner}, set(v){e._inner=v; e._opts=null;}}); return e; }
async function run(cityApi){
  const ls=store(), ss=store(); ls.setItem("pt_cart_v1", JSON.stringify({"235193":{id:"235193",name:"Картина",price:468,photo:"x",qty:1}}));
  const E={}, ready=[], posts=[], calls=[];
  ["checkout-form","checkout-msg","checkout-submit","f-name","f-phone","f-email","f-city","f-warehouse","ac-city","ac-warehouse","cart-badge"].forEach(id=>{E[id]=el(id)});
  const doc={addEventListener(t,f){ if(t==="DOMContentLoaded") ready.push(f); }, getElementById(id){return E[id]||null}, querySelector(sel){
      if(sel==='input[name="payment"]:checked') return {value:"cod"}; return null; },
    querySelectorAll(){return []}, body:{addEventListener(){},appendChild(){}}, createElement(){return {style:{},appendChild(){},submit(){}}}};
  const fetchStub=function(url, init){
    calls.push(url);
    if(String(url).indexOf("api/np/city")===0){
      if(cityApi==="dead") return Promise.reject(new Error("net"));
      const cities = cityApi==="empty" ? [] : [{name:"Іванівка",area:"Харківська",ref:"aaaaaaaa-1111-2222-3333-444444444444"},{name:"Іванівка",area:"Одеська",ref:"bbbbbbbb-1111-2222-3333-444444444444"}];
      return Promise.resolve({ok:true, json(){return Promise.resolve({cities})}});
    }
    if(String(url).indexOf("api/order")===0){ posts.push(JSON.parse(init.body)); return Promise.resolve({ok:true, json(){return Promise.resolve({order_id:"PT-TEST-1", liqpay:null})}}); }
    return Promise.resolve({ok:true, json(){return Promise.resolve({warehouses:[]})}});
  };
  const ctx={document:doc,localStorage:ls,sessionStorage:ss,console,fetch:fetchStub,navigator:{},location:{href:"cart.html"},setTimeout,clearTimeout};
  ctx.window=ctx; ctx.self=ctx;
  vm.createContext(ctx); vm.runInContext(code,ctx);
  ready.forEach(f=>f());
  return {E, posts, calls};
}
const sleep=ms=>new Promise(x=>setTimeout(x,ms));
async function typeCity(r, text){ r.E["f-city"].value=text; (r.E["f-city"].h.input||[]).forEach(f=>f()); await sleep(420); }
async function fill(r, wh){ r.E["f-name"].value="  Іван Петренко "; r.E["f-phone"].value="0501234567"; r.E["f-warehouse"].value=wh; }
async function submit(r){ (r.E["checkout-form"].h.submit||[]).forEach(f=>f({preventDefault(){}})); await sleep(60); }
function pick(r, i){ const o=r.E["ac-city"]._opts||r.E["ac-city"].querySelectorAll(".opt"); o[i]._h.mousedown({preventDefault(){}}); }
(async()=>{
  let r;
  if(scenario==="listed_not_picked"){
    r=await run("list"); await typeCity(r,"Іванівка"); await fill(r,"відділення 1"); await submit(r);
    console.log(JSON.stringify({posts:r.posts.length, msg:r.E["checkout-msg"].textContent, err:r.E["checkout-msg"].classList?true:false, btn:r.E["checkout-submit"].disabled, focus:!!r.E["f-city"]._focused}));
  } else if(scenario==="picked"){
    r=await run("list"); await typeCity(r,"Іванівка"); pick(r,0); r.E["f-warehouse"].value="Відділення №1"; await fill(r,"Відділення №1"); await submit(r);
    console.log(JSON.stringify({posts:r.posts}));
  } else if(scenario==="dead"){
    r=await run("dead"); await typeCity(r,"Іванівка"); await fill(r,"відділення 1"); await submit(r);
    console.log(JSON.stringify({posts:r.posts}));
  } else if(scenario==="empty"){
    r=await run("empty"); await typeCity(r,"Ксямлвпр"); await fill(r,"відділення 1"); await submit(r);
    console.log(JSON.stringify({posts:r.posts}));
  } else if(scenario==="picked_then_edited"){
    r=await run("list"); await typeCity(r,"Іванівка"); pick(r,1); await fill(r,"відділення 1");
    r.E["f-city"].value="Іванівк"; (r.E["f-city"].h.input||[]).forEach(f=>f()); await sleep(420);   // правка тексту скидає ref, довідник знову показує список
    await submit(r);
    console.log(JSON.stringify({posts:r.posts.length}));
  } else if(scenario==="nothing_typed"){
    r=await run("list"); await fill(r,"відділення 1"); await submit(r);       // місто не торкались зовсім
    console.log(JSON.stringify({posts:r.posts}));
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

    a = run("listed_not_picked")
    chk("S1 довідник показав міста, жодне не обране → на сервер НІЧОГО не йде", a is not None and a["posts"] == 0)
    chk("S1 …покупець бачить зрозуміле повідомлення «Оберіть місто зі списку» і кнопка знову активна", a is not None and "зі списку" in a["msg"] and a["btn"] is False)
    chk("S1 …фокус повертається на поле міста", a is not None and a["focus"] is True)
    b = run("picked")
    chk("S2 місто обране зі списку → рівно ОДИН POST з np_city_ref і np_city_area (Харківська)", b is not None and len(b["posts"]) == 1
        and b["posts"][0]["np_city_ref"].startswith("aaaaaaaa") and b["posts"][0]["np_city_area"] == "Харківська")
    if b and b["posts"]:
        p = b["posts"][0]
        chk("S6 payload: ім'я обрізане по краях, телефон як ввели, місто/відділення, items з id і qty", p["name"] == "Іван Петренко" and p["phone"] == "0501234567"
            and p["city_name"] == "Іванівка" and p["warehouse_name"] == "Відділення №1" and p["items"] == [{"id": "235193", "qty": 1}] and p["payment_method"] == "cod")
    d = run("dead")
    chk("S3 довідник НП недоступний → ручне місто ЙДЕ (форма не в глухому куті), np_city_ref порожній", d is not None and len(d["posts"]) == 1 and d["posts"][0]["np_city_ref"] == "")
    e = run("empty")
    chk("S4 довідник відповів ПОРОЖНІМ списком (міста в ньому нема) → ручне місто йде", e is not None and len(e["posts"]) == 1)
    f = run("picked_then_edited")
    chk("S5 обрав місто, потім відредагував текст → ref скинуто, довідник знову показує список → submit знову ЗАБЛОКОВАНО (застарілий ref не лишається)", f is not None and f["posts"] == 0)
    n = run("nothing_typed")
    chk("S7 поле міста не торкались (порожнє, довідник не запитувався) → форма не блокується фронтом (порожнє місто відсікає сервер/required)", n is not None and len(n["posts"]) == 1)

# сервер: кома в назві міста не ріже np_branch
sys.path.insert(0, str(HERE))
import site_order_api as so  # noqa: E402
so.price_map = lambda: {"100": {"price": 250, "name": "Тест"}}
base = {"items": [{"id": "100", "qty": 1}], "name": "Іван Петренко", "phone": "0501234567", "city_name": "Київ, Оболонь", "warehouse_name": "відділення 5", "payment_method": "cod"}
o, _ = so.build_order(base)
chk("сервер: «Київ, Оболонь» → np_branch без коми в місті (інакше parse_np_branch зрізав би місто до «Київ»)", o["np_branch"].startswith("Київ Оболонь,") and o["np_branch"].count(",") == 1)
o2, _ = so.build_order(dict(base, city_name="  Львів  "))
chk("сервер: пробіли по краях міста обрізані, звичайне місто без змін", o2["np_branch"].startswith("Львів,"))

# форма: підказки покупцю
sys.path.insert(0, str(HERE / "site"))
src = (HERE / "site" / "build_site.py").read_text(encoding="utf-8")
chk("форма: під полем імені підказка «ім'я, потім прізвище — без по батькові» (покупці пишуть «Прізвище Ім'я По-батькові»)", "без по батькові" in src)
chk("форма: підказка під містом просить обрати зі списку, ручний ввід — лише якщо довідник мертвий", "Оберіть своє місто зі списку підказок" in src and "Довідник не працює" in src)

print()
if F:
    print(f"FAILED ({len(F)}):")
    for n in F:
        print("  -", n)
    sys.exit(1)
print("ALL OK")

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_site_checkout_manual_entry.py — довідник Нової Пошти може бути мертвий (реальний інцидент на телефоні), а покупець мусить
мати змогу оформити замовлення РУКАМИ. Інваріанти (запит Консультанта 10.10.2026, «НЕ ламати» №1 у передачі сайту):
  • поле відділення розблоковується щойно покупець торкнувся міста — НЕЗАЛЕЖНО від того, чи відповів довідник
    (fetch відхилено / порожня відповідь / відповідь ок), і навіть якщо введено лише 1 символ;
  • воно не блокується знову пізніше (після другого вводу міста, після помилки довідника);
  • на сторінці кошика біля поля міста є підказка про ручний ввід.
Поведінка app.js перевіряється в node (vm зі стабами DOM). Без node тест падає ЯВНО (а не мовчки проходить)."""
import os
import re
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "site"))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
for k in ("SITE_LIQPAY_LIVE", "SITE_SELLER_TAXID"):
    os.environ.pop(k, None)
F = []


def chk(n, c):
    print(f"[{'OK ' if c else 'FAIL'}] {n}")
    if not c:
        F.append(n)


# ── підказка на сторінці кошика
import build_site as b  # noqa: E402

tmp = tempfile.mkdtemp()
b.OUT = tmp
b.write_cart()
cart = open(os.path.join(tmp, "cart.html"), encoding="utf-8").read()
m = re.search(r'id="f-city"[^>]*>(.*?)<div class="ac" id="ac-city">', cart, re.S)
chk("кошик: між полем міста і випадайкою є підказка", m is not None and "Впишіть назву самі" in m.group(1))
chk("кошик: підказка згадує відділення (ручний ввід обох полів)", m is not None and "відділення" in m.group(1))
chk("кошик: поле відділення в розмітці disabled лише ДО першого вводу міста (розблокує app.js)",
    re.search(r'<input id="f-warehouse"[^>]*\bdisabled\b', cart) is not None)

# ── поведінка app.js при мертвому довіднику
node = shutil.which("node")
if not node:
    chk("node доступний (без нього поведінку не перевірити)", False)
else:
    harness = r'''
const fs=require("fs"), vm=require("vm");
const code=fs.readFileSync(process.argv[2],"utf8");
const mode=process.argv[3];   // reject | empty | ok
function el(){ const h={}; return {value:"",disabled:false,placeholder:"",innerHTML:"",style:{},h,
  addEventListener(t,f){ (h[t]=h[t]||[]).push(f); }, querySelectorAll(){return []}, closest(){return null}, focus(){}, getAttribute(){return null}}; }
const E={ "f-city":el(), "ac-city":el(), "f-warehouse":el(), "ac-warehouse":el() };
E["f-warehouse"].disabled=true;   // як у розмітці до першого вводу
const ready=[];
const doc={addEventListener(t,f){ if(t==="DOMContentLoaded") ready.push(f); }, getElementById(id){return E[id]||null},
  querySelector(){return null}, querySelectorAll(){return []}, body:{addEventListener(){}}, createElement(){return {style:{}}}};
let fetchCalls=0;
const fetchStub=function(){ fetchCalls++;
  if(mode==="reject") return Promise.reject(new Error("np down"));
  if(mode==="empty") return Promise.resolve({json(){return Promise.resolve({cities:[]})}});
  return Promise.resolve({json(){return Promise.resolve({cities:[{name:"Київ",area:"Київська",ref:"r1"}]})}}); };
const win={}; const ctx={window:win,document:doc,localStorage:{getItem(){return null},setItem(){}},sessionStorage:{getItem(){return null},setItem(){}},console,
  fetch:fetchStub,navigator:{},setTimeout,clearTimeout}; ctx.self=ctx;
vm.createContext(ctx); vm.runInContext(code,ctx);
ready.forEach(f=>f());
const city=E["f-city"], wh=E["f-warehouse"];
const fire=(v)=>{ city.value=v; (city.h.input||[]).forEach(f=>f.call(city)); };
const sleep=ms=>new Promise(r=>setTimeout(r,ms));
(async()=>{
  const out={handlers:(city.h.input||[]).length, before:wh.disabled};
  fire("К");                  await sleep(500); out.after1=wh.disabled;       // 1 символ — запит не робиться, але поле має відкритись
  fire("Київ");               await sleep(500); out.after2=wh.disabled;
  fire("Київ, Святошинський"); await sleep(500); out.after3=wh.disabled;
  out.fetchCalls=fetchCalls; out.placeholder=wh.placeholder;
  console.log(JSON.stringify(out));
})();
'''
    hf = os.path.join(tmp, "harness.js")
    open(hf, "w", encoding="utf-8").write(harness)
    import json
    for mode in ("reject", "empty", "ok"):
        r = subprocess.run([node, hf, os.path.join(HERE, "site", "assets", "app.js"), mode], capture_output=True, text=True, encoding="utf-8", timeout=60)
        try:
            o = json.loads(r.stdout.strip().splitlines()[-1])
        except Exception:
            chk(f"[{mode}] харнес відпрацював (stdout={r.stdout[-200:]!r} stderr={r.stderr[-300:]!r})", False)
            continue
        chk(f"[{mode}] обробник вводу міста зареєстровано", o["handlers"] >= 1)
        chk(f"[{mode}] до вводу відділення заблоковане (як у розмітці)", o["before"] is True)
        chk(f"[{mode}] після 1 символу міста відділення розблоковане", o["after1"] is False)
        chk(f"[{mode}] після повного міста розблоковане", o["after2"] is False)
        chk(f"[{mode}] після повторного вводу міста не заблоковане знову", o["after3"] is False)
        chk(f"[{mode}] плейсхолдер відділення пропонує ввести вручну", "введіть відділення" in o["placeholder"] or "Номер або адреса" in o["placeholder"])
        if mode == "reject":
            chk("[reject] довідник справді опитувався і відхиляв (тест не порожній)", o["fetchCalls"] >= 2)

print()
if F:
    print(f"FAILED ({len(F)}):")
    for n in F:
        print("  -", n)
    sys.exit(1)
print("ALL OK")

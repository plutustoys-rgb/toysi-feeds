#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_site_hero_mascot.py — 12.10.2026, SMM (09.10 + 12.10): Плутус у герої головної ОЖИВАЄ. Перевіряє:
  • збірка: героїв <img id="hero-mascot"> — poster (idle-кадр, webp) з width/height (без CLS), data-clips лише з кліпами «хвіст праворуч» (tailtrip, ear_scratch), data-ms; файли існують;
    вага poster ≤ 30 КБ (критерій SMM «до першої анімації +≤30 КБ» — poster ЗАМІНЮЄ старий статичний PNG, тож сайт не важчає);
  • поведінка (node, керований годинник і Image): перший кліп не раніше ніж через 3 с після load; раз на 12–20 с; один у польоті; кліп програється ОДИН раз і повертається до poster;
    перемішаний цикл без повтору підряд; не граємо й нічого не вантажимо при prefers-reduced-motion / Save-Data / 2g; пауза при прихованій вкладці і коли Плутус поза екраном;
    збій завантаження кліпу — тиша; аналітика нічого не отримує.
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
out = Path(tempfile.mkdtemp())
bs.OUT = str(out)
bs.tp.fetch_toysi_catalog = lambda: dict(catalog)
bs.tp.assert_catalog_size_sane = lambda c, *a, **k: None
import site_photo_probe  # noqa: E402
site_photo_probe.run = lambda idx: None
with contextlib.redirect_stderr(io.StringIO()):
    bs.build()
home = (out / "index.html").read_text(encoding="utf-8")

m = re.search(r'<img class="mascot" id="hero-mascot" src="([^"]+)" alt="[^"]*" width="(\d+)" height="(\d+)" decoding="async" data-clips="([^"]+)" data-ms="([^"]+)">', home)
chk("головна: <img id=hero-mascot> з poster, width/height, data-clips, data-ms", m is not None)
if m:
    poster, w, h, clips, ms = m.group(1), int(m.group(2)), int(m.group(3)), m.group(4).split("|"), m.group(5).split(",")
    chk("poster — webp зі сталою версією ?v=хеш, розміри 130×100 задані атрибутами (CLS 0)", re.fullmatch(r"assets/mascot/plutus_hero_poster\.webp\?v=[0-9a-f]{8}", poster) is not None and (w, h) == (130, 100))
    names = sorted(re.search(r"plutus_(\w+)\.webp", c).group(1) for c in clips)
    chk("у кліпах ЛИШЕ «хвіст праворуч» (tailtrip, ear_scratch) — інші сцени в героя не потрапляють (рішення SMM)", names == ["ear_scratch", "tailtrip"])
    chk("data-ms: тривалість кожного кліпу в мс, число на кліп", len(ms) == len(clips) and all(x.isdigit() and 1000 <= int(x) <= 8000 for x in ms))
    chk("файли існують у site/assets/mascot, кліпи ≤ 250 КБ, poster ≤ 30 КБ (poster замінює PNG 14,6 КБ → головна не важчає)",
        (HERE / "site/assets/mascot/plutus_hero_poster.webp").stat().st_size <= 30 * 1024
        and all((HERE / f"site/assets/mascot/plutus_{n}.webp").stat().st_size <= 250 * 1024 for n in names))
    chk("старий статичний PNG у герої замінено на poster (у .hero2 нема plutus_mascot_s.png)", "plutus_mascot_s.png" not in re.search(r'<div class="hero2">(.*?)<div class="txt">', home, re.S).group(1))
chk("у хедері лишається статичний 24px (SMM: анімація лише в герої)", 'src="assets/plutus_mascot_s.png" alt="Плутус" width="24" height="22"' in home)
chk("розмітка не додає жодного <video>/<script> для кліпів (вантажаться з app.js після load)", "<video" not in home)

node = shutil.which("node")
if not node:
    chk("node доступний (без нього поведінку не перевірити)", False)
else:
    harness = r'''
const fs=require("fs"), vm=require("vm");
const code=fs.readFileSync(process.argv[2],"utf8"); const scenario=process.argv[3];
function store(){ const m={}; return {getItem:k=>(k in m?m[k]:null), setItem:(k,v)=>{m[k]=String(v)}, removeItem:k=>{delete m[k]}}; }
(async()=>{
  const attrs={ "src":"assets/mascot/plutus_hero_poster.webp?v=aaaaaaaa", "data-clips": scenario==="noclips"?"":"assets/mascot/plutus_tailtrip.webp?v=1|assets/mascot/plutus_ear_scratch.webp?v=2", "data-ms":"3170,4000" };
  const img={id:"hero-mascot", get src(){return attrs.src}, set src(v){ srcLog.push(v); attrs.src=v; }, getAttribute(k){return attrs[k]===undefined?null:attrs[k]}, addEventListener(){}};
  const srcLog=[]; const timers=[]; const imgs=[]; const docH={}, winH={}; const gt=[];
  let now=0; let ioCb=null;
  const doc={hidden:false, readyState: scenario==="loading"?"loading":"complete", addEventListener(t,f){ (docH[t]=docH[t]||[]).push(f); }, getElementById(id){ return id==="hero-mascot"?img:null; }, querySelector(){return null}, querySelectorAll(){return []}, body:{addEventListener(){}}, createElement(){return {style:{}}}};
  const nav = scenario==="savedata"?{connection:{saveData:true}}: scenario==="twog"?{connection:{effectiveType:"2g"}}: scenario==="slow2g"?{connection:{effectiveType:"slow-2g"}}: scenario==="threeg"?{connection:{effectiveType:"3g"}}:{};
  function ImageStub(){ const o={onload:null,onerror:null,_src:""}; Object.defineProperty(o,"src",{set(v){o._src=v; imgs.push(o);}, get(){return o._src}}); return o; }
  const ctx={document:doc, localStorage:store(), sessionStorage:store(), console, fetch:()=>Promise.reject(), navigator:nav, location:{pathname:"/",href:"/"}, Image:ImageStub,
    setTimeout:function(f,ms){ const id=timers.length+1; timers.push({id,f,ms,done:false}); return id; }, clearTimeout:function(id){ const t=timers.find(x=>x.id===id); if(t) t.done=true; },
    setInterval:function(){return 1;}, addEventListener(t,f){ (winH[t]=winH[t]||[]).push(f); }, Math:Object.create(Math)};
  ctx.window=ctx; ctx.self=ctx; ctx.matchMedia=function(q){ return {matches: scenario==="reduce" && /reduce/.test(q)}; };
  ctx.PT_ANALYTICS={ga4:"G-T", fb:"1"}; ctx.gtag=function(){gt.push(Array.from(arguments))}; ctx.fbq=function(){gt.push(["fbq"])};
  if(scenario!=="noio"){ ctx.IntersectionObserver=function(cb){ ioCb=cb; this.observe=function(){}; this.disconnect=function(){}; }; }
  let rnd=0.5; ctx.Math.random=function(){ return rnd; };
  vm.createContext(ctx); vm.runInContext(code,ctx);
  const pending=()=>timers.filter(t=>!t.done);
  const fireNext=()=>{ const p=pending(); if(!p.length) return null; const t=p[0]; t.done=true; t.f(); return t; };
  const out={};
  try{ (docH.DOMContentLoaded||[]).forEach(f=>f()); }catch(e){}
  // initMascot викликається з DOMContentLoaded-обробника app.js (init) — у цьому harness-і запускаємо його так само
  out.timers0=pending().map(t=>t.ms);
  if(scenario==="loading"){ out.beforeLoad=pending().length; (winH.load||[]).forEach(f=>f()); out.afterLoad=pending().map(t=>t.ms); }
  if(["basic","hidden_at_load","visibility","onerror","one_inflight","visible_toggle","threeg","noio","cycle","delays","boundary"].includes(scenario)){
    const vis=(v)=>{ if(ioCb) ioCb([{isIntersecting:v}]); };
    if(scenario!=="visible_toggle") vis(true);
    if(scenario==="visible_toggle"){ const t1=fireNext(); out.notVisibleResched=pending().map(t=>t.ms); vis(true); }
    else if(scenario==="basic"||scenario==="threeg"||scenario==="noio"){
      const t1=fireNext(); out.first=t1&&t1.ms; out.imgsAfterFire=imgs.length; out.srcBeforeLoad=srcLog.length;
      imgs[0].onload(); out.srcAfterLoad=srcLog.slice(); out.endMs=pending().map(t=>t.ms);
      fireNext(); out.srcAfterEnd=srcLog.slice(); out.nextMs=pending().map(t=>t.ms); out.gt=gt.length;
    } else if(scenario==="hidden_at_load"){
      fireNext(); doc.hidden=true; imgs[0].onload(); out.src=srcLog.slice(); out.resched=pending().map(t=>t.ms);
    } else if(scenario==="visibility"){
      fireNext(); imgs[0].onload(); out.playing=srcLog.slice(); doc.hidden=true; (docH.visibilitychange||[]).forEach(f=>f()); out.afterHide=srcLog.slice();
    } else if(scenario==="onerror"){
      fireNext(); imgs[0].onerror(); out.src=srcLog.slice(); out.resched=pending().map(t=>t.ms);
    } else if(scenario==="one_inflight"){
      fireNext(); imgs[0].onload();                       // кліп грає
      out.imgsBefore=imgs.length; // примусово викликаємо ще один play (таймер планувальника) під час відтворення — не має створити другий Image
      const sched=pending(); out.pendingWhilePlaying=sched.length;
    } else if(scenario==="cycle"){
      const seq=[]; for(let i=0;i<8;i++){ fireNext(); imgs[imgs.length-1].onload(); seq.push(srcLog[srcLog.length-1]); fireNext(); }
      out.seq=seq;
    } else if(scenario==="boundary"){
      const seq=[]; for(let i=0;i<3;i++){ if(i===2) rnd=0; fireNext(); imgs[imgs.length-1].onload(); seq.push(srcLog[srcLog.length-1]); fireNext(); }       // новий цикл перемішується так, що починається з кліпу, яким закінчився попередній
      out.seq=seq;
    } else if(scenario==="delays"){
      const ds=[]; for(const r of [0, 0.999, 0.5]){ rnd=r; fireNext(); imgs[imgs.length-1].onload(); fireNext(); ds.push(pending()[0].ms); }
      out.ds=ds;
    }
  } else if(["reduce","savedata","twog","slow2g","noclips"].includes(scenario)){
    out.imgs=imgs.length; out.timers=pending().length;
  }
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

    b = run("basic")
    chk("після load рівно ОДИН таймер на 3000 мс (перший кліп не раніше ніж через 3 с)", b is not None and b["timers0"] == [3000])
    chk("тік: кліп спершу ПЕРЕДЗАВАНТАЖУЄТЬСЯ (Image), <img> не міняється до onload", b is not None and b["imgsAfterFire"] == 1 and b["srcBeforeLoad"] == 0)
    chk("onload → <img>.src = кліп (з версією ?v), таймер кінця = тривалість + 150 мс", b is not None and len(b["srcAfterLoad"]) == 1 and "plutus_" in b["srcAfterLoad"][0] and b["srcAfterLoad"][0].endswith(("?v=1", "?v=2"))
        and b["endMs"] in ([3320], [4150]))
    chk("після кінця <img>.src повертається до poster (кліп грає ОДИН раз, не луп) і планується наступний через 12–20 с", b is not None and b["srcAfterEnd"][-1].startswith("assets/mascot/plutus_hero_poster.webp")
        and len(b["nextMs"]) == 1 and 12000 <= b["nextMs"][0] <= 20000)
    chk("аналітика не отримала жодної події від анімації", b is not None and b["gt"] == 0)
    ld = run("loading")
    chk("сторінка ще вантажиться: таймерів нема ДО load; після load — 3000 мс", ld is not None and ld["beforeLoad"] == 0 and ld["afterLoad"] == [3000])
    for sc, label in (("reduce", "prefers-reduced-motion"), ("savedata", "Save-Data"), ("twog", "2g"), ("slow2g", "slow-2g"), ("noclips", "нема data-clips")):
        r = run(sc)
        chk(f"{label}: жодного таймера і жодного завантаження кліпу", r is not None and r["imgs"] == 0 and r["timers"] == 0)
    t3 = run("threeg")
    chk("3g (не 2g) — анімація дозволена", t3 is not None and t3["timers0"] == [3000] and len(t3["srcAfterLoad"]) == 1)
    nv = run("visible_toggle")
    chk("Плутус поза екраном (IO ще не повідомив) → кліп не вантажиться, лише перепланування 12–20 с", nv is not None and len(nv["notVisibleResched"]) == 1 and 12000 <= nv["notVisibleResched"][0] <= 20000)
    hl = run("hidden_at_load")
    chk("вкладку сховали, поки вантажився кліп → не граємо (src не змінено), перепланування", hl is not None and hl["src"] == [] and len(hl["resched"]) == 1)
    vs = run("visibility")
    chk("вкладка прихована ПІД ЧАС відтворення → одразу повернення до poster", vs is not None and len(vs["playing"]) == 1 and vs["afterHide"][-1].startswith("assets/mascot/plutus_hero_poster.webp"))
    oe = run("onerror")
    chk("кліп не завантажився (onerror) → <img> не чіпаємо, тиша, перепланування", oe is not None and oe["src"] == [] and len(oe["resched"]) == 1)
    cy = run("cycle")
    chk("8 відтворень: кліпи чергуються, жодного повтору підряд; обидва кліпи використано", cy is not None and all(cy["seq"][i] != cy["seq"][i + 1] for i in range(len(cy["seq"]) - 1)) and len(set(cy["seq"])) == 2)
    bd = run("boundary")
    chk("стик циклів: новий цикл не починається з кліпу, яким закінчився попередній (3 відтворення без повтору підряд)", bd is not None and bd["seq"][1] != bd["seq"][2] and bd["seq"][0] != bd["seq"][1])
    dl = run("delays")
    chk("пауза між відтвореннями у межах 12–20 с на краях генератора (0 → 12000, 0.999 → <20000)", dl is not None and dl["ds"][0] == 12000 and 19900 <= dl["ds"][1] < 20000 and 12000 <= dl["ds"][2] <= 20000)
    nio = run("noio")
    chk("без IntersectionObserver (старий браузер) Плутус вважається видимим → грає", nio is not None and len(nio["srcAfterLoad"]) == 1)

print()
if F:
    print(f"FAILED ({len(F)}):")
    for n in F:
        print("  -", n)
    sys.exit(1)
print("ALL OK")

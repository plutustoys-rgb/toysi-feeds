#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_site_hero_mascot.py — 12.10.2026, SMM (09.10 + 12.10): Плутус у герої головної ОЖИВАЄ. Перевіряє:
  • збірка: poster <img id=hero-mascot> (webp, width/height — без CLS) + накладений <img id=hero-mascot-clip> без src (кросфейд, аудит #688: кадри кліпів не збігаються з poster за позою);
    data-clips лише з кліпами «хвіст праворуч» (tailtrip, ear_scratch), data-ms; файли існують; poster ≤ 30 КБ і легший за PNG, який у герої замінив (у хедері лишається PNG 24 px, тож головна важчає
    лише на poster — ≤ 30 КБ за критерієм SMM);
  • поведінка (node, керований годинник, Image, IO): перший кліп не раніше ніж через 3 с після load; раз на 12–20 с; один у польоті; кліп з'являється на clip-<img> (клас on) і розчиняється, poster не чіпаємо;
    src кліпу знімається після розчинення (наступний показ з першого кадру); запас до кінця ≥ 300 мс; перемішаний цикл без повтору підряд (і на стику циклів); не граємо й не вантажимо при prefers-reduced-motion /
    Save-Data / 2g; прихована вкладка ПІД ЧАС кліпу гасить його й ПЛАНУЄ наступний (баг #688: анімація вмирала назавжди); Плутус поза екраном гасить і планує; помилка кліпу — тиша; IO threshold 0,3; аналітика порожня.
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
with contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(io.StringIO()):
    bs.build()
home = (out / "index.html").read_text(encoding="utf-8")

m = re.search(r'<span class="mascot-wrap"><img class="mascot" id="hero-mascot" src="([^"]+)" alt="[^"]*" width="(\d+)" height="(\d+)" decoding="async" data-clips="([^"]+)" data-ms="([^"]+)">'
              r'<img class="mascot mascot-clip" id="hero-mascot-clip" alt="" aria-hidden="true" width="130" height="100" decoding="async"></span>', home)
chk("головна: герой = обгортка .mascot-wrap із poster (data-clips, data-ms) і накладеним clip-<img> БЕЗ src (aria-hidden)", m is not None)
if m:
    poster, w, h, clips, ms = m.group(1), int(m.group(2)), int(m.group(3)), m.group(4).split("|"), m.group(5).split(",")
    chk("poster — webp зі сталою версією ?v=хеш, розміри 130×100 задані атрибутами (CLS 0)", re.fullmatch(r"assets/mascot/plutus_hero_poster\.webp\?v=[0-9a-f]{8}", poster) is not None and (w, h) == (130, 100))
    names = sorted(re.search(r"plutus_(\w+)\.webp", c).group(1) for c in clips)
    chk("у кліпах ЛИШЕ «хвіст праворуч» (tailtrip, ear_scratch) — інші сцени в героя не потрапляють (рішення SMM)", names == ["ear_scratch", "tailtrip"])
    chk("data-ms: тривалість кожного кліпу в мс, число на кліп", len(ms) == len(clips) and all(x.isdigit() and 1000 <= int(x) <= 8000 for x in ms))
    ps = (HERE / "site/assets/mascot/plutus_hero_poster.webp").stat().st_size
    chk("файли існують у site/assets/mascot, кліпи ≤ 250 КБ, poster ≤ 30 КБ і легший за PNG 14,6 КБ, який у герої замінив", ps <= 30 * 1024 and ps < (HERE / "site/assets/plutus_mascot_s.png").stat().st_size
        and all((HERE / f"site/assets/mascot/plutus_{n}.webp").stat().st_size <= 250 * 1024 for n in names))
    chk("старий статичний PNG у герої замінено на poster (у .hero2 нема plutus_mascot_s.png)", "plutus_mascot_s.png" not in re.search(r'<div class="hero2">(.*?)<div class="txt">', home, re.S).group(1))
chk("у хедері лишається статичний 24px PNG (SMM: анімація лише в герої) — головна важчає лише на poster", 'src="assets/plutus_mascot_s.png" alt="Плутус" width="24" height="22"' in home)
chk("розмітка не додає жодного <video>/<script> для кліпів (вантажаться з app.js після load)", "<video" not in home)
css = (HERE / "site" / "assets" / "styles.css").read_text(encoding="utf-8")
chk("CSS: clip-<img> накладений (absolute, opacity 0 → .on 1, transition); для reduced-motion transition вимкнено", re.search(r"\.mascot-clip\{position:absolute;left:0;top:0;opacity:0;transition:opacity", css) is not None and ".mascot-clip.on{opacity:1}" in css
    and "prefers-reduced-motion:reduce){.hero2 .mascot-wrap .mascot-clip{transition:none}" in css)

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
  const posterLog=[]; const clipLog=[]; const timers=[]; const imgs=[]; const docH={}, winH={}; const gt=[]; const ioOpts=[];
  const img={id:"hero-mascot", get src(){return attrs.src}, set src(v){ posterLog.push(v); attrs.src=v; }, getAttribute(k){return attrs[k]===undefined?null:attrs[k]}, addEventListener(){}};
  const clipAttrs={}; let clsOn=false;
  const clip={id:"hero-mascot-clip", set src(v){ clipLog.push(["set", v]); clipAttrs.src=v; }, get src(){ return clipAttrs.src; }, removeAttribute(k){ clipLog.push(["rm", k]); delete clipAttrs[k]; }, classList:{add(c){ if(c==="on"){ clsOn=true; } }, remove(c){ if(c==="on"){ clsOn=false; } }}, getAttribute(){return null}};
  let ioCb=null;
  const doc={hidden:false, readyState: scenario==="loading"?"loading":"complete", addEventListener(t,f){ (docH[t]=docH[t]||[]).push(f); }, getElementById(id){ return id==="hero-mascot"?img:(id==="hero-mascot-clip"&&scenario!=="noclipimg"?clip:null); }, querySelector(){return null}, querySelectorAll(){return []}, body:{addEventListener(){}}, createElement(){return {style:{}}}};
  const nav = scenario==="savedata"?{connection:{saveData:true}}: scenario==="twog"?{connection:{effectiveType:"2g"}}: scenario==="slow2g"?{connection:{effectiveType:"slow-2g"}}: scenario==="threeg"?{connection:{effectiveType:"3g"}}:{};
  function ImageStub(){ const o={onload:null,onerror:null,_src:""}; Object.defineProperty(o,"src",{set(v){o._src=v; imgs.push(o);}, get(){return o._src}}); return o; }
  const ctx={document:doc, localStorage:store(), sessionStorage:store(), console, fetch:()=>Promise.reject(), navigator:nav, location:{pathname:"/",href:"/"}, Image:ImageStub,
    setTimeout:function(f,ms){ const id=timers.length+1; timers.push({id,f,ms,done:false}); return id; }, clearTimeout:function(id){ const t=timers.find(x=>x.id===id); if(t) t.done=true; },
    setInterval:function(){return 1;}, addEventListener(t,f){ (winH[t]=winH[t]||[]).push(f); }, Math:Object.create(Math)};
  ctx.window=ctx; ctx.self=ctx; ctx.matchMedia=function(q){ return {matches: scenario==="reduce" && /reduce/.test(q)}; };
  ctx.PT_ANALYTICS={ga4:"G-T", fb:"1"}; ctx.gtag=function(){gt.push(Array.from(arguments))}; ctx.fbq=function(){gt.push(["fbq"])};
  if(scenario!=="noio"){ ctx.IntersectionObserver=function(cb, opts){ ioCb=cb; ioOpts.push(opts); this.observe=function(){}; this.disconnect=function(){}; }; }
  let rnd=0.5; ctx.Math.random=function(){ return rnd; };
  vm.createContext(ctx); vm.runInContext(code,ctx);
  const pending=()=>timers.filter(t=>!t.done);
  const next12=()=>pending().filter(t=>t.ms>=12000);
  const fireNext=()=>{ const p=pending(); if(!p.length) return null; const t=p[0]; t.done=true; t.f(); return t; };
  const fireMs=(pred)=>{ const t=pending().find(pred); if(!t) return null; t.done=true; t.f(); return t; };
  const out={};
  try{ (docH.DOMContentLoaded||[]).forEach(f=>f()); }catch(e){}
  out.timers0=pending().map(t=>t.ms);
  const vis=(v)=>{ if(ioCb) ioCb([{isIntersecting:v}]); };
  out.ioThreshold=ioOpts.length?ioOpts[0].threshold:null;
  const lastSet=()=>clipLog.filter(x=>x[0]==="set").slice(-1)[0][1];
  if(scenario==="loading"){ out.beforeLoad=pending().length; (winH.load||[]).forEach(f=>f()); out.afterLoad=pending().map(t=>t.ms); }
  else if(["basic","threeg","noio"].includes(scenario)){
    vis(true);
    const t1=fireNext(); out.first=t1&&t1.ms; out.imgsAfterFire=imgs.length; out.clipBeforeLoad=clipLog.length;
    imgs[0].onload(); out.clipAfterLoad=clipLog.slice(); out.onAfterLoad=clsOn; out.endMs=pending().filter(t=>t.ms<12000).map(t=>t.ms);
    fireMs(t=>t.ms<12000);                                    // кінець кліпу
    out.onAfterEnd=clsOn; out.posterLog=posterLog.slice(); out.nextMs=next12().map(t=>t.ms); out.gt=gt.length;
    fireMs(t=>t.ms===350); out.clipAfterFade=clipLog.slice();  // після розчинення src знімається
  } else if(scenario==="visible_toggle"){
    fireNext(); out.notVisibleResched=next12().map(t=>t.ms); out.imgs=imgs.length;
  } else if(scenario==="hidden_at_load"){
    vis(true); fireNext(); doc.hidden=true; imgs[0].onload(); out.clip=clipLog.slice(); out.resched=next12().length;
  } else if(scenario==="hide_during"){
    vis(true); fireNext(); imgs[0].onload(); out.playing=clsOn; doc.hidden=true; (docH.visibilitychange||[]).forEach(f=>f());
    out.afterHide=clsOn; out.resched=next12().map(t=>t.ms);                 // наступний кліп ЗАПЛАНОВАНО (баг #688: анімація вмирала)
    doc.hidden=false; fireMs(t=>t.ms>=12000); imgs[imgs.length-1].onload(); out.playsAgain=clsOn;
  } else if(scenario==="hide_when_idle"){
    vis(true); const before=next12().length+pending().filter(t=>t.ms===3000).length; doc.hidden=true; (docH.visibilitychange||[]).forEach(f=>f()); out.before=before; out.after=pending().length;
  } else if(scenario==="offscreen_during"){
    vis(true); fireNext(); imgs[0].onload(); out.playing=clsOn; vis(false); out.afterOff=clsOn; out.resched=next12().length;
  } else if(scenario==="onerror"){
    vis(true); fireNext(); imgs[0].onerror(); out.clip=clipLog.slice(); out.resched=next12().length;
  } else if(scenario==="one_inflight"){
    vis(true); fireNext(); imgs[0].onload();
    out.imgs=imgs.length; out.nextWhilePlaying=next12().length; out.endTimers=pending().filter(t=>t.ms<12000).length;
  } else if(scenario==="cycle"){
    vis(true); const seq=[]; for(let i=0;i<8;i++){ fireMs(t=>t.ms>=3000); imgs[imgs.length-1].onload(); seq.push(lastSet()); fireMs(t=>t.ms<12000&&t.ms>350); }
    out.seq=seq;
  } else if(scenario==="boundary"){
    vis(true); const seq=[]; for(let i=0;i<3;i++){ if(i===2) rnd=0; fireMs(t=>t.ms>=3000); imgs[imgs.length-1].onload(); seq.push(lastSet()); fireMs(t=>t.ms<12000&&t.ms>350); }
    out.seq=seq;
  } else if(scenario==="delays"){
    vis(true); const ds=[]; for(const r of [0, 0.999, 0.5]){ rnd=r; fireMs(t=>t.ms>=3000); imgs[imgs.length-1].onload(); fireMs(t=>t.ms<12000&&t.ms>350); ds.push(next12()[0].ms); }
    out.ds=ds;
  } else if(["reduce","savedata","twog","slow2g","noclips","noclipimg"].includes(scenario)){
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
    chk("IntersectionObserver з threshold 0,3", b is not None and b["ioThreshold"] == 0.3)
    chk("тік: кліп спершу ПЕРЕДЗАВАНТАЖУЄТЬСЯ (Image), clip-<img> не чіпаємо до onload", b is not None and b["imgsAfterFire"] == 1 and b["clipBeforeLoad"] == 0)
    chk("onload → clip-<img>.src = кліп (з ?v) і клас «on» (кросфейд), таймер кінця = тривалість + 400 мс (запас на декодування)", b is not None and len(b["clipAfterLoad"]) == 1 and b["clipAfterLoad"][0][0] == "set"
        and b["clipAfterLoad"][0][1].endswith(("?v=1", "?v=2")) and b["onAfterLoad"] is True and b["endMs"] in ([3570], [4400]))
    chk("кінець кліпу: клас «on» знято (розчинення), poster-<img> НЕ чіпали (завжди під низом), наступний показ через 12–20 с", b is not None and b["onAfterEnd"] is False and b["posterLog"] == [] and len(b["nextMs"]) == 1 and 12000 <= b["nextMs"][0] <= 20000)
    chk("після розчинення (350 мс) src кліпу знімається — наступний показ стартує з першого кадру", b is not None and b["clipAfterFade"][-1] == ["rm", "src"])
    chk("аналітика не отримала жодної події від анімації", b is not None and b["gt"] == 0)
    ld = run("loading")
    chk("сторінка ще вантажиться: таймерів нема ДО load; після load — 3000 мс", ld is not None and ld["beforeLoad"] == 0 and ld["afterLoad"] == [3000])
    for sc, label in (("reduce", "prefers-reduced-motion"), ("savedata", "Save-Data"), ("twog", "2g"), ("slow2g", "slow-2g"), ("noclips", "нема data-clips"), ("noclipimg", "нема clip-<img>")):
        r = run(sc)
        chk(f"{label}: жодного таймера і жодного завантаження кліпу", r is not None and r["imgs"] == 0 and r["timers"] == 0)
    t3 = run("threeg")
    chk("3g (не 2g) — анімація дозволена", t3 is not None and t3["timers0"] == [3000] and len(t3["clipAfterLoad"]) == 1)
    nv = run("visible_toggle")
    chk("Плутус поза екраном (IO ще не повідомив) → кліп не вантажиться, лише перепланування 12–20 с", nv is not None and nv["imgs"] == 0 and len(nv["notVisibleResched"]) == 1 and 12000 <= nv["notVisibleResched"][0] <= 20000)
    hl = run("hidden_at_load")
    chk("вкладку сховали, поки вантажився кліп → не граємо (clip-<img> не чіпано), перепланування", hl is not None and hl["clip"] == [] and hl["resched"] == 1)
    hd = run("hide_during")
    chk("вкладка прихована ПІД ЧАС кліпу → клас «on» знято одразу І наступний кліп ЗАПЛАНОВАНО (баг #688: анімація вмирала назавжди)", hd is not None and hd["playing"] is True and hd["afterHide"] is False and len(hd["resched"]) == 1 and 12000 <= hd["resched"][0] <= 20000)
    chk("…і після повернення на вкладку наступний кліп справді грає", hd is not None and hd["playsAgain"] is True)
    hi = run("hide_when_idle")
    chk("вкладка прихована, кліп НЕ грає → таймерів не додається (лишається той самий один)", hi is not None and hi["before"] == 1 and hi["after"] == 1)
    od = run("offscreen_during")
    chk("Плутус зник з екрана ПІД ЧАС кліпу → гасимо одразу й плануємо наступний", od is not None and od["playing"] is True and od["afterOff"] is False and od["resched"] == 1)
    oe = run("onerror")
    chk("кліп не завантажився (onerror) → clip-<img> не чіпаємо, тиша, перепланування", oe is not None and oe["clip"] == [] and oe["resched"] == 1)
    fl = run("one_inflight")
    chk("під час відтворення: один Image, один таймер кінця, наступний показ не заплановано (ланцюжок єдиний)", fl is not None and fl["imgs"] == 1 and fl["endTimers"] == 1 and fl["nextWhilePlaying"] == 0)
    cy = run("cycle")
    chk("8 відтворень: кліпи чергуються, жодного повтору підряд; обидва кліпи використано", cy is not None and all(cy["seq"][i] != cy["seq"][i + 1] for i in range(len(cy["seq"]) - 1)) and len(set(cy["seq"])) == 2)
    bd = run("boundary")
    chk("стик циклів: новий цикл не починається з кліпу, яким закінчився попередній (3 відтворення без повтору підряд)", bd is not None and bd["seq"][1] != bd["seq"][2] and bd["seq"][0] != bd["seq"][1])
    dl = run("delays")
    chk("пауза між відтвореннями у межах 12–20 с на краях генератора (0 → 12000, 0.999 → <20000)", dl is not None and dl["ds"][0] == 12000 and 19900 <= dl["ds"][1] < 20000 and 12000 <= dl["ds"][2] <= 20000)
    nio = run("noio")
    chk("без IntersectionObserver (старий браузер) Плутус вважається видимим → грає", nio is not None and len(nio["clipAfterLoad"]) == 1)

print()
if F:
    print(f"FAILED ({len(F)}):")
    for n in F:
        print("  -", n)
    sys.exit(1)
print("ALL OK")

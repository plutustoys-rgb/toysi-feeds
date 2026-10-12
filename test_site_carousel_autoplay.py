#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_site_carousel_autoplay.py — 12.10.2026 (SMM: власник бачить нерухому карусель як «не крутиться»). Автопрокрутка «Добірки Плутуса» в node (vm, керований годинник):
  по 1 картці кожні ~4 с (крок = відстань між слайдами), на краю — плавно на початок; пауза: pointer-hover мишею (touch-«наведення» НЕ рахується), клавіатурний фокус
  (:focus-visible; клік мишею по стрілці фокус не тримає), палець на екрані (+10 с після відпускання), дія покупця (стрілка/колесо/клавіша — 10 с), приховану вкладку,
  карусель менше ніж на 40% у вікні (IntersectionObserver; до першого повідомлення — «не видно»), prefers-reduced-motion. В аналітику автопрокрутка нічого не пише.
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
const code=fs.readFileSync(process.argv[2],"utf8"); const scenario=process.argv[3];
function store(){ const m={}; return {getItem:k=>(k in m?m[k]:null), setItem:(k,v)=>{m[k]=String(v)}, removeItem:k=>{delete m[k]}}; }
function mkEl(o){ const h={}; const cls=new Set(); const e=Object.assign({h, style:{}, classList:{add(c){cls.add(c)},remove(c){cls.delete(c)},toggle(){},contains(c){return cls.has(c)}}, textContent:"", disabled:false, scrollLeft:0, clientWidth:600, scrollWidth:2400,
  addEventListener(t,f){(h[t]=h[t]||[]).push(f)}, getAttribute(k){return (o.attrs||{})[k]||null}, querySelector(){return null}, querySelectorAll(){return []}, closest(){return null}}, o); return e; }
function fire(el,t,ev){ (el.h[t]||[]).forEach(f=>f(ev||{})); }
const sleep=ms=>new Promise(x=>setTimeout(x,ms));
(async()=>{
  const NC = scenario==="one" ? 1 : 6;
  const cards=Array.from({length:NC},(_,k)=>k).map(i=>{ const nm=mkEl({textContent:"Т"+i}), pr=mkEl({textContent:"100 ₴"}); const a=mkEl({attrs:{href:"product-"+(10+i)+".html"}});
    a.offsetLeft=i*260; a.querySelector=s=>s===".nm"?nm:(s===".pr"?pr:null); a.closest=s=>s==="a.card"?a:null; return a; });
  const left=mkEl({}), right=mkEl({}), track=mkEl({});
  const moves=[];
  track.scrollWidth=NC*260+40; track.clientWidth=600;
  track.scrollBy=function(a){ moves.push(["by",a.left,a.behavior]); track.scrollLeft+=a.left; };
  track.scrollTo=function(a){ moves.push(["to",a.left,a.behavior]); track.scrollLeft=a.left; };
  const car=mkEl({attrs:{"data-list":"Добірка Плутуса"}});
  car.querySelector=s=>s===".track"?track:(s===".arrow.l"?left:(s===".arrow.r"?right:null));
  car.querySelectorAll=s=>s==="a.card"?cards:[];
  let now=1000000; let tick=null, intervals=[];
  const gt=[]; const ioAll=[]; let reduce=(scenario==="reduce");
  const ready=[]; const doc={hidden:false, addEventListener(t,f){ if(t==="DOMContentLoaded") ready.push(f); }, getElementById(id){return id==="home-car"?car:null}, querySelector(){return null}, querySelectorAll(){return []}, body:{addEventListener(){}}, createElement(){return {style:{}}}};
  const FakeDate={now:()=>now};
  const ctx={document:doc, localStorage:store(), sessionStorage:store(), console, fetch:()=>Promise.reject(), navigator:{}, location:{pathname:"/",href:"/"}, setTimeout, clearTimeout, Date:FakeDate,
    setInterval:function(f,ms){ intervals.push(ms); tick=f; return 1; }, addEventListener(){}};
  ctx.window=ctx; ctx.self=ctx;
  ctx.matchMedia=function(q){ return {matches: /reduce/.test(q) ? reduce : false}; };
  ctx.PT_ANALYTICS={ga4:"G-T", fb:"1"}; ctx.gtag=function(){gt.push(Array.from(arguments))}; ctx.fbq=function(){};
  if(scenario!=="noio"){ ctx.IntersectionObserver=function(cb){ ioAll.push(cb); this.observe=function(){}; this.disconnect=function(){}; }; }
  vm.createContext(ctx); vm.runInContext(code,ctx);
  let threw=null; try{ ready.forEach(f=>f()); }catch(e){ threw=String(e); }
  const T=()=>{ if(tick) tick(); };
  const visible=()=>{ ioAll.forEach(cb=>cb([{isIntersecting:true}])); };   // автопрокрутка слухає ОКРЕМИЙ IO (поруч із IO для view_item_list) — подаємо в обидва
  const hide=()=>{ ioAll.forEach(cb=>cb([{isIntersecting:false}])); };
  const out={threw, intervals};
  const res=out;
  res.moves0=moves.length;
  if(scenario==="basic"){
    T(); res.beforeVisible=moves.length;                   // до повідомлення IO — «не видно» → тиші
    visible(); T(); T(); res.afterVisible=moves.slice();
    // докручуємо до краю: scrollLeft ≈ 2*260; кінець = scrollWidth-clientWidth = 1600+40-600 = 1040 → ще 2 кроки до краю
    T(); T(); T(); res.afterMore=moves.slice();
    res.gt=gt.filter(e=>e[1]==="select_item").length;
  } else if(scenario==="noio"){
    T(); res.moves=moves.slice();
  } else if(scenario==="hover"){
    visible(); fire(car,"pointerenter",{pointerType:"mouse"}); T(); res.hov=moves.length;
    fire(car,"pointerleave",{pointerType:"mouse"}); T(); res.afterLeave=moves.length;
    fire(car,"pointerenter",{pointerType:"touch"}); T(); res.touchHover=moves.length;     // touch-«наведення» не блокує
  } else if(scenario==="focus"){
    visible(); fire(car,"focusin",{target:{matches:()=>true}}); T(); res.kbFocus=moves.length;
    fire(car,"focusout"); T(); res.afterBlur=moves.length;
    fire(car,"focusin",{target:{matches:()=>false}}); T(); res.mouseFocus=moves.length;       // клік мишею по стрілці: фокус не :focus-visible
  } else if(scenario==="touch"){
    visible(); fire(car,"touchstart"); T(); res.during=moves.length;
    fire(car,"touchend"); now+=5000; T(); res.after5=moves.length;
    now+=5200; T(); res.after10=moves.length;
  } else if(scenario==="user"){
    visible(); fire(right,"click"); let b0=moves.length;         // сам клік по стрілці скролить (це дія покупця) — рахуємо автокроки ВІДНОСНО нього
    T(); res.afterArrow=moves.length-b0;
    now+=10100; T(); res.afterGrace=moves.length-b0;
    fire(car,"wheel"); b0=moves.length; T(); res.afterWheel=moves.length-b0;
    now+=10100; fire(car,"keydown"); b0=moves.length; T(); res.afterKey=moves.length-b0;
  } else if(scenario==="hidden"){
    visible(); doc.hidden=true; T(); res.hidden=moves.length; doc.hidden=false; T(); res.shown=moves.length;
  } else if(scenario==="offscreen"){
    visible(); hide(); T(); res.off=moves.length; visible(); T(); res.on=moves.length;
  } else if(scenario==="reduce"){
    visible(); T(); T(); res.moves=moves.length;
  } else if(scenario==="one"){
    res.n=intervals.length;
  } else if(scenario==="end"){
    visible(); track.scrollLeft=track.scrollWidth-track.clientWidth; T(); res.moves=moves.slice();
  }
  console.log(JSON.stringify(res));
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
    chk("ініціалізація не падає; інтервал рівно ОДИН і ≈4 с", b is not None and b["threw"] is None and b["intervals"] == [4000])
    chk("до першого повідомлення IntersectionObserver карусель вважається «не видно» → не крутиться", b is not None and b["beforeVisible"] == 0)
    chk("видно → кожен тік = ОДИН крок на відстань між слайдами (260 px), smooth", b is not None and b["afterVisible"] == [["by", 260, "smooth"], ["by", 260, "smooth"]])
    if b:
        last = b["afterMore"][-1]
        chk("на краю — плавно на початок (scrollTo left 0, smooth), безкінечного циклу немає (до краю йшли кроками)", last == ["to", 0, "smooth"] and sum(1 for m in b["afterMore"] if m[0] == "to") == 1)
    chk("в аналітику автопрокрутка не пише (жодного select_item)", b is not None and b["gt"] == 0)
    nio = run("noio")
    chk("без IntersectionObserver (старий браузер) карусель крутиться (вважаємо видимою)", nio is not None and nio["moves"] == [["by", 260, "smooth"]])
    h = run("hover")
    chk("pointer-hover мишею → пауза; відвів курсор → крутиться", h is not None and h["hov"] == 0 and h["afterLeave"] == 1)
    chk("touch-«наведення» (pointerType=touch) НЕ блокує автопрокрутку (на телефоні залипало б)", h is not None and h["touchHover"] == 2)
    f = run("focus")
    chk("клавіатурний фокус (:focus-visible) всередині → пауза; blur → крутиться", f is not None and f["kbFocus"] == 0 and f["afterBlur"] == 1)
    chk("фокус після кліку мишею (не :focus-visible) автопрокрутку НЕ зупиняє", f is not None and f["mouseFocus"] == 2)
    t = run("touch")
    chk("палець на екрані → пауза; після відпускання ще 10 с тиші; далі крутиться", t is not None and t["during"] == 0 and t["after5"] == 0 and t["after10"] == 1)
    u = run("user")
    chk("клік по стрілці → 10 с тиші, потім крутиться", u is not None and u["afterArrow"] == 0 and u["afterGrace"] == 1)
    chk("колесо / клавіша → ті самі 10 с тиші", u is not None and u["afterWheel"] == 0 and u["afterKey"] == 0)
    hd = run("hidden")
    chk("вкладка прихована (document.hidden) → не крутиться; повернувся → крутиться", hd is not None and hd["hidden"] == 0 and hd["shown"] == 1)
    o = run("offscreen")
    chk("карусель поза екраном (IO isIntersecting=false) → не крутиться; повернулась → крутиться", o is not None and o["off"] == 0 and o["on"] == 1)
    r = run("reduce")
    chk("prefers-reduced-motion → жодної автопрокрутки", r is not None and r["moves"] == 0)
    one = run("one")
    chk("один слайд → інтервал не створюється", one is not None and one["n"] == 0)
    e = run("end")
    chk("на правому краї → одразу на початок, а не scrollBy за межі", e is not None and e["moves"] == [["to", 0, "smooth"]])

print()
if F:
    print(f"FAILED ({len(F)}):")
    for n in F:
        print("  -", n)
    sys.exit(1)
print("ALL OK")

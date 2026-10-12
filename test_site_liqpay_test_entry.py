#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_site_liqpay_test_entry.py — 12.10.2026 (запит Коду): прихований вхід для ТЕСТОВОЇ оплати LiqPay. Радіо «Оплата карткою онлайн (ТЕСТОВИЙ РЕЖИМ)» є в розмітці кошика лише поки LiqPay не live,
схованим (hidden); розкривається тільки з ?liqpay_test=1 (пам'ятається в sessionStorage). Покупцям без параметра його не видно; у режимі SITE_LIQPAY_LIVE=1 звичайне радіо без тестового."""
import json, os, shutil, subprocess, sys, tempfile
from pathlib import Path

HERE = Path(__file__).parent
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
os.environ.setdefault("AUDIT_NO_TELEGRAM", "1")
os.environ.pop("SITE_LIQPAY_LIVE", None)
sys.path.insert(0, str(HERE / "site"))
sys.path.insert(0, str(HERE))
import build_site as bs  # noqa: E402

F = []


def chk(n, c):
    print(f"[{'OK ' if c else 'FAIL'}] {n}")
    if not c:
        F.append(n)


def cart(live):
    out = Path(tempfile.mkdtemp())
    bs.OUT = str(out)
    bs.LIQPAY_LIVE = live
    bs.write_cart([])
    return (out / "cart.html").read_text(encoding="utf-8")


h = cart(False)
chk("не live: тестове радіо є в розмітці, але hidden, з value=prepaid і чесним підписом «ТЕСТОВИЙ РЕЖИМ — реальних списань немає»", '<label class="pay" id="pay-test" hidden><input type="radio" name="payment" value="prepaid"> Оплата карткою онлайн (ТЕСТОВИЙ РЕЖИМ — реальних списань немає)</label>' in h)
chk("не live: видимого радіо prepaid без «ТЕСТОВИЙ» нема; тексти для покупців не змінено (PAY_HERO/PAY_SHORT лише про накладений платіж)", 'value="prepaid"> Оплата карткою онлайн</label>' not in h and bs.PAY_HERO == "оплата при отриманні на Новій Пошті" and bs.PAY_SHORT == "оплата при отриманні")
hl = cart(True)
chk("live: звичайне радіо «Оплата карткою онлайн» без тестового, #pay-test нема", 'id="pay-test"' not in hl and hl.count('value="prepaid"') == 1 and "ТЕСТОВИЙ" not in hl)
bs.LIQPAY_LIVE = False
css = (HERE / "site/assets/styles.css").read_text(encoding="utf-8")
chk("CSS: .pay[hidden]{display:none} (hidden не перебивається display у .pay)", ".pay[hidden]{display:none}" in css)

node = shutil.which("node")
if not node:
    chk("node доступний", False)
else:
    harness = r'''
const fs=require("fs"), vm=require("vm"); const code=fs.readFileSync(process.argv[2],"utf8"); const search=process.argv[3]; const ssMode=process.argv[4];
function mk(){ const ss={}; const store={getItem:k=>(k in ss?ss[k]:null), setItem:(k,v)=>{ss[k]=String(v)}}; return {ss,store}; }
(async()=>{
  const el={hidden:true, id:"pay-test"}; const ready=[]; const {ss,store}=mk();
  const doc={hidden:false, addEventListener(t,f){ if(t==="DOMContentLoaded") ready.push(f); }, getElementById(id){ return id==="pay-test"?(process.argv[5]==="noel"?null:el):null; }, querySelector(){return null}, querySelectorAll(){return []}, body:{addEventListener(){},appendChild(){}}};
  const ctx={document:doc, localStorage:{getItem(){return null},setItem(){}}, sessionStorage: ssMode==="throw"?{getItem(){throw new Error("x")},setItem(){throw new Error("x")}}:store, console, fetch:()=>Promise.reject(), navigator:{}, location:{pathname:"/cart.html",search:search,href:"/cart.html"+search}, setTimeout, clearTimeout, Date, setInterval:()=>1, addEventListener(){}};
  ctx.window=ctx; ctx.self=ctx; ctx.matchMedia=()=>({matches:false});
  vm.createContext(ctx); vm.runInContext(code,ctx);
  let threw=null; try{ ready.forEach(f=>f()); }catch(e){ threw=String(e); }
  console.log(JSON.stringify({threw, hidden:el.hidden, flag:ss["pt_liqpay_test"]||null}));
})();
'''
    hf = Path(tempfile.mkdtemp()) / "h.js"
    hf.write_text(harness, encoding="utf-8")
    app = str(HERE / "site/assets/app.js")

    def run(search, ss="ok", extra=""):
        r = subprocess.run([node, str(hf), app, search, ss, extra], capture_output=True, text=True, encoding="utf-8", timeout=60)
        try:
            return json.loads(r.stdout.strip().splitlines()[-1])
        except Exception:
            print(r.stdout[-300:], r.stderr[:500])
            return None

    a = run("")
    chk("без параметра: радіо лишається прихованим, прапорець не ставиться", a is not None and a["threw"] is None and a["hidden"] is True and a["flag"] is None)
    b = run("?liqpay_test=1")
    chk("?liqpay_test=1: радіо розкрито, прапорець у sessionStorage", b is not None and b["hidden"] is False and b["flag"] == "1")
    c = run("?x=1&liqpay_test=1&y=2")
    chk("параметр серед інших працює", c is not None and c["hidden"] is False)
    for bad in ("?liqpay_test=0", "?liqpay_test=10", "?liqpay_test", "?xliqpay_test=1"):
        d = run(bad)
        chk(f"{bad}: НЕ розкриває", d is not None and d["hidden"] is True)
    e = run("?liqpay_test=1", "throw")
    chk("sessionStorage кидає виняток (приватний режим): параметр усе одно розкриває, без помилки", e is not None and e["threw"] is None and e["hidden"] is False)
    g = run("?liqpay_test=1", "ok", "noel")
    chk("збірка з LIQPAY_LIVE (елемента нема): без помилки", g is not None and g["threw"] is None)

print()
if F:
    print(f"FAILED ({len(F)}):")
    for n in F:
        print("  -", n)
    sys.exit(1)
print("ALL OK")

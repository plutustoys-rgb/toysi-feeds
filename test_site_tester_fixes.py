#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_site_tester_fixes.py — 12.10.2026, знахідки Тестувальника по живому сайту:
  • кошик (localStorage): qty=-5 / price="abc" / qty 999 не дають «-50 ₴»/«NaN ₴» — read() чистить (qty 1…50, нечислова ціна → позиція відкидається), кількість обмежена серверною межею 50;
  • форма: ім'я без пробілу / телефон «123», «abcdefghij», 9 цифр блокуються ДО відправки з поясненням; нормальні формати проходять; пояснення про ліміт накладеного платежу (3 000 ₴) з'являється в кошику лише коли сума більша;
  • сервер: помилка НП («FindByString is not specified») = порожній результат 200, а не 502 із внутрішнім текстом; інші збої НП — 502 БЕЗ поля detail;
  • розмітка/CSS: catalog select не ширший за екран, липкий хедер не ховає поле, нижній рядок футера не під панеллю «У кошик», «Оформлення» ховається з порожнім кошиком, опечатка «першого брязкальця», description «Контакти» без вигаданих месенджерів/графіка.
Без node частина про поведінку падає ЯВНО."""
import json
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import urllib.error
import urllib.request
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
const code=fs.readFileSync(process.argv[2],"utf8"); const scenario=process.argv[3]; const cartJson=process.argv[4]||"{}";
function store(){ const m={}; return {getItem:k=>(k in m?m[k]:null), setItem:(k,v)=>{m[k]=String(v)}, removeItem:k=>{delete m[k]}, _m:m}; }
function el(id){ const h={}; const e={id,value:"",textContent:"",_inner:"",style:{},disabled:false,classList:{add(){},remove(){},toggle(){},contains(){return false}},addEventListener(t,f){(h[t]=h[t]||[]).push(f)},h,
  set innerHTML(v){this._inner=v}, get innerHTML(){return this._inner}, querySelectorAll(){return []}, querySelector(){return null}, closest(){return null}, getAttribute(){return null}, setAttribute(){}, focus(){ e._focused=true; }}; return e; }
(async()=>{
  const ls=store(), ss=store(); ls.setItem("pt_cart_v1", cartJson);
  const E={}, ready=[], posts=[], docH={};
  ["cart-body","cart-summary","checkout-form","checkout-msg","checkout-submit","f-name","f-phone","f-email","f-city","f-warehouse","ac-city","ac-warehouse","cart-badge","cart-aside","aside-items","aside-goods","aside-total","ship-today","sum-goods","sum-delivery","sum-total","cod-limit-note","checkout-title"].forEach(i=>E[i]=el(i));
  const doc={addEventListener(t,f){ if(t==="DOMContentLoaded") ready.push(f); else (docH[t]=docH[t]||[]).push(f); }, getElementById(id){return E[id]||null}, querySelector(sel){ if(sel==='input[name="payment"]:checked') return {value:"cod"}; return null; },
    querySelectorAll(){return []}, body:{addEventListener(t,f){ (docH[t]=docH[t]||[]).push(f); },appendChild(){}}, createElement(){return {style:{},appendChild(){}}}};
  const fetchStub=function(url, init){ if(String(url).indexOf("api/order")===0){ posts.push(JSON.parse(init.body)); return Promise.resolve({ok:true, json(){return Promise.resolve({order_id:"PT-T-1", liqpay:null})}}); } return Promise.resolve({ok:true, json(){return Promise.resolve({cities:[],warehouses:[]})}}); };
  const ctx={document:doc, localStorage:ls, sessionStorage:ss, console, fetch:fetchStub, navigator:{}, location:{href:"cart.html",pathname:"/cart.html"}, setTimeout, clearTimeout, setInterval:function(){return 1;}, addEventListener(){}};
  ctx.window=ctx; ctx.self=ctx; ctx.matchMedia=function(){ return {matches:false}; };
  vm.createContext(ctx); vm.runInContext(code,ctx);
  ready.forEach(f=>f());
  const out={};
  const sleep=ms=>new Promise(x=>setTimeout(x,ms));
  if(scenario==="read"){ out.cart=ctx.PT.read(); out.count=ctx.PT.count(); out.total=ctx.PT.total(); }
  else if(scenario==="codnote"){ out.display=E["cod-limit-note"].style.display; out.title=E["checkout-title"].style.display; }
  else if(scenario==="empty"){ out.title=E["checkout-title"].style.display; out.form=E["checkout-form"].style.display; }
  else if(scenario.startsWith("submit")){
    const [,nm,ph]=JSON.parse(process.argv[5]);
    E["f-name"].value=nm; E["f-phone"].value=ph; E["f-warehouse"].value="відділення 5"; E["f-city"].value="Київ";
    (E["checkout-form"].h.submit||[]).forEach(f=>f({preventDefault(){}})); await sleep(80);
    out.posts=posts.length; out.msg=E["checkout-msg"].textContent; out.focusName=!!E["f-name"]._focused; out.focusPhone=!!E["f-phone"]._focused;
  }
  console.log(JSON.stringify(out));
})();
'''
    tmp = Path(tempfile.mkdtemp())
    hf = tmp / "h.js"
    hf.write_text(harness, encoding="utf-8")
    app = str(HERE / "site" / "assets" / "app.js")

    def run(sc, cart, extra="[]"):
        r = subprocess.run([node, str(hf), app, sc, json.dumps(cart, ensure_ascii=False), extra], capture_output=True, text=True, encoding="utf-8", timeout=60)
        try:
            return json.loads(r.stdout.strip().splitlines()[-1])
        except Exception:
            print("   stdout:", r.stdout[-300:], "stderr:", r.stderr[:900])
            return None

    good = {"1": {"id": "1", "name": "Картина", "price": 468, "photo": "x", "qty": 2}}
    dirty = {"a": {"id": "a", "name": "Мінус", "price": 100, "photo": "", "qty": -5}, "b": {"id": "b", "name": "NaN", "price": "abc", "photo": "", "qty": 1},
             "c": {"id": "c", "name": "Багато", "price": 10, "photo": "", "qty": 999}, "d": {"id": "d", "name": "Дроб", "price": 10, "photo": "", "qty": 2.7}, "e": "рядок", "f": None, "g": {"id": "g", "name": "Нуль", "price": 5, "photo": "", "qty": 0}}
    r = run("read", dirty)
    chk("кошик з localStorage: qty -5 → 1 (не «-50 ₴»), ціна «abc» → позицію відкинуто (не «NaN ₴»), qty 999 → 50, 2,7 → 2, 0 → 1, не-обʼєкти відкинуто",
        r is not None and r["cart"]["a"]["qty"] == 1 and "b" not in r["cart"] and r["cart"]["c"]["qty"] == 50 and r["cart"]["d"]["qty"] == 2 and r["cart"]["g"]["qty"] == 1 and "e" not in r["cart"] and "f" not in r["cart"])
    chk("лічильник і сума після очищення — числа (жодного NaN/від'ємного)", r is not None and r["count"] == 1 + 50 + 2 + 1 and r["total"] == 100 + 500 + 20 + 5)
    chk("«мусор» верхнього рівня (масив/рядок у localStorage) → порожній кошик без винятку", all(run("read", x) is not None and run("read", x)["count"] == 0 for x in ([1, 2], "рядок", None)))
    big = {"1": {"id": "1", "name": "Дорога", "price": 1700, "photo": "", "qty": 2}}
    c = run("codnote", big)
    chk("сума 3 400 ₴ > 3 000: пояснення про ліміт накладеного платежу показано; «Оформлення» видиме", c is not None and c["display"] == "" and c["title"] == "")
    c2 = run("codnote", good)
    chk("сума 936 ₴ ≤ 3 000: пояснення про ліміт сховано", c2 is not None and c2["display"] == "none")
    c3 = run("codnote", {"1": {"id": "1", "name": "Рівно", "price": 3000, "photo": "", "qty": 1}})
    chk("рівно 3 000 ₴ (межа сервера: >3 000) — пояснення сховано", c3 is not None and c3["display"] == "none")
    em = run("empty", {})
    chk("порожній кошик: заголовок «Оформлення» сховано разом із формою", em is not None and em["title"] == "none" and em["form"] == "none")
    for nm, ph, label in (("Іван", "0501234567", "імʼя з одного слова"), ("   ", "0501234567", "імʼя з пробілів"), ("Іван Петренко", "123", "телефон «123»"), ("Іван Петренко", "abcdefghij", "телефон з літер"),
                          ("Іван Петренко", "050123456", "телефон 9 цифр"), ("Іван Петренко", "0501234567890123", "телефон 16 цифр"), ("Іван Петренко", "+1 202 555 0123", "не український телефон"), ("Іван Петренко", "", "порожній телефон")):
        s = run("submit", good, json.dumps(["x", nm, ph], ensure_ascii=False))
        chk(f"блок ДО відправки: {label} → 0 запитів, пояснення, фокус на полі", s is not None and s["posts"] == 0 and ("ім" in s["msg"] or "телефон" in s["msg"]) and (s["focusName"] or s["focusPhone"]))
    for ph in ("0501234567", "050 123 45 67", "+380 50 123 45 67", "+38 (050) 123-45-67", "050-123-45-67", "380501234567"):
        s = run("submit", good, json.dumps(["x", "Іван Петренко", ph], ensure_ascii=False))
        chk(f"телефон «{ph}» проходить валідацію (запит пішов)", s is not None and s["posts"] == 1)
    s = run("submit", good, json.dumps(["x", "Петренко Іван Олексійович", "0501234567"], ensure_ascii=False))
    chk("ПІБ з трьох слів проходить", s is not None and s["posts"] == 1)

# сервер: помилки НП
sys.path.insert(0, str(HERE))
import site_order_api as so  # noqa: E402
from http.server import ThreadingHTTPServer  # noqa: E402

httpd = ThreadingHTTPServer(("127.0.0.1", 0), so.Handler)
port = httpd.server_address[1]
threading.Thread(target=httpd.serve_forever, daemon=True).start()


def get(path):
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=10) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode("utf-8"))


def raising(msg):
    def f(*a, **k):
        raise so.nova_poshta.NovaPoshtaAPIError(msg)
    return f


orig_c, orig_w = so.nova_poshta.search_cities, so.nova_poshta.search_warehouses
so.nova_poshta.search_cities = raising("API повернув помилку: ['FindByString is not specified']")
st, body = get("/api/np/city?q=Kyiv")
chk("/api/np/city: «FindByString is not specified» → 200 і порожній список (не 502)", st == 200 and body == {"cities": []})
so.nova_poshta.search_warehouses = raising("API повернув помилку: ['CityRef is not specified']")
st, body = get("/api/np/warehouse?city_ref=bad")
chk("/api/np/warehouse: «CityRef is not specified» → 200 і порожній список", st == 200 and body == {"warehouses": []})
so.nova_poshta.search_cities = raising("API повернув помилку: ['Internal error 500 secret-detail']")
st, body = get("/api/np/city?q=%D0%9A%D0%B8%D1%97%D0%B2")
chk("інші збої НП → 502 БЕЗ поля detail (внутрішній текст не віддається покупцеві)", st == 502 and "detail" not in body and "secret" not in json.dumps(body, ensure_ascii=False) and body.get("error"))
so.nova_poshta.search_cities, so.nova_poshta.search_warehouses = orig_c, orig_w
httpd.shutdown()

# розмітка / CSS / тексти
css = (HERE / "site" / "assets" / "styles.css").read_text(encoding="utf-8")
src = (HERE / "site" / "build_site.py").read_text(encoding="utf-8")
chk("CSS: #cf-cat не ширший за екран (max-width:100%)", re.search(r"#cf-cat\{[^}]*max-width:100%", css) is not None)
chk("CSS: scroll-margin-top для полів (липкий хедер не ховає поле)", re.search(r"input,select,textarea\{scroll-margin-top:84px\}", css) is not None)
chk("CSS: нижній рядок футера не під панеллю «У кошик» (body:has(.buybar) .foot margin-bottom)", re.search(r"body:has\(\.buybar\) \.foot\{margin-bottom:\d+px\}", css) is not None)
chk("форма: #checkout-title і #cod-limit-note в розмітці кошика", 'id="checkout-title"' in src and 'id="cod-limit-note"' in src and "до 3 000 ₴" in src)
chk("опечатка «від першої брязкальця» виправлена → «від першого брязкальця»", "від першої брязкальця" not in src and "від першого брязкальця" in src)
chk("description «Контакти» не обіцяє месенджери й графік роботи (їх на сторінці нема)", "месенджери" not in src.split("Контакти інтернет-магазину PlutusToys")[1][:120] and "графік роботи" not in src.split("Контакти інтернет-магазину PlutusToys")[1][:120])

print()
if F:
    print(f"FAILED ({len(F)}):")
    for n in F:
        print("  -", n)
    sys.exit(1)
print("ALL OK")

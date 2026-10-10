#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_site_search.py — пошук сайту (зауваження Тестувальника/Консультанта 10.10.2026): токени AND у будь-якому порядку («лялька барбі» ≠ 0),
апострофи U+02BC/U+2019/U+0027 зведені («мʼяка»=«м'яка»=«мяка»), лічильник «Показано N з M» і сортування в накладці.
Логіка перевіряється в node (vm зі стабами window/document), решта — статично. Без node — лише статика."""
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
F = []


def chk(n, c):
    print(f"[{'OK ' if c else 'FAIL'}] {n}")
    if not c:
        F.append(n)


JS = os.path.join(HERE, "site", "assets", "app.js")
src = open(JS, encoding="utf-8").read()
css = open(os.path.join(HERE, "site", "assets", "styles.css"), encoding="utf-8").read()
chk("лічильник «Показано N з M» і три сортування в рендері", "Показано '+out.length+' з '+lastMatches.length" in src and all(x in src for x in ('"cheap","Дешевші"', '"dear","Дорожчі"', '"rec","Рекомендовані"')))
chk("клік по [data-sort] перемальовує без перепошуку", 'closest("#search-overlay [data-sort]")' in src and "renderSearch(); return;" in src)
chk("стилі .sr-meta/.sr-sort є", ".sr-meta{" in css and ".sr-sort button.on{" in css)
chk("літеральний підрядок цілого запиту прибрано", "indexOf(q)>=0" not in src)

node = shutil.which("node")
if node:
    harness = r'''
const fs=require("fs"), vm=require("vm");
const code=fs.readFileSync(process.argv[2],"utf8");
const doc={addEventListener(){},getElementById(){return null},querySelector(){return null},querySelectorAll(){return []},body:{addEventListener(){}},createElement(){return {style:{}}}};
const win={}; const ctx={window:win,document:doc,localStorage:{getItem(){return null},setItem(){}},sessionStorage:{getItem(){return null},setItem(){}},console,fetch(){return Promise.reject()},navigator:{}};
ctx.self=ctx; vm.createContext(ctx); vm.runInContext(code,ctx);
const S=win.PT.search;
const names=["Лялька Барбі в сукні","Барбі та лялька-пупс","Машинка поліцейська","Мʼяка іграшка Зайчик","М'яка іграшка Ведмідь","Мя\u2019ка іграшка Кіт","Конструктор для хлопчика"];
const f=q=>{const t=S.tokens(q);return names.filter(n=>S.match(S.norm(n),t)).length;};
console.log(JSON.stringify({a:f("лялька барбі"),b:f("барбі лялька"),c:f("машинка поліція"),d:f("мʼяка іграшка"),e:f("м'яка іграшка"),g:f("мяка іграшка"),h:f("іграшка для хлопчика"),i:f("ЛЯЛЬКА  БАРБІ "),j:f("лялька барбі драконів"),k:f("конструктор хлопчика")}));
'''
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as fh:
        fh.write(harness)
        hp = fh.name
    r = subprocess.run([node, hp, JS], capture_output=True, text=True, encoding="utf-8")
    os.unlink(hp)
    if r.returncode != 0:
        chk("node-харнес виконався: " + (r.stderr or "")[-200:], False)
    else:
        o = json.loads(r.stdout.strip().splitlines()[-1])
        chk("«лялька барбі» і «барбі лялька» → 2 (обидва порядки)", o["a"] == 2 and o["b"] == 2)
        chk("«машинка поліція» → 0 лише тому, що в тестових назвах нема слова з підрядком «поліція» («поліцейська» його не містить) — токени AND, не нечіткий пошук", o["c"] == 0)
        chk("апострофи: «мʼяка/м'яка/мяка іграшка» → однаково 3", o["d"] == 3 and o["e"] == 3 and o["g"] == 3)
        chk("«іграшка для хлопчика» → 0 (жодна назва не має усіх трьох токенів; AND)", o["h"] == 0)
        chk("регістр і зайві пробіли ігноруються", o["i"] == 2)
        chk("зайвий токен, якого нема ніде → 0 (AND, не OR)", o["j"] == 0)
        chk("«конструктор хлопчика» → 1", o["k"] == 1)
else:
    print("(node не знайдено — логіку перевірено лише статично)")
print()
if F:
    print("FAILED:", F)
    sys.exit(1)
print("ALL OK")

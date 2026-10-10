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
const items=[
 {n:"Лялька Барбі в сукні",c:"Ляльки"},{n:"Барбі та лялька-пупс",c:"Ляльки"},{n:"Машинка поліцейська",c:"Машинки"},
 {n:"Мʼяка іграшка Зайчик",c:"Мʼякі іграшки"},{n:"М'яка іграшка Ведмідь",c:"Мʼякі іграшки"},{n:"Мя’ка іграшка Кіт",c:"Мʼякі іграшки"},
 {n:"Конструктор для хлопчика",c:"Конструктори"},{n:"Машина на радіокеруванні",c:"Радіокеровані машини"},
 {n:"Barbie Dreamhouse",c:"Ляльки"},{n:"Funko POP Marvel Spider-Man",c:"Фігурки"},{n:"Фанко Поп Людина-павук",c:"Фігурки"},
 {n:"Hot Wheels набір треку",c:"Машинки"},{n:"Хот Вілс Монстер",c:"Машинки"},{n:"Солодкий лол набір",c:"Різне"},{n:"L.O.L. Surprise лялька",c:"Ляльки"},{n:"Людина-павук костюм",c:"Костюми"}];
const f=q=>{const t=S.tokens(q);return items.filter(it=>S.match(S.hay(it),t)).length;};
console.log(JSON.stringify({a:f("лялька барбі"),b:f("барбі лялька"),c:f("машинка поліція"),d:f("мʼяка іграшка"),e:f("м'яка іграшка"),g:f("мяка іграшка"),h:f("іграшка для хлопчика"),i:f("ЛЯЛЬКА  БАРБІ "),j:f("лялька барбі драконів"),k:f("конструктор хлопчика"),
 cat:f("радіокерована машина"),emp:S.tokens("''").length,funkoL:f("funko"),funkoC:f("фанко"),barL:f("barbie"),barC:f("барбі"),hwL:f("hot wheels"),hwC:f("хот вілс"),spC:f("павук"),spL:f("spider"),lolC:f("лол"),lolL:f("lol"),lolD:f("l.o.l")}));
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
        chk("«лялька барбі» і «барбі лялька» → 3 в обох порядках (Лялька Барбі, Барбі та лялька-пупс, Barbie Dreamhouse з категорії «Ляльки»)", o["a"] == 3 and o["b"] == 3)
        chk("«машинка поліція» → 1: основа «поліц» знаходить «поліцейська» (закінчення відкидаються)", o["c"] == 1)
        chk("апострофи: «мʼяка/м'яка/мяка іграшка» → однаково 3", o["d"] == 3 and o["e"] == 3 and o["g"] == 3)
        chk("«іграшка для хлопчика» → 0 (жодна назва/категорія не має всіх токенів; AND)", o["h"] == 0)
        chk("регістр і зайві пробіли ігноруються", o["i"] == 3)
        chk("зайвий токен, якого нема ніде → 0 (AND, не OR)", o["j"] == 0)
        chk("«конструктор хлопчика» → 1", o["k"] == 1)
        chk("КАТЕГОРІЯ в haystack: «радіокерована машина» знаходить товар із категорії «Радіокеровані машини» (було 0)", o["cat"] == 1)
        chk("запит лише з апострофів → нуль токенів (не «весь каталог»)", o["emp"] == 0)
        chk("теги брендів не колізять префіксом (#b1 ≠ #b10…): «фанко» не підтягує «Людина-павук костюм»", o["funkoC"] == 2)
        chk("бренд обома абетками: funko↔фанко, barbie↔барбі, hot wheels↔хот вілс, spider↔павук, lol↔лол↔l.o.l — однакові результати",
            o["funkoL"] == o["funkoC"] == 2 and o["barL"] == o["barC"] == 3 and o["hwL"] == o["hwC"] == 2 and o["spL"] == o["spC"] == 3 and o["lolC"] == o["lolL"] == o["lolD"] == 2)
else:
    print("(node не знайдено — логіку перевірено лише статично)")
print()
if F:
    print("FAILED:", F)
    sys.exit(1)
print("ALL OK")

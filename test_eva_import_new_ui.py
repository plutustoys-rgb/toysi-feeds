#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_eva_import_new_ui.py — eva_cabinet_scraper.trigger_full_import на двох верстках кабінету EVA (локальний HTML, без мережі).
Контекст: 26.10.2026 (між 05:50 і 11:50) EVA прибрала поле «Введіть адресу XML файлу»; адреса збережена текстом, запуск — кнопка «Оновити каталог».
Інваріанти: нова верстка → чекбокс модерації ставиться, DRY-RUN кнопку НЕ тисне, APPLY тисне; збережена адреса ≠ наш фід → EvaCabinetError і кнопку не тисне;
нема ні поля, ні кнопки → EvaCabinetError; стара верстка (поле) працює як раніше. `python test_eva_import_new_ui.py` → exit 0/1.
"""
import os
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
os.environ["AUDIT_NO_TELEGRAM"] = "1"
sys.path.insert(0, str(Path(__file__).parent))

from playwright.sync_api import sync_playwright  # noqa: E402
import eva_cabinet_scraper as ev  # noqa: E402

F = []


def chk(n, c):
    print(f"[{'OK ' if c else 'FAIL'}] {n}")
    if not c:
        F.append(n)


FEED = "https://raw.githubusercontent.com/plutustoys-rgb/toysi-feeds/feed-data/feeds/eva_feed.xml"
CLICK = "<script>document.addEventListener('submit',e=>{e.preventDefault();window.__sent=(window.__sent||0)+1;});</script>"

NEW = f"""<html><body>
<form><label><input type=radio name=import_method_choice value=file> Через файл</label>
<label><input type=radio name=import_method_choice value=url checked> За посиланням</label></form>
<form>Адреса XML файлу<div>{{url}}</div>
<label><input type=checkbox name=send_new_to_moderation> Відправити нові товари з співставленою категорією на модерацію</label>
<button type=submit>Оновити каталог</button></form>{CLICK}</body></html>"""

OLD = f"""<html><body><form>
<label><input type=radio name=m> через посилання</label>
<label>Введіть адресу XML файлу <input type=text aria-label="Введіть адресу XML файлу"></label>
<label><input type=checkbox name=cb> Відправити нові товари на модерацію</label>
<button type=submit>Почати</button></form>{CLICK}</body></html>"""

NEW_HIST = f"""<html><body>
<form><label><input type=radio name=import_method_choice value=url checked> За посиланням</label></form>
<form>Адреса XML файлу<div>https://example.com/other.xml</div>
<label><input type=checkbox name=send_new_to_moderation> модерація</label>
<button type=submit>Оновити каталог</button></form>
<div>Історія: {FEED}</div>{CLICK}</body></html>"""

NONE = "<html><body><p>Нічого</p></body></html>"


def run(html, apply):
    with sync_playwright() as p:
        b = p.chromium.launch(headless=True)
        page = b.new_page()
        page.goto("about:blank")
        page.set_content(html)
        page.goto = lambda *a, **k: None            # trigger_full_import робить goto на IMPORTS_NEW_URL — тут верстка вже в сторінці
        page.wait_for_timeout = lambda *a, **k: None
        ev._ensure_session = lambda pg: None
        try:
            res = ev.trigger_full_import(page, FEED, apply)
            err = None
        except ev.EvaCabinetError as e:
            res, err = None, str(e)
        sent = page.evaluate("window.__sent||0")
        checked = page.evaluate("(()=>{const c=document.querySelector('input[type=checkbox]');return c?c.checked:null})()")
        b.close()
    return res, err, sent, checked


r, e, sent, ck = run(NEW.replace("{url}", FEED), apply=False)
chk("нова верстка, DRY-RUN: чекбокс поставлено, кнопку НЕ натиснуто", r is not None and r["moderation_checked"] is True and ck is True and sent == 0 and r["submitted"] is False)
r, e, sent, ck = run(NEW.replace("{url}", FEED), apply=True)
chk("нова верстка, APPLY: кнопку натиснуто 1 раз, submitted=True", r is not None and r["submitted"] is True and sent == 1 and r["moderation_checked"] is True)
r, e, sent, ck = run(NEW.replace("{url}", "https://example.com/other.xml"), apply=True)
chk("збережена адреса ≠ наш фід → EvaCabinetError, кнопку не натиснуто", r is None and e and "не збігається" in e and sent == 0)
r, e, sent, ck = run(NEW_HIST, apply=True)
chk("наш URL є лише в «історії» поза формою, у формі інша адреса → відмова, кнопку не натиснуто", r is None and e and "не збігається" in e and sent == 0)
r, e, sent, ck = run(NEW.replace("{url}", FEED + ".old"), apply=True)
chk("збережена адреса — довший URL (…eva_feed.xml.old) → відмова", r is None and e and "не збігається" in e and sent == 0)
r, e, sent, ck = run(NEW.replace("{url}", "«" + FEED + "»"), apply=True)
chk("адреса в лапках-ялинках (як у тексті «Остання зміна») → збіг, запуск", r is not None and r["submitted"] is True and sent == 1)
r, e, sent, ck = run(NONE, apply=True)
chk("нема ні поля, ні кнопки → EvaCabinetError", r is None and e and "Оновити каталог" in e and sent == 0)
r, e, sent, ck = run(OLD, apply=True)
chk("стара верстка (поле + «Почати») працює як раніше", r is not None and r["submitted"] is True and sent == 1)

sys.exit(1 if F else 0)

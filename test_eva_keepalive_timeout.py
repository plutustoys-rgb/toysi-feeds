#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_eva_keepalive_timeout.py — eva_cabinet_scraper.keepalive(): таймаут кабінету ≠ протухла сесія.

Факт 22.10: keepalive впав на goto «Timeout 30000ms» (ПК 16:24, VPS 18:20), а алерт казав «сесія протухла, перелогінься».
ІНВАРІАНТИ:
  - один таймаут → друга спроба з ПОДВОЄНИМ таймаутом, сесія оновлена (storage_state збережено), без алерту й без exit;
  - два таймаути підряд → exit 1 і алерт, що це збій мережі/кабінету, а НЕ доказ протухлої сесії;
  - справжня протухла сесія (EvaCabinetError із _ensure_session) → БЕЗ повтору goto, алерт «сесія протухла»;
  - нема файла сесії → як і раніше exit 1 з алертом.
Без мережі й браузера: sync_playwright підмінено.
"""
import sys
import tempfile
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
import eva_cabinet_scraper as ev

F = []


def chk(n, c):
    print(f"[{'OK ' if c else 'FAIL'}] {n}")
    if not c:
        F.append(n)


class FakePage:
    def __init__(self, goto_plan):
        self.plan = list(goto_plan)
        self.timeouts = []

    def goto(self, url, timeout=None, wait_until=None):
        self.timeouts.append(timeout)
        step = self.plan.pop(0)
        if step == "timeout":
            raise ev.PlaywrightTimeoutError("Page.goto: Timeout")

    def wait_for_timeout(self, ms):
        pass


class FakeCtx:
    def __init__(self, page):
        self.page = page
        self.saved = False

    def new_page(self):
        return self.page

    def storage_state(self, path=None):
        self.saved = True


class FakeBrowser:
    def __init__(self, ctx):
        self.ctx = ctx

    def new_context(self, storage_state=None):
        return self.ctx

    def close(self):
        pass


class FakePW:
    def __init__(self, page):
        self.ctx = FakeCtx(page)
        self.chromium = self

    def launch(self, headless=True):
        return FakeBrowser(self.ctx)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


tmp = Path(tempfile.mkdtemp())
state = tmp / "state.json"
state.write_text("{}", encoding="utf-8")
ev.STATE_FILE = state
notes = []
ev._notify = lambda m: notes.append(m)


def run(goto_plan, session_ok=True):
    notes.clear()
    page = FakePage(goto_plan)
    pw = FakePW(page)
    ev.sync_playwright = lambda: pw

    def ensure(_page):
        if not session_ok:
            raise ev.EvaCabinetError("редірект на логін")
    ev._ensure_session = ensure
    code = None
    try:
        ev.keepalive()
    except SystemExit as e:
        code = e.code
    return code, page, pw


code, page, pw = run(["timeout", "ok"])
chk("один таймаут: друга спроба з подвоєним таймаутом", page.timeouts == [ev.NAV_TIMEOUT_MS, ev.NAV_TIMEOUT_MS * 2])
chk("один таймаут: сесію оновлено, exit нема, алерту нема", code is None and pw.ctx.saved and notes == [])

code, page, pw = run(["timeout", "timeout"])
chk("два таймаути: exit 1, сесію НЕ перезаписано", code == 1 and not pw.ctx.saved and len(page.timeouts) == 2)
chk("два таймаути: рівно один алерт, і він каже «НЕ доказ протухлої сесії», без вимоги --login",
    len(notes) == 1 and "НЕ доказ" in notes[0] and "--login" not in notes[0] and "таймаут" in notes[0])

code, page, pw = run(["ok"], session_ok=False)
chk("справжня протухла сесія: goto лише раз (без повтору), exit 1", code == 1 and page.timeouts == [ev.NAV_TIMEOUT_MS])
chk("справжня протухла сесія: алерт «сесія протухла» + --login", len(notes) == 1 and "сесія протухла" in notes[0] and "--login" in notes[0])

state.unlink()
notes.clear()
code = None
try:
    ev.keepalive()
except SystemExit as e:
    code = e.code
chk("нема файла сесії: exit 1 з алертом --login", code == 1 and len(notes) == 1 and "--login" in notes[0])

sys.exit(1 if F else 0)

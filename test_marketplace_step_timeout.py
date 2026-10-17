#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_marketplace_step_timeout.py — сторожовий таймер кроків run_marketplace_actions._run (16-17.10: eva_category_check завис ~17 год і заблокував ланцюг).
Інваріанти: (1) швидкий крок → код 0, вивід збережено; (2) крок без завершення → знято за timeout, код 124, ЛАНЦЮГ ПОВЕРТАЄТЬСЯ (не висить);
(3) дочірній процес завислого кроку теж знято; (4) запис у канал: один на добу на крок, вставка перед першим рядком «## », блок-цитата ціла; нема каналу → False без винятку.
`python test_marketplace_step_timeout.py` → exit 0/1."""
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).parent))
import run_marketplace_actions as r  # noqa: E402

F = []


def chk(n, c):
    print(f"[{'OK ' if c else 'FAIL'}] {n}")
    if not c:
        F.append(n)


d = Path(tempfile.mkdtemp())
r.CHANNEL_FILE = d / "EXECUTOR_CHANNEL.md"
r.CHANNEL_FILE.write_text("<!-- шапка -->" + chr(10) + "> ## 📇 адреси" + chr(10) + chr(10) + "## [Хтось → Код] старе" + chr(10) + "текст" + chr(10), encoding="utf-8")
chk("швидкий крок: код 0", r._run("швидкий", [sys.executable, "-c", "print('ok')"], timeout=20) == 0)

pidfile = d / "child.pid"
hang = ("import subprocess,sys,time;"
        "c=subprocess.Popen([sys.executable,'-c','import time;time.sleep(300)']);"
        f"open(r'{pidfile}','w').write(str(c.pid));"
        "time.sleep(300)")
t0 = time.time()
rc = r._run("завислий", [sys.executable, "-c", hang], timeout=4)
dt = time.time() - t0
chk(f"завислий крок знято за timeout: код 124, повернулись за {dt:.1f} с (не 300)", rc == 124 and dt < 40)
time.sleep(1)
alive = False
try:
    cpid = int(pidfile.read_text())
    out = subprocess.run(["tasklist", "/FI", f"PID eq {cpid}", "/NH"], capture_output=True, text=True).stdout
    alive = str(cpid) in out
except Exception:  # noqa: BLE001
    alive = False
chk("дочірній процес завислого кроку теж знято", pidfile.exists() and not alive)
txt = r.CHANNEL_FILE.read_text(encoding="utf-8")
chk("у каналі рівно один запис про завислий крок; перед першим «## »; блок-цитата ціла", txt.count("MarketplaceActions: крок «завислий» завис") == 1 and txt.index("MarketplaceActions") < txt.index("## [Хтось → Код] старе") and "> ## 📇 адреси" in txt)
chk("повторний запис за ту саму добу не дублюється", r._post_hang("завислий", 4) is False and r.CHANNEL_FILE.read_text(encoding="utf-8").count("«завислий»") == 1)
chk("нема каналу → False без винятку", r._post_hang("x", 4, d / "нема.md") is False)
# закінчення рядків: LF-файл лишається LF (read_text+write_text на Windows перетворювали весь файл на CRLF), CRLF-файл лишається CRLF
lf = d / "lf.md"
lf.write_bytes("## [A → B] старе\nтекст\n".encode("utf-8"))
r._post_hang("крок-lf", 4, lf)
bl = lf.read_bytes()
chk("LF-канал після запису: жодного CR", b"\r" not in bl and "крок-lf".encode("utf-8") in bl)
crlf = d / "crlf.md"
crlf.write_bytes("## [A → B] старе\r\nтекст\r\n".encode("utf-8"))
r._post_hang("крок-crlf", 4, crlf)
bc = crlf.read_bytes()
chk("CRLF-канал після запису: усі переводи рядків CRLF, голих LF нема", bc.count(b"\n") == bc.count(b"\r\n") and b"\r\n" in bc)
print("\nFAIL:" if F else "\nOK: усі перевірки пройдені", *F, sep="\n")
sys.exit(1 if F else 0)

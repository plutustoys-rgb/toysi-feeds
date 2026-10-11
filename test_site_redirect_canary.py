#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_site_redirect_canary.py — site_redirect_canary.py: канарка «покритий Prom-ID → 301 на картку, вигаданий → 410, ціль → 200» після кожного ребілду.
Локальний HTTP-сервер імітує Apache у різних станах: норма; усе 410 (порожня мапа); усе 301 на /catalog.html (старе правило); 301 не туди; ціль мапи 404;
сайт недоступний. Реальні алерти НЕ шлемо (telegram_notify.send_throttled_alert підмінено). Самодостатній."""
import os
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

HERE = Path(__file__).parent
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, str(HERE))
import telegram_notify  # noqa: E402

ALERTS = []
telegram_notify.send_throttled_alert = lambda *a, **k: (ALERTS.append((a, k)) or True)
import site_redirect_canary as sc  # noqa: E402

F = []


def chk(n, c):
    print(f"[{'OK ' if c else 'FAIL'}] {n}")
    if not c:
        F.append(n)


MODE = {"v": "normal"}
BASE_URL = {}


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        m = MODE["v"]
        p = self.path
        host = f"http://127.0.0.1:{self.server.server_port}"
        if "/p999999999999-" in p:                                   # вигаданий Prom-ID
            code = {"normal": 410, "all410": 410, "to_catalog": 301, "wrong_target": 410, "target404": 410, "fake200": 200}[m]
            self.send_response(code)
            if code == 301:
                self.send_header("Location", f"{host}/catalog.html")
            self.end_headers()
            return
        if "/p1001-" in p:                                           # покритий ключ мапи
            if m == "all410":
                self.send_response(410); self.end_headers(); return
            if m == "to_catalog":
                self.send_response(301); self.send_header("Location", f"{host}/catalog.html"); self.end_headers(); return
            if m == "wrong_target":
                self.send_response(301); self.send_header("Location", f"{host}/product-999.html"); self.end_headers(); return
            self.send_response(301); self.send_header("Location", f"{host}/product-7.html"); self.end_headers(); return
        if p == "/product-7.html":
            self.send_response(404 if m == "target404" else 200); self.end_headers(); return
        self.send_response(404); self.end_headers()


srv = HTTPServer(("127.0.0.1", 0), H)
threading.Thread(target=srv.serve_forever, daemon=True).start()
base = f"http://127.0.0.1:{srv.server_port}"

tmp = Path(tempfile.mkdtemp())
mapf = tmp / "prom_redirects_apache.map"
mapf.write_text("# header\n1001 /product-7.html\n1002 /product-8.html\n", encoding="utf-8")
chk("first_map_entry: перший запис мапи (коментар пропущено)", sc.first_map_entry(mapf) == ("1001", "/product-7.html"))
empty = tmp / "empty.map"
empty.write_text("# header only\n", encoding="utf-8")
chk("first_map_entry: порожня мапа → None; відсутній файл → None", sc.first_map_entry(empty) is None and sc.first_map_entry(tmp / "nope.map") is None)

ok, probs = sc.run(base, mapf)
chk("норма: покритий → 301 на /product-7.html, ціль 200, вигаданий → 410 → ok", ok and probs == [])
MODE["v"] = "all410"
ok, probs = sc.run(base, mapf)
chk("усе 410 (порожня/зіпсована мапа на проді) → проблема «очікую 301, отримано 410»", not ok and any("очікую 301, отримано 410" in p for p in probs))
MODE["v"] = "to_catalog"
ok, probs = sc.run(base, mapf)
chk("старе правило (непокритий → 301 на каталог) → проблема «вигаданий … очікую 410»", not ok and any("очікую 410" in p for p in probs))
MODE["v"] = "wrong_target"
ok, probs = sc.run(base, mapf)
chk("301 веде не на ціль мапи → проблема", not ok and any("веде на" in p for p in probs))
MODE["v"] = "target404"
ok, probs = sc.run(base, mapf)
chk("ціль мапи віддає 404 → проблема", not ok and any("віддає 404" in p for p in probs))
MODE["v"] = "normal"
ok, probs = sc.run(base, empty)
chk("порожня мапа на диску → проблема (Apache дасть 410 для всіх)", not ok and any("порожня" in p for p in probs))

# ── main(): алерти
os.environ["SITE_CANARY_BASE"] = base
sc.SITE_BASE = base
sc.MAP_FILE = mapf
ALERTS.clear()
chk("main(): норма → код 0, алертів нема", sc.main() == 0 and ALERTS == [])
MODE["v"] = "all410"
chk("main(): проблема → код 1 і ОДИН тротлений алерт site_redirect_canary (cooldown 6 год)", sc.main() == 1 and len(ALERTS) == 1
    and ALERTS[0][0][0] == "site_redirect_canary" and ALERTS[0][1].get("cooldown_sec") == 6 * 3600)
MODE["v"] = "normal"
srv.shutdown()
srv.server_close()
ALERTS.clear()
sc.SITE_BASE = "http://127.0.0.1:1"      # порт без сервера
chk("main(): сайт недоступний → код 1 і алерт site_redirect_canary_unreachable", sc.main() == 1 and len(ALERTS) == 1 and ALERTS[0][0][0] == "site_redirect_canary_unreachable")

svc = (HERE / "deploy" / "site-rebuild.service").read_text(encoding="utf-8")
chk("site-rebuild.service: канарка — ExecStartPost=- ПІСЛЯ generate_prom_redirects (не фатальна)",
    "ExecStartPost=-/opt/plutustoys/venv/bin/python3 /opt/plutustoys/site_redirect_canary.py" in svc
    and svc.index("generate_prom_redirects.py") < svc.index("site_redirect_canary.py"))

print()
if F:
    print(f"FAILED ({len(F)}):")
    for n in F:
        print("  -", n)
    sys.exit(1)
print("ALL OK")

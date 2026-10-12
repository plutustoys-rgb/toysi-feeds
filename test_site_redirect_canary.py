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
GMODE = {"v": "ok"}      # режим правила груп Prom (12.10.2026): ok | to_catalog (старе правило) | wrong (не та категорія) | query (?query не відкинуто) | gone
BASE_URL = {}


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        m = MODE["v"]
        p = self.path
        host = f"http://127.0.0.1:{self.server.server_port}"
        if "/g155145045-" in p:                                      # група Prom, перший рядок мапи груп
            gm = GMODE["v"]
            if gm == "gone":
                self.send_response(410); self.end_headers(); return
            loc = {"ok": "/category-samokaty-trykolisni.html", "to_catalog": "/catalog.html", "wrong": "/category-lialky.html",
                   "query": "/category-samokaty-trykolisni.html?utm=1"}[gm]
            self.send_response(301); self.send_header("Location", host + loc); self.end_headers(); return
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
binm = tmp / "bin.map"
binm.write_bytes(bytes([0xff, 0xfe, 0x00, 0x62, 0x61, 0x64, 0x80]))
chk("first_map_entry: бінарна/зіпсована мапа → None (а не виняток)", sc.first_map_entry(binm) is None)
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

# ── групи Prom (12.10.2026): одна перевірка, лише коли передано мапу груп
gmap = tmp / "prom_group_redirects_apache.map"
gmap.write_text("# header\n155145045 /category-samokaty-trykolisni.html\n155145323 /category-dytiachyy-posud.html\n", encoding="utf-8")
gempty = tmp / "gempty.map"
gempty.write_text("# header only\n", encoding="utf-8")
gbad = tmp / "gbad.map"
gbad.write_text("# h\nabc /category-x.html\n155145045 /product-1.html\n155145045 /category-A.html\n", encoding="utf-8")
chk("first_group_entry: перший запис мапи груп (коментар пропущено)", sc.first_group_entry(gmap) == ("155145045", "/category-samokaty-trykolisni.html"))
chk("first_group_entry: порожня/відсутня/з рядками поза форматом → None", sc.first_group_entry(gempty) is None and sc.first_group_entry(tmp / "nope.map") is None and sc.first_group_entry(gbad) is None)
chk("first_group_entry: бінарна мапа → None (а не виняток)", sc.first_group_entry(binm) is None)
ok, probs = sc.run(base, mapf, gmap)
chk("групи: норма (301 на свою категорію) → ok", ok and probs == [])
GMODE["v"] = "to_catalog"
ok, probs = sc.run(base, mapf, gmap)
chk("групи: старе правило (301 на /catalog.html) → проблема «веде на … очікую /category-…»", not ok and any("група Prom g155145045" in p and "очікую /category-samokaty-trykolisni.html" in p for p in probs))
ok, probs = sc.run(base, mapf)
chk("групи: перевірку вимкнено (мапа не передана) → старе правило НЕ дає хибної тривоги", ok and probs == [])
ok, probs = sc.run(base, mapf, gempty)
chk("групи: порожня мапа груп → перевірку пропущено без алерту", ok and probs == [])
GMODE["v"] = "wrong"
ok, probs = sc.run(base, mapf, gmap)
chk("групи: 301 на чужу категорію → проблема", not ok and any("група Prom" in p and "веде на" in p for p in probs))
GMODE["v"] = "query"
ok, probs = sc.run(base, mapf, gmap)
chk("групи: ?query не відкинуто (QSD не діє) → проблема", not ok and any("без ?query" in p for p in probs))
GMODE["v"] = "gone"
ok, probs = sc.run(base, mapf, gmap)
chk("групи: 410 замість 301 → проблема «отримано 410»", not ok and any("група Prom" in p and "отримано 410" in p for p in probs))
GMODE["v"] = "to_catalog"

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
# прапорець SITE_CANARY_GROUPS: вимкнено за замовчуванням (до activate_site_apache.sh Apache ще шле групи на /catalog.html) → main() мовчить; увімкнено → алерт
ALERTS.clear()
sc.GROUP_MAP_FILE = gmap
sc.CHECK_GROUPS = False
chk("main(): групи вимкнено (за замовчуванням), Apache ще на старому правилі → код 0, алертів нема", sc.main() == 0 and ALERTS == [])
sc.CHECK_GROUPS = True
chk("main(): групи ввімкнено, а правило не діє → код 1 і алерт site_redirect_canary", sc.main() == 1 and len(ALERTS) == 1 and ALERTS[0][0][0] == "site_redirect_canary" and "група Prom" in ALERTS[0][0][1])
GMODE["v"] = "ok"
ALERTS.clear()
chk("main(): групи ввімкнено, правило діє → код 0", sc.main() == 0 and ALERTS == [])
sc.CHECK_GROUPS = False
srv.shutdown()
srv.server_close()
ALERTS.clear()
sc.SITE_BASE = "http://127.0.0.1:1"      # порт без сервера
chk("main(): сайт недоступний → код 1 і алерт site_redirect_canary_unreachable", sc.main() == 1 and len(ALERTS) == 1 and ALERTS[0][0][0] == "site_redirect_canary_unreachable")

svc = (HERE / "deploy" / "site-rebuild.service").read_text(encoding="utf-8")
chk("site-rebuild.service: канарка — ExecStartPost=- ПІСЛЯ generate_prom_redirects (не фатальна)",
    "ExecStartPost=-/opt/plutustoys/venv/bin/python3 /opt/plutustoys/site_redirect_canary.py" in svc
    and svc.index("generate_prom_redirects.py") < svc.index("site_redirect_canary.py"))

chk("site-rebuild.service: SITE_CANARY_GROUPS=1 ставить юніт (його копіює лише activate_site_apache.sh) — перевірка груп вмикається разом із правилом Apache",
    "\nEnvironment=SITE_CANARY_GROUPS=1\n" in svc)

print()
if F:
    print(f"FAILED ({len(F)}):")
    for n in F:
        print("  -", n)
    sys.exit(1)
print("ALL OK")

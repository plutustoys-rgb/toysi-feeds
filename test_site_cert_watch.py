#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_site_cert_watch.py — 18.10.2026 (власник: «контролювати автоматично»): site_cert_watch.py — щоденний сторож TLS-сертифіката.
Логіка перевіряється на підмінених days_left/renew/alert (без мережі й certbot): тиша, коли ≥ 60 днів; самооновлення рівно одним certbot-викликом ТІЛЬКИ нашого сертифіката; сигнали ⚠️ (< 30) і 🚨 (< 14/протермінований/недоступний).
Плюс справжній TLS у локальному процесі (самопідписаний сертифікат НЕ проходить перевірку ланцюга → None, а не «гаразд»), юніти systemd і розклад."""
import os
import ssl
import subprocess
import sys
import tempfile
import threading
import socket
from pathlib import Path

os.environ["AUDIT_NO_TELEGRAM"] = "1"
HERE = Path(__file__).parent
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, str(HERE))
import site_cert_watch as cw  # noqa: E402

F = []


def chk(n, c):
    print(f"[{'OK ' if c else 'FAIL'}] {n}")
    if not c:
        F.append(n)


class Sim:
    """Підміна: послідовність days_left, результат renew, журнал викликів і сигналів."""

    def __init__(self, days, renew_ok=True):
        self.days = list(days)
        self.renew_ok = renew_ok
        self.renews = 0
        self.alerts = []

    def run(self):
        cw.days_left = lambda *a, **k: self.days.pop(0) if self.days else self.last
        cw.renew = self._renew
        cw.alert = lambda key, text: self.alerts.append((key, text))
        self.last = self.days[-1] if self.days else None
        return cw.main()

    def _renew(self):
        self.renews += 1
        return self.renew_ok


s = Sim([76.0])
rc = s.run()
chk("76 днів: тиша — не оновлює, без сигналів, код 0", rc == 0 and s.renews == 0 and not s.alerts)
s = Sim([60.0])
chk("рівно 60 днів: ще не оновлює (поріг «< 60»)", s.run() == 0 and s.renews == 0)
s = Sim([45.0, 89.5])
rc = s.run()
chk("45 днів: САМ оновлює одним викликом і перечитує (89 днів) — код 0, без сигналів (самовилікувався)", rc == 0 and s.renews == 1 and not s.alerts)
s = Sim([45.0, 45.0], renew_ok=False)
rc = s.run()
chk("45 днів, оновлення не вдалось: код 1, ще без сигналу (≥ 30 днів запасу), один виклик", rc == 1 and s.renews == 1 and not s.alerts)
s = Sim([25.0, 25.0], renew_ok=False)
rc = s.run()
chk("25 днів, не вдалось: ⚠️-сигнал (site_cert_warn) і код 1", rc == 1 and [a[0] for a in s.alerts] == ["site_cert_warn"] and "25" in s.alerts[0][1])
s = Sim([10.0, 10.0], renew_ok=False)
rc = s.run()
chk("10 днів, не вдалось: 🚨-сигнал (site_cert_crit) і код 1", rc == 1 and [a[0] for a in s.alerts] == ["site_cert_crit"] and s.alerts[0][1].startswith("🚨"))
s = Sim([-1.0, -1.0], renew_ok=False)
rc = s.run()
chk("протермінований: 🚨 «ПРОТЕРМІНОВАНИЙ», код 2", rc == 2 and s.alerts and "ПРОТЕРМІНОВАНИЙ" in s.alerts[0][1])
s = Sim([None])
rc = s.run()
chk("HTTPS недоступний/ланцюг не проходить: 🚨 site_cert_unreachable, код 2, оновлення НЕ запускаємо наосліп", rc == 2 and [a[0] for a in s.alerts] == ["site_cert_unreachable"] and s.renews == 0)
s = Sim([20.0, 85.0], renew_ok=True)
rc = s.run()
chk("20 днів, оновилось успішно (85 днів): код 0, без сигналів", rc == 0 and not s.alerts and s.renews == 1)

cmd = cw.renew_cmd()
chk(f"certbot-команда оновлює ЛИШЕ наш сертифікат (--cert-name {cw.DOMAIN}) примусово й без запитань: {cmd}", cmd[:2] == [cw.CERTBOT, "renew"] and "--cert-name" in cmd and cmd[cmd.index("--cert-name") + 1] == cw.DOMAIN and "--force-renewal" in cmd and "--non-interactive" in cmd)
chk("пороги: оновлення < 60, ⚠️ < 30, 🚨 < 14", (cw.RENEW_BELOW_DAYS, cw.WARN_DAYS, cw.CRIT_DAYS) == (60, 30, 14))

# справжній TLS: сервер із самопідписаним сертифікатом — перевірка ланцюга НЕ проходить → None (а не «діє 90 днів»)
openssl = subprocess.run(["openssl", "version"], capture_output=True, text=True).returncode == 0 if True else False
if openssl:
    d = Path(tempfile.mkdtemp())
    r = subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-keyout", str(d / "k.pem"), "-out", str(d / "c.pem"), "-days", "30", "-subj", "/CN=localhost"], capture_output=True, text=True)
    if r.returncode == 0:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(str(d / "c.pem"), str(d / "k.pem"))
        srv = socket.socket()
        srv.bind(("127.0.0.1", 0))
        srv.listen(5)
        port = srv.getsockname()[1]

        def serve():
            for _ in range(3):
                try:
                    c, _a = srv.accept()
                    try:
                        ctx.wrap_socket(c, server_side=True).close()
                    except Exception:
                        c.close()
                except Exception:
                    return

        threading.Thread(target=serve, daemon=True).start()
        cw2 = __import__("importlib").reload(cw)
        chk("самопідписаний сертифікат не проходить перевірку ланцюга → days_left = None (сторож не вважає це «гаразд»)", cw2.days_left("localhost", port) is None)
        srv.close()
    else:
        print("   (openssl req недоступний — TLS-проба пропущена)")
else:
    print("   (openssl нема — TLS-проба пропущена)")

# юніти й розклад
svc = (HERE / "deploy" / "site-cert-watch.service").read_text(encoding="utf-8")
tim = (HERE / "deploy" / "site-cert-watch.timer").read_text(encoding="utf-8")
chk("service: oneshot, venv-python /opt/plutustoys/site_cert_watch.py", "Type=oneshot" in svc and "/opt/plutustoys/venv/bin/python3 /opt/plutustoys/site_cert_watch.py" in svc)
chk("timer: щодня 04:17, Persistent, WantedBy=timers.target", "OnCalendar=*-*-* 04:17:00" in tim and "Persistent=true" in tim and "WantedBy=timers.target" in tim)
print()
if F:
    print(f"FAILED ({len(F)}):")
    for n in F:
        print("  -", n)
    sys.exit(1)
print("ALL OK")

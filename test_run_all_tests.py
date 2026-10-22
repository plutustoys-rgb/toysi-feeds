# -*- coding: utf-8 -*-
"""run_all_tests.py: безпека (секрети не доходять до тестів), статуси pass/fail/timeout, мережа, stdin,
«нестабільні», код виходу. Працює лише в тимчасовій теці, нічого справжнього не чіпає."""
import contextlib
import io
import os
import sys
import tempfile
from pathlib import Path
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
import run_all_tests as rat

F = []
def chk(n, c):
    print(f"[{'OK ' if c else 'FAIL'}] {n}")
    if not c: F.append(n)

tmp = Path(tempfile.mkdtemp(prefix="rat_test_"))
def mk(name, body):
    p = tmp / name
    p.write_text(body, encoding="utf-8")
    return p

# --- середовище: allowlist ---
os.environ["TELEGRAM_BOT_TOKEN"] = "SECRET123"
os.environ["PLUTUS_FAKE_PASSWORD"] = "hunter2"
os.environ["HTTP_PROXY"] = "http://real-proxy:3128"
env = rat.build_env()
chk("секрети/токени НЕ потрапляють у середовище тестів",
    "TELEGRAM_BOT_TOKEN" not in env and "PLUTUS_FAKE_PASSWORD" not in env)
chk("PATH передається (інакше нічого не запуститься)", any(k.upper() == "PATH" for k in env))
chk("офлайн за замовчуванням: проксі → мертвий порт, справжній проксі затертий",
    env["HTTP_PROXY"] == rat.DEAD_PROXY and env["HTTPS_PROXY"] == rat.DEAD_PROXY)
chk("--online: проксі не підміняється (і чужий не передається)", "HTTP_PROXY" not in rat.build_env(offline=False))
chk("позначка запускача виставлена", env["PLUTUS_TEST_RUNNER"] == "1")

# --- домівка: тимчасова порожня, не справжня; локальні адреси напряму ---
real_home = str(Path.home())
chk("домашня тека тестів — НЕ справжня, і існує", env["USERPROFILE"] != real_home and Path(env["USERPROFILE"]).is_dir())
chk("NO_PROXY містить локальні адреси", "127.0.0.1" in env["NO_PROXY"] and "localhost" in env["NO_PROXY"])
pp = mk("test_home.py", "\n".join([
    "from pathlib import Path", "import sys",
    f'sys.exit(0 if str(Path.home()) != r"{real_home}" else 1)', ""]))
chk("Path.home() у тесті працює й веде в тимчасову теку", rat.run_one(pp, 30, env, tmp)["status"] == "pass")
import http.server, threading
class _H(http.server.BaseHTTPRequestHandler):
    def do_GET(self): self.send_response(200); self.end_headers(); self.wfile.write(b"ok")
    def log_message(self, *a): pass
srv = http.server.HTTPServer(("127.0.0.1", 0), _H); threading.Thread(target=srv.serve_forever, daemon=True).start()
pl = mk("test_local_server.py", "\n".join([
    "import urllib.request, sys",
    f'sys.exit(0 if urllib.request.urlopen("http://127.0.0.1:{srv.server_port}/", timeout=10).read() == b"ok" else 1)', ""]))
chk("ЛОКАЛЬНИЙ http-сервер у офлайн-режимі доступний (тести з власним сервером не ламаються)", rat.run_one(pl, 30, env, tmp)["status"] == "pass")
pe = mk("test_external.py", "\n".join([
    "import urllib.request", 'urllib.request.urlopen("http://example.com/", timeout=10)', ""]))
re_ = rat.run_one(pe, 30, env, tmp)
chk("ЗОВНІШНІЙ адрес у офлайн-режимі блокується (мережа назовні закрита)", re_["status"] == "fail" and re_["network"])
srv.shutdown()

# --- дочірній процес реально не бачить секретів ---
p = mk("test_sees_secret.py", 'import os,sys\nsys.exit(1 if os.environ.get("TELEGRAM_BOT_TOKEN") or os.environ.get("PLUTUS_FAKE_PASSWORD") else 0)\n')
chk("дочірній процес не бачить TELEGRAM_BOT_TOKEN/пароля", rat.run_one(p, 30, env, tmp)["status"] == "pass")
p = mk("test_sees_proxy.py", 'import os,sys\nsys.exit(0 if os.environ.get("HTTPS_PROXY","").endswith(":9") else 1)\n')
chk("дочірній процес бачить мертвий проксі (мережа заблокована)", rat.run_one(p, 30, env, tmp)["status"] == "pass")

# --- статуси ---
r_pass = rat.run_one(mk("test_ok.py", "pass\n"), 30, env, tmp)
r_fail = rat.run_one(mk("test_bad.py", 'print("boom")\nraise SystemExit(3)\n'), 30, env, tmp)
r_to = rat.run_one(mk("test_slow.py", "import time\ntime.sleep(60)\n"), 2, env, tmp)
chk("pass: код 0", r_pass["status"] == "pass" and r_pass["code"] == 0 and r_pass["tail"] == "")
chk("fail: код 3, хвіст виводу збережено", r_fail["status"] == "fail" and r_fail["code"] == 3 and "boom" in r_fail["tail"])
chk("timeout: зависання вбито за таймаутом", r_to["status"] == "timeout" and r_to["seconds"] < 30)
chk("мережевий провал позначено", rat.run_one(mk("test_net.py", 'print("ProxyError: x")\nraise SystemExit(1)\n'), 30, env, tmp)["network"])
chk("звичайний провал НЕ позначено як мережевий", not r_fail["network"])
chk("stdin закритий: input() падає, а не висить",
    rat.run_one(mk("test_input.py", "input()\n"), 20, env, tmp)["status"] == "fail")

# --- нестабільні: упав паралельно, пройшов поодинці ---
marker = tmp / "marker.flag"
flaky = mk("test_flaky.py", f'import os,sys\nm=r"{marker}"\nif os.path.exists(m):\n    sys.exit(0)\nopen(m,"w").close()\nsys.exit(1)\n')
res = rat.run_suite([flaky], jobs=2, timeout=30, env=env, cwd=tmp)
chk("нестабільний (fail → pass при повторі) позначено flaky, не fail", res[0]["status"] == "flaky" and res[0]["first_status"] == "fail")
marker.unlink()
res1 = rat.run_suite([flaky], jobs=1, timeout=30, env=env, cwd=tmp)
chk("послідовно (jobs=1) повтору нема — чесний fail", res1[0]["status"] == "fail")

# --- discover / summarize / main ---
chk("discover знаходить лише test_*.py", all(x.name.startswith("test_") for x in rat.discover(tmp)))
chk("--only фільтрує за підрядком", [x.name for x in rat.discover(tmp, "slow")] == ["test_slow.py"])
s = rat.summarize([r_pass, r_fail, r_to, {"status": "flaky", "network": False}])
chk("summarize рахує", s["pass"] == 1 and s["fail"] == 1 and s["timeout"] == 1 and s["flaky"] == 1)

for f in tmp.glob("test_*.py"):
    if f.name not in ("test_ok.py", "test_bad.py"):
        f.unlink()
rat.BASE = tmp
buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    code_bad = rat.main(["--jobs", "1", "--timeout", "30"])
chk("main: є провал → код 1 і ім'я провалу в звіті", code_bad == 1 and "test_bad.py" in buf.getvalue())
(tmp / "test_bad.py").unlink()
with contextlib.redirect_stdout(io.StringIO()):
    code_ok = rat.main(["--jobs", "1", "--timeout", "30"])
chk("main: усе пройшло → код 0", code_ok == 0)
(tmp / "test_ok.py").unlink()
with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
    code_none = rat.main([])
chk("main: тестів нема → код 2", code_none == 2)

print("\nРЕЗУЛЬТАТ:", "УСЕ ОК" if not F else f"{len(F)} FAIL: {F}")
sys.exit(0 if not F else 1)

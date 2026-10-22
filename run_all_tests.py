#!/usr/bin/env python3
"""run_all_tests.py — БЕЗПЕЧНИЙ запуск усіх `test_*.py` репо одним викликом.

ЧОМУ (архітектурний аудит 21.10.2026): у корені ~209 тестів-скриптів (exit 0 = ок), але НІЧОГО не запускає
їх автоматично: ні CI (GitHub Actions у репо нема), ні єдиного скрипта; за 12 днів змерджено 325 PR. Регресію
в тесті, якого автор не прогнав, ніхто не бачить. Раніше окремий тест уже слав РЕАЛЬНІ Telegram-алерти в
бойовий канал (fix 62e8b5d) — тож «просто запустити всі» небезпечно без ізоляції.

БЕЗПЕКА (чому цим можна ганяти все підряд):
  • Дочірній процес отримує ЛИШЕ allowlist змінних середовища (PATH, TEMP, SYSTEMROOT…). Жодних
    TELEGRAM_*/токенів/паролів — навіть якщо вони є в оточенні того, хто запустив.
  • За замовчуванням ОФЛАЙН для HTTP(S): HTTP(S)_PROXY → мертвий локальний порт, тож `requests`/`urlopen` падають
    одразу, а не б'ють по справжніх API (локальні адреси 127.0.0.1/localhost — напряму). `--online` вимикає.
    ⚠️ МЕЖА: це змінні середовища, а не файрвол. Сирі сокети (imaplib/smtplib/ssl) і код, що явно задає
    `proxies={}`, НЕ блокуються. Станом на 21.10.2026 сирі сокети в тестах лише в test_site_cert_watch.py
    (і тільки на 127.0.0.1); зовнішні програми — лише `git` проти локальних тимчасових репо.
  • stdin = DEVNULL (тест, що чекає `input()`, падає, а не висне); таймаут на тест.
  • Запускати в ЧИСТОМУ checkout/worktree (там нема .env, orders.db, .local_secrets) — НЕ в десктопній теці,
    з якої працюють заплановані задачі. Після прогону друкує, які файли тести створили/змінили.

ЯК ЧИТАТИ: pass / fail / timeout. «Мережа?» — провал, чий вивід схожий на відсутність мережі (офлайн-режим) —
тест залежить від живого сервісу; це окрема категорія, не обов'язково баг коду. «Нестабільні» — впали у
паралельному прогоні, але пройшли при повторі поодинці (конфлікт за спільні файли між тестами).

ЗАПУСК:
  python run_all_tests.py                 # усі, 4 паралельно, 120 с на тест, офлайн
  python run_all_tests.py --only novapay  # лише тести, у назві яких є підрядок
  python run_all_tests.py --json out.json --jobs 1 -v
Код виходу: 0 — усе пройшло; 1 — є fail/timeout; 2 — нема тестів / помилка запуску.
"""
import argparse
import concurrent.futures as cf
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE = Path(__file__).resolve().parent
ENV_ALLOW = {"PATH", "PATHEXT", "SYSTEMROOT", "SYSTEMDRIVE", "COMSPEC", "WINDIR", "TEMP", "TMP", "OS",
             "NUMBER_OF_PROCESSORS", "PROCESSOR_ARCHITECTURE"}
DEAD_PROXY = "http://127.0.0.1:9"
NETWORK_MARKERS = ("ProxyError", "Max retries exceeded", "Connection refused", "getaddrinfo failed",
                   "NameResolutionError", "Failed to establish a new connection", "URLError",
                   "Name or service not known", "Tunnel connection failed")


def build_env(offline: bool = True, utf8: bool = True, home: str = "") -> dict:
    """Allowlist: усе, чого тут нема (токени, паролі, TELEGRAM_*), дочірній процес НЕ бачить.
    ДОМАШНЯ ТЕКА — порожня тимчасова (а не справжня): `Path.home()` працює, але тест не дістанеться до
    реального ~/.ssh чи профілю (перший прогін 21.10 показав: без домівки впав test_gmc_compare_and_delivery)."""
    env = {k: v for k, v in os.environ.items() if k.upper() in ENV_ALLOW}
    env["PLUTUS_TEST_RUNNER"] = "1"
    home = home or tempfile.mkdtemp(prefix="rat_home_")
    env["USERPROFILE"] = env["HOME"] = home
    if utf8:
        env["PYTHONIOENCODING"] = "utf-8"
    if offline:
        for k in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
            env[k] = DEAD_PROXY
        # локальні адреси — НАПРЯМУ: тести піднімають власний http-сервер на 127.0.0.1 і стукають у нього
        # (перший прогін 21.10: без цього 3 тести хибно падали «з'єднання відхилено» через мертвий проксі)
        env["NO_PROXY"] = env["no_proxy"] = "127.0.0.1,localhost,::1"
    return env


def discover(directory: Path, only: str = "") -> list:
    tests = sorted(p for p in Path(directory).glob("test_*.py") if p.is_file())
    if only:
        tests = [p for p in tests if only.lower() in p.name.lower()]
    return tests


def run_one(path: Path, timeout: int, env: dict, cwd: Path) -> dict:
    t0 = time.time()
    try:
        r = subprocess.run([sys.executable, str(path)], cwd=str(cwd), env=env, stdin=subprocess.DEVNULL,
                           capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)
        out = (r.stdout or "") + (r.stderr or "")
        status, code = ("pass" if r.returncode == 0 else "fail"), r.returncode
    except subprocess.TimeoutExpired as e:
        raw = (e.stdout or b"") if not isinstance(e.stdout, str) else e.stdout.encode()
        out = raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else str(raw)
        status, code = "timeout", None
    tail = "\n".join(out.strip().splitlines()[-15:]) if status != "pass" else ""
    return {"test": path.name, "status": status, "code": code, "seconds": round(time.time() - t0, 1),
            "network": status != "pass" and any(m in out for m in NETWORK_MARKERS), "tail": tail}


def run_suite(tests: list, jobs: int, timeout: int, env: dict, cwd: Path) -> list:
    """Фаза 1 — паралельно. Фаза 2 — провали повторно ПООДИНЦІ: пройшли → «нестабільний»."""
    with cf.ThreadPoolExecutor(max_workers=max(1, jobs)) as ex:
        results = list(ex.map(lambda p: run_one(p, timeout, env, cwd), tests))
    if jobs > 1:
        by_name = {p.name: p for p in tests}
        for i, r in enumerate(results):
            if r["status"] in ("fail", "timeout"):
                again = run_one(by_name[r["test"]], timeout, env, cwd)
                if again["status"] == "pass":
                    results[i] = {**again, "status": "flaky", "first_status": r["status"]}
    return results


def _git_dirty(cwd: Path):
    try:
        r = subprocess.run(["git", "status", "--porcelain"], cwd=str(cwd), capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=60)
        return {ln[3:] for ln in r.stdout.splitlines()} if r.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired):
        return None


def summarize(results: list) -> dict:
    c = {"pass": 0, "fail": 0, "timeout": 0, "flaky": 0}
    for r in results:
        c[r["status"]] += 1
    c["network_fail"] = sum(1 for r in results if r["status"] in ("fail", "timeout") and r["network"])
    return c


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--jobs", type=int, default=4, help="паралельність (1 = послідовно)")
    ap.add_argument("--timeout", type=int, default=120, help="секунд на тест")
    ap.add_argument("--only", default="", help="лише тести, у назві яких є цей підрядок")
    ap.add_argument("--online", action="store_true", help="НЕ блокувати мережу (свідомо; тести можуть бити по API)")
    ap.add_argument("--native-encoding", action="store_true", help="не форсувати UTF-8 (відтворити cp1251-середовище)")
    ap.add_argument("--json", default="", help="записати повний звіт у файл")
    ap.add_argument("-v", "--verbose", action="store_true", help="показати хвіст виводу КОЖНОГО провалу")
    a = ap.parse_args(argv)

    tests = discover(BASE, a.only)
    if not tests:
        print("[tests] тестів не знайдено", file=sys.stderr)
        return 2
    env = build_env(offline=not a.online, utf8=not a.native_encoding)
    fake_home = env["USERPROFILE"]
    print(f"[tests] {len(tests)} тестів, {a.jobs} паралельно, таймаут {a.timeout}с, "
          f"{'ОНЛАЙН' if a.online else 'офлайн'}; секретів у середовищі тестів нема (allowlist)")
    before = _git_dirty(BASE)
    t0 = time.time()
    try:
        results = run_suite(tests, a.jobs, a.timeout, env, BASE)
    finally:
        shutil.rmtree(fake_home, ignore_errors=True)
    total = round(time.time() - t0)
    c = summarize(results)
    bad = [r for r in results if r["status"] in ("fail", "timeout")]
    flaky = [r for r in results if r["status"] == "flaky"]
    print(f"\n[tests] {total}с | пройшло {c['pass']} | ПРОВАЛ {c['fail']} | ТАЙМАУТ {c['timeout']} | "
          f"нестабільних {c['flaky']} | з них провалів схожих на «нема мережі»: {c['network_fail']}")
    for r in bad:
        print(f"  ✗ {r['test']} [{r['status']}{', мережа?' if r['network'] else ''}, {r['seconds']}с]")
        if a.verbose:
            print("      " + r["tail"].replace("\n", "\n      "))
    for r in flaky:
        print(f"  ~ {r['test']} (нестабільний: {r['first_status']} паралельно, пройшов поодинці)")
    after = _git_dirty(BASE)
    if before is not None and after is not None and after - before:
        extra = sorted(after - before)
        print(f"[tests] ⚠️ тести створили/змінили {len(extra)} файл(ів) у репо: {', '.join(extra[:8])}"
              + (" …" if len(extra) > 8 else ""))
    if a.json:
        Path(a.json).write_text(json.dumps({"summary": c, "seconds": total, "results": results},
                                           ensure_ascii=False, indent=1), encoding="utf-8")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())

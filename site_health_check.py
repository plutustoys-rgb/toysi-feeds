# -*- coding: utf-8 -*-
"""site_health_check.py — машинна перевірка власного сайту «очима покупця» (лише читання, зовнішній HTTPS-запит; рішення власника 16.10.2026: правила й перевірка результату і для сайту).

ЧОМУ: сайт перевіряв лише Аудитор вручну раз на день (САЙТ_ЩОДЕННО.md); між обходами збій (сертифікат, 5xx, зламаний index.json, мертві картки, розбіжність ціни) ніхто не бачив. Це дублює щоденний ручний обхід машинним мінімумом, який крутиться кожні 6 годин.
ЩО РОБИТЬ (кожна умова бінарна, без вгаданих порогів): головна / каталог / sitemap.xml / robots.txt / index.json → 200; сертифікат ≥14 днів (факт із правил §3.6); N=10 випадкових карток із index.json (зерно = дата) → 200, JSON-LD Product з price і availability, ціна на сторінці = ціна в index.json (±1 ₴: округлення),
http→https редірект, api/np/city відповідає 200. Рядок на запуск у reports/site_health.csv. Є порушення → запис `## [Автомат → Код] Сайт: … <дата>` у EXECUTOR_CHANNEL.md (не частіше раз на 1 добу на набір порушень; Telegram власнику НЕ шле).
Запускається кроком у run_marketplace_actions.py (таск PlutusToys_MarketplaceActions, кожні 6 год, десктоп = «зовнішній» погляд на сайт)."""
import csv
import json
import os
import random
import re
import socket
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE_DIR = Path(__file__).parent
SITE = os.environ.get("SITE_HEALTH_URL", "https://plutustoys.com.ua").rstrip("/")
HOST = SITE.split("//", 1)[-1]
DEFAULT_REPORT_DIR = r"C:\Users\smach\Claude\Projects\PlutusToys_avtonomiya\reports"
REPORT_DIR = Path(os.environ.get("AUDIT_REPORT_DIR") or DEFAULT_REPORT_DIR)
CSV_FILE = REPORT_DIR / "site_health.csv"
CHANNEL_FILE = Path(os.environ.get("SITE_HEALTH_CHANNEL_FILE") or (REPORT_DIR.parent / "EXECUTOR_CHANNEL.md"))
SAMPLE_N = 10
CERT_MIN_DAYS = 14
MARK = "[Автомат → Код] Сайт:"
UA = {"User-Agent": "PlutusToys-health-check/1.0"}
_LD = re.compile(r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', re.S | re.I)


def fetch(url: str, timeout: int = 25):
    """(код, тіло|None, секунд). Виняток мережі → (0, None, t)."""
    t0 = time.monotonic()
    try:
        req = urllib.request.Request(url, headers=UA)
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8", errors="replace"), time.monotonic() - t0
    except urllib.error.HTTPError as e:
        return e.code, None, time.monotonic() - t0
    except Exception:  # noqa: BLE001
        return 0, None, time.monotonic() - t0


def cert_days(host: str):
    try:
        ctx = ssl.create_default_context()
        with socket.create_connection((host, 443), timeout=15) as s, ctx.wrap_socket(s, server_hostname=host) as ss:
            exp = ssl.cert_time_to_seconds(ss.getpeercert()["notAfter"])
        return int((exp - time.time()) // 86400)
    except Exception:  # noqa: BLE001
        return None


def jsonld_product(html: str):
    """(price|None, availability|None) першого Product у JSON-LD сторінки."""
    for m in _LD.finditer(html or ""):
        try:
            d = json.loads(m.group(1))
        except ValueError:
            continue
        for node in (d if isinstance(d, list) else [d]):
            if isinstance(node, dict) and node.get("@type") == "Product":
                off = node.get("offers")
                off = off[0] if isinstance(off, list) and off else off
                if isinstance(off, dict):
                    return off.get("price"), off.get("availability")
                return None, None
    return None, None


def check_cards(index: list, today: date, fetcher=fetch, n: int = SAMPLE_N) -> list:
    """Порушення по вибірці карток: сторінка не 200, нема JSON-LD price/availability, ціна сторінки ≠ index.json."""
    rng = random.Random(today.isoformat())
    pool = [it for it in index if isinstance(it, dict) and it.get("id") and it.get("pr")]
    out = []
    for it in rng.sample(pool, min(n, len(pool))):
        pid, pr = str(it["id"]), it["pr"]
        code, html, _ = fetcher(f"{SITE}/product-{pid}.html")
        if code != 200 or not html:
            out.append(f"картка product-{pid}: HTTP {code}")
            continue
        price, avail = jsonld_product(html)
        if price in (None, "") or not avail:
            out.append(f"картка product-{pid}: у JSON-LD нема price/availability")
            continue
        try:
            if abs(float(price) - float(pr)) > 1:
                out.append(f"картка product-{pid}: ціна на сторінці {price} ≠ index.json {pr}")
        except (TypeError, ValueError):
            out.append(f"картка product-{pid}: нечислова ціна у JSON-LD ({price!r})")
    return out


def run_checks(today: date, fetcher=fetch, certfn=cert_days) -> tuple:
    """(рядок для CSV, список порушень). Чиста щодо мережі (fetcher/certfn підміняються в тестах)."""
    problems, info = [], {}
    for path in ("/", "/catalog.html", "/sitemap.xml", "/robots.txt"):
        code, _, t = fetcher(SITE + path)
        info[path] = (code, round(t, 2))
        if code != 200:
            problems.append(f"{path}: HTTP {code}")
    code, body, t = fetcher(SITE + "/index.json")
    index = []
    if code != 200 or not body:
        problems.append(f"/index.json: HTTP {code}")
    else:
        try:
            index = json.loads(body)
            if not isinstance(index, list) or not index:
                problems.append("/index.json порожній або не список")
                index = []
        except ValueError:
            problems.append("/index.json не JSON")
    if index:
        problems += check_cards(index, today, fetcher)
    days = certfn(HOST)
    if days is None:
        problems.append("сертифікат: не вдалось прочитати")
    elif days < CERT_MIN_DAYS:
        problems.append(f"сертифікат спливає за {days} днів (<{CERT_MIN_DAYS})")
    code, _, _ = fetcher(SITE + "/api/np/city?q=" + urllib.parse.quote("Київ"))
    if code != 200:
        problems.append(f"/api/np/city: HTTP {code}")
    row = {"at": datetime.now().strftime("%Y-%m-%d %H:%M"), "home": info.get("/", (0, 0))[0], "home_s": info.get("/", (0, 0))[1],
           "cert_days": days if days is not None else "", "index_n": len(index), "problems": len(problems), "detail": " | ".join(problems)[:500]}
    return row, problems


def write_row(row: dict, path: Path | None = None) -> None:
    path = Path(path or CSV_FILE)
    path.parent.mkdir(parents=True, exist_ok=True)
    new = not path.exists()
    with open(path, "a", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(row))
        if new:
            w.writeheader()
        w.writerow(row)


def post_to_channel(problems: list, now: datetime, channel: Path | None = None) -> bool:
    """Запис угорі каналу (перед першим рядком, що ПОЧИНАЄТЬСЯ з «## »); дедуп: за добу не більше одного запису; атомарно; недоступний канал → False."""
    channel = Path(channel or CHANNEL_FILE)
    try:
        s = channel.read_text(encoding="utf-8")
    except OSError:
        return False
    head = f"## {MARK} {len(problems)} порушень {now:%Y-%m-%d}"
    if re.search(r"(?m)^## " + re.escape(MARK) + r" \d+ порушень " + now.strftime("%Y-%m-%d"), s):
        return False
    nl = "\r\n" if "\r\n" in s else "\n"
    entry = (head + "\n" + "; ".join(problems[:8]) + (f"; … ще {len(problems) - 8}" if len(problems) > 8 else "") + "\n"
             + "Перевірка очима покупця (кожні 6 год); Код розбирає, Аудитор підтверджує зі свого боку.\n\n").replace("\n", nl)
    m = re.search(r"(?m)^## ", s)
    new = (s[:m.start()] + entry + s[m.start():]) if m else (s + nl + entry)
    try:
        tmp = channel.with_name(channel.name + ".tmp")
        tmp.write_text(new, encoding="utf-8")
        os.replace(tmp, channel)
    except OSError:
        return False
    return True


def main() -> int:
    now = datetime.now()
    row, problems = run_checks(now.date())
    try:
        write_row(row)
    except OSError as e:
        print(f"[site_health] CSV не записано: {type(e).__name__}", file=sys.stderr)
    print(f"[site_health] {row['at']}: головна {row['home']} ({row['home_s']} с), сертифікат {row['cert_days']} дн., карток у index {row['index_n']}, порушень {len(problems)}")
    for p in problems:
        print("  ✗", p)
    if problems:
        print("[site_health] запис у канал:", "додано" if post_to_channel(problems, now) else "за сьогодні вже є / канал недоступний")
    return 0


if __name__ == "__main__":
    sys.exit(main())

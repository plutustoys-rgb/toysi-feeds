#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""site_redirect_canary.py — канарка старих Prom-URL після кожного ребілду сайту (ExecStartPost у deploy/site-rebuild.service).

НАВІЩО (11.10.2026, Консультант): Apache віддає для `p<id>-….html` 301 на картку, якщо prom_id є в мапі `prom_redirects_apache.map`, і 410 Gone, якщо нема.
Порожня/зіпсована/застаріла мапа дає 410 для ВСІХ старих Prom-URL (замість 301 на картки) — Google це читає як «товару більше нема» і втрачає видимість.
Разова проба в activate_site_apache.sh перевіряє лише момент активації; цей скрипт перевіряє ЖИВИЙ сайт після КОЖНОЇ збірки (раз на 2 год):
  • перший ключ мапи → 301 на /product-<id>.html (абсолютний Location на наш домен);
  • вигаданий prom_id → 410;
  • (опційно) картка-ціль мапи → 200;
  • (12.10.2026, лише з SITE_CANARY_GROUPS=1) перший gid мапи груп Prom (`prom_group_redirects_apache.map`, найбільше показів) → 301 на його /category-<slug>.html.
    Вимкнено за замовчуванням: до запуску activate_site_apache.sh живий Apache ще шле /g<gid>-… на /catalog.html, і канарка кричала б хибно. Прапорець ставить
    deploy/site-rebuild.service (юніт копіює лише activate_site_apache.sh, а не авто-синхронізація юнітів) — тобто вмикається разом із правилом Apache.
    Порожня/відсутня мапа груп — НЕ проблема (усе → /catalog.html, як було): перевірку пропускаємо без алерту.
Невідповідність → тротлений алерт у Telegram + exit 1 (ExecStartPost=- не валить ребілд). Сайт недоступний → окремий алерт «не вдалося перевірити».
Мережа — лише GET на наш домен без редіректів, таймаут 15 с. БЕЗ записів у БД/файли."""
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE_DIR = Path(__file__).parent
MAP_FILE = Path(os.environ.get("PROM_REDIRECTS_MAP_APACHE", str(BASE_DIR / "prom_redirects_apache.map")))
SITE_BASE = os.environ.get("SITE_CANARY_BASE", "https://plutustoys.com.ua").rstrip("/")
GROUP_MAP_FILE = Path(os.environ.get("PROM_GROUP_REDIRECTS_MAP_APACHE", str(BASE_DIR / "prom_group_redirects_apache.map")))
CHECK_GROUPS = os.environ.get("SITE_CANARY_GROUPS") == "1"
_GROUP_LINE_RE = re.compile(r"([0-9]{1,20}) (/category-[a-z0-9-]{1,200}\.html)")
FAKE_PROM_ID = "999999999999"
TIMEOUT = 15
ALERT_COOLDOWN_SEC = 6 * 3600


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):   # повертаємо 3xx як відповідь, а не йдемо за ним
        return None


def fetch(url: str):
    """(status, location) без слідування за редіректами. Виняток мережі пробрасується."""
    opener = urllib.request.build_opener(_NoRedirect)
    req = urllib.request.Request(url, headers={"User-Agent": "plutustoys-redirect-canary/1.0"})
    try:
        with opener.open(req, timeout=TIMEOUT) as r:
            return r.status, r.headers.get("Location", "")
    except urllib.error.HTTPError as e:
        return e.code, e.headers.get("Location", "") if e.headers else ""


def first_map_entry(path: Path = MAP_FILE):
    """(prom_id, '/product-<id>.html') першого запису мапи або None, якщо мапа порожня/нечитна."""
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            parts = line.split()
            if len(parts) == 2 and parts[0].isdigit() and parts[1].startswith("/product-"):
                return parts[0], parts[1]
    except (OSError, ValueError):   # ValueError ⊃ UnicodeDecodeError: бінарна/зіпсована мапа теж = «мапа нечитна»
        return None
    return None


def first_group_entry(path: Path = GROUP_MAP_FILE):
    """(gid, '/category-<slug>.html') першого запису мапи груп або None (порожня/відсутня/нечитна/рядок поза форматом)."""
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            m = _GROUP_LINE_RE.fullmatch(line.strip())
            if m:
                return m.group(1), m.group(2)
    except (OSError, ValueError):
        return None
    return None


def run(base: str = SITE_BASE, map_file: Path = MAP_FILE, group_map_file: Path = None):
    """Повертає (ok: bool, problems: list[str]). group_map_file=None — перевірку груп Prom вимкнено."""
    problems = []
    entry = first_map_entry(map_file)
    if entry is None:
        problems.append(f"мапа {map_file.name} порожня/нечитна — Apache віддасть 410 для ВСІХ старих Prom-URL")
    else:
        prom_id, target = entry
        st, loc = fetch(f"{base}/ua/p{prom_id}-canary.html")
        if st != 301:
            problems.append(f"покритий Prom-ID {prom_id}: очікую 301, отримано {st}")
        else:
            path = urlsplit(loc).path if loc else ""
            if path != target:
                problems.append(f"покритий Prom-ID {prom_id}: 301 веде на {loc!r}, очікую {target}")
            else:
                st2, _ = fetch(f"{base}{target}")
                if st2 != 200:
                    problems.append(f"ціль мапи {target} віддає {st2}, а не 200")
    st, _ = fetch(f"{base}/ua/p{FAKE_PROM_ID}-canary-gone.html")
    if st != 410:
        problems.append(f"вигаданий Prom-ID: очікую 410 Gone, отримано {st}")
    if group_map_file is not None:
        g = first_group_entry(group_map_file)
        if g is None:
            print(f"[canary] групи Prom: мапа {group_map_file.name} порожня/відсутня — перевірку пропущено (усі /g<gid>-… → /catalog.html, як було)")
        else:
            gid, gtarget = g
            st, loc = fetch(f"{base}/ua/g{gid}-canary.html")
            parts = urlsplit(loc) if loc else None
            if st != 301:
                problems.append(f"група Prom g{gid}: очікую 301 на {gtarget}, отримано {st}")
            elif parts is None or parts.path != gtarget or parts.query:
                problems.append(f"група Prom g{gid}: 301 веде на {loc!r}, очікую {gtarget} (без ?query)")
    return (not problems), problems


def main() -> int:
    try:
        ok, problems = run(SITE_BASE, MAP_FILE, GROUP_MAP_FILE if CHECK_GROUPS else None)
    except Exception as e:  # noqa: BLE001 — мережа/DNS/TLS: це теж сигнал, але інший
        msg = f"канарка Prom-URL: не вдалося перевірити сайт ({type(e).__name__}: {str(e)[:150]})"
        print(f"[canary] {msg}", file=sys.stderr)
        _alert("site_redirect_canary_unreachable", f"⚠️ {msg}")
        return 1
    if ok:
        print("[canary] OK: покритий Prom-URL → 301 на картку, вигаданий → 410" + (", група Prom → 301 на категорію" if CHECK_GROUPS else ""))
        return 0
    text = "🚨 канарка старих Prom-URL: " + "; ".join(problems) + ". Перевір prom_redirects_apache.map і vhost (activate_site_apache.sh)."
    print(f"[canary] {text}", file=sys.stderr)
    _alert("site_redirect_canary", text)
    return 1


def _alert(key: str, text: str) -> None:
    try:
        from telegram_notify import send_throttled_alert
        send_throttled_alert(key, text, cooldown_sec=ALERT_COOLDOWN_SEC)
    except Exception:  # noqa: BLE001 — алерт best-effort
        pass


if __name__ == "__main__":
    sys.exit(main())

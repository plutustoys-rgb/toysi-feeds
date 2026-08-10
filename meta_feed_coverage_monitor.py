"""
meta_feed_coverage_monitor.py — стежить за покриттям Meta(Facebook)-фіда й алертить у
Telegram, якщо кількість товарів різко впала (товари зникають із FB-каталогу).

НАВІЩО (2026-08-10, завдання Cowork): FB-каталог живиться `meta_feed.xml`; треба помічати,
коли покриття просідає (регресія фіда → товари зникають із Facebook). Живо звірено
2026-08-10: FB активних 3894 (усі активні, архів 0) ≈ meta_feed 3897 — тобто FB-сторона
здорова; єдиний розрив — ~92 товари, що є на сайті, але не у фіді (feed-coverage хвіст
link-кешу, той самий клас, що GMC #227). Цей монітор ловить, якщо цей розрив РОСТЕ.

Точний «сайт vs FB active» потребував би Meta Catalog API (токен) — не налаштовано; тому
моніторимо сам розмір фіда проти власної історії (FB active ≈ meta_feed count, звірено).

БЕЗ станів кабінету/браузера — лише читає публічний feed-data + пише історію + Telegram.

ЗАПУСК: python meta_feed_coverage_monitor.py   (щодня, напр. окремим systemd-таймером
або в наявному денному джобі VPS).
"""
import json
import re
import sys
import urllib.request
from datetime import datetime
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from telegram_notify import send_telegram_message

BASE_DIR = Path(__file__).parent
HISTORY_FILE = BASE_DIR / "meta_feed_count_history.jsonl"
FEED_URL = "https://raw.githubusercontent.com/plutustoys-rgb/toysi-feeds/feed-data/feeds/meta_feed.xml"
REQUEST_TIMEOUT = 60

# Поріг тривоги: падіння на ≥ цю частку АБО на ≥ стільки товарів (що більше в абсолюті),
# порівняно з попереднім заміром. Порожній/крихітний фід — завжди тривога.
DROP_FRACTION = 0.05      # 5%
DROP_ABSOLUTE = 150       # або ≥150 товарів
MIN_SANE_COUNT = 500      # менше — вважаємо фід зламаним


def fetch_feed_count() -> int:
    """Кількість <item> у живому meta_feed.xml (з feed-data)."""
    data = urllib.request.urlopen(FEED_URL, timeout=REQUEST_TIMEOUT).read().decode("utf-8", "replace")
    return len(re.findall(r"<item>", data))


def _load_last() -> dict | None:
    """Останній запис історії (найновіший рядок), або None."""
    if not HISTORY_FILE.exists():
        return None
    last = None
    try:
        for line in HISTORY_FILE.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                last = json.loads(line)
    except (ValueError, OSError):
        return None
    return last


def evaluate(prev_count, current: int) -> tuple:
    """Чи бити тривогу. Повертає (alert: bool, msg: str|None). Чисто-функційно (тестовно):
    - фід зламаний (< MIN_SANE_COUNT) → тривога;
    - падіння на ≥ DROP_FRACTION АБО ≥ DROP_ABSOLUTE проти попереднього → тривога;
    - інакше — без тривоги (зростання/стабільність не турбують)."""
    if current < MIN_SANE_COUNT:
        return True, (f"🔴 Meta-фід (Facebook) зламаний: лише {current} товарів "
                      f"(< {MIN_SANE_COUNT}). Перевір генерацію meta_feed.xml.")
    if prev_count is None:
        return False, None  # перший замір — нема з чим порівнювати
    drop = prev_count - current
    if drop >= DROP_ABSOLUTE or (prev_count > 0 and drop / prev_count >= DROP_FRACTION):
        return True, (f"🔴 Meta-фід (Facebook) просів: було {prev_count} → стало {current} "
                      f"(−{drop} товарів). Товари зникають із FB-каталогу — перевір фід/link-кеш.")
    return False, None


def run() -> dict:
    try:
        current = fetch_feed_count()
    except Exception as e:
        print(f"[MetaMonitor] не вдалось прочитати meta_feed.xml: {e}", file=sys.stderr)
        return {"ok": False, "error": str(e)}

    last = _load_last()
    prev_count = last.get("count") if last else None
    alert, msg = evaluate(prev_count, current)

    now = datetime.now().isoformat(timespec="seconds")
    try:
        with HISTORY_FILE.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": now, "count": current, "prev": prev_count,
                                "alerted": alert}, ensure_ascii=False) + "\n")
    except OSError as e:
        print(f"[MetaMonitor] не вдалось дописати історію: {e}", file=sys.stderr)

    print(f"[MetaMonitor] meta_feed.xml: {current} товарів"
          + (f" (було {prev_count})" if prev_count is not None else " (перший замір)"))
    if alert and msg:
        print(f"[MetaMonitor] ТРИВОГА: {msg}")
        try:
            send_telegram_message(msg)
        except Exception as e:
            print(f"[MetaMonitor] Telegram не надіслано (не критично): {e}", file=sys.stderr)
    return {"ok": True, "count": current, "prev": prev_count, "alert": alert}


if __name__ == "__main__":
    run()

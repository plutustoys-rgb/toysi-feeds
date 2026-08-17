"""rozetka_price_monitor.py — Фаза 1 Rozetka-репрайсера (задача власника 2026-08-16):
тягне з кабінету Rozetka дані для конкурентного ціноутворення, БЕЗ скрейпінгу вітрини:

  1. «Моніторинг цін» (Аналітика) — по SKU: поточна ціна, РЕКОМЕНДОВАНА ціна Rozetka, мін. ринкова,
     % відхилення, сегмент. Внутрішні (продавці Rozetka) + Зовнішні (інші майданчики).
  2. «Комісія по товарам» (Мій баланс) — ТОЧНА комісія % на кожен SKU + Rozetka-категорія (rz_id).

ЧОМУ ТАК (а не токен-API): дані живуть на `cabinet-seller.rozetka.com.ua` за автентифікацією
Bearer-токеном застосунку (не за ROZETKA_API_TOKEN, який для `api-seller`). Токен НЕ в cookie й не в
localStorage.authData (там лише notif-JWT) — Angular тримає його в пам'яті сервісу. Тому робочий шлях:
Playwright відкриває кабінет під збереженою сесією, ми ПЕРЕХОПЛЮЄМО заголовок `Authorization`, який
шле сам застосунок, і робимо ним прямі API-запити (пагінація під нашим контролем). Без auth ці
ендпоінти віддавали 504/invalid_credentials — саме тому.

АВТЕНТИФІКАЦІЯ: спільна сесія з `rozetka_cabinet_scraper.py` (`--login` раз, headed, локально на
Windows — потрібен екран). storageState у .local_secrets/ (gitignore). Токен НІКОЛИ не в код/лог/чат.

ДОВГОВІЧНІСТЬ (keepalive): токен рефрешиться, поки застосунок активний. `--keepalive` відкриває кабінет
(рефреш) і пересохраняє storageState — ганяти по таймеру ~30-40 хв. Протухання → Telegram-алерт.

РЕЗУЛЬТАТ: `.local_secrets/rozetka_competitor_state.json` — читає Фаза 2 (репрайсер).

ЗАПУСК:
    python rozetka_price_monitor.py --login       # раз: вікно, логін, сесію збережено
    python rozetka_price_monitor.py               # пул даних (headless)
    python rozetka_price_monitor.py --keepalive   # тримати сесію теплою (по таймеру)
"""
import argparse
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from telegram_notify import send_telegram_message

load_dotenv()

BASE_DIR = Path(__file__).parent
STATE_FILE = Path(os.environ.get("ROZETKA_CABINET_STATE_FILE",
                                 str(BASE_DIR / ".local_secrets" / "rozetka_cabinet_state.json")))
OUTPUT_FILE = BASE_DIR / ".local_secrets" / "rozetka_competitor_state.json"
_NO_TELEGRAM = os.environ.get("AUDIT_NO_TELEGRAM") == "1"

CABINET_URL = "https://seller.rozetka.com.ua/main/cabinet"
LOGIN_START_URL = "https://seller.rozetka.com.ua/"
API = "https://cabinet-seller.rozetka.com.ua"
NAV_TIMEOUT_MS = 30000
API_TIMEOUT_MS = 45000
API_RETRIES = 3
PAGE_SIZE = 100


class RozetkaSessionError(Exception):
    pass


def _notify(msg: str) -> None:
    if _NO_TELEGRAM:
        return
    try:
        send_telegram_message(msg)
    except Exception as e:
        print(f"[RzPrice] Telegram не надіслано (не критично): {e}", file=sys.stderr)


def create_state() -> None:
    print("[RzPrice] Відкриваю seller.rozetka.com.ua. Залогінься, потім натисни Enter у терміналі...")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        ctx = browser.new_context()
        ctx.new_page().goto(LOGIN_START_URL, timeout=NAV_TIMEOUT_MS)
        try:
            input()
        except EOFError:
            print("[RzPrice] Немає інтерактивного вводу — --login запускай вручну в терміналі.", file=sys.stderr)
            browser.close(); sys.exit(1)
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        ctx.storage_state(path=str(STATE_FILE))
        browser.close()
    print(f"[RzPrice] Сесію збережено: {STATE_FILE}")


def _open_cabinet(page, cap: dict) -> None:
    """Відкриває кабінет під сесією й перехоплює Authorization-заголовок застосунку (у cap['auth']).
    Редірект на логін / нема токена → RozetkaSessionError (сесія протухла)."""
    def on_req(req):
        if "cabinet-seller.rozetka.com.ua" in req.url:
            a = req.headers.get("authorization")
            if a and "auth" not in cap:
                cap["auth"] = a
    page.on("request", on_req)
    page.goto(CABINET_URL, timeout=NAV_TIMEOUT_MS)
    page.wait_for_load_state("networkidle", timeout=NAV_TIMEOUT_MS)
    if "cabinet" not in page.url and "/main" not in page.url:
        raise RozetkaSessionError(f"сесію не прийнято — редірект на {page.url}")
    if not cap.get("auth"):
        raise RozetkaSessionError("не перехоплено Authorization-заголовок (сесія протухла / змінився кабінет)")


def _api_get(page, path: str, auth: str):
    """GET на cabinet-seller із перехопленим Bearer-заголовком. Ретраї на транзієнтні 502/503/504."""
    for attempt in range(API_RETRIES):
        try:
            r = page.request.get(API + path, headers={"authorization": auth}, timeout=API_TIMEOUT_MS)
            if r.ok:
                try:
                    return r.json()
                except Exception:
                    return None
            if r.status not in (502, 503, 504):
                return None
        except PlaywrightTimeoutError:
            pass
        time.sleep(1.5 * (attempt + 1))
    return None


def _pull_paginated(page, path_base: str, key: str, auth: str) -> list:
    """Тягне всі сторінки {path_base}?page&pageSize, збираючи content[key]. Спиняється, коли
    сторінка порожня/коротша за PAGE_SIZE, або досягнуто pageCount із _meta."""
    items, pg = [], 1
    while pg <= 1000:
        data = _api_get(page, f"{path_base}?page={pg}&pageSize={PAGE_SIZE}", auth)
        content = (data or {}).get("content") if isinstance(data, dict) else None
        rows = content.get(key) if isinstance(content, dict) else None
        if not rows:
            break
        items.extend(rows)
        meta = content.get("_meta") if isinstance(content, dict) else None
        page_count = (meta or {}).get("pageCount")
        if len(rows) < PAGE_SIZE or (page_count and pg >= page_count):
            break
        pg += 1
    return items


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def pull(page, auth: str) -> dict:
    """Моніторинг цін (internal+external) + комісії — ВСЕ ключується НАШИМ offer id.

    КЛЮЧІ (звірено живо 2026-08-16): наш offer id == `item_article` комісії (1762/1762 збіг).
    Моніторинг має лише внутрішній Rozetka `goods_id`, який == `item_id` комісії → комісія
    служить МІСТКОМ (goods_id → item_id → item_article = наш offer id). Тому спершу тягнемо
    комісії (будуємо міст), потім моніторинг (ремапимо goods_id → наш offer id)."""
    out = {"at": datetime.now().isoformat(), "recommended": {}, "commission": {},
           "summary": {}, "unmapped_recommended": 0}

    bridge = {}  # rz_goods_id -> наш offer id (з комісій)
    for it in _pull_paginated(page, "/items-commissions/search", "commissions", auth):
        our_id = str(it.get("item_article") or "").strip()   # = наш offer id
        if not our_id:
            continue
        rz_gid = str(it.get("item_id") or "").strip()
        out["commission"][our_id] = {
            "rz_category_id": it.get("catalog_id"),
            "rz_category": it.get("catalog_name"),
            "commission_pct": _num(it.get("commission")),
            "price": _num(it.get("item_price")),
            "rz_goods_id": rz_gid or None,
        }
        if rz_gid:
            bridge[rz_gid] = our_id

    for scope in ("internal", "external"):
        summ = _api_get(page, f"/analytics/item-recommended-price/{scope}/percent-count", auth)
        if isinstance(summ, dict):
            out["summary"][scope] = summ.get("content") or summ
        for it in _pull_paginated(page, f"/analytics/item-recommended-price/{scope}/search", "items", auth):
            our_id = bridge.get(str(it.get("goods_id") or "").strip())
            if not our_id:                    # моніторинг без містка до нашого товару — пропускаємо
                out["unmapped_recommended"] += 1
                continue
            out["recommended"].setdefault(our_id, {})[scope] = {
                "current": _num(it.get("goods_price")),
                "recommended": _num(it.get("recommend_price")),
                "min_market": _num(it.get("min_price")),
                "diff_pct": _num(it.get("perc_diff_price")),
                "segment_id": it.get("segment_id"),
            }
    return out


def _with_session(fn):
    """Обгортка: headless-браузер під збереженою сесією, відкрити кабінет (+перехопити auth),
    викликати fn(page, auth), пересохранити оновлену сесію. Протухання → алерт + вихід."""
    if not STATE_FILE.exists():
        msg = f"🚨 rozetka_price_monitor: нема сесії ({STATE_FILE.name}). Запусти раз `--login`."
        print(f"[RzPrice] {msg}", file=sys.stderr); _notify(msg); sys.exit(1)
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(storage_state=str(STATE_FILE))
        page = ctx.new_page()
        cap = {}
        try:
            _open_cabinet(page, cap)
            result = fn(page, cap["auth"])
            ctx.storage_state(path=str(STATE_FILE))  # оновлений (рефрешнутий) стан
            return result
        except (PlaywrightTimeoutError, RozetkaSessionError) as e:
            msg = f"🚨 rozetka_price_monitor: сесія протухла/збій ({e}). Перелогінься: `--login`."
            print(f"[RzPrice] {msg}", file=sys.stderr); _notify(msg); sys.exit(1)
        finally:
            try:
                browser.close()
            except Exception:
                pass


def run_pull() -> None:
    data = _with_session(pull)
    rec_n, com_n = len(data["recommended"]), len(data["commission"])
    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"[RzPrice] Збережено {OUTPUT_FILE.name}: рекомендацій {rec_n} SKU, комісій {com_n} SKU.")
    if rec_n == 0 and com_n == 0:
        _notify("⚠️ rozetka_price_monitor: 0 даних (ендпоінти лежать або верстка змінилась) — перевір.")


def keepalive() -> None:
    _with_session(lambda page, auth: None)
    print("[RzPrice] keepalive: сесію оновлено.")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--login", action="store_true", help="Раз: вікно, логін, зберегти спільну сесію.")
    ap.add_argument("--keepalive", action="store_true", help="Тримати сесію теплою (по таймеру ~30-40 хв).")
    a = ap.parse_args()
    if a.login:
        create_state()
    elif a.keepalive:
        keepalive()
    else:
        run_pull()


if __name__ == "__main__":
    main()

# -*- coding: utf-8 -*-
"""stock_snapshot.py — свіжий знімок залишків Toysi для сайту: site/stock.json (рішення власника 13.10.2026: «відставання наявності — критично»).

ПРОБЛЕМА (виміряно 13.10): XML Toysi генерується на запит (Last-Modified = час запиту, Cache-Control max-age=0), тож лаг НАШ: сайт перебудовується раз на 2 год, а залишок у index.json — із моменту збірки.
Цей модуль (таймер кожні 15 хв, ~18 с і 70 МБ) качає XML і АТОМАРНО пише у DocumentRoot сайту site/stock.json = {"ts": "<ISO>", "n": <к-ть позицій>, "s": {"<id>": <залишок>}}.
Файл ПУБЛІЧНИЙ (лише id і залишок, без цін/собівартості). Його читають: (1) бекенд замовлень site_order_api — відмовляє на рядки, яких уже нема (409 stock_changed); (2) сторінки сайту (Shopify-code) — ховають «Купити».
Захист: порожній чи явно усічений каталог (parser.assert_catalog_size_sane — існуючий поріг Toysi) НЕ перезаписує останній добрий файл, сигнал throttled раз на добу.
"""
import json
import os
import sys
from datetime import datetime
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

SITE_DIR = Path(os.environ.get("SITE_DIR", str(Path(__file__).resolve().parent / "site")))
STOCK_FILE = SITE_DIR / "stock.json"


def _alert(key: str, text: str) -> None:
    try:
        from telegram_notify import send_throttled_alert
        send_throttled_alert(key, text, cooldown_sec=24 * 60 * 60)
    except Exception as e:  # noqa: BLE001
        print(f"[stock_snapshot] сигнал не надіслано: {type(e).__name__}", file=sys.stderr)


def _ts(now: datetime | None) -> str:
    """ISO-час НАЇВНИЙ локальний (як і був). Пишемо без пояса свідомо: старий читач site_order_api на час з поясом мовчки вимикав перевірку залишку (TypeError → fail-open без сліду), тож перехід на пояс можливий лише ПІСЛЯ того, як усі читачі (нова версія site_order_api вже вміє aware-ts) розгорнуто й перезапущено."""
    return (now or datetime.now()).isoformat(timespec="seconds")


def build_snapshot(catalog: dict, now: datetime | None = None) -> dict:
    """{"ts", "n", "s": {id: залишок(int)}} з каталогу Toysi (parser.fetch_toysi_catalog). Для діапазонних залишків Toysi («5...10 шт.», ≈21% каталогу) пишемо ВЕРХНЮ межу (stock_max):
    сайт відмовляє лише коли замовлено більше за те, що Toysi МОЖЕ мати; нижня межа дала б хибні відмови (клієнт хоче 7 шт. із «5...10»)."""
    s = {}
    for pid, it in catalog.items():
        if it.get("stock_known") is False:      # залишок у XML відсутній/без цифр: «невідомо», у знімок НЕ пишемо (клієнт не повинен ховати товар через нерозпізнане поле)
            continue
        try:
            s[str(pid)] = max(0, int(it.get("stock_max", it.get("stock")) or 0))
        except (TypeError, ValueError):
            continue                             # нечислове значення — теж «невідомо», не «0»
    return {"ts": _ts(now), "n": len(s), "s": s}


def write_cost_file(catalog: dict, out=None, now: datetime | None = None) -> bool:
    """ПРИВАТНИЙ файл собівартості для знижок сайту (site_promo): {"ts", "c": {id: собівартість}} ПОЗА DocumentRoot (/opt/plutustoys/promo_cost.json, права 0600). Збій тут НЕ ламає знімок залишків:
    без файлу знижок просто нема (fail-closed). Собівартість = competitor_pricing.real_toysi_cost (та сама, що в закритій формулі ціни сайту)."""
    try:
        import competitor_pricing as cp
        import site_promo
        out = Path(out or site_promo.COST_FILE)
        costs = {}
        for pid, it in catalog.items():
            try:
                c = cp.real_toysi_cost(it)
            except (ValueError, TypeError, KeyError):
                continue
            if c and c > 0:
                costs[str(pid)] = round(float(c), 2)
        tmp = out.with_name(out.name + ".tmp")
        tmp.write_text(json.dumps({"ts": (now or datetime.now()).isoformat(timespec="seconds"), "c": costs}, separators=(",", ":")), encoding="utf-8")
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            pass
        os.replace(tmp, out)
        return True
    except Exception as e:  # noqa: BLE001
        print(f"[stock_snapshot] файл собівартості не записано: {type(e).__name__}: {e}", file=sys.stderr)
        _alert("promo_cost_write", f"⚠️ stock_snapshot: файл собівартості для знижок не записано ({type(e).__name__}); через 6 год знижки сайту тихо зникнуть (fail-closed).")
        return False


def run_once(fetch=None, out=None, now=None, cost_out=None) -> bool:
    """True — знімок записано. Не кидає назовні."""
    out = Path(out or STOCK_FILE)
    try:
        if fetch is None:
            import parser as tp
            fetch = tp.fetch_toysi_catalog
        catalog = fetch()
        if not catalog:
            _alert("stock_snapshot_empty", "⚠️ stock_snapshot: каталог Toysi порожній або недоступний — site/stock.json НЕ оновлено (лишається попередній).")
            return False
        try:
            import parser as tp
            tp.assert_catalog_size_sane(catalog)
        except Exception as e:  # noqa: BLE001 (CatalogSizeError)
            _alert("stock_snapshot_small", f"⚠️ stock_snapshot: {e} site/stock.json НЕ оновлено (лишається попередній).")
            return False
        snap = build_snapshot(catalog, now)
        if snap["n"] < len(catalog):          # факт, не поріг: частина позицій з невідомим залишком (формат Toysi змінився?) — у знімок їх немає, сайт їх не чіпає
            _alert("stock_snapshot_unknown", f"⚠️ stock_snapshot: у {len(catalog) - snap['n']} із {len(catalog)} позицій залишок Toysi не розпізнано (поле відсутнє/без цифр) — їх немає у stock.json (сайт не ховає й не відмовляє). Перевірити формат <ostatok>.")
        out.parent.mkdir(parents=True, exist_ok=True)
        tmp = out.with_name(out.name + ".tmp")
        tmp.write_text(json.dumps(snap, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        os.replace(tmp, out)                     # атомарно: відвідувач/бекенд ніколи не бачить напівзаписаний файл
        if os.environ.get("PROMO_COST_WRITE") == "1":   # рішення власника 13.10: знижки не погоджені → приватний файл собівартості НЕ пишемо, доки він не скаже (вмикається змінною в .env)
            write_cost_file(catalog, cost_out, now)
        print(f"[stock_snapshot] ✅ {snap['n']} позицій, ts={snap['ts']}")
        return True
    except Exception as e:  # noqa: BLE001
        print(f"[stock_snapshot] збій: {type(e).__name__}: {e}", file=sys.stderr)
        return False


if __name__ == "__main__":
    sys.exit(0 if run_once() else 1)

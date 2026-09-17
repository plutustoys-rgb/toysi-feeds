#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
nova_poshta_warehouse_cache.py — локальний кеш довідника відділень/поштоматів Нової Пошти,
за офіційно рекомендованим патерном (developers.novaposhta.ua, метод getWarehouses):
«Регулярне щоденне оновлення довідника є необхідним... рекомендовано налаштувати регламентне
оновлення довідника всіх складів, відділень та поштоматів ЩОНОЧІ» (перевірено живо 2026-09-17,
технічні_вимоги_маркетплейсів/nova_poshta.md).

ЧОМУ: nova_poshta.warehouse_by_ref() досі робив ЖИВИЙ запит getWarehouses(Ref=...) на КОЖНЕ
замовлення — throttling НП під навантаженням спричинив money-risk (інцидент 906260104,
Rozetka-замовлення пішло в Toysi без відділення; закрито ретраєм+відкладанням форварду,
PR #553-556). Кеш прибирає ПРИЧИНУ throttling структурно для цього класу запитів: warehouse_by_ref
читає з ЛОКАЛЬНОЇ таблиці (мілісекунди, без мережі, без throttling), а не з живого API на
кожне замовлення. Лише коли Ref немає в кеші (нове відділення / кеш ще не синхронізовано) —
фолбек на живий запит, як і досі.

СИНХРОНІЗАЦІЯ: окремий nightly-процес (np-warehouse-sync.service/.timer), НЕ частина
order_pipeline — повний прогін через ~десятки тисяч записів (посторінково, Page/Limit=500,
з паузою між сторінками — throttling ловиться навіть на 2 запитах поспіль без паузи, перевірено
живо) не повинен сповільнювати форвард замовлень.

СХЕМА: окремий SQLite-файл (НЕ orders.db — інша природа даних: довідник, не транзакції).
Існуючі записи, яких немає в новому прогоні (відділення закрилось), НЕ видаляються
автоматично — тихе видалення через часткову/перервану синхронізацію гірше за трохи застарілий
запис, що просто не використається (Ref з боку замовлення теж застаріє разом з ним).
"""
import os
import sqlite3
import sys
import time
from contextlib import contextmanager
from datetime import datetime

from nova_poshta import _call, NovaPoshtaAPIError

CACHE_DB_PATH = os.environ.get("NP_WAREHOUSE_CACHE_PATH", "np_warehouses_cache.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS warehouses (
    ref         TEXT PRIMARY KEY,
    city_ref    TEXT NOT NULL,
    number      TEXT NOT NULL,
    description TEXT,
    updated_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sync_meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""

PAGE_LIMIT = 500          # максимум на сторінку за документацією getWarehouses
PAGE_DELAY_SEC = 1.0      # пауза між сторінками — throttling ловиться навіть на 2 запитах
                          # поспіль без паузи (звірено живо 2026-09-17)
PAGE_RETRY_ATTEMPTS = 3
PAGE_RETRY_DELAY_SEC = 3.0  # відновлення throttle ~3с (той самий факт, що й у warehouse_by_ref)


@contextmanager
def _connection(db_path=None):
    conn = sqlite3.connect(db_path or CACHE_DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def init_cache(db_path=None) -> None:
    with _connection(db_path) as conn:
        conn.executescript(SCHEMA)
        conn.commit()


def lookup_ref(ref: str, db_path=None) -> dict | None:
    """Читає {"city_ref":.., "number":.., "description":..} з ЛОКАЛЬНОГО кешу. None — Ref не
    в кеші (кеш ще не синхронізовано / нове відділення) — виклику вирішувати, чи фолбечити на
    живий API. НЕ робить жодного мережевого виклику."""
    if not ref or not os.path.exists(db_path or CACHE_DB_PATH):
        return None
    with _connection(db_path) as conn:
        row = conn.execute(
            "SELECT city_ref, number, description FROM warehouses WHERE ref=?", (ref,)
        ).fetchone()
    if not row:
        return None
    return {"city_ref": row["city_ref"], "number": row["number"], "description": row["description"] or ""}


def cache_age_hours(db_path=None) -> float | None:
    """Скільки годин тому востаннє УСПІШНО завершилась повна синхронізація. None — кешу ще
    нема або жодна синхронізація ще не завершилась чисто."""
    if not os.path.exists(db_path or CACHE_DB_PATH):
        return None
    with _connection(db_path) as conn:
        row = conn.execute("SELECT value FROM sync_meta WHERE key='last_full_sync_at'").fetchone()
    if not row:
        return None
    try:
        last = datetime.fromisoformat(row["value"])
    except ValueError:
        return None
    return (datetime.now() - last).total_seconds() / 3600


def _fetch_page(page: int, page_limit: int) -> list:
    """Одна сторінка з ретраєм на throttle (той самий патерн, що nova_poshta.warehouse_by_ref)."""
    last_err = None
    for attempt in range(PAGE_RETRY_ATTEMPTS):
        try:
            return _call("AddressGeneral", "getWarehouses", {"Page": str(page), "Limit": str(page_limit)})
        except NovaPoshtaAPIError as e:
            last_err = e
            if attempt < PAGE_RETRY_ATTEMPTS - 1:
                time.sleep(PAGE_RETRY_DELAY_SEC)
    raise last_err


def sync_full_directory(db_path=None, page_limit: int = PAGE_LIMIT) -> dict:
    """Повне посторінкове завантаження довідника — умова завершення циклу: порожня сторінка
    (за офіційною документацією). UPSERT у кеш. Повертає {"pages":N, "records":N, "errors":[...]}.
    Помилка ОДНІЄЇ сторінки (усі ретраї вичерпано) зупиняє прогін best-effort — наступний
    нічний прогін підхопить решту; sync_meta.last_full_sync_at НЕ оновлюється при помилці, щоб
    cache_age_hours() чесно показував, що ПОВНОЇ синхронізації давно не було."""
    init_cache(db_path)
    page = 1
    total = 0
    errors = []
    with _connection(db_path) as conn:
        while True:
            try:
                batch = _fetch_page(page, page_limit)
            except NovaPoshtaAPIError as e:
                errors.append(f"сторінка {page}: {e}")
                break
            if not batch:
                break
            now = datetime.now().isoformat(timespec="seconds")
            for w in batch:
                ref = w.get("Ref")
                city_ref = w.get("CityRef")
                number = w.get("Number")
                if not ref or not city_ref or number is None:
                    continue  # неповний запис від НП — пропускаємо, не псуємо кеш сміттям
                conn.execute(
                    "INSERT INTO warehouses (ref, city_ref, number, description, updated_at) "
                    "VALUES (?,?,?,?,?) ON CONFLICT(ref) DO UPDATE SET "
                    "city_ref=excluded.city_ref, number=excluded.number, "
                    "description=excluded.description, updated_at=excluded.updated_at",
                    (ref, city_ref, str(number), w.get("Description") or "", now),
                )
                total += 1
            conn.commit()
            print(f"[np_warehouse_cache] сторінка {page}: {len(batch)} записів (разом {total})",
                  file=sys.stderr)
            if len(batch) < page_limit:
                break  # неповна сторінка — останній фрагмент даних (документований критерій)
            page += 1
            time.sleep(PAGE_DELAY_SEC)
        if not errors:
            conn.execute(
                "INSERT INTO sync_meta (key, value) VALUES ('last_full_sync_at', ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (datetime.now().isoformat(timespec="seconds"),),
            )
            conn.commit()
    return {"pages": page, "records": total, "errors": errors}


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    result = sync_full_directory()
    print(f"[np_warehouse_cache] Синхронізація завершена: {result['records']} записів, "
          f"{result['pages']} сторінок, помилок: {len(result['errors'])}")
    if result["errors"]:
        for e in result["errors"]:
            print(f"  - {e}", file=sys.stderr)
        sys.exit(1)
    sys.exit(0)

"""seo_content_db.py — сховище й доступ до SEO-описів товарів (seo_content.db, SQLite).

Навіщо (задача Cowork STATUS.md найновіше-18/19/20, 2026-08-13): описи у фіді йдуть майже
без змін від Toysi — той самий текст у десятків дропшиперів (duplicate content, Google/Prom
не ранжують). Рішення: унікальні SEO-описи. Пілот — 30 «золотих» текстів, написаних Cowork
вручну й ЗАТВЕРДЖЕНИХ власником («мені подобається», 2026-08-13), у `seo_pilot_manual_batch.json`.
Фаза 2 (пізніше) — шаблонний rule-based генератор пише сюди ж на масштаб.

Модель даних: SQLite `seo_content.db` (VPS-runtime, у .gitignore — account-specific, лише
тексти, без секретів). Таблиця `seo` (sku PK, source_hash, seo_title, seo_meta_description,
seo_long_html, source, approved, generated_at). `source_hash` = хеш тексту-джерела Toysi →
детекція зміни для регенерації (Фаза 2); для ручного пілота лишається порожнім (він
затверджений людиною, не залежить від автозмін).

Інтеграція у фід: `load_approved_prom_overrides()` віддає {sku: {"description": long_html}}
у ТОМУ САМОМУ форматі, що вже вживає `generate_prom_feed._build_xml` (desc_override) — тож
approved SEO ПОВНІСТЮ замінює сирий опис Toysi, з фолбеком на Toysi для решти SKU (поступова
безпечна розкатка). БЕЗ-ручного-кроку деплой: якщо БД порожня/відсутня, бутстрапимо з
committed `seo_pilot_manual_batch.json` (idempotent).
"""
import argparse
import hashlib
import json
import sys
from pathlib import Path

import sqlite3

SEO_DB = Path(__file__).parent / "seo_content.db"
PILOT_BATCH = Path(__file__).parent / "seo_pilot_manual_batch.json"
TEMPLATE_BATCH = Path(__file__).parent / "seo_template_batch.json"
# Committed batch-файли, які bootstrap-имо у VPS-runtime БД. Пілот ОСТАННІМ → рукописне
# «золото» перекриває шаблон на будь-якому спільному SKU (шаблон їх і так виключає при генерації).
BOOTSTRAP_BATCHES = [TEMPLATE_BATCH, PILOT_BATCH]

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(SEO_DB)
    conn.execute(
        """CREATE TABLE IF NOT EXISTS seo (
            sku TEXT PRIMARY KEY,
            source_hash TEXT DEFAULT '',
            seo_title TEXT DEFAULT '',
            seo_meta_description TEXT DEFAULT '',
            seo_long_html TEXT DEFAULT '',
            source TEXT DEFAULT '',
            approved INTEGER DEFAULT 0,
            generated_at TEXT DEFAULT ''
        )"""
    )
    return conn


def compute_source_hash(item: dict) -> str:
    """Хеш тексту-джерела Toysi (name+description+params) — щоб Фаза 2 регенерувала лише
    коли Toysi реально змінив опис. Для ручного пілота не використовується (approved людиною)."""
    parts = [
        str((item or {}).get("name", "")),
        str((item or {}).get("description", "")),
        json.dumps((item or {}).get("params", []), ensure_ascii=False, sort_keys=True),
    ]
    return hashlib.sha256("\x1e".join(parts).encode("utf-8")).hexdigest()


def upsert(sku, *, source_hash="", seo_title="", seo_meta_description="",
           seo_long_html="", source="", approved=0, generated_at="") -> None:
    conn = _connect()
    conn.execute(
        "INSERT OR REPLACE INTO seo "
        "(sku, source_hash, seo_title, seo_meta_description, seo_long_html, source, approved, generated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (str(sku), source_hash, seo_title, seo_meta_description, seo_long_html,
         source, int(bool(approved)), generated_at),
    )
    conn.commit()
    conn.close()


def import_batch(json_path=PILOT_BATCH, catalog: dict = None) -> int:
    """Імпорт batch-файлу (seo_pilot_manual_batch.json) у БД. `approved` — з meta.approved
    (пілот затверджений власником). Idempotent (INSERT OR REPLACE за sku). source_hash —
    з catalog, якщо переданий (для майбутньої детекції змін), інакше порожній. Повертає
    кількість імпортованих."""
    data = json.loads(Path(json_path).read_text(encoding="utf-8"))
    meta = data.get("meta", {}) if isinstance(data, dict) else {}
    approved = 1 if meta.get("approved") else 0
    source = meta.get("source", "batch")
    gen = meta.get("generated_at", "")
    n = 0
    for it in data.get("items", []):
        sku = str(it.get("sku") or "").strip()
        if not sku:
            continue
        sh = compute_source_hash(catalog.get(sku, {})) if catalog else ""
        upsert(
            sku, source_hash=sh,
            seo_title=it.get("seo_title", ""),
            seo_meta_description=it.get("seo_meta_description", ""),
            seo_long_html=it.get("seo_long_html", ""),
            source=source, approved=approved, generated_at=gen,
        )
        n += 1
    return n


def _count() -> int:
    conn = _connect()
    try:
        return conn.execute("SELECT COUNT(*) FROM seo").fetchone()[0]
    finally:
        conn.close()


def _ensure_batches_imported() -> None:
    """Імпортує committed batch-файли (шаблонний + пілот) у БД — по одному разу на кожну ВЕРСІЮ
    файлу (ledger за sha вмісту). Розв'язує count==0-пастку: коли БД уже має пілот, новий/змінений
    batch усе одно підхоплюється (без ручного VPS-кроку). Незмінний batch не реімпортується.
    Пілот імпортується ОСТАННІМ → золото перекриває шаблон на спільних SKU."""
    conn = _connect()
    conn.execute("CREATE TABLE IF NOT EXISTS imported_batches (sha TEXT PRIMARY KEY, name TEXT)")
    seen = {r[0] for r in conn.execute("SELECT sha FROM imported_batches").fetchall()}
    conn.close()
    for b in BOOTSTRAP_BATCHES:
        if not b.exists():
            continue
        sha = hashlib.sha256(b.read_bytes()).hexdigest()
        if sha in seen:
            continue
        import_batch(b)
        c = _connect()
        c.execute("INSERT OR REPLACE INTO imported_batches (sha, name) VALUES (?, ?)", (sha, b.name))
        c.commit()
        c.close()


def load_approved_prom_overrides() -> dict:
    """{sku: {"description": seo_long_html}} для approved=1 з непорожнім long_html — формат
    desc_override, який уже вживає generate_prom_feed._build_xml (Prom першим). Бутстрап без
    ручного кроку: committed batch-файли (пілот + шаблон Фази-2) імпортуються по версії вмісту.
    Пошкодження БД/файлу → порожній dict (фід тихо падає на сирі описи Toysi, нічого не ламає)."""
    try:
        _ensure_batches_imported()
        conn = _connect()
        rows = conn.execute(
            "SELECT sku, seo_long_html FROM seo WHERE approved = 1 AND seo_long_html != ''"
        ).fetchall()
        conn.close()
        return {str(sku): {"description": html} for sku, html in rows}
    except (sqlite3.Error, ValueError, OSError) as e:
        print(f"[seo] load_approved_prom_overrides впало ({e}) — фолбек на сирі описи Toysi.",
              file=sys.stderr)
        return {}


def load_all_meta() -> dict:
    """{sku: {"approved": int, "source_hash": str}} для ВСІХ записів — для генератора Фази 2
    (пропускати вже approved=1 «золотий» пілот і незмінені за source_hash). Помилка → {}."""
    try:
        if not SEO_DB.exists():
            return {}
        conn = _connect()
        rows = conn.execute("SELECT sku, approved, source_hash FROM seo").fetchall()
        conn.close()
        return {str(sku): {"approved": int(appr or 0), "source_hash": sh or ""} for sku, appr, sh in rows}
    except (sqlite3.Error, ValueError, OSError):
        return {}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--import", dest="import_path", metavar="JSON", nargs="?", const=str(PILOT_BATCH),
                    help="Імпортувати batch-файл (за замовчуванням seo_pilot_manual_batch.json) у seo_content.db.")
    ap.add_argument("--stats", action="store_true", help="Показати кількість записів (усього / approved).")
    args = ap.parse_args()

    if args.import_path:
        n = import_batch(args.import_path)
        print(f"[seo] Імпортовано {n} записів у {SEO_DB.name}.")
    if args.stats or not args.import_path:
        conn = _connect()
        total = conn.execute("SELECT COUNT(*) FROM seo").fetchone()[0]
        appr = conn.execute("SELECT COUNT(*) FROM seo WHERE approved=1 AND seo_long_html!=''").fetchone()[0]
        conn.close()
        print(f"[seo] seo_content.db: усього {total}, approved із описом {appr}.")


if __name__ == "__main__":
    main()

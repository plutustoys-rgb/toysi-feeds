#!/usr/bin/env python3
"""social_ledger_report.py — звіти для маркетолога (SMM) з журналу постингу.

Закриває дві заявки SMM (2026-08-18, MARKETING_CHANNEL):
  • ЗАЯВКА 2 — експорт ledger `social_posted_ledger.json` у читабельний CSV (покриття каталогу/історія).
  • ЗАЯВКА 3 — самодіагностика «розклад vs факт»: скільки постів МАВ дати таймер vs скільки реально в
    ledger, з переліком днів-недоборів (за правилом власника про самодіагностичні алерти).

Дані НЕФІНАНСОВІ (platform/sku/at/post_id/url) — CSV кладеться у reports/ (спільна папка), фінансів нема.

ЛЕДЖЕР: `{platform: {sku: {at, post_id, url}}}` (social_auto_poster). Кожен SKU — один запис (пости
йдуть по НОВИХ sku), тож дата `at` = коли той sku запостили → лічба записів по даті ≈ пости/день.

РОЗКЛАД (очікування/день на платформу): FB 1, IG 1 (соц-таймери 1×/день; env override нижче).

ЗАПУСК:
    python social_ledger_report.py                       # обидва: CSV + розклад-vs-факт за 14 днів
    python social_ledger_report.py --csv --out post.csv   # лише експорт CSV (заявка 2)
    python social_ledger_report.py --schedule --days 7 --alert   # лише діагностика + Telegram при недоборі
"""
import argparse
import csv
import json
import os
import sys
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE_DIR = Path(__file__).parent
LEDGER = Path(os.environ.get("SOCIAL_LEDGER_PATH", str(BASE_DIR / "social_posted_ledger.json")))
REPORTS_DIR = BASE_DIR / "reports"
# очікувана к-сть постів/день на платформу (соц-таймери). Override: SOCIAL_EXPECTED_FB / _IG.
EXPECTED = {
    "fb": int(os.environ.get("SOCIAL_EXPECTED_FB", "1")),
    "ig": int(os.environ.get("SOCIAL_EXPECTED_IG", "1")),
}


def load_ledger() -> dict:
    """{platform: {sku: {at, post_id, url}}}. Legacy плаский {sku:{...}} → під 'fb' (як social_auto_poster)."""
    if not LEDGER.exists():
        return {}
    try:
        raw = json.loads(LEDGER.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(raw, dict) or not raw:
        return {}
    if any(isinstance(v, dict) and ("at" in v or "post_id" in v) for v in raw.values()):
        return {"fb": raw}
    return raw


def _rows(led: dict) -> list:
    out = []
    for platform, skus in led.items():
        if not isinstance(skus, dict):
            continue
        for sku, rec in skus.items():
            if not isinstance(rec, dict):
                continue
            out.append({"platform": platform, "sku": sku, "at": rec.get("at", ""),
                        "post_id": rec.get("post_id", ""), "url": rec.get("url", "")})
    return sorted(out, key=lambda r: r["at"], reverse=True)


def export_csv(led: dict, out_path: Path) -> int:
    rows = _rows(led)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["platform", "sku", "at", "post_id", "url"])
        w.writeheader()
        w.writerows(rows)
    return len(rows)


def _date_of(at: str) -> str:
    """'2026-08-14T11:00:00' → '2026-08-14' (порожнє → '')."""
    return at[:10] if at and len(at) >= 10 else ""


def schedule_vs_fact(led: dict, days: int) -> dict:
    """За останні `days` днів: факт постів/день на платформу проти EXPECTED. Повертає структуру
    з переліком днів-недоборів. День БЕЗ жодного запису в ledger по даті рахуємо як 0 (пропущено)."""
    # факт: {(platform, date): count}
    counts = defaultdict(int)
    for r in _rows(led):
        d = _date_of(r["at"])
        if d:
            counts[(r["platform"], d)] += 1
    today = datetime.now().date()
    window = [(today - timedelta(days=i)).isoformat() for i in range(days)]
    report = {}
    for platform, expected in EXPECTED.items():
        shortfalls = []
        total_expected = expected * days
        total_actual = 0
        for d in window:
            actual = counts.get((platform, d), 0)
            total_actual += actual
            if actual < expected:
                shortfalls.append({"date": d, "expected": expected, "actual": actual})
        report[platform] = {"expected_total": total_expected, "actual_total": total_actual,
                            "shortfall_days": shortfalls}
    return report


def _alert(text: str) -> None:
    try:
        from telegram_notify import send_telegram_message
        send_telegram_message(text)
    except Exception as e:  # noqa: BLE001
        print(f"[social-report] Telegram не надіслано (не критично): {e}", file=sys.stderr)


def print_schedule(report: dict, days: int) -> bool:
    """Друкує звіт; повертає True, якщо є недобір (для --alert / exit-коду)."""
    any_short = False
    print(f"\n=== РОЗКЛАД vs ФАКТ (останні {days} днів) ===")
    for platform, r in report.items():
        line = (f"{platform.upper()}: очікувалось {r['expected_total']}, реально {r['actual_total']}"
                f" — недоборів днів: {len(r['shortfall_days'])}")
        print(line)
        for s in r["shortfall_days"]:
            print(f"    {s['date']}: {s['actual']}/{s['expected']} (пропущено)")
            any_short = True
    if not any_short:
        print("  ✅ недоборів нема — розклад тримається")
    return any_short


def main() -> None:
    ap = argparse.ArgumentParser(description="Звіти постингу для SMM: ledger→CSV + розклад-vs-факт.")
    ap.add_argument("--csv", action="store_true", help="лише експорт ledger у CSV (заявка 2)")
    ap.add_argument("--schedule", action="store_true", help="лише діагностика розклад-vs-факт (заявка 3)")
    ap.add_argument("--days", type=int, default=14, help="вікно днів для розклад-vs-факт")
    ap.add_argument("--out", help="шлях CSV (за замовч. reports/social_ledger_YYYY-MM-DD.csv)")
    ap.add_argument("--alert", action="store_true", help="Telegram-алерт при недоборі (для таймера)")
    a = ap.parse_args()
    both = not (a.csv or a.schedule)

    led = load_ledger()
    if not led:
        print(f"[social-report] ledger порожній/відсутній: {LEDGER}", file=sys.stderr)

    if a.csv or both:
        out = Path(a.out) if a.out else REPORTS_DIR / f"social_ledger_{datetime.now().strftime('%Y-%m-%d')}.csv"
        n = export_csv(led, out)
        print(f"[social-report] ledger → {out} ({n} записів)")

    short = False
    if a.schedule or both:
        report = schedule_vs_fact(led, a.days)
        short = print_schedule(report, a.days)
        if a.alert and short:
            lines = [f"📅 SMM розклад-vs-факт ({a.days}д): недобір"]
            for platform, r in report.items():
                if r["shortfall_days"]:
                    lines.append(f"{platform.upper()}: {r['actual_total']}/{r['expected_total']}, "
                                 f"пропущено {len(r['shortfall_days'])} дн.")
            _alert("\n".join(lines) + "\n→ перевір лог social_auto_poster (rate-limit/збій).")

    sys.exit(1 if short else 0)


if __name__ == "__main__":
    main()

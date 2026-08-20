#!/usr/bin/env python3
"""social_insights_report.py — per-post метрики соцмереж у CSV (ЗАЯВКА SMM 1, 2026-08-18).

Для КОЖНОГО поста з ledger тягне метрики через Graph API і пише CSV, щоб SMM міряв гачки/формати
машинно, а не очима в Business Suite. Поля (за заявкою): platform, sku, posted_at, post_id,
media_type, tagged, views, reach, reactions, comments, shares, saves, profile_visits, link_clicks.

СТАН ПЛАТФОРМ:
  • IG — РОБОЧА: акаунт Business, insights віддаються (post_id у ledger = published media id).
  • FB — ЗАБЛОКОВАНА: PAGE-токену бракує scope `read_insights` (в OWNER_INBOX до власника). FB-рядки
    йдуть із даними ledger (posted_at/post_id) і ПОРОЖНІМИ метриками, доки scope не додано.

⚠️ АДАПТИВНО до версії API (v21.0): набір валідних insight-метрик залежить від типу медіа й версії;
код запитує розумний список, а при відмові — мінімальний безпечний, і бере ЩО ПОВЕРНУЛОСЬ (незнані
метрики → порожньо). Живу відповідність метрик звірити першим прогоном на VPS (токен там); є --probe.

ЗАПУСК (на VPS, де токен):
    python social_insights_report.py                      # IG per-post CSV у reports/
    python social_insights_report.py --platform all       # + FB-рядки (метрики порожні до scope)
    python social_insights_report.py --probe 1784xxxxxxx  # сирі insights одного медіа (звірити метрики)
"""
import argparse
import csv
import json
import os
import sys
from datetime import datetime
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE_DIR = Path(__file__).parent
LEDGER = Path(os.environ.get("SOCIAL_LEDGER_PATH", str(BASE_DIR / "social_posted_ledger.json")))
REPORTS_DIR = BASE_DIR / "reports"
GRAPH_VERSION = os.environ.get("GRAPH_VERSION", "v21.0")
FB_PAGE_ACCESS_TOKEN = os.environ.get("FB_PAGE_ACCESS_TOKEN", "").strip()
REQUEST_TIMEOUT = 30

# Мапа insight-метрик IG → колонки CSV. Різні типи медіа віддають різні підмножини; беремо що є.
_IG_METRIC_TO_COL = {
    "reach": "reach", "likes": "reactions", "comments": "comments", "shares": "shares",
    "saved": "saves", "plays": "views", "video_views": "views", "impressions": "views",
    "profile_visits": "profile_visits", "profile_activity": "profile_visits",
    "website_clicks": "link_clicks",
}
# Запитувані метрики (широкий список; API відкине незастосовні до типу — тоді мінімальний фолбек).
_IG_METRICS_WIDE = ["reach", "likes", "comments", "shares", "saved", "plays",
                    "profile_visits", "website_clicks"]
_IG_METRICS_MIN = ["reach", "likes", "comments", "saved"]

_COLUMNS = ["platform", "sku", "posted_at", "post_id", "media_type", "tagged", "views", "reach",
            "reactions", "comments", "shares", "saves", "profile_visits", "link_clicks"]


def load_ledger() -> dict:
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


def _get(url, params):
    import requests
    return requests.get(url, params=params, timeout=REQUEST_TIMEOUT)


def ig_media_fields(media_id: str) -> dict:
    """media_type/media_product_type/timestamp/like_count/comments_count. {} при збої."""
    base = f"https://graph.facebook.com/{GRAPH_VERSION}/{media_id}"
    try:
        r = _get(base, {"fields": "media_type,media_product_type,timestamp,like_count,comments_count",
                        "access_token": FB_PAGE_ACCESS_TOKEN})
        return r.json() if r.content else {}
    except Exception:  # noqa: BLE001
        return {}


def ig_media_insights(media_id: str, metrics: list) -> dict:
    """{metric_name: value} з /insights. При помилці на широкому наборі — фолбек на мінімальний.
    Незастосовні метрики IG відкидає помилкою на весь запит, тож і робимо два кроки."""
    base = f"https://graph.facebook.com/{GRAPH_VERSION}/{media_id}/insights"
    for metric_set in (metrics, _IG_METRICS_MIN):
        try:
            r = _get(base, {"metric": ",".join(metric_set), "access_token": FB_PAGE_ACCESS_TOKEN})
            j = r.json() if r.content else {}
        except Exception:  # noqa: BLE001
            return {}
        if isinstance(j, dict) and j.get("data"):
            out = {}
            for m in j["data"]:
                name = m.get("name")
                vals = m.get("values") or [{}]
                out[name] = vals[0].get("value") if isinstance(vals[0], dict) else None
            return out
        # якщо помилка саме через незастосовну метрику — пробуємо мінімальний набір
        if isinstance(j, dict) and j.get("error") and metric_set is not _IG_METRICS_MIN:
            continue
        return {}
    return {}


def _row_from_insights(platform, sku, posted_at, post_id, media_fields, insights) -> dict:
    row = {c: "" for c in _COLUMNS}
    row.update({"platform": platform, "sku": sku, "posted_at": posted_at, "post_id": post_id})
    mpt = (media_fields.get("media_product_type") or media_fields.get("media_type") or "").upper()
    row["media_type"] = "video" if mpt in ("REELS", "VIDEO") else ("photo" if mpt else "")
    row["posted_at"] = media_fields.get("timestamp") or posted_at
    # like/comments можуть прийти й окремими полями медіа (надійніше за insights для feed)
    if media_fields.get("like_count") is not None:
        row["reactions"] = media_fields["like_count"]
    if media_fields.get("comments_count") is not None:
        row["comments"] = media_fields["comments_count"]
    for metric, val in (insights or {}).items():
        col = _IG_METRIC_TO_COL.get(metric)
        if col and val is not None:
            row[col] = val
    return row


def build_ig_rows(led: dict) -> list:
    rows = []
    for sku, rec in (led.get("ig") or {}).items():
        if not isinstance(rec, dict):
            continue
        mid = rec.get("post_id")
        if not mid:
            continue
        mf = ig_media_fields(mid)
        ins = ig_media_insights(mid, _IG_METRICS_WIDE)
        rows.append(_row_from_insights("ig", sku, rec.get("at", ""), mid, mf, ins))
    return rows


def build_fb_placeholder_rows(led: dict) -> list:
    """FB заблокована scope read_insights → рядки лише з ledger-даними, метрики порожні."""
    rows = []
    for sku, rec in (led.get("fb") or {}).items():
        if not isinstance(rec, dict):
            continue
        row = {c: "" for c in _COLUMNS}
        row.update({"platform": "fb", "sku": sku, "posted_at": rec.get("at", ""),
                    "post_id": rec.get("post_id", ""), "media_type": "", "tagged": ""})
        rows.append(row)
    return rows


def write_csv(rows: list, out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=_COLUMNS, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def main() -> None:
    ap = argparse.ArgumentParser(description="Per-post метрики соцмереж у CSV (заявка SMM 1).")
    ap.add_argument("--platform", choices=["ig", "all"], default="ig",
                    help="ig (робоча) або all (+FB-рядки з порожніми метриками до scope)")
    ap.add_argument("--out", help="шлях CSV (за замовч. reports/social_insights_YYYY-MM-DD.csv)")
    ap.add_argument("--probe", metavar="MEDIA_ID", help="надрукувати сирі insights одного медіа (звірка метрик)")
    a = ap.parse_args()

    if not FB_PAGE_ACCESS_TOKEN:
        print("[insights] нема FB_PAGE_ACCESS_TOKEN (токен на VPS) — метрики не зняти.", file=sys.stderr)
        if not a.probe:
            sys.exit(2)

    if a.probe:
        print(f"[insights] --probe {a.probe}: media_fields =", json.dumps(ig_media_fields(a.probe), ensure_ascii=False))
        print(f"[insights] --probe {a.probe}: insights(wide) =",
              json.dumps(ig_media_insights(a.probe, _IG_METRICS_WIDE), ensure_ascii=False))
        return

    led = load_ledger()
    rows = build_ig_rows(led)
    if a.platform == "all":
        rows += build_fb_placeholder_rows(led)
    out = Path(a.out) if a.out else REPORTS_DIR / f"social_insights_{datetime.now().strftime('%Y-%m-%d')}.csv"
    write_csv(rows, out)
    ig_n = sum(1 for r in rows if r["platform"] == "ig")
    print(f"[insights] {out} — рядків {len(rows)} (IG {ig_n}"
          + (f", FB-плейсхолдери {len(rows)-ig_n}" if a.platform == "all" else "") + ")")
    if a.platform != "all":
        print("[insights] FB per-post — pending scope read_insights (OWNER_INBOX). Додам колонки, щойно токен матиме scope.")


if __name__ == "__main__":
    main()

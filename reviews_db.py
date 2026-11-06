# -*- coding: utf-8 -*-
"""reviews_db.py — бекенд відгуків покупців сайту (рішення власника 03.11.2026 «дозволяю, робіть відгуки»; контракт — site/REVIEWS_CONTRACT.md, код сайту — PR #1102).

Що робить (лише логіка й БД; HTTP-ендпоінти — site_order_api.py, модерація CLI — review_moderate.py):
  • таблиця `reviews` у тій самій orders.db: ОДИН рядок на (замовлення, товар); спершу «invited» (видано одноразове посилання), потім «pending» (покупець написав), далі «published» / «rejected» (модерація);
  • токен: `secrets.token_urlsafe(24)` (192 біти), у БД лише SHA-256 (сирий токен існує лише в листі/посиланні), термін TOKEN_TTL_DAYS, одноразовий; прив'язаний до order_id + product_id;
  • видача токена ЛИШЕ для замовлення сайту зі статусом доставки `delivered`, не скасованого/не поверненого/не sandbox, товар — канонічний id Toysi (лише цифри; rt-/dropt-/інше не отримують відгуків — контракт сайту їх відкидає);
  • прийом відгуку: ті самі правила, що збірка сайту (_review_clean): оцінка 1..5, текст 3..1500 видимих символів, автор ≤40; невалідне → 400, токен НЕ спалюється;
  • export_reviews(): site/reviews.json (лише published; атомарно; _meta.count = кількість записів), формат рівно за контрактом.
Нічого не шле назовні й не відправляє листів: видачу посилань і розсилку (лист) робить окремий крок з окремим дозволом власника."""
import hashlib
import json
import os
import re
import secrets
import sqlite3
import sys
import unicodedata
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
TOKEN_TTL_DAYS = 60                    # ≥30 за контрактом SEO; довше — менше шансів, що покупець прийде на «недійсне»
TEXT_MIN, TEXT_MAX, AUTHOR_MAX = 3, 1500, 40
AUTHOR_DEFAULT = "Покупець"
EXPORT_FILE = Path(os.environ.get("REVIEWS_EXPORT_FILE", str(HERE / "site" / "reviews.json")))
STATUSES = ("invited", "pending", "published", "rejected")
_TOKEN_RE = re.compile(r"[A-Za-z0-9_-]{20,64}")
_PRODUCT_RE = re.compile(r"[1-9][0-9]{0,11}")      # канонічний id Toysi: цифри без ведучих нулів (як ключі reviews.json)
_BIDI_RX = re.compile("[‪-‮⁦-⁩‎‏؜]")
_INVISIBLE_CATS = frozenset(("Cf", "Zs", "Zl", "Zp", "Cc", "Mn", "Me"))

SCHEMA = """
CREATE TABLE IF NOT EXISTS reviews (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id        TEXT NOT NULL,                 -- internal_order_id (site_PT-…)
    product_id      TEXT NOT NULL,                 -- id Toysi (цифри)
    token_hash      TEXT NOT NULL UNIQUE,          -- sha256(токена); сирий токен не зберігається
    token_expires_at TEXT NOT NULL,
    token_used_at   TEXT,
    invited_at      TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'invited' CHECK (status IN ('invited', 'pending', 'published', 'rejected')),
    rating          INTEGER,
    text            TEXT,
    author          TEXT,
    submitted_at    TEXT,
    moderated_at    TEXT,
    moderated_by    TEXT,
    UNIQUE (order_id, product_id)
);
CREATE INDEX IF NOT EXISTS idx_reviews_status ON reviews (status);
"""


class ReviewError(Exception):
    """Невалідні дані відгуку (HTTP 400/422)."""


def _now(now=None) -> datetime:
    return (now or datetime.now(timezone.utc)).astimezone(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.replace(microsecond=0).isoformat()


def init(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def valid_token_shape(token) -> bool:
    return isinstance(token, str) and bool(_TOKEN_RE.fullmatch(token))


def _visible_len(s: str) -> int:
    return sum(1 for ch in s if unicodedata.category(ch) not in _INVISIBLE_CATS)


def _utf8_clean(s: str) -> bool:
    if "\x00" in s:
        return False
    try:
        s.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return True


def clean_fields(rating, text, author):
    """(rating, text, author) → нормалізовані; ReviewError при невалідному. Правила = site/build_site.py::_review_clean (збірка однаково відкине інше)."""
    if type(rating) is not int or not 1 <= rating <= 5:
        raise ReviewError("rating")
    if not isinstance(text, str):
        raise ReviewError("text")
    text = _BIDI_RX.sub("", text.replace("\r\n", "\n")).strip()
    if not TEXT_MIN <= len(text) <= TEXT_MAX or not _utf8_clean(text) or _visible_len(text) < TEXT_MIN:
        raise ReviewError("text")
    if author is None:
        author = ""
    if not isinstance(author, str):
        raise ReviewError("author")
    author = " ".join(_BIDI_RX.sub("", author).split())
    if len(author) > AUTHOR_MAX or not _utf8_clean(author):
        raise ReviewError("author")
    if not _visible_len(author):
        author = ""
    return rating, text, author or AUTHOR_DEFAULT


def eligible_order(order) -> bool:
    """Замовлення сайту, ДОСТАВЛЕНЕ, не скасоване/не sandbox/не повернене."""
    if not order or order.get("platform") != "site":
        return False
    st = (order.get("status") or "").lower()
    if st == "sandbox_test" or "cancel" in st or order.get("cancelled_at") or order.get("refund_receipt_at"):
        return False
    return (order.get("delivery_status") or "") == "delivered"


def issue_token(conn: sqlite3.Connection, order: dict, product_id: str, now=None):
    """Видає одноразове посилання → СИРИЙ токен (його одразу вставляють у посилання; більше ніде не зберігається) або None, якщо не можна:
    замовлення не доставлене/не сайту, товар не Toysi-цифровий, для пари (замовлення, товар) відгук уже запрошено."""
    pid = str(product_id or "").strip()
    if not eligible_order(order) or not _PRODUCT_RE.fullmatch(pid):
        return None
    codes = {str((i or {}).get("toysi_code") or "").strip() for i in (order.get("items") or [])}
    if pid not in codes:
        return None
    init(conn)
    t = _now(now)
    token = secrets.token_urlsafe(24)
    try:
        conn.execute("INSERT INTO reviews (order_id, product_id, token_hash, token_expires_at, invited_at, status) VALUES (?, ?, ?, ?, ?, 'invited')",
                     (order["internal_order_id"], pid, _hash(token), _iso(t + timedelta(days=TOKEN_TTL_DAYS)), _iso(t)))
    except sqlite3.IntegrityError:
        # (order, product) уже є: друге запрошення не видаємо, КРІМ випадку «лист не дійшов / термін вийшов» — рядок досі 'invited' (текст не писали) і токен ПРОСТРОЧЕНИЙ:
        # тоді перевидаємо новий токен на той самий рядок (старий хеш замінюється → старе посилання мертве). Живе посилання перевидати не можна (аудит #1108).
        new_hash = _hash(token)
        cur = conn.execute("UPDATE reviews SET token_hash=?, token_expires_at=?, invited_at=? WHERE order_id=? AND product_id=? AND status='invited' AND token_used_at IS NULL AND token_expires_at <= ?",
                           (new_hash, _iso(t + timedelta(days=TOKEN_TTL_DAYS)), _iso(t), order["internal_order_id"], pid, _iso(t)))
        return token if cur.rowcount == 1 else None
    return token


def _row_by_token(conn, token):
    if not valid_token_shape(token):
        return None
    init(conn)
    return conn.execute("SELECT * FROM reviews WHERE token_hash = ?", (_hash(token),)).fetchone()


def form_info(conn: sqlite3.Connection, token, now=None):
    """Для POST api/review/form: {'product_id'} якщо токен існує, не використаний і не прострочений; інакше None (→ 404). Причини не розрізняємо назовні."""
    row = _row_by_token(conn, token)
    if not row or row["token_used_at"] or row["status"] != "invited" or row["token_expires_at"] <= _iso(_now(now)):
        return None
    return {"product_id": row["product_id"]}


def submit(conn: sqlite3.Connection, token, rating, text, author, now=None):
    """Приймає відгук → ('ok', id) | ('invalid', None) [404: нема/прострочений] | ('used', None) [409: вже використано] | піднімає ReviewError [400: поля — токен НЕ спалюється].
    Токен спалюється ОДНИМ UPDATE ... WHERE token_used_at IS NULL (гонка двох відправок: друга отримає 'used')."""
    row = _row_by_token(conn, token)
    if not row:
        return "invalid", None
    t = _now(now)
    if row["token_used_at"] or row["status"] != "invited":
        return "used", None
    if row["token_expires_at"] <= _iso(t):
        return "invalid", None
    r, tx, au = clean_fields(rating, text, author)          # ReviewError → 400 (токен лишається)
    cur = conn.execute("UPDATE reviews SET status='pending', rating=?, text=?, author=?, submitted_at=?, token_used_at=? WHERE id=? AND token_used_at IS NULL AND status='invited'",
                       (r, tx, au, _iso(t), _iso(t), row["id"]))
    if cur.rowcount != 1:
        return "used", None
    return "ok", row["id"]


def get(conn, review_id):
    init(conn)
    row = conn.execute("SELECT * FROM reviews WHERE id = ?", (review_id,)).fetchone()
    return dict(row) if row else None


def list_by_status(conn, status="pending", limit=50):
    init(conn)
    return [dict(r) for r in conn.execute("SELECT * FROM reviews WHERE status = ? ORDER BY id LIMIT ?", (status, int(limit))).fetchall()]


def moderate(conn: sqlite3.Connection, review_id, action, by="cli", now=None):
    """action: 'approve' | 'reject'. Дозволені переходи: pending → published/rejected; published → rejected (зняти); rejected → published (повернути). invited — ні (нема тексту).
    Повертає новий статус або None (нема такого / недозволений перехід). Експорт файлу — окремим викликом export_reviews()."""
    if action not in ("approve", "reject"):
        raise ValueError(action)
    row = get(conn, review_id)
    if not row or row["status"] not in ("pending", "published", "rejected"):
        return None
    new = "published" if action == "approve" else "rejected"
    if row["status"] == new:
        return new
    conn.execute("UPDATE reviews SET status=?, moderated_at=?, moderated_by=? WHERE id=?", (new, _iso(_now(now)), str(by)[:40], review_id))
    return new


def _kyiv_date(iso_ts: str) -> str:
    dt = datetime.fromisoformat(iso_ts)
    try:
        from zoneinfo import ZoneInfo
        return dt.astimezone(ZoneInfo("Europe/Kyiv")).date().isoformat()
    except Exception:  # noqa: BLE001
        return (dt.astimezone(timezone.utc) + timedelta(hours=2)).date().isoformat()


def build_export(conn: sqlite3.Connection, now=None) -> dict:
    """Словник reviews.json: лише published; від нових до старих; ключі — id Toysi."""
    init(conn)
    out, n = {}, 0
    for r in conn.execute("SELECT * FROM reviews WHERE status='published' AND rating IS NOT NULL ORDER BY submitted_at DESC, id DESC"):
        try:
            rec = {"rating": int(r["rating"]), "text": r["text"], "author": r["author"] or AUTHOR_DEFAULT, "date": _kyiv_date(r["submitted_at"])}
        except (ValueError, TypeError):
            continue
        out.setdefault(str(r["product_id"]), []).append(rec)
        n += 1
    meta = {"exported_at": _now(now).strftime("%Y-%m-%dT%H:%M:%SZ"), "count": n}
    return {"_meta": meta, **out}


def export_reviews(conn: sqlite3.Connection, path=None, now=None) -> int:
    """Пише reviews.json атомарно (tmp + os.replace); повертає кількість записів. Помилка запису → виняток (викликач вирішує; стара версія файлу лишається)."""
    path = Path(path or EXPORT_FILE)
    doc = build_export(conn, now)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(doc, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    os.replace(tmp, path)
    return doc["_meta"]["count"]

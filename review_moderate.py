# -*- coding: utf-8 -*-
"""review_moderate.py — модерація відгуків покупців (CLI; другий інтерфейс; рішення власника 03.11 + вимога SEO 04.11).
Запуск на VPS: /opt/plutustoys/venv/bin/python3 review_moderate.py <команда>
  --list [--status pending|published|rejected|invited]   перелік (id, статус, оцінка, товар, замовлення, дата, початок тексту)
  --show ID                                              повний запис (текст і автор — це те, що напише сайт)
  --approve ID | --reject ID                             змінює статус і ОДРАЗУ перегенеровує site/reviews.json (атомарно, з _meta.count); сайт підхопить на найближчій збірці (site-rebuild)
  --export                                               лише перегенерувати reviews.json (діагностика)
  --eligible                                             доставлені замовлення сайту з товарами Toysi, яким ще не видано посилання (кому можна запропонувати відгук)
  --issue ORDER_ID PRODUCT_ID                            видає ОДНЕ посилання `https://plutustoys.com.ua/review.html#t=<токен>` (токен — лише тут; у БД лише хеш). Листи НЕ шле.
Нічого не відправляє назовні."""
import argparse
import sys

import orders_db
import reviews_db

LINK = "https://plutustoys.com.ua/review.html#t={t}"


def _short(s, n=70):
    s = " ".join(str(s or "").split())
    return s if len(s) <= n else s[: n - 1] + "…"


def cmd_list(conn, status):
    rows = reviews_db.list_by_status(conn, status, limit=200)
    if not rows:
        print(f"(нема відгуків зі статусом {status})")
    for r in rows:
        print(f"#{r['id']} [{r['status']}] ★{r['rating'] or '-'} товар {r['product_id']} · {r['order_id']} · {r['submitted_at'] or r['invited_at']} · {_short(r['text'])}")


def cmd_show(conn, rid):
    r = reviews_db.get(conn, rid)
    if not r:
        print(f"нема відгуку #{rid}")
        return 1
    for k in ("id", "status", "rating", "product_id", "order_id", "author", "submitted_at", "moderated_at", "moderated_by"):
        print(f"{k}: {r[k]}")
    print("text:\n" + (r["text"] or ""))
    return 0


def cmd_moderate(conn, rid, action):
    new = reviews_db.moderate(conn, rid, action, by="cli")
    if not new:
        print(f"відгук #{rid}: нема або перехід недозволений (invited без тексту змінити не можна)")
        return 1
    conn.commit()
    n = reviews_db.export_reviews(conn)
    print(f"відгук #{rid} → {new}; reviews.json перегенеровано, записів: {n}")
    return 0


def cmd_eligible(conn):
    reviews_db.init(conn)
    have = {(r["order_id"], r["product_id"]) for r in conn.execute("SELECT order_id, product_id FROM reviews")}
    n = 0
    for row in conn.execute("SELECT * FROM orders WHERE platform='site' AND delivery_status='delivered' ORDER BY delivered_at DESC").fetchall():
        o = orders_db._row_to_dict(row)
        if not reviews_db.eligible_order(o):
            continue
        for it in o.get("items") or []:
            pid = str((it or {}).get("toysi_code") or "").strip()
            if reviews_db._PRODUCT_RE.fullmatch(pid) and (o["internal_order_id"], pid) not in have:
                n += 1
                print(f"{o['internal_order_id']} товар {pid} доставлено {o.get('delivered_at')} email: {'є' if (o.get('email') or '').strip() else 'нема'}")
    print(f"разом можна запросити: {n}")


def cmd_issue(conn, order_id, product_id):
    row = conn.execute("SELECT * FROM orders WHERE internal_order_id = ?", (order_id,)).fetchone()
    if not row:
        print("нема такого замовлення")
        return 1
    token = reviews_db.issue_token(conn, orders_db._row_to_dict(row), product_id)
    if not token:
        print("не можна видати: замовлення не доставлене/не сайту, товар не Toysi, або для цієї пари посилання вже видавалось")
        return 1
    conn.commit()
    print(LINK.format(t=token))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--list", action="store_true")
    g.add_argument("--show", type=int, metavar="ID")
    g.add_argument("--approve", type=int, metavar="ID")
    g.add_argument("--reject", type=int, metavar="ID")
    g.add_argument("--export", action="store_true")
    g.add_argument("--eligible", action="store_true")
    g.add_argument("--issue", nargs=2, metavar=("ORDER_ID", "PRODUCT_ID"))
    ap.add_argument("--status", default="pending", choices=reviews_db.STATUSES)
    a = ap.parse_args(argv)
    orders_db.init_db()
    with orders_db.get_connection() as conn:
        reviews_db.init(conn)
        if a.list:
            cmd_list(conn, a.status)
            return 0
        if a.show:
            return cmd_show(conn, a.show)
        if a.approve:
            return cmd_moderate(conn, a.approve, "approve")
        if a.reject:
            return cmd_moderate(conn, a.reject, "reject")
        if a.export:
            print(f"reviews.json перегенеровано, записів: {reviews_db.export_reviews(conn)}")
            return 0
        if a.eligible:
            cmd_eligible(conn)
            return 0
        if a.issue:
            return cmd_issue(conn, *a.issue)
    return 0


if __name__ == "__main__":
    sys.exit(main())

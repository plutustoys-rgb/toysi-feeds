#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""rt_set_state.py — ручне виставлення стану rt-замовлення, що застрягло після невідомого результату (rt_ordering / ttn_creating). 20.10.2026.

Навіщо: конвеєр сам НЕ повторює крок із невідомим результатом (щоб не створити дубль ТТН/резерву) і просить «виставте стан вручну». Раніше інструмента для цього не було.

Безпека: лише з STUCK-стану (rt_ordering → rt_ordered/marking_ready/manual; ttn_creating → ttn_created/manual); для rt_ordered ОБОВ'ЯЗКОВИЙ номер замовлення RoyalToys (--ref RT…) і збіг ТТН (--ttn) з тим, що в базі; без --yes нічого не пишеться (показує, що зробить).
Приклад:  python rt_set_state.py --order PT-20261020-153620-193ef9 --to rt_ordered --ref RT000773594 --ttn 20451565972503 --yes
"""
import argparse
import re
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

TO_STATES = ("rt_ordered", "marking_ready", "ttn_created", "manual")


ALLOWED = {"rt_ordering": ("rt_ordered", "marking_ready", "manual"), "ttn_creating": ("ttn_created", "manual")}


def plan(row: dict, to: str, ref: str | None, ttn: str | None) -> str | None:
    """Чиста перевірка: None — можна; інакше текст відмови."""
    st = row.get("rt_state")
    if st not in ALLOWED:
        return f"стан {st!r} не застряглий (дозволено лише {', '.join(ALLOWED)})"
    if to not in ALLOWED[st]:
        return f"з {st} дозволено лише: {', '.join(ALLOWED[st])}"
    if to == "rt_ordered":
        if not ref or not re.fullmatch(r"RT\d{6,}", ref):
            return "для rt_ordered потрібен --ref у форматі RT000000"
        if not ttn or str(row.get("toysi_ttn") or "") != ttn:
            return "для rt_ordered потрібен --ttn, що збігається з ТТН замовлення в базі"
    if to == "ttn_created" and not str(row.get("toysi_ttn") or ""):
        return "ttn_created без ТТН у базі неможливий (спершу перевірте кабінет НП)"
    return None


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--order", required=True, help="номер замовлення сайту PT-… (з префіксом site_ чи без)")
    ap.add_argument("--to", required=True, choices=TO_STATES)
    ap.add_argument("--ref")
    ap.add_argument("--ttn")
    ap.add_argument("--yes", action="store_true", help="справді записати (без нього — лише показ)")
    a = ap.parse_args(argv)
    import orders_db
    oid_like = "%" + a.order.replace("site_", "", 1)
    with orders_db.get_connection() as conn:
        rows = conn.execute("SELECT internal_order_id, rt_state, toysi_ttn, rt_order_ref FROM orders WHERE internal_order_id LIKE ?", (oid_like,)).fetchall()
        if len(rows) != 1:
            print(f"[rt_set_state] знайдено {len(rows)} замовлень за «{a.order}» — потрібне рівно одне", file=sys.stderr)
            return 2
        row = dict(rows[0])
        why = plan(row, a.to, a.ref, a.ttn)
        if why:
            print(f"[rt_set_state] відмова: {why}", file=sys.stderr)
            return 2
        print(f"[rt_set_state] {row['internal_order_id']}: {row['rt_state']} -> {a.to}" + (f", rt_order_ref={a.ref}" if a.ref else ""))
        if not a.yes:
            print("[rt_set_state] без --yes нічого не записано")
            return 0
        fields = {"rt_error": None}
        if a.ref:
            fields["rt_order_ref"] = a.ref
        orders_db.set_rt_state(conn, row["internal_order_id"], a.to, **fields)
        conn.commit()
        now = conn.execute("SELECT rt_state, rt_order_ref FROM orders WHERE internal_order_id = ?", (row["internal_order_id"],)).fetchone()
        print(f"[rt_set_state] записано: rt_state={now[0]}, rt_order_ref={now[1]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

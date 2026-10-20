# -*- coding: utf-8 -*-
"""rt_order_runner.py — конвеєр замовлень САЙТУ з товарами RoyalToys (20.10.2026): оплачене замовлення → чек → ТТН нашим ключем НП → наліпка PDF 100×100 → оформлення в кабінеті RoyalToys («По своей ТТН») → трекінг НП до доставки/повернення.

СТАНИ (orders.rt_state): NULL → ttn_created → marking_ready → rt_ordered → shipped → delivered | returned. Кожен крок ідемпотентний, стан пишеться й комітиться ОДРАЗУ після кроку (повторний запуск продовжує з місця зупинки).
Запобіжники:
  • ЗА ЗАМОВЧУВАННЯМ — DRY-RUN: нічого не створюється (ні ТТН, ні замовлення в RoyalToys); звіт: що б зробили (маппінг позицій, залишки, вартість доставки НП, перевірка кошика/форми RoyalToys на тестовому PDF). Реальний режим: RT_RUNNER_APPLY=1 І NP_TTN_APPLY=1 І RT_ORDER_SUBMIT=1 І rt_cabinet.FINAL_IMPLEMENTED=True (фінальну кнопку ще НЕ підключено — до першого спостережуваного замовлення власника реальний режим відмовляє).
  • Лише оплачені (prepaid+payment_confirmed) замовлення сайту ЗІ ВСІМА позиціями rt- (змішані ловить сервер; тут — стоп з причиною), з розпізнаним відділенням НП (np_city_ref+np_warehouse_number), фіскальним чеком ДО передачі постачальнику, залишком у supplier_rt.json ≥ кількості, габаритами.
  • Персональні дані клієнта в RoyalToys НЕ передаються (усе на наліпці ТТН).
Запуск: rt-order-runner.timer (VPS, кожні 15 хв; без rt-замовлень — миттєвий вихід). Збій → один самодіагностичний алерт на замовлення/стан/6 год."""
import json
import os
import sys
import tempfile
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = Path(__file__).resolve().parent
MARKING_DIR = Path(os.environ.get("RT_MARKING_DIR", str(HERE / ".secrets" / "rt_marking")))
MAX_ORDERS_PER_RUN = 5


class RunnerError(Exception):
    pass


def load_supplier(path=None) -> dict:
    """items із приватного supplier_rt.json (cost, vendor_code, dims, stock). Нема/битий → RunnerError (fail-closed)."""
    from rt_supplier_export import OUT_FILE
    try:
        return (json.loads(Path(path or OUT_FILE).read_text(encoding="utf-8-sig")).get("items") or {})
    except (OSError, ValueError) as e:
        raise RunnerError(f"supplier_rt.json недоступний ({type(e).__name__})") from None


def map_items(order: dict, supplier: dict) -> list:
    """Позиції замовлення → [{toysi_code, qty, price, vendor_code, cost, dims_cm, weight_kg, name}]. Усі мають бути rt-, у файлі постачальника, з артикулом, оптом, габаритами й достатнім залишком; інакше RunnerError з причиною."""
    out = []
    for it in order.get("items") or []:
        code = str(it.get("toysi_code") or "").strip().lower()
        if not code.startswith("rt-"):
            raise RunnerError(f"змішане замовлення (позиція {code or '?'} не RoyalToys) — потрібен ручний розділ")
        src = supplier.get(code)
        if not src:
            raise RunnerError(f"{code}: нема в supplier_rt.json (знята з публікації/зник у постачальника)")
        try:
            qty = int(it.get("qty"))
        except (TypeError, ValueError):
            raise RunnerError(f"{code}: кількість некоректна") from None
        if qty < 1:
            raise RunnerError(f"{code}: кількість < 1")
        if not str(src.get("vendor_code") or "").strip() or not src.get("cost") or not src.get("dims_cm"):
            raise RunnerError(f"{code}: у даних постачальника нема артикулу/опту/габаритів")
        if int(src.get("stock") or 0) < qty:
            raise RunnerError(f"{code}: залишок у постачальника {src.get('stock')} < замовлено {qty}")
        out.append({"toysi_code": code, "qty": qty, "price": it.get("price"), "name": it.get("name"), "vendor_code": str(src["vendor_code"]).strip(),
                    "cost": float(src["cost"]), "dims_cm": src["dims_cm"], "weight_kg": src.get("weight_kg")})
    if not out:
        raise RunnerError("у замовленні нема позицій")
    return out


def apply_enabled() -> tuple:
    """(True, '') лише коли ВСІ ворота відкриті, інакше (False, причина)."""
    import rt_cabinet
    if os.environ.get("RT_RUNNER_APPLY") != "1":
        return False, "RT_RUNNER_APPLY≠1 (dry-run)"
    if os.environ.get("NP_TTN_APPLY") != "1":
        return False, "NP_TTN_APPLY≠1: реальне створення ТТН не дозволено"
    if os.environ.get("RT_ORDER_SUBMIT") != "1":
        return False, "RT_ORDER_SUBMIT≠1: фінальне оформлення в RoyalToys не дозволено"
    if not getattr(rt_cabinet, "FINAL_IMPLEMENTED", False):
        return False, "фінальну кнопку оформлення в кабінеті RoyalToys ще не підключено (перше спостережуване замовлення власника)"
    return True, ""


def _alert(oid: str, key: str, text: str) -> None:
    try:
        from telegram_notify import send_throttled_alert
        send_throttled_alert(f"rt_order:{oid}:{key}", f"🟠 RoyalToys {oid}: {text}", cooldown_sec=6 * 60 * 60)
    except Exception as e:  # noqa: BLE001
        print(f"[rt_runner] алерт не надіслано: {type(e).__name__}", file=sys.stderr)


def _tracking_state(info: dict | None) -> tuple:
    """Статус НП → (rt_state, delivery_status). None (ТТН ще не відскановано) → без змін."""
    if not info:
        return None, None
    text = (info.get("status") or "").lower()
    code = str(info.get("status_code") or "")
    if info.get("delivered"):
        return "delivered", "delivered"
    if "відмов" in text or "повернен" in text:          # лише за текстом статусу (коди НП 104/111 — зміна адреси/невдала спроба, НЕ повернення)
        return "returned", "returned"
    if code in ("1", "2", "3"):
        return None, None                           # створена/очікує надходження — ще не відправлена
    return "shipped", "shipped"


def process_order(conn, order: dict, supplier: dict, apply: bool, deps=None) -> dict:
    """Один крок(и) для замовлення. Повертає звіт-словник. deps — підміна зовнішніх викликів у тестах."""
    import orders_db
    d = deps or _Deps()
    oid = order["internal_order_id"]
    rep = {"order": oid, "state": order.get("rt_state"), "apply": apply, "actions": [], "problems": []}
    try:
        state = order.get("rt_state")
        if state in ("rt_ordered", "shipped"):
            new_state, ds = _tracking_state(d.tracking(order.get("toysi_ttn")))
            if new_state and new_state != state:
                if apply:
                    orders_db.set_rt_state(conn, oid, new_state, delivery_status=ds)
                    conn.commit()
                    if new_state == "returned":
                        _alert(oid, "returned", "посилка повертається/відмова одержувача — потрібне рішення: повернення коштів клієнту (LiqPay) і розбір із RoyalToys (повернення на нас чи на склад — див. відповідь менеджера)")
                rep["actions"].append(f"трекінг НП: {state} → {new_state}")
            return rep
        items = map_items(order, supplier)
        rep["items"] = [(i["toysi_code"], i["qty"], i["vendor_code"]) for i in items]
        ttn_order = {**order, "items": [{**i, "toysi_code": i["toysi_code"]} for i in items]}
        if not (order.get("np_city_ref") and order.get("np_warehouse_number")):
            raise RunnerError("відділення НП не визначено (нема np_city_ref/np_warehouse_number) — уточніть у клієнта")
        if not order.get("checkbox_ettn_registered_at"):
            rep["problems"].append("фіскальний чек ще не виписано (роутер для rt більше не викликається — чек виписує цей конвеєр у реальному режимі)")
        if not apply:
            price = d.ttn_dry_run(ttn_order)
            rep["actions"].append(f"ТТН (розрахунок): доставка {price.get('cost')} грн, місць {len(items)}")
            rt = d.rt_dry_run(items)
            rep["actions"].append(f"кабінет RoyalToys (форма, без відправки): ready={rt.get('ready')}, кошик={rt.get('cart_total')}, мін.замовлення={rt.get('min_order_note')}")
            if not rt.get("ready"):
                rep["problems"].append(f"кабінет RoyalToys: {rt.get('problems')}")
            return rep
        ok, why = apply_enabled()
        if not ok:
            raise RunnerError(f"реальний режим недоступний: {why}")
        if state is None:
            if not d.ensure_receipt(conn, order):
                raise RunnerError("фіскальний чек не видано — передачу постачальнику не починаємо")
            res = d.create_ttn(ttn_order)
            orders_db.set_rt_state(conn, oid, "ttn_created", rt_ttn_ref=res["ref"], toysi_ttn=res["number"], rt_error=None)
            conn.commit()
            state = "ttn_created"
            rep["actions"].append(f"ТТН створено {res['number']}")
            order = {**order, "toysi_ttn": res["number"], "rt_ttn_ref": res["ref"]}
        if state == "ttn_created":
            path = MARKING_DIR / f"{oid}.pdf"
            d.save_marking(order["rt_ttn_ref"], path)
            orders_db.set_rt_state(conn, oid, "marking_ready", rt_marking_path=str(path), rt_error=None)
            conn.commit()
            state = "marking_ready"
            rep["actions"].append("наліпка PDF збережена")
            order = {**order, "rt_marking_path": str(path)}
        if state == "marking_ready":
            res = d.rt_order(items, order["toysi_ttn"], order["rt_marking_path"])
            if not (res.get("submitted") and res.get("rt_order_ref")):
                raise RunnerError(f"оформлення в RoyalToys не підтверджено: {res.get('problems')}")
            orders_db.set_rt_state(conn, oid, "rt_ordered", rt_order_ref=str(res["rt_order_ref"]), rt_error=None)
            conn.commit()
            rep["actions"].append(f"замовлення RoyalToys {res['rt_order_ref']}")
    except RunnerError as e:
        rep["problems"].append(str(e))
    except Exception as e:  # noqa: BLE001
        rep["problems"].append(f"збій {type(e).__name__}: {str(e)[:200]}")
    if rep["problems"] and apply:
        orders_db.set_rt_state(conn, oid, None, rt_error="; ".join(rep["problems"])[:500])
        conn.commit()
        _alert(oid, str(order.get("rt_state")), "; ".join(rep["problems"])[:400] + " — конвеєр зупинено на цьому замовленні, повторить наступного циклу")
    return rep


class _Deps:
    """Справжні зовнішні виклики (мережа/кабінет/НП). У тестах підміняються."""

    def tracking(self, ttn):
        import nova_poshta
        return nova_poshta.get_tracking_status(ttn) if ttn else None

    def ttn_dry_run(self, ttn_order):
        import np_ttn
        r = np_ttn.create_ttn(np_ttn.with_supplier_dims(ttn_order), apply=False)
        return r.get("price") or {}

    def rt_dry_run(self, items):
        import rt_cabinet
        with tempfile.TemporaryDirectory() as td:
            pdf = Path(td) / "marking_dryrun.pdf"
            pdf.write_bytes(b"%PDF-1.4\n%" + b"x" * 400 + b"\n%%EOF\n")
            order = {"items": [{"vendor_code": i["vendor_code"], "qty": i["qty"], "cost": i["cost"]} for i in items], "ttn_number": "00000000000000", "marking_pdf": str(pdf)}
            return rt_cabinet.order_flow(order, submit=False)

    def ensure_receipt(self, conn, order):
        from order_router import _ensure_site_prepaid_receipt
        return _ensure_site_prepaid_receipt(conn, order)

    def create_ttn(self, ttn_order):
        import np_ttn
        return np_ttn.create_ttn(np_ttn.with_supplier_dims(ttn_order), apply=True)

    def save_marking(self, ref, path):
        import np_ttn
        data = np_ttn.fetch_marking_pdf(ref)
        path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(data)

    def rt_order(self, items, ttn, pdf_path):
        import rt_cabinet
        order = {"items": [{"vendor_code": i["vendor_code"], "qty": i["qty"], "cost": i["cost"]} for i in items], "ttn_number": ttn, "marking_pdf": pdf_path}
        return rt_cabinet.order_flow(order, submit=True)


def run(apply: bool | None = None, deps=None, db_path=None) -> list:
    import orders_db
    if apply is None:
        apply = os.environ.get("RT_RUNNER_APPLY") == "1"
    reports = []
    with orders_db.get_connection(db_path) if db_path else orders_db.get_connection() as conn:
        pending = orders_db.get_rt_orders_pending(conn)[:MAX_ORDERS_PER_RUN]
        if not pending:
            print("[rt_runner] немає замовлень RoyalToys у роботі")
            return reports
        try:
            supplier = load_supplier()
        except RunnerError as e:
            print(f"[rt_runner] {e}", file=sys.stderr)
            return [{"problems": [str(e)]}]
        for order in pending:
            rep = process_order(conn, order, supplier, apply, deps)
            reports.append(rep)
            print("[rt_runner]", json.dumps(rep, ensure_ascii=False, default=str))
    return reports


if __name__ == "__main__":
    try:
        from dotenv import load_dotenv
        load_dotenv(HERE / ".env")      # ворота RT_RUNNER_APPLY/NP_TTN_APPLY/RT_ORDER_SUBMIT та ключі НП — з .env (явний шлях)
    except ImportError:
        pass
    from orders_db import init_db
    init_db()
    out = run()
    sys.exit(0 if all(not r.get("problems") for r in out) else 1)

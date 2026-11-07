# -*- coding: utf-8 -*-
"""dropt_order_runner.py — конвеєр замовлень САЙТУ з товарами Dropt (07.11.2026, рішення власника 04.11: лише передплата, формула ціни як RT).
Оплачене замовлення → чек → ТТН нашим ключем НП → наліпка PDF → оформлення в кабінеті Dropt («Зовнішня ТТН», оплата з балансу Dropt) → трекінг НП до доставки/повернення.

Модель — rt_order_runner.py (RoyalToys), ті самі стани й колонки orders (rt_state / rt_ttn_ref / rt_marking_path / rt_order_ref / rt_total / rt_error): замовлення або лише rt-, або лише dropt- (змішані сайт відхиляє),
тому окремої схеми не заводимо. Відмінності від RT: (1) «резерву» нема — Dropt списує з балансу одразу при оформленні, тож крок «З балансу» відсутній; (2) контактів отримувача Dropt не просить
(ПІБ/телефон потрібні лише для нашої ТТН НП); (3) вага/габарити Dropt публічно не дає — ТТН йде зі стандартним місцем НП (np_ttn.DEFAULT_SEAT_*); (4) тільки platform='site' (EVA-замовлення з dropt- — окремий етап разом із фідом EVA).

СТАНИ (orders.rt_state): NULL → ttn_created → marking_ready → rt_ordered → shipped → delivered | returned. Кожен крок ідемпотентний; стан пишеться й комітиться ОДРАЗУ після кроку.
Запобіжники:
  • ЗА ЗАМОВЧУВАННЯМ — DRY-RUN: нічого не створюється (ні ТТН, ні замовлення в Dropt). Реальний режим: DROPT_RUNNER_APPLY=1 І NP_TTN_APPLY=1 І DROPT_ORDER_SUBMIT=1 І DROPT_RUNNER_SINCE=<ISO-дата> І dropt_cabinet.final_implemented().
  • Лише оплачені (prepaid+payment_confirmed) замовлення сайту ЗІ ВСІМА позиціями dropt-, з розпізнаним відділенням НП, фіскальним чеком ДО передачі постачальнику, свіжим залишком у supplier_dropt.json, ціною продажу не нижчою за закупівлю «Дроп».
  • Кабінет Dropt перед ТТН: сесія жива, баланс ≥ суми закупівлі (інакше ТТН-сирота): не вистачає — ТТН НЕ створюється (🟠 «поповніть баланс»), наступний цикл повторить.
Запуск: dropt-order-runner.timer (VPS, кожні 15 хв; без dropt-замовлень — миттєвий вихід). Збій → один самодіагностичний алерт на замовлення/стан/добу."""
import json
import os
import re
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import rt_order_runner as rt_run          # спільні стани, трекінг НП, ПІБ/телефон, claim-и: поведінку RoyalToys не змінюємо

HERE = Path(__file__).resolve().parent
MARKING_DIR = Path(os.environ.get("DROPT_MARKING_DIR", str(HERE / ".secrets" / "dropt_marking")))
MAX_ORDERS_PER_RUN = 5
MAX_PDF_BYTES = 2 * 1024 * 1024           # кабінет Dropt приймає PDF ТТН ≤ 2 МБ (uploadTTN)
DROPT_PREFIX = "dropt-"
RunnerError = rt_run.RunnerError
STUCK = rt_run.STUCK
ACTION_STATES = rt_run.ACTION_STATES
STALE_DAYS = rt_run.STALE_DAYS
INFO_ALERT_KEYS = ("waiting", "wait", "since", "since_mid", "supplier_stale")      # інформаційні (dry-run/ворота) — «⚪», у Telegram не йдуть; решта — «🟠» власнику


SUPPLIER_MAX_AGE_S = 3 * 3600      # як ITEM_MAX_AGE_S в експортері й DROPT_MAX_AGE у stock_snapshot: файл/позиція, прочитані давніше, — ціну й наявність «не знаємо»


def load_supplier(path=None, now=None) -> dict:
    """items із приватного supplier_dropt.json (cost, vendor_code, stock, checked_at). Нема/битий/без `updated`/старший за 3 год → RunnerError (fail-closed: якщо експортер помер, ціну й наявність не вгадуємо — аудит #1112 S1)."""
    from datetime import datetime, timezone
    from dropt_supplier_export import OUT_FILE
    try:
        raw = json.loads(Path(path or OUT_FILE).read_text(encoding="utf-8-sig"))
        upd = datetime.fromisoformat(str(raw["updated"]).replace("Z", "+00:00"))
    except (OSError, ValueError, KeyError, TypeError) as e:
        raise RunnerError(f"supplier_dropt.json недоступний ({type(e).__name__})") from None
    if upd.tzinfo is None:
        upd = upd.replace(tzinfo=timezone.utc)
    age = ((now or datetime.now(timezone.utc)) - upd).total_seconds()
    if age > SUPPLIER_MAX_AGE_S or age < -300:
        raise RunnerError(f"supplier_dropt.json застарів/з майбутнього (вік {age / 3600:.1f} год > 3 год) — ціна й наявність не свіжі, експортер не оновлює файл")
    return raw.get("items") or {}


def _item_fresh(src: dict, now_ts=None) -> bool:
    """Позицію читали не давніше 3 год (checked_at — epoch-секунди; нема/некоректне → НЕ свіжа, fail-closed)."""
    import time
    try:
        return 0 <= (now_ts or time.time()) - float(src.get("checked_at")) <= SUPPLIER_MAX_AGE_S
    except (TypeError, ValueError):
        return False


def map_items(order: dict, supplier: dict) -> list:
    """Позиції замовлення → [{toysi_code, qty, price, vendor_code, cost, name}]. Усі мають бути dropt-, у свіжому файлі постачальника, з артикулом, закупівлею «Дроп» і достатньою ознакою залишку (по СУМІ однакових рядків);
    ціна, яку сплатив клієнт, не нижча за закупівлю (інакше збиток — ручне рішення). Однакові рядки зливаються (форма Dropt дублі артикулів не приймає)."""
    merged = {}
    for it in order.get("items") or []:
        code = str(it.get("toysi_code") or "").strip().lower()
        if not code.startswith(DROPT_PREFIX):
            raise RunnerError(f"змішане замовлення (позиція {code or '?'} не Dropt) — потрібен ручний розділ")
        src = supplier.get(code)
        if not src:
            raise RunnerError(f"{code}: нема у свіжому supplier_dropt.json (знята з публікації/нема в наявності у постачальника)")
        try:
            qty = int(it.get("qty"))
        except (TypeError, ValueError):
            raise RunnerError(f"{code}: кількість некоректна") from None
        if qty < 1:
            raise RunnerError(f"{code}: кількість < 1")
        if not _item_fresh(src):
            raise RunnerError(f"{code}: ціну/наявність давно не перевіряли (checked_at старший за 3 год) — потрібне свіже читання")
        if not str(src.get("vendor_code") or "").strip() or not src.get("cost"):
            raise RunnerError(f"{code}: у даних постачальника нема артикулу/закупівлі")
        if code in merged:
            merged[code]["qty"] += qty
        else:
            merged[code] = {"toysi_code": code, "qty": qty, "price": it.get("price"), "name": it.get("name"), "vendor_code": str(src["vendor_code"]).strip(), "cost": float(src["cost"]), "source_url": src.get("source_url")}
    out = list(merged.values())
    if not out:
        raise RunnerError("у замовленні нема позицій")
    for i in out:
        if int(supplier[i["toysi_code"]].get("stock") or 0) < i["qty"]:
            raise RunnerError(f"{i['toysi_code']}: ознака залишку у постачальника {supplier[i['toysi_code']].get('stock')} < замовлено {i['qty']} — точної кількості Dropt не дає, потрібне ручне рішення")
        try:
            paid = float(i["price"])
        except (TypeError, ValueError):
            raise RunnerError(f"{i['toysi_code']}: ціна в замовленні некоректна") from None
        if paid < i["cost"]:
            raise RunnerError(f"{i['toysi_code']}: клієнт сплатив {paid} < поточна закупівля {i['cost']} — збиток, потрібне ручне рішення")
    return out


def _since() -> str:
    """DROPT_RUNNER_SINCE → ISO-рядок; порожнє → ''; НЕРОЗІБРАНЕ → 'INVALID' (fail-closed)."""
    from datetime import datetime
    raw = (os.environ.get("DROPT_RUNNER_SINCE") or "").strip()
    if not raw:
        return ""
    try:
        return datetime.fromisoformat(raw).isoformat()
    except ValueError:
        return "INVALID"


def apply_enabled() -> tuple:
    """(True, '') лише коли ВСІ ворота відкриті, інакше (False, причина)."""
    import dropt_cabinet
    if os.environ.get("DROPT_RUNNER_APPLY") != "1":
        return False, "DROPT_RUNNER_APPLY≠1 (dry-run)"
    if os.environ.get("NP_TTN_APPLY") != "1":
        return False, "NP_TTN_APPLY≠1: реальне створення ТТН не дозволено"
    if os.environ.get("DROPT_ORDER_SUBMIT") != "1":
        return False, "DROPT_ORDER_SUBMIT≠1: фінальне оформлення в Dropt не дозволено"
    if _since() in ("", "INVALID"):
        return False, "DROPT_RUNNER_SINCE не задано або не ISO-дата (напр. 2026-11-08): старіші за неї замовлення конвеєр не чіпає"
    if not dropt_cabinet.final_implemented():
        return False, "оформлення в кабінеті Dropt не підключено (dropt_cabinet.final_implemented() = False)"
    return True, ""


def _alert(oid: str, key: str, text: str, cooldown: int = 24 * 60 * 60) -> None:
    try:
        from telegram_notify import send_throttled_alert
        mark = "⚪" if key in INFO_ALERT_KEYS else "🟠"
        send_throttled_alert(f"dropt_order:{oid}:{key}", f"{mark} Dropt {oid}: {text}", cooldown_sec=cooldown)
    except Exception as e:  # noqa: BLE001
        print(f"[dropt_runner] алерт не надіслано: {type(e).__name__}", file=sys.stderr)


def _pdf_ok(path) -> bool:
    try:
        p = Path(path)
        return p.is_file() and 0 < p.stat().st_size <= MAX_PDF_BYTES and p.read_bytes()[:5] == b"%PDF-"
    except (OSError, TypeError):          # TypeError: шляху нема (rt_marking_path = None)
        return False


def process_order(conn, order: dict, supplier: dict, apply: bool, deps=None) -> dict:
    """Один крок(и) для замовлення. Повертає звіт-словник. deps — підміна зовнішніх викликів у тестах."""
    import orders_db
    d = deps or _Deps()
    oid = order["internal_order_id"]
    rep = {"order": oid, "state": order.get("rt_state"), "apply": apply, "actions": [], "problems": []}
    state = order.get("rt_state")
    try:
        if state in STUCK:
            raise RunnerError(f"крок «{state}» розпочато, але результат невідомий (збій/таймаут) — конвеєр сам НЕ повторює. Звірте ТТН у кабінеті НП і замовлення в кабінеті Dropt, потім rt_set_state.py: rt_ordering → marking_ready (якщо в Dropt замовлення НЕ створено) або manual (якщо створено/сумнів — "
                              "доведіть вручну); ttn_creating → лише manual")
        if state in ("rt_ordered", "shipped"):
            try:
                _info = d.tracking(order.get("toysi_ttn"))
            except Exception as e:  # noqa: BLE001
                print(f"[dropt_runner] {oid}: трекінг НП недоступний ({type(e).__name__}) — повтор наступного циклу", file=sys.stderr)
                rep["actions"].append("трекінг НП недоступний — повтор наступного циклу")
                return rep
            new_state, ds = rt_run._tracking_state(_info)
            if new_state and new_state != state:
                if apply:
                    orders_db.set_rt_state(conn, oid, new_state, delivery_status=ds)
                    conn.commit()
                    if new_state == "returned":
                        _alert(oid, "returned", "посилка повертається/відмова одержувача — потрібне рішення: повернення коштів клієнту (LiqPay) і розбір із Dropt (повернення товару, умови постачальника)")
                rep["actions"].append(f"трекінг НП: {state} → {new_state}")
            elif rt_run._stale(order):
                _alert(oid, "stale", f"стан «{state}» без змін понад {STALE_DAYS} днів (ТТН {order.get('toysi_ttn')}) — звірте статус посилки в НП і замовлення в кабінеті Dropt")
                rep["actions"].append(f"стан без змін понад {STALE_DAYS} днів — алерт")
            return rep
        if state is None:
            # фіскальний чек — ПЕРШИМ кроком (бухгалтер 12.10: не пізніше передачі товару), НЕ залежить ні від воріт, ні від залишку/відділення/ціни
            if not d.ensure_receipt(conn, order):
                raise RunnerError("фіскальний чек не видано — передачу постачальнику не починаємо")
        items = map_items(order, supplier)
        rep["items"] = [(i["toysi_code"], i["qty"], i["vendor_code"]) for i in items]
        try:
            recipient = rt_run._recipient(order)          # ПІБ і телефон потрібні для НАШОЇ ТТН НП (у Dropt не передаються) — перевіряємо ДО ТТН
        except RunnerError as e:
            raise RunnerError(str(e).replace("для RoyalToys", "для ТТН Нової пошти")) from None
        if not (order.get("np_city_ref") and order.get("np_warehouse_number")):
            raise RunnerError("відділення НП не визначено (нема np_city_ref/np_warehouse_number) — уточніть у клієнта")
        ok, why = apply_enabled() if apply else (False, "dry-run")
        if not ok:
            price = d.ttn_dry_run(rt_run.order_for_ttn(order, items))
            rep["actions"].append(f"ТТН (розрахунок): доставка {price.get('cost')} грн, позицій {len(items)}")
            if apply:
                raise RunnerError(f"реальний режим недоступний: {why}")
            if state is None:
                _alert(oid, "waiting", f"оплачене замовлення Dropt ({', '.join(i['vendor_code'] + '×' + str(i['qty']) for i in items)}) чекає ручної обробки — конвеєр у dry-run", cooldown=24 * 60 * 60)
            return rep
        if state is None:
            # усе, що можна перевірити БЕЗ незворотних дій, — ДО створення ТТН (ТТН-сирота не повинна виникати через сесію/баланс/кошик)
            d.ttn_dry_run(rt_run.order_for_ttn(order, items))
            try:
                d.ensure_cabinet_session()
            except Exception as e:  # noqa: BLE001
                raise RunnerError(f"кабінет Dropt недоступний: {e}")
            chk = d.dropt_dry_run(items)
            if not chk.get("ready"):
                if chk.get("low_balance"):
                    _alert(oid, "low_balance", f"недостатньо балансу Dropt для замовлення: {'; '.join(chk.get('problems') or [])}. Поповніть баланс — оформимо автоматично наступного циклу (ТТН ще не створено)", cooldown=6 * 60 * 60)
                raise RunnerError(f"кабінет Dropt не готовий до оформлення: {chk.get('problems')}")
            if not orders_db.claim_rt_state(conn, oid, None, "ttn_creating"):
                raise RunnerError("замовлення вже взяте іншим запуском (claim) — пропускаю")
            res = d.create_ttn(rt_run.order_for_ttn(order, items))
            print(f"[dropt_runner] {oid}: ТТН НП створено {res['number']} (ref {res['ref']}) — далі запис у БД", flush=True)
            orders_db.set_rt_state(conn, oid, "ttn_created", rt_ttn_ref=res["ref"], toysi_ttn=res["number"], rt_error=None)
            conn.commit()
            state = "ttn_created"
            rep["actions"].append(f"ТТН створено {res['number']}")
            order = {**order, "toysi_ttn": res["number"], "rt_ttn_ref": res["ref"]}
        if state == "ttn_created":
            path = MARKING_DIR / f"{oid}.pdf"
            d.save_marking(order["rt_ttn_ref"], path)
            if not _pdf_ok(path):
                raise RunnerError(f"наліпка ТТН не є коректним PDF ≤ 2 МБ ({path.name}) — Dropt її не прийме; ТТН {order.get('toysi_ttn')} створено, крок повториться")
            orders_db.set_rt_state(conn, oid, "marking_ready", rt_marking_path=str(path), rt_error=None)
            conn.commit()
            state = "marking_ready"
            rep["actions"].append("наліпка PDF збережена")
            order = {**order, "rt_marking_path": str(path)}
        if state == "marking_ready":
            if not _pdf_ok(order.get("rt_marking_path")):
                raise RunnerError(f"наліпка ТТН {order.get('rt_marking_path')} зникла/не є коректним PDF ≤ 2 МБ — ТТН {order.get('toysi_ttn')} створено: поверніть файл наліпки на місце, або позначте замовлення manual (rt_set_state.py --to manual) і доведіть його вручну")
            if not orders_db.claim_rt_state(conn, oid, "marking_ready", "rt_ordering"):
                raise RunnerError("замовлення вже взяте іншим запуском (claim) — пропускаю")
            res = d.dropt_order(items, order["toysi_ttn"], order["rt_marking_path"])
            if not (res.get("submitted") and res.get("dropt_order_ref")):
                if res.get("not_submitted_certain") is True and not res.get("submitted"):
                    orders_db.claim_rt_state(conn, oid, "rt_ordering", "marking_ready")     # однозначно НЕ відправлено → повертаємо крок
                    if res.get("low_balance"):
                        _alert(oid, "low_balance", f"недостатньо балансу Dropt: {'; '.join(res.get('problems') or [])}. Поповніть баланс — оформимо автоматично (ТТН НП {order.get('toysi_ttn')} створено й живе обмежений час)", cooldown=6 * 60 * 60)
                raise RunnerError(f"оформлення в Dropt не підтверджено: {res.get('problems')}")
            _tot = res.get("total_on_checkout")
            orders_db.set_rt_state(conn, oid, "rt_ordered", rt_order_ref=str(res["dropt_order_ref"]), rt_error=None, **({"rt_total": float(_tot)} if isinstance(_tot, (int, float)) else {}))
            conn.commit()
            rep["actions"].append(f"замовлення Dropt {res['dropt_order_ref']}")
            _alert(oid, "ordered", f"✅ замовлення Dropt {res['dropt_order_ref']} оформлено (ТТН {order['toysi_ttn']}), оплата з балансу Dropt: {_tot}. Баланс після: {res.get('balance_after')}", cooldown=24 * 60 * 60)
    except RunnerError as e:
        rep["problems"].append(str(e))
    except Exception as e:  # noqa: BLE001
        rep["problems"].append(f"збій {type(e).__name__}: {str(e)[:200]}")
    if rep["problems"] and (apply or state in STUCK):
        try:
            orders_db.set_rt_state(conn, oid, None, rt_error="; ".join(rep["problems"])[:500])    # стан НЕ скидається (None = без зміни)
            conn.commit()
        except Exception as e:  # noqa: BLE001
            print(f"[dropt_runner] {oid}: не вдалося записати rt_error ({type(e).__name__})", file=sys.stderr)
        _alert(oid, str(state), "; ".join(rep["problems"])[:400] + " — конвеєр зупинено на цьому замовленні")
    elif rep["problems"]:
        _alert(oid, "wait", "; ".join(rep["problems"])[:400] + " — замовлення Dropt чекає (конвеєр у dry-run: оформлюйте вручну або відкрийте ворота)")
    return rep


class _Deps(rt_run._Deps):
    """Справжні зовнішні виклики. Чек, ТТН НП, наліпка, трекінг — ті самі, що в RoyalToys (успадковано); кабінет — Dropt. У тестах підміняється."""

    def ensure_cabinet_session(self):
        import dropt_cabinet
        dropt_cabinet.ensure_session()

    @staticmethod
    def _cab_items(items):
        return [{"vendor_code": i["vendor_code"], "qty": i["qty"], "cost": i["cost"], "sale_price": i["price"], "source_url": i.get("source_url")} for i in items]

    def dropt_dry_run(self, items):
        import dropt_cabinet
        try:
            return dropt_cabinet.preflight({"items": self._cab_items(items)})
        except dropt_cabinet.OrderError as e:
            return {"ready": False, "problems": [str(e)]}

    def dropt_order(self, items, ttn, pdf_path):
        import dropt_cabinet
        order = {"items": self._cab_items(items), "ttn_number": ttn, "marking_pdf": pdf_path}
        try:
            return dropt_cabinet.order_flow(order, submit=True)
        except dropt_cabinet.OrderError as e:
            # OrderError, що ВИЙШЛА з order_flow, означає відмову ДО кліку (валідація, ворота, сесія): збої після кліку order_flow повертає всередині res
            return {"submitted": False, "not_submitted_certain": True, "problems": [str(e)]}


def run(apply: bool | None = None, deps=None, db_path=None) -> list:
    if apply is None:
        apply = os.environ.get("DROPT_RUNNER_APPLY") == "1"
    return _run_pipeline(apply, deps, db_path)


def _run_pipeline(apply: bool, deps=None, db_path=None) -> list:
    import orders_db
    since = _since()
    reports = []
    with orders_db.get_connection(db_path) if db_path else orders_db.get_connection() as conn:
        pending = orders_db.get_dropt_orders_pending(conn)
        for _o in orders_db.get_dropt_cod_orders(conn):            # dropt- + накладений платіж: конвеєр їх не бере (лише передплата) — не мовчимо
            _alert(_o["internal_order_id"], "dropt_cod", f"замовлення з товарами Dropt і НАКЛАДЕНИМ платежем ({_o.get('platform')}) — конвеєр Dropt їх не оформлює (лише передплата, рішення власника 04.11): потрібне ручне рішення (скасувати/узгодити з покупцем)")
        if since == "INVALID":
            print("[dropt_runner] DROPT_RUNNER_SINCE не розібрано як ISO-дата — реальний режим закритий", file=sys.stderr)
        if since and since != "INVALID":
            pending = [o for o in pending if str(o.get("created_at") or "") >= since or o.get("rt_state")]
        elif apply:
            print("[dropt_runner] apply без дійсного DROPT_RUNNER_SINCE: нові кроки не беремо (трекінг посилок у дорозі триває)", file=sys.stderr)
            dd = deps or _Deps()
            for o in [o for o in pending if o.get("rt_state") in ("ttn_created", "marking_ready")]:
                _alert(o["internal_order_id"], "since_mid", f"DROPT_RUNNER_SINCE не задано або не ISO-дата — замовлення зупинено на стані {o.get('rt_state')} (ТТН {o.get('toysi_ttn')})")
            for o in [o for o in pending if o.get("rt_state") is None]:       # нові без дійсного SINCE: чек (бухгалтер) і сигнал ВСЕ ОДНО
                try:
                    ok_r = dd.ensure_receipt(conn, o)
                except Exception as e:  # noqa: BLE001
                    ok_r = False
                    print(f"[dropt_runner] {o['internal_order_id']}: чек збій {type(e).__name__}", file=sys.stderr)
                _alert(o["internal_order_id"], "since", "DROPT_RUNNER_SINCE не задано або не ISO-дата — оплачене замовлення Dropt не обробляється" + ("" if ok_r else " (чек НЕ видано!)"))
            pending = [o for o in pending if o.get("rt_state") in ("rt_ordered", "shipped") or o.get("rt_state") in STUCK]
        if not pending:
            print("[dropt_runner] немає замовлень Dropt у роботі")
            return reports
        supplier, sup_err = {}, None
        try:
            supplier = load_supplier()
        except RunnerError as e:           # файл постачальника нема/застарів: НОВІ кроки (map_items) стоять, але трекінг посилок у дорозі й STUCK-діагностика файла не потребують — не зупиняємо їх (аудит #1114)
            sup_err = str(e)
            print(f"[dropt_runner] {e}", file=sys.stderr)
        light = [o for o in pending if o.get("rt_state") not in ACTION_STATES]
        action_all = sorted([o for o in pending if o.get("rt_state") in ACTION_STATES], key=lambda o: (o.get("rt_updated_at") or "", o.get("created_at") or ""))
        action, rest = action_all[:MAX_ORDERS_PER_RUN], action_all[MAX_ORDERS_PER_RUN:]
        d = deps or _Deps()
        for order in rest:                       # поза лімітом кроків — але чек (бухгалтер) отримують ВСІ нові замовлення
            if order.get("rt_state") is None:
                try:
                    ok_r = d.ensure_receipt(conn, order)
                except Exception as e:  # noqa: BLE001
                    ok_r = False
                    print(f"[dropt_runner] {order['internal_order_id']}: чек збій {type(e).__name__}", file=sys.stderr)
                if not apply:
                    _alert(order["internal_order_id"], "waiting", "оплачене замовлення Dropt чекає ручної обробки" + ("" if ok_r else " (чек НЕ видано!)") + " — конвеєр у dry-run")
        for order in light + action:
            if sup_err and order.get("rt_state") in ACTION_STATES:
                reports.append({"order": order["internal_order_id"], "state": order.get("rt_state"), "apply": apply, "actions": [], "problems": [sup_err]})
                _alert(order["internal_order_id"], "supplier_stale", sup_err + " — нові кроки замовлення стоять, трекінг посилок триває")
                continue
            rep = process_order(conn, order, supplier, apply, d)
            reports.append(rep)
            print("[dropt_runner]", json.dumps(rep, ensure_ascii=False, default=str))
    return reports


if __name__ == "__main__":
    try:
        from dotenv import load_dotenv
        load_dotenv(HERE / ".env")      # ворота DROPT_RUNNER_APPLY/NP_TTN_APPLY/DROPT_ORDER_SUBMIT та ключі НП — з .env (явний шлях)
    except ImportError:
        pass
    from orders_db import init_db
    init_db()
    out = run()
    # dry-run: «проблеми» — очікування ручної обробки (алерт уже надіслано), юніт не має падати кожні 15 хв; у реальному режимі код 1 = збій
    sys.exit(0 if (os.environ.get("DROPT_RUNNER_APPLY") != "1" or all(not r.get("problems") for r in out)) else 1)

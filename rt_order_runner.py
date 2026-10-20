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
    """Позиції замовлення → [{toysi_code, qty, price, vendor_code, cost, dims_cm, weight_kg, name}]. Усі мають бути rt-, у файлі постачальника, з артикулом, оптом, габаритами й достатнім залишком (по СУМІ однакових рядків);
    ціна, яку сплатив клієнт, не нижча за опт (інакше збиток — ручне рішення); інакше RunnerError з причиною. Однакові rt- рядки зливаються в один (кабінет RoyalToys дублі артикулів не приймає)."""
    merged = {}
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
        if code in merged:
            merged[code]["qty"] += qty
        else:
            merged[code] = {"toysi_code": code, "qty": qty, "price": it.get("price"), "name": it.get("name"), "vendor_code": str(src["vendor_code"]).strip(),
                            "cost": float(src["cost"]), "dims_cm": src["dims_cm"], "weight_kg": src.get("weight_kg")}
    out = list(merged.values())
    if not out:
        raise RunnerError("у замовленні нема позицій")
    for i in out:
        if int(supplier[i["toysi_code"]].get("stock") or 0) < i["qty"]:
            raise RunnerError(f"{i['toysi_code']}: залишок у постачальника {supplier[i['toysi_code']].get('stock')} < замовлено {i['qty']}")
        try:
            paid = float(i["price"])
        except (TypeError, ValueError):
            raise RunnerError(f"{i['toysi_code']}: ціна в замовленні некоректна") from None
        if paid < i["cost"]:
            raise RunnerError(f"{i['toysi_code']}: клієнт сплатив {paid} < поточний опт {i['cost']} — збиток, потрібне ручне рішення")
    return out


def _since() -> str:
    """RT_RUNNER_SINCE → ISO-рядок для порівняння з created_at; порожнє → ''; НЕРОЗІБРАНЕ (друкарська помилка) → 'INVALID' (fail-closed: ворота закриті)."""
    from datetime import datetime
    raw = (os.environ.get("RT_RUNNER_SINCE") or "").strip()
    if not raw:
        return ""
    try:
        return datetime.fromisoformat(raw).isoformat()
    except ValueError:
        return "INVALID"


STUCK = ("ttn_creating", "rt_ordering")          # claim взято, результат кроку невідомий — лише людина
ACTION_STATES = (None, "ttn_created", "marking_ready")
STALE_DAYS = 7                                    # посилка в rt_ordered/shipped без зміни стану довше — алерт


def apply_enabled() -> tuple:
    """(True, '') лише коли ВСІ ворота відкриті, інакше (False, причина)."""
    import rt_cabinet
    if os.environ.get("RT_RUNNER_APPLY") != "1":
        return False, "RT_RUNNER_APPLY≠1 (dry-run)"
    if os.environ.get("NP_TTN_APPLY") != "1":
        return False, "NP_TTN_APPLY≠1: реальне створення ТТН не дозволено"
    if os.environ.get("RT_ORDER_SUBMIT") != "1":
        return False, "RT_ORDER_SUBMIT≠1: фінальне оформлення в RoyalToys не дозволено"
    if _since() in ("", "INVALID"):
        return False, "RT_RUNNER_SINCE не задано або не ISO-дата (напр. 2026-10-21): старіші за неї замовлення конвеєр не чіпає — їх власник виконав вручну"
    if not getattr(rt_cabinet, "FINAL_IMPLEMENTED", False):
        return False, "фінальну кнопку оформлення в кабінеті RoyalToys ще не підключено (перше спостережуване замовлення власника)"
    return True, ""


def _alert(oid: str, key: str, text: str, cooldown: int = 6 * 60 * 60) -> None:
    try:
        from telegram_notify import send_throttled_alert
        send_throttled_alert(f"rt_order:{oid}:{key}", f"🟠 RoyalToys {oid}: {text}", cooldown_sec=cooldown)
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


def _stale(order: dict) -> bool:
    from datetime import datetime, timedelta
    try:
        return datetime.fromisoformat(order.get("rt_updated_at") or "") < datetime.now() - timedelta(days=STALE_DAYS)
    except ValueError:
        return False


def _recipient(order: dict) -> tuple:
    """(ПІБ, телефон) отримувача для форми RoyalToys. RoyalToys не пропускає замовлення без них (менеджер 20.10); власник 20.10: «ПІБ передавай» (раніше рішення було не передавати). Некоректні → RunnerError."""
    import re as _re
    name = " ".join(str(order.get("customer_name") or "").split())
    digits = _re.sub(r"\D", "", str(order.get("phone") or ""))
    if len(digits) == 10 and digits.startswith("0"):
        digits = "38" + digits
    if len(name.split()) < 2 or not _re.fullmatch(r"380\d{9}", digits):
        raise RunnerError("ПІБ (прізвище й ім'я) і телефон отримувача обов'язкові для RoyalToys, а в замовленні вони некоректні/неповні — уточніть у клієнта")
    return name, "+" + digits


def _recipient_fields(recipient) -> dict:
    if not recipient:
        return {}
    return {"recipient_name": recipient[0], "recipient_phone": recipient[1], "require_recipient": True}


def order_for_ttn(order: dict, items: list) -> dict:
    return {**order, "items": items}


def process_order(conn, order: dict, supplier: dict, apply: bool, deps=None) -> dict:
    """Один крок(и) для замовлення. Повертає звіт-словник. deps — підміна зовнішніх викликів у тестах."""
    import orders_db
    d = deps or _Deps()
    oid = order["internal_order_id"]
    rep = {"order": oid, "state": order.get("rt_state"), "apply": apply, "actions": [], "problems": []}
    state = order.get("rt_state")
    try:
        if state in STUCK:
            raise RunnerError(f"крок «{state}» розпочато, але результат невідомий (збій/таймаут) — конвеєр сам НЕ повторює. Звірте ТТН у кабінеті НП / замовлення в кабінеті RoyalToys і виставте стан вручну "
                              "(ttn_created / marking_ready / rt_ordered / manual)")
        if state in ("rt_ordered", "shipped"):
            new_state, ds = _tracking_state(d.tracking(order.get("toysi_ttn")))
            if new_state and new_state != state:
                if apply:
                    orders_db.set_rt_state(conn, oid, new_state, delivery_status=ds)
                    conn.commit()
                    if new_state == "returned":
                        _alert(oid, "returned", "посилка повертається/відмова одержувача — потрібне рішення: повернення коштів клієнту (LiqPay) і розбір із RoyalToys (повернення на нас чи на склад — див. відповідь менеджера)")
                rep["actions"].append(f"трекінг НП: {state} → {new_state}")
            elif _stale(order):
                _alert(oid, "stale", f"стан «{state}» без змін понад {STALE_DAYS} днів (ТТН {order.get('toysi_ttn')}) — звірте статус посилки в НП (видалена/зберігання/відмова)")
                rep["actions"].append(f"стан без змін понад {STALE_DAYS} днів — алерт")
            return rep
        if state is None:
            # фіскальний чек — ПЕРШИМ кроком, як у роутері (бухгалтер 12.10: не пізніше передачі товару), НЕ залежить ні від воріт, ні від залишку/відділення/ціни
            if not d.ensure_receipt(conn, order):
                raise RunnerError("фіскальний чек не видано — передачу постачальнику не починаємо")
        items = map_items(order, supplier)
        rep["items"] = [(i["toysi_code"], i["qty"], i["vendor_code"]) for i in items]
        recipient = _recipient(order)          # ПІБ і телефон ОБОВ'ЯЗКОВІ для RoyalToys — перевіряємо ДО ТТН (інакше ТТН-сирота)
        if not (order.get("np_city_ref") and order.get("np_warehouse_number")):
            raise RunnerError("відділення НП не визначено (нема np_city_ref/np_warehouse_number) — уточніть у клієнта")
        ok, why = apply_enabled() if apply else (False, "dry-run")
        if not ok:
            price = d.ttn_dry_run(order_for_ttn(order, items))
            rep["actions"].append(f"ТТН (розрахунок): доставка {price.get('cost')} грн, місць {len(items)}")
            if os.environ.get("RT_DRYRUN_CABINET") == "1":     # кабінет RoyalToys міняє спільний кошик акаунта — у таймерному dry-run НЕ чіпаємо (власник може оформлювати вручну)
                rt = d.rt_dry_run(items, recipient)
                rep["actions"].append(f"кабінет RoyalToys (форма, без відправки): ready={rt.get('ready')}, кошик={rt.get('cart_total')}, мін.замовлення={rt.get('min_order_note')}")
                if not rt.get("ready"):
                    rep["problems"].append(f"кабінет RoyalToys: {rt.get('problems')}")
            if apply:
                raise RunnerError(f"реальний режим недоступний: {why}")
            if state is None:
                _alert(oid, "waiting", f"оплачене замовлення RoyalToys ({', '.join(i['vendor_code'] + '×' + str(i['qty']) for i in items)}) чекає ручної обробки — конвеєр у dry-run", cooldown=24 * 60 * 60)
            return rep
        if state is None:
            # усе, що можна перевірити БЕЗ незворотних дій, — ДО створення ТТН (ТТН-сирота не повинна виникати через ціну/форму/кошик)
            d.ttn_dry_run(order_for_ttn(order, items))
            rt = d.rt_dry_run(items, recipient)
            if not rt.get("ready"):
                raise RunnerError(f"кабінет RoyalToys не готовий до оформлення: {rt.get('problems')}")
            if not orders_db.claim_rt_state(conn, oid, None, "ttn_creating"):
                raise RunnerError("замовлення вже взяте іншим запуском (claim) — пропускаю")
            res = d.create_ttn(order_for_ttn(order, items))
            print(f"[rt_runner] {oid}: ТТН НП створено {res['number']} (ref {res['ref']}) — далі запис у БД", flush=True)    # номер у журналі ДО запису в БД
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
            if not orders_db.claim_rt_state(conn, oid, "marking_ready", "rt_ordering"):
                raise RunnerError("замовлення вже взяте іншим запуском (claim) — пропускаю")
            res = d.rt_order(items, order["toysi_ttn"], order["rt_marking_path"], _recipient(order))
            if not (res.get("submitted") and res.get("rt_order_ref")):
                if res.get("not_submitted_certain") is True and not res.get("submitted"):
                    orders_db.claim_rt_state(conn, oid, "rt_ordering", "marking_ready")     # однозначно НЕ відправлено → повертаємо крок
                raise RunnerError(f"оформлення в RoyalToys не підтверджено: {res.get('problems')}")
            orders_db.set_rt_state(conn, oid, "rt_ordered", rt_order_ref=str(res["rt_order_ref"]), rt_error=None)
            conn.commit()
            rep["actions"].append(f"замовлення RoyalToys {res['rt_order_ref']}")
    except RunnerError as e:
        rep["problems"].append(str(e))
    except Exception as e:  # noqa: BLE001
        rep["problems"].append(f"збій {type(e).__name__}: {str(e)[:200]}")
    if rep["problems"] and (apply or state in STUCK):
        try:
            orders_db.set_rt_state(conn, oid, None, rt_error="; ".join(rep["problems"])[:500])    # стан НЕ скидається (None = без зміни)
            conn.commit()
        except Exception as e:  # noqa: BLE001
            print(f"[rt_runner] {oid}: не вдалося записати rt_error ({type(e).__name__})", file=sys.stderr)
        _alert(oid, str(state), "; ".join(rep["problems"])[:400] + " — конвеєр зупинено на цьому замовленні")
    elif rep["problems"]:
        _alert(oid, "wait", "; ".join(rep["problems"])[:400] + " — замовлення RoyalToys чекає (конвеєр у dry-run: оформлюйте вручну або відкрийте ворота)")
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

    def rt_dry_run(self, items, recipient=None):
        import rt_cabinet
        with tempfile.TemporaryDirectory() as td:
            pdf = Path(td) / "marking_dryrun.pdf"
            pdf.write_bytes(b"%PDF-1.4\n%" + b"x" * 400 + b"\n%%EOF\n")
            order = {"items": [{"vendor_code": i["vendor_code"], "qty": i["qty"], "cost": i["cost"]} for i in items], "ttn_number": "00000000000000", "marking_pdf": str(pdf), **_recipient_fields(recipient)}
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

    def rt_order(self, items, ttn, pdf_path, recipient=None):
        import rt_cabinet
        order = {"items": [{"vendor_code": i["vendor_code"], "qty": i["qty"], "cost": i["cost"]} for i in items], "ttn_number": ttn, "marking_pdf": pdf_path, **_recipient_fields(recipient)}
        return rt_cabinet.order_flow(order, submit=True)


def run(apply: bool | None = None, deps=None, db_path=None) -> list:
    import orders_db
    if apply is None:
        apply = os.environ.get("RT_RUNNER_APPLY") == "1"
    since = _since()
    reports = []
    with orders_db.get_connection(db_path) if db_path else orders_db.get_connection() as conn:
        pending = orders_db.get_rt_orders_pending(conn)
        if since == "INVALID":
            print("[rt_runner] RT_RUNNER_SINCE не розібрано як ISO-дата — реальний режим закритий", file=sys.stderr)
        if since and since != "INVALID":
            pending = [o for o in pending if str(o.get("created_at") or "") >= since or o.get("rt_state")]      # старіші без стану — виконані вручну
        elif apply:
            print("[rt_runner] apply без дійсного RT_RUNNER_SINCE: нові кроки не беремо (трекінг посилок у дорозі триває)", file=sys.stderr)
            midway = [o for o in pending if o.get("rt_state") in ("ttn_created", "marking_ready")]
            for o in midway:                   # ланцюг посередині, а SINCE зник/зіпсований — не мовчимо
                _alert(o["internal_order_id"], "since_mid", f"RT_RUNNER_SINCE не задано або не ISO-дата — замовлення зупинено на стані {o.get('rt_state')} (ТТН {o.get('toysi_ttn')})", cooldown=24 * 60 * 60)
            dropped = [o for o in pending if o.get("rt_state") is None]
            pending = [o for o in pending if o.get("rt_state") in ("rt_ordered", "shipped") or o.get("rt_state") in STUCK]
            dd = deps or _Deps()
            for o in dropped:                  # нові замовлення без дійсного SINCE: чек (бухгалтер) і сигнал ВСЕ ОДНО — вони не повинні мовчки зависнути
                try:
                    ok_r = dd.ensure_receipt(conn, o)
                except Exception as e:  # noqa: BLE001
                    ok_r = False
                    print(f"[rt_runner] {o['internal_order_id']}: чек збій {type(e).__name__}", file=sys.stderr)
                _alert(o["internal_order_id"], "since", "RT_RUNNER_SINCE не задано або не ISO-дата — оплачене замовлення RoyalToys не обробляється" + ("" if ok_r else " (чек НЕ видано!)"), cooldown=24 * 60 * 60)
        if not pending:
            print("[rt_runner] немає замовлень RoyalToys у роботі")
            return reports
        try:
            supplier = load_supplier()
        except RunnerError as e:
            print(f"[rt_runner] {e}", file=sys.stderr)
            return [{"problems": [str(e)]}]
        # відстеження посилок і «застряглі» — без ліміту (дешево); нові кроки — не більше MAX_ORDERS_PER_RUN, найдавніше пробовані першими (збої не заступають нові замовлення)
        light = [o for o in pending if o.get("rt_state") not in ACTION_STATES]
        action_all = sorted([o for o in pending if o.get("rt_state") in ACTION_STATES], key=lambda o: (o.get("rt_updated_at") or "", o.get("created_at") or ""))
        action, rest = action_all[:MAX_ORDERS_PER_RUN], action_all[MAX_ORDERS_PER_RUN:]
        d = deps or _Deps()
        for order in rest:                       # поза лімітом кроків — але чек (бухгалтер) і сигнал «чекає» отримують ВСІ нові замовлення
            if order.get("rt_state") is None:
                try:
                    ok_r = d.ensure_receipt(conn, order)
                except Exception as e:  # noqa: BLE001
                    ok_r = False
                    print(f"[rt_runner] {order['internal_order_id']}: чек збій {type(e).__name__}", file=sys.stderr)
                if not apply:
                    _alert(order["internal_order_id"], "waiting", "оплачене замовлення RoyalToys чекає ручної обробки" + ("" if ok_r else " (чек НЕ видано!)") + " — конвеєр у dry-run", cooldown=24 * 60 * 60)
        for order in light + action:
            rep = process_order(conn, order, supplier, apply, d)
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
    # dry-run: «проблеми» — очікування ручної обробки (алерт уже надіслано), юніт не має падати кожні 15 хв; у реальному режимі код 1 = збій
    sys.exit(0 if (os.environ.get("RT_RUNNER_APPLY") != "1" or all(not r.get("problems") for r in out)) else 1)

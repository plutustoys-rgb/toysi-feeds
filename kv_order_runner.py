# -*- coding: utf-8 -*-
"""kv_order_runner.py — конвеєр замовлень САЙТУ з товарами KIDDISVIT (kv-/kvu-), етап Б2 (21.11.2026).
Оплачене замовлення (prepaid, чек видано) → orders/create у B2B KIDDISVIT («my_ttn», без redelivery) → наша ТТН НП із відділення Дніпро №28 (номер замовлення КЗ-… у двох полях) → PDF наліпки 100×100 + експрес-накладної → orders/delivery/ttn-data.

СТАНИ (orders.kv_state): NULL → kv_creating → kv_created → ttn_creating → ttn_created → docs_ready → docs_sent (→ shipped/delivered/returned — трекінг, етап Б3).
  • kv_creating / kv_unknown / ttn_creating = «результат кроку невідомий»: ЖОДНОГО автоповтору (create/ТТН платні/незворотні) — лише сигнал і ручна звірка. kv_unknown: результат orders/create невизначений (таймаут, 5xx, відповідь без номера).
  • manual = термінальний: замовлення створено, але НЕ зарезервовано (status_OK=0) або рішення людини.
Запобіжники (усе ВІДЧИНЕНО лише разом): KV_RUNNER_APPLY=1 І NP_TTN_APPLY=1 І KV_ORDERS_LIVE=1 І KV_RUNNER_SINCE=<ISO-дата> (замовлення без стану, створені раніше, не беремо). Інакше DRY-RUN: нічого не створюється, звіт «що б зробили».
  • Перед create — свіжа перевірка замовлення з БД (повернення/скасування/чек), живий залишок products/rests на складі, сухий прогін ТТН (getDocumentPrice), атомарний claim.
Запуск: kv-order-runner.timer (VPS, кожні 15 хв; без kv-замовлень — миттєвий вихід). Збій → один алерт на замовлення/стан/добу."""
import base64
import json
import os
import sys
from datetime import datetime
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = Path(__file__).resolve().parent
DOCS_DIR = Path(os.environ.get("KV_DOCS_DIR", str(HERE / ".secrets" / "kv_docs")))
MAX_ORDERS_PER_RUN = 3
STUCK = ("kv_creating", "kv_unknown", "ttn_creating")
ACTION_STATES = (None, "kv_created", "ttn_created", "docs_ready")      # стани, де runner робить КРОК (решта — лише спостереження/сигнал)
SENDER_KIND = "kv"


class RunnerError(Exception):
    pass


def _since() -> str | None:
    """KV_RUNNER_SINCE: None — не задано; 'INVALID' — не ISO; інакше нормалізований ISO-рядок для порівняння з orders.created_at."""
    raw = os.environ.get("KV_RUNNER_SINCE", "").strip()
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw).isoformat(timespec="seconds")
    except ValueError:
        return "INVALID"


def apply_enabled() -> tuple:
    """(bool, why) — реальний режим лише при ВСІХ воротах і дійсному KV_RUNNER_SINCE."""
    miss = [k for k in ("KV_RUNNER_APPLY", "NP_TTN_APPLY", "KV_ORDERS_LIVE") if os.environ.get(k, "").strip() != "1"]
    if miss:
        return False, "ворота закриті: " + ", ".join(miss)
    s = _since()
    if not s or s == "INVALID":
        return False, "KV_RUNNER_SINCE не задано або не ISO-дата"
    return True, ""


def _alert(oid: str, key: str, text: str, cooldown: int = 24 * 60 * 60) -> None:
    try:
        from telegram_notify import send_throttled_alert
        send_throttled_alert(f"kv_order:{oid}:{key}", f"🟠 KIDDISVIT {oid}: {text}", cooldown_sec=cooldown)
    except Exception as e:  # noqa: BLE001
        print(f"[kv_runner] алерт не надіслано: {type(e).__name__}", file=sys.stderr)


class _Deps:
    """Справжні зовнішні виклики (мережа). У тестах підміняються."""

    def __init__(self):
        from kiddisvit_api import KiddisvitClient
        self.client = KiddisvitClient()
        self._ids = None

    def ids(self) -> dict:
        if self._ids is None:
            d = self.client.clients_list()
            cl = (d.get("clients") or [{}])[0]
            sh = (d.get("shops") or [{}])[0]
            self._ids = {"OrganizationID": cl.get("OrganizationID"), "ClientID": cl.get("ClientID"), "ContractID": cl.get("ContractID"), "shopID": sh.get("shopID")}
        return self._ids

    def lookup(self, city_ref, number):
        import kiddisvit_orders
        return kiddisvit_orders.lookup_np_ref(city_ref, number)

    def rests(self) -> dict:
        """{ProductID: {WarehouseID: Rest}} — свіжі залишки всього каталогу (GET, читання)."""
        out = {}
        for p in self.client.rests():
            out[str(p.get("ProductID"))] = {str(w.get("WarehouseID")): w.get("Rest") for w in (p.get("rest") or [])}
        return out

    def create_order(self, payload: dict) -> dict:
        return self.client.orders_create(payload)

    def search_orders(self, ids: dict, date_from: str, date_to: str) -> list:
        return self.client.orders_search(ids["ClientID"], ids["ContractID"], date_from, date_to)

    def ttn_dry_run(self, order: dict, extra: dict) -> dict:
        import np_ttn
        return np_ttn.create_ttn(order, apply=False, sender_kind=SENDER_KIND, extra_props=extra)

    def create_ttn(self, order: dict, extra: dict) -> dict:
        import np_ttn
        return np_ttn.create_ttn(order, apply=True, sender_kind=SENDER_KIND, extra_props=extra)

    def save_docs(self, ref: str, oid: str) -> dict:
        import np_ttn
        DOCS_DIR.mkdir(parents=True, exist_ok=True)
        out = {}
        for kind, fetch in (("marking", np_ttn.fetch_marking_pdf), ("document", np_ttn.fetch_document_pdf)):
            data = fetch(ref)
            p = DOCS_DIR / f"{oid}_{kind}.pdf"
            tmp = p.with_suffix(".tmp")
            tmp.write_bytes(data)
            os.replace(tmp, p)
            out[kind] = str(p)
        return out

    def push_docs(self, kv_order_id: str, paths: dict) -> list:
        docs = [{"type": k, "BinaryData": base64.b64encode(Path(paths[k]).read_bytes()).decode("ascii")} for k in ("marking", "document")]
        return self.client.orders_ttn_data(kv_order_id, docs)


def _ttn_extra(number: str) -> dict:
    return {"AdditionalInformation": number, "InfoRegClientBarcodes": number}


def docs_push_ok(res: list) -> bool:
    """ttn-data → [{id,status,errorMessage}]: успіх лише коли відповідь непорожня, ВСІ елементи без errorMessage і зі status, що не схожий на відмову. Неоднозначне → False (стан лишається docs_ready, сигнал)."""
    if not isinstance(res, list) or not res:
        return False
    for r in res:
        if not isinstance(r, dict) or str(r.get("errorMessage") or "").strip():
            return False
        st = r.get("status")
        if st in (False, 0, "0", "false", "False", "error", "Error", "ERROR", "fail", "failed", None, ""):
            return False
    return True


def _release_claim(conn, oid: str, from_state: str) -> bool:
    """Повертає claim у NULL (крок однозначно НЕ виконано). Умовний UPDATE: чужий/інший стан не чіпаємо."""
    cur = conn.execute("UPDATE orders SET kv_state = NULL, kv_updated_at = ? WHERE internal_order_id = ? AND kv_state = ?", (datetime.now().isoformat(timespec="seconds"), oid, from_state))
    conn.commit()
    return cur.rowcount == 1


def _fresh(conn, oid: str) -> dict:
    import orders_db
    o = orders_db.get_order(conn, oid)
    if not o:
        raise RunnerError("замовлення зникло з БД")
    return o


def _reconcile_hint(d, order: dict, since_days: int = 3) -> str:
    """Лише ПІДКАЗКА для алерту (стан не міняємо): що показує orders/search за Comment=internal_order_id. Помилки — ігноруємо."""
    try:
        from datetime import timedelta
        today = datetime.now()
        rows = d.search_orders(d.ids(), (today - timedelta(days=since_days)).strftime("%Y%m%d"), today.strftime("%Y%m%d"))
        oid = order["internal_order_id"]
        found = [r for r in rows if oid in json.dumps(r, ensure_ascii=False, default=str)]
        if len(found) == 1:
            return f" У кабінеті знайдено рівно одне замовлення з цим номером: {found[0].get('number')} (id {found[0].get('id')}) — після звірки виставте kv_state=kv_created із kv_order_number/kv_order_id."
        if not found:
            return " За Comment у orders/search нічого не знайдено (за 3 дні) — перевірте кабінет вручну; якщо замовлення НЕМАЄ, виставте kv_state=NULL."
        return f" За Comment знайдено {len(found)} замовлень — потрібна ручна звірка."
    except Exception:  # noqa: BLE001
        return ""


def process_order(conn, order: dict, supplier: dict, apply: bool, deps=None) -> dict:
    import kiddisvit_orders as ko
    import orders_db
    from kiddisvit_api import KiddisvitError, KiddisvitUncertain
    d = deps or _Deps()
    oid = order["internal_order_id"]
    state = order.get("kv_state")
    rep = {"order": oid, "state": state, "apply": apply, "actions": [], "problems": []}
    try:
        if state in STUCK:
            hint = _reconcile_hint(d, order) if state in ("kv_creating", "kv_unknown") else ""
            raise RunnerError(f"крок «{state}» розпочато, але результат невідомий — конвеєр НЕ повторює (create/ТТН платні й незворотні). Звірте кабінет KIDDISVIT / кабінет НП і виставте kv_state вручну." + hint)
        if state in ("docs_sent", "shipped"):
            return rep                      # трекінг — етап Б3
        if state is None:
            ids = d.ids()
            plan = ko.dry_run(order, supplier, ids, d.lookup)
            if not plan["ok"]:
                raise RunnerError("; ".join(plan["problems"]))
            rests = d.rests()
            for it in plan["payload"]["Items"]:
                have = (rests.get(str(it["Product"])) or {}).get(str(it["Warehouse"]))
                try:
                    ok = int(have) >= int(it["Quantity"])
                except (TypeError, ValueError):
                    ok = False
                if not ok:
                    raise RunnerError(f"живий залишок на складі {it['Warehouse']} недостатній для {it['Product']} (потрібно {it['Quantity']}, є {have})")
            d.ttn_dry_run(order, _ttn_extra("КЗ-DRYRUN"))                       # адреса/габарити/вага — ДО створення замовлення в постачальника
            rep["actions"].append(f"готово до створення: позицій {len(plan['items'])}, закупівля {plan['cost_total']} грн")
            if not apply:
                _alert(oid, "waiting", f"оплачене замовлення KIDDISVIT готове до оформлення ({plan['items']}, закупівля {plan['cost_total']} грн) — конвеєр у dry-run", cooldown=24 * 60 * 60)
                return rep
            fresh = _fresh(conn, oid)
            ko._kv_only(fresh)                                                  # повторна перевірка перед незворотним кроком: повернення/скасування за цей час
            if fresh.get("kv_state") is not None:
                raise RunnerError("стан замовлення вже змінено іншим запуском — пропускаю")
            if not orders_db.claim_kv_state(conn, oid, None, "kv_creating"):
                raise RunnerError("замовлення вже взяте іншим запуском (claim) — пропускаю")
            try:
                res = ko.interpret_create(d.create_order(plan["payload"]))
            except KiddisvitUncertain as e:
                orders_db.set_kv_state(conn, oid, "kv_unknown", kv_error=str(e)[:300])
                conn.commit()
                raise RunnerError(f"orders/create: результат невідомий ({str(e)[:160]}) — стан kv_unknown, повтору немає")
            except KiddisvitError as e:           # визначена відмова ДО створення (4xx/ворота): повертаємо крок
                _release_claim(conn, oid, "kv_creating")
                raise RunnerError(f"orders/create відхилено: {str(e)[:200]}")
            if not res["reserved"]:
                orders_db.set_kv_state(conn, oid, "manual", kv_order_number=res["number"], kv_order_id=res["id"], kv_error=("створено, але НЕ зарезервовано: " + res["comment"])[:300])
                conn.commit()
                _alert(oid, "unreserved", f"замовлення {res['number']} створено в KIDDISVIT, але НЕ зарезервовано ({res['comment']}) — далі вручну (стан manual)")
                rep["actions"].append(f"створено {res['number']}, не зарезервовано → manual")
                return rep
            orders_db.set_kv_state(conn, oid, "kv_created", kv_order_number=res["number"], kv_order_id=res["id"], kv_error=None)
            conn.commit()
            print(f"[kv_runner] {oid}: замовлення KIDDISVIT {res['number']} (id {res['id']})", flush=True)
            rep["actions"].append(f"замовлення KIDDISVIT {res['number']}")
            state = "kv_created"
            order = {**order, "kv_state": state, "kv_order_number": res["number"], "kv_order_id": res["id"]}
        if state == "kv_created":
            if not apply:
                return rep
            num = str(order.get("kv_order_number") or "").strip()
            if not num:
                raise RunnerError("kv_created без kv_order_number — ручна перевірка")
            extra = _ttn_extra(num)
            d.ttn_dry_run(order, extra)                                         # валідація ДО claim: збій тут ще не «невідомий результат»
            if not orders_db.claim_kv_state(conn, oid, "kv_created", "ttn_creating"):
                raise RunnerError("замовлення вже взяте іншим запуском (claim) — пропускаю")
            res = d.create_ttn(order, extra)                                    # будь-який збій тут лишає ttn_creating (STUCK): ТТН могла створитись
            orders_db.set_kv_state(conn, oid, "ttn_created", kv_ttn_ref=res["ref"], toysi_ttn=res["number"], kv_error=None)
            conn.commit()
            print(f"[kv_runner] {oid}: ТТН НП {res['number']} (ref {res['ref']})", flush=True)
            rep["actions"].append(f"ТТН {res['number']}")
            state = "ttn_created"
            order = {**order, "kv_state": state, "kv_ttn_ref": res["ref"], "toysi_ttn": res["number"]}
        if state == "ttn_created":
            if not apply:
                return rep
            paths = d.save_docs(order["kv_ttn_ref"], oid)
            orders_db.set_kv_state(conn, oid, "docs_ready", kv_docs_path=json.dumps(paths, ensure_ascii=False), kv_error=None)
            conn.commit()
            rep["actions"].append("наліпка 100×100 і накладна збережені")
            state = "docs_ready"
            order = {**order, "kv_state": state, "kv_docs_path": json.dumps(paths, ensure_ascii=False)}
        if state == "docs_ready":
            if not apply:
                return rep
            try:
                paths = json.loads(order.get("kv_docs_path") or "{}")
            except ValueError:
                paths = {}
            if not all(paths.get(k) and Path(paths[k]).is_file() for k in ("marking", "document")):
                raise RunnerError("файли наліпки/накладної не знайдені — перезбережіть (kv_state=ttn_created)")
            res = d.push_docs(str(order["kv_order_id"]), paths)
            if not docs_push_ok(res):
                raise RunnerError(f"ttn-data: відповідь неоднозначна/з помилкою ({json.dumps(res, ensure_ascii=False, default=str)[:200]}) — документи не підтверджені, повтор наступного циклу")
            orders_db.set_kv_state(conn, oid, "docs_sent", kv_error=None)
            conn.commit()
            rep["actions"].append("документи передано в KIDDISVIT (ttn-data)")
    except RunnerError as e:
        rep["problems"].append(str(e))
    except Exception as e:  # noqa: BLE001
        rep["problems"].append(f"збій {type(e).__name__}: {str(e)[:200]}")
    if rep["problems"] and apply:
        try:
            orders_db.set_kv_state(conn, oid, None, kv_error="; ".join(rep["problems"])[:500])
            conn.commit()
        except Exception as e:  # noqa: BLE001
            print(f"[kv_runner] {oid}: не вдалося записати kv_error ({type(e).__name__})", file=sys.stderr)
        _alert(oid, str(state), "; ".join(rep["problems"])[:400] + " — конвеєр зупинено на цьому замовленні")
    elif rep["problems"]:
        _alert(oid, "wait", "; ".join(rep["problems"])[:400] + " — замовлення KIDDISVIT чекає (dry-run)")
    return rep


def run(apply: bool | None = None, deps=None, db_path=None) -> list:
    if apply is None:
        apply, why = apply_enabled()
        if not apply:
            print(f"[kv_runner] DRY-RUN ({why})")
    return _run_pipeline(apply, deps, db_path)


def _run_pipeline(apply: bool, deps=None, db_path=None) -> list:
    import kiddisvit_orders as ko
    import orders_db
    since = _since()
    reports = []
    with (orders_db.get_connection(db_path) if db_path else orders_db.get_connection()) as conn:
        pending = orders_db.get_kv_orders_pending(conn)
        if since and since != "INVALID":
            pending = [o for o in pending if str(o.get("created_at") or "") >= since or o.get("kv_state")]      # старіші без стану — оформлені вручну
        elif apply:
            pending = [o for o in pending if o.get("kv_state")]      # apply без дійсного SINCE не станеться (apply_enabled), але лишаємо захист: нові кроки не беремо
        if not pending:
            print("[kv_runner] немає замовлень KIDDISVIT у роботі")
            return reports
        try:
            supplier = ko.load_supplier()
        except Exception as e:  # noqa: BLE001
            print(f"[kv_runner] постачальницький файл недоступний ({type(e).__name__})", file=sys.stderr)
            return [{"problems": [f"supplier недоступний ({type(e).__name__})"]}]
        light = [o for o in pending if o.get("kv_state") not in ACTION_STATES]
        action_all = sorted([o for o in pending if o.get("kv_state") in ACTION_STATES], key=lambda o: (o.get("kv_updated_at") or "", o.get("created_at") or ""))
        action = action_all[:MAX_ORDERS_PER_RUN]
        d = deps or _Deps()
        for order in light + action:
            rep = process_order(conn, order, supplier, apply, d)
            reports.append(rep)
            print("[kv_runner]", json.dumps(rep, ensure_ascii=False, default=str))
    return reports


if __name__ == "__main__":
    try:
        from dotenv import load_dotenv
        load_dotenv(HERE / ".env")
    except ImportError:
        pass
    from orders_db import init_db
    init_db()
    out = run()
    sys.exit(0 if (os.environ.get("KV_RUNNER_APPLY") != "1" or all(not r.get("problems") for r in out)) else 1)

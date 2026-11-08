#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_site_order_status_notify.py — Telegram-стан замовлення САЙТУ від передачі в Toysi до «отримано»/«скасовано» (рішення власника 08.11.2026).
Інваріанти: (1) повідомлення лише на ЗМІНУ стану (дедуп у orders.site_tg_state), після фінального — тиша; (2) «отримано» = текст статусу НП; «Прибув у відділення» і відмова НП — не «отримано»; (3) скасовано — окреме повідомлення;
(4) старі замовлення (передані до SITE_TG_SINCE) лише засіваються мовчки; (5) збій Telegram → стан не пишеться, повтор у наступному циклі; (6) не-site платформи не чіпаємо; (7) ПІБ/телефон у тексті немає;
(8) повідомлення проходять режим «лише замовлення»; (9) збій НП не ламає трекер. `python test_site_order_status_notify.py` → exit 0/1."""
import os
import sys
import tempfile
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
os.environ["AUDIT_NO_TELEGRAM"] = "1"
os.environ["ORDERS_DB_PATH"] = os.path.join(tempfile.mkdtemp(), "orders.db")
os.environ.pop("TELEGRAM_ORDERS_ONLY", None)
sys.path.insert(0, str(Path(__file__).parent))
import orders_db  # noqa: E402
import nova_poshta  # noqa: E402
import order_status_tracker as ost  # noqa: E402
import site_order_status_notify as sn  # noqa: E402
import telegram_notify as tn  # noqa: E402
import telegram_triage as tt  # noqa: E402

F = []


def chk(n, c):
    print(f"[{'OK ' if c else 'FAIL'}] {n}")
    if not c:
        F.append(n)


orders_db.init_db()


def mk(oid, platform="site", fwd="2026-11-08T19:30:00", tid="1", ds=None, carrier="nova_poshta"):
    with orders_db.get_connection() as c:
        orders_db.insert_order(c, {"order_id": oid, "platform": platform, "payment_method": "cod", "payment_confirmed": 0, "customer_name": "Іван Петренко", "phone": "380671112233", "email": "b@e.com",
                                   "np_branch": "Київ, №5", "np_city_ref": "C", "np_warehouse_number": "5", "carrier": carrier, "items": [{"toysi_code": "100", "name": "x", "qty": 1, "price": 100}]})
        iid = f"{platform}_{oid}"
        c.execute("UPDATE orders SET forwarded_to_toysi_at=?, toysi_order_id=?, delivery_status=? WHERE internal_order_id=?", (fwd, tid, ds, iid))
        c.commit()
    return iid


def row(iid):
    with orders_db.get_connection() as c:
        return orders_db._row_to_dict(c.execute("SELECT * FROM orders WHERE internal_order_id=?", (iid,)).fetchone())


def npi(status, code, ret=None, typ=None):
    return {"status": status, "status_code": code, "return_ttn": ret, "based_ttn": None, "based_type": typ}


sent = []
tg_ok = {"v": True}
ost.send_telegram_message = lambda t: (sent.append(t) or tg_ok["v"])
NP = {}
np_fail = {"v": False}
TOYSI = {}


def _np(ttns):
    if np_fail["v"]:
        raise nova_poshta.NovaPoshtaAPIError("boom")
    return {t: NP[t] for t in ttns if t in NP}


nova_poshta.get_tracking_batch = _np
ost.fetch_order_statuses = lambda ids: {i: TOYSI[i] for i in ids if i in TOYSI}
for fn in ("_maybe_issue_receipt", "_maybe_advance_rozetka_processing", "_maybe_push_ttn_to_rozetka", "_maybe_push_ttn_to_prom", "_maybe_push_ttn_to_eva", "_maybe_push_delivered_to_prom",
           "_maybe_ticket_rozetka_cancelled", "_maybe_create_np_return", "_maybe_sync_eva_status", "_maybe_return_prom_eva_cancelled", "_maybe_record_observed_discount"):
    setattr(ost, fn, lambda *a, **k: None)


def cycle():
    n = len(sent)
    ost.track_orders()
    return sent[n:]


s1 = mk("S1", tid="21")
old = mk("OLD", tid="22", fwd="2026-10-01T10:00:00", ds="shipped")
p1 = mk("P1", platform="prom", tid="23")
TOYSI.update({"21": {"status": 30, "TTN": None}, "22": {"status": 60, "TTN": "20451500000099"}, "23": {"status": 30, "TTN": None}})
NP["20451500000099"] = npi("Відправлення отримано", "9")

m = cycle()
chk("перший цикл: нове site-замовлення → рівно ОДНЕ повідомлення «передано в Toysi», з номером Toysi", len([x for x in m if "Замовлення сайту" in x]) == 1 and "передано в Toysi" in m[0] and "#21" in m[0])
chk("старе site-замовлення (до SITE_TG_SINCE) засіяно мовчки: повідомлення нема, стан записано", not any("20451500000099" in x for x in m) and row(old)["site_tg_state"] == "received")
chk("prom-замовлення не чіпаємо (стан NULL)", row(p1)["site_tg_state"] is None)
chk("повторний цикл без змін → тиша (дедуп)", cycle() == [])

TOYSI["21"] = {"status": 40, "TTN": None}
m = cycle()
chk("Toysi 40 → «на збиранні»", len(m) == 1 and "на збиранні" in m[0])
TOYSI["21"] = {"status": 50, "TTN": None}
chk("Toysi 50 → «запаковано»", "запаковано" in "".join(cycle()))
TOYSI["21"] = {"status": 60, "TTN": "20451500000021"}
m = cycle()
chk("Toysi 60 + ТТН без даних НП → «відвантажено» із ТТН", len(m) == 1 and "відвантажено" in m[0] and "20451500000021" in m[0])
NP["20451500000021"] = npi("Відправлення прямує до міста Київ", "5")
m = cycle()
chk("НП «прямує» → одне повідомлення 🚚 з ТТН і текстом НП", len(m) == 1 and m[0].startswith("🚚") and "прямує" in m[0])
chk("той самий НП-статус ще цикл → тиша", cycle() == [])
NP["20451500000021"] = npi("Прибув у відділення", "7")
m = cycle()
chk("НП «Прибув у відділення» — НЕ «отримано»: повідомлення 🚚, стан не фінальний", len(m) == 1 and m[0].startswith("🚚") and row(s1)["site_tg_state"] == "np:7")
np_fail["v"] = True
chk("збій НП → трекер не падає, нових повідомлень нема", cycle() == [] and row(s1)["site_tg_state"] == "np:7")
np_fail["v"] = False
NP["20451500000021"] = npi("Відправлення отримано", "9")
tg_ok["v"] = False
m = cycle()
chk("збій Telegram (send→False): повідомлення намагались, стан НЕ записано (повтор у наступному циклі)", len(m) == 1 and row(s1)["site_tg_state"] == "np:7")
tg_ok["v"] = True
m = cycle()
chk("Telegram повернувся → «✅ отримано», стан received", len(m) == 1 and m[0].startswith("✅") and "отримано" in m[0] and row(s1)["site_tg_state"] == "received")
NP["20451500000021"] = npi("Щось нове", "1")
chk("після «отримано» — тиша назавжди (навіть якщо НП зміниться)", cycle() == [])

allm = " ".join(sent)
chk("приватність: у жодному повідомленні нема ПІБ/телефону/адреси/email", "Петренко" not in allm and "380671112233" not in allm and "Київ, №5" not in allm and "b@e.com" not in allm)

# ── скасування
s2 = mk("S2", tid="31")
TOYSI["31"] = {"status": 30, "TTN": None}
cycle()
TOYSI["31"] = {"status": 10, "TTN": None}
m = cycle()
chk("скасовано в Toysi (10) → одне повідомлення ❌, стан cancelled", len([x for x in m if x.startswith("❌")]) == 1 and row(s2)["site_tg_state"] == "cancelled")

# ── відмова НП → returned
s3 = mk("S3", tid="41")
TOYSI["41"] = {"status": 60, "TTN": "20451500000041"}
NP["20451500000041"] = npi("Прямує", "5")
cycle()
NP["20451500000041"] = npi("Відмова від отримання", "102", "59001806649372", "CargoReturn")
m = cycle()
chk("відмова НП (returned): є ↩️-повідомлення, НЕ «отримано», стан returned", any(x.startswith("↩️") for x in m) and not any(x.startswith("✅") and "20451500000041" in x for x in m) and row(s3)["site_tg_state"] == "returned")

# ── аудит #1126 п.1: скасування, коли Telegram недоступний, не губиться (замовлення вже поза активними)
s5 = mk("S5", tid="61")
TOYSI["61"] = {"status": 30, "TTN": None}
cycle()
tg_ok["v"] = False
TOYSI["61"] = {"status": 10, "TTN": None}
m = cycle()
chk("скасовано, Telegram недоступний: спроба була, стан НЕ фінальний, delivery_status=cancelled", len(m) >= 1 and row(s5)["site_tg_state"] == "processing" and row(s5)["delivery_status"] == "cancelled")
tg_ok["v"] = True
m = cycle()
chk("Telegram повернувся → ❌ скасовано надіслано ПОВТОРНО (замовлення вже не в активних), стан cancelled", len([x for x in m if x.startswith("❌") and "site_S5" in x]) == 1 and row(s5)["site_tg_state"] == "cancelled")
chk("і більше не повторюється", [x for x in cycle() if "site_S5" in x] == [])

# ── аудит #1126 п.5: старе замовлення не сповіщається, навіть якщо НП вперше відповіла пізніше
old2 = mk("OLD2", tid="71", fwd="2026-10-02T10:00:00", ds="shipped")
TOYSI["71"] = {"status": 60, "TTN": "20451500000071"}
np_fail["v"] = True
cycle()
np_fail["v"] = False
NP["20451500000071"] = npi("Відправлення отримано", "9")
m = cycle()
chk("старе замовлення: НП спершу мовчала, потім «отримано» → повідомлення нема, стан оновлено до received", not any("20451500000071" in x for x in m) and row(old2)["site_tg_state"] == "received")

# ── аудит #1129: слово «отримано» у статусі без коду вручення — НЕ доставлено
chk("текст «отримано відправником», код 5 → не доставлено; код 9 → доставлено; код 11 «видано одержувачу» → доставлено; 106 → доставлено",
    not sn.np_delivered(npi("Відправлення отримано відправником", "5")) and sn.np_delivered(npi("Відправлення отримано", "9")) and sn.np_delivered(npi("Відправлення отримано. Грошовий переказ видано одержувачу", "11"))
    and sn.np_delivered(npi("Одержано і створено ЄН повернення коштів", "106")) and not sn.np_delivered(None) and not sn.np_delivered(npi("Відправлення отримано", "9", "59001806649372", "CargoReturn")))

# ── аудит #1126 п.3: документ повернення важить більше за слово «отримано»
chk("«отримано» разом з документом повернення (return_ttn) — НЕ отримано", sn.compute_stage({"public_number": "PT-1"}, "9", "shipped", "20451500000088", npi("Відправлення отримано", "9", "59001806649372", "CargoReturn"))[0] != "received")

# ── delivered для site за сигналом НП (інакше reviews_db.eligible_order нікого не пускає до відгуку)
import reviews_db  # noqa: E402
r1 = row(s1)
chk("site-замовлення, вручене за НП: delivery_status=delivered, delivered_at проставлено, і воно НЕ відкочується в shipped наступними циклами", r1["delivery_status"] == "delivered" and r1["delivered_at"] and cycle() is not None and row(s1)["delivery_status"] == "delivered")
chk("доставлене site-замовлення має право на відгук (reviews_db.eligible_order)", reviews_db.eligible_order(row(s1)) is True)
chk("відмова НП → returned, НЕ delivered (і не право на відгук)", row(s3)["delivery_status"] == "returned" and reviews_db.eligible_order(row(s3)) is False)
chk("скасоване site → не delivered", row(s2)["delivery_status"] == "cancelled")
chk("prom-замовлення цим не чіпаємо: delivery_status лишається Toysi-ним (processing)", row(p1)["delivery_status"] == "processing")
chk("старе site-замовлення, вручене за НП (OLD2): теж delivered (стан засіяно мовчки, повідомлення нема)", row(old2)["delivery_status"] == "delivered")

# ── повний режим Telegram: скасування не дублюємо
os.environ["TELEGRAM_ORDERS_ONLY"] = "0"
s4 = mk("S4", tid="51")
TOYSI["51"] = {"status": 30, "TTN": None}
cycle()
TOYSI["51"] = {"status": 10, "TTN": None}
m = cycle()
chk("повний режим Telegram: скасування site НЕ дублюється нашим ❌ (шле трекер своє ⚠️)", not any(x.startswith("❌") for x in m) and any("скасовано" in x for x in m) and row(s4)["site_tg_state"] == "cancelled")
os.environ.pop("TELEGRAM_ORDERS_ONLY", None)

# ── режим «лише замовлення»: тексти проходять
tmp = Path(tempfile.mkdtemp())
tn.ALERTS_LOG_FILE = tmp / "telegram_alerts.md"
tn.ALERT_THROTTLE_FILE = tmp / ".alert_throttle.json"
tn.TELEGRAM_BOT_TOKEN, tn.TELEGRAM_CHAT_ID = "T", "C"
posts = []


class _R:
    def raise_for_status(self):
        pass

    def json(self):
        return {"ok": True}


tn.requests.post = lambda url, json=None, timeout=0: (posts.append(json["text"]) or _R())
site_msgs = [x for x in sent if "Замовлення сайту" in x]
chk("зібрано повідомлень сайту для перевірки фільтра", len(site_msgs) >= 8)
for t in site_msgs + ["🛑 site_PT-20261108-193000-abc123: замовлення сайту НЕ передано в Toysi — нема валідного номера відділення"]:
    posts.clear()
    tn.send_telegram_message(t)
    chk(f"режим «лише замовлення»: проходить у Telegram — {t[:44]}", len(posts) == 1 and tt.classify("order_status_tracker.py", t)[1] == "send")
posts.clear()
tn.send_telegram_message("ℹ️ щось стороннє про замовлення сайту")
chk("стороннє повідомлення режим усе одно глушить", posts == [])
chk("compute_stage: Toysi 70 → received; невідомий статус без ТТН → None", sn.compute_stage({"public_number": "PT-1"}, "9", "delivered", None, None)[0] == "received" and sn.compute_stage({"public_number": "PT-1"}, "9", "unknown_77", None, None) is None)

print("\nFAIL:" if F else "\nOK: усі перевірки пройдені", *F, sep="\n")
sys.exit(1 if F else 0)

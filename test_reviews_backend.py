#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_reviews_backend.py — бекенд відгуків (06.11.2026): reviews_db.py, ендпоінти POST api/review/form та api/review у site_order_api.py, CLI review_moderate.py. Контракт — site/REVIEWS_CONTRACT.md.
Інваріанти: (1) токен лише для ДОСТАВЛЕНОГО замовлення сайту й товару Toysi з цього замовлення (не rt-/dropt-/скасоване/sandbox/чуже), один раз на пару; у БД лише хеш; (2) form: 200 лише для живого токена,
інакше однаково 404 (нема/використаний/прострочений); (3) submit: поля за правилами збірки, невалідні → 400 і токен НЕ спалено; успіх спалює; повторний → 409; невідомий → 404; (4) модерація:
published лише після approve, reject знімає; reviews.json за контрактом і ПРИЙМАЄТЬСЯ збіркою сайту (_load_reviews) без попереджень; (5) ліміт запитів 429; (6) відповіді не містять токена; нічого не шле назовні."""
import io
import json
import os
import sys
import tempfile
from contextlib import redirect_stderr
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).parent
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
os.environ["AUDIT_NO_TELEGRAM"] = "1"
TMP = Path(tempfile.mkdtemp())
os.environ["ORDERS_DB_PATH"] = str(TMP / "orders.db")
os.environ["REVIEWS_EXPORT_FILE"] = str(TMP / "reviews.json")
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "site"))
import orders_db  # noqa: E402
import reviews_db as rdb  # noqa: E402
import review_moderate as rm  # noqa: E402
import site_order_api as api  # noqa: E402

F = []


def chk(n, c):
    print(f"[{'OK ' if c else 'FAIL'}] {n}")
    if not c:
        F.append(n)


orders_db.init_db()
NOW = datetime(2026, 11, 6, 12, 0, tzinfo=timezone.utc)


def new_order(oid, items, delivery="delivered", status=None, platform="site", cancelled=None):
    with orders_db.get_connection() as c:
        orders_db.insert_order(c, {"order_id": oid, "platform": platform, "payment_method": "prepaid", "payment_confirmed": 1, "customer_name": "Іван Петренко", "phone": "380671112233", "email": "b@e.com",
                                   "np_branch": "Київ, Відділення №5", "carrier": "nova_poshta", "items": items})
        iid = f"{platform}_{oid}"
        c.execute("UPDATE orders SET delivery_status=?, delivered_at=?, status=COALESCE(?, status), cancelled_at=? WHERE internal_order_id=?", (delivery, "2026-11-01T10:00:00", status, cancelled, iid))
        c.commit()
        return orders_db._row_to_dict(c.execute("SELECT * FROM orders WHERE internal_order_id=?", (iid,)).fetchone())


ITEMS = [{"toysi_code": "251827", "name": "Тест", "qty": 1, "price": 100}, {"toysi_code": "rt-5", "name": "RT", "qty": 1, "price": 100}, {"toysi_code": "dropt-7", "name": "D", "qty": 1, "price": 100}]
o1 = new_order("A1", ITEMS)
BADO = (("не доставлене", new_order("A2", ITEMS, delivery="shipped")), ("sandbox", new_order("A3", ITEMS, status="sandbox_test")),
        ("скасоване", new_order("A4", ITEMS, cancelled="2026-11-02T10:00:00")), ("не сайт (Prom)", new_order("A5", ITEMS, platform="prom")))
B1O = new_order("B1", [{"toysi_code": "300", "name": "Т2", "qty": 1, "price": 10}])
with orders_db.get_connection() as conn:
    t_ok = rdb.issue_token(conn, o1, "251827", NOW)
    chk("видано токен для доставленого замовлення сайту й товару Toysi", bool(t_ok) and rdb.valid_token_shape(t_ok))
    chk("у БД лише хеш токена, сирого токена нема", t_ok not in json.dumps([dict(r) for r in conn.execute("SELECT * FROM reviews")], ensure_ascii=False))
    chk("друге запрошення на ту саму пару (замовлення, товар) не видається", rdb.issue_token(conn, o1, "251827", NOW) is None)
    chk("rt-товар/dropt-товар/нецифровий id → без посилання", rdb.issue_token(conn, o1, "rt-5", NOW) is None and rdb.issue_token(conn, o1, "dropt-7", NOW) is None and rdb.issue_token(conn, o1, "abc", NOW) is None)
    chk("товару, якого нема в замовленні → без посилання", rdb.issue_token(conn, o1, "999", NOW) is None)
    for name, od in BADO:
        chk(f"замовлення «{name}» → без посилання", rdb.issue_token(conn, od, "251827", NOW) is None)

    # form_info
    chk("form_info: живий токен → product_id", rdb.form_info(conn, t_ok, NOW) == {"product_id": "251827"})
    chk("form_info: невідомий/сміттєвий/порожній/None токен → None", all(rdb.form_info(conn, x, NOW) is None for x in ("A" * 32, "x", "", None, 123, "../../etc/passwd")))
    chk("form_info: прострочений (через TOKEN_TTL_DAYS+1) → None", rdb.form_info(conn, t_ok, NOW + timedelta(days=rdb.TOKEN_TTL_DAYS + 1)) is None)

    # submit: невалідні поля не спалюють токен
    bad = [(0, "Чудовий товар"), (6, "Чудовий товар"), ("5", "Чудовий товар"), (True, "Чудовий товар"), (5.0, "Чудовий товар"), (5, "ab"), (5, "   ​​ ­"), (5, "x" * 1501), (5, None), (5, 123)]
    errs = 0
    for r, t in bad:
        try:
            rdb.submit(conn, t_ok, r, t, "Олена", NOW)
        except rdb.ReviewError:
            errs += 1
    chk("submit: оцінка 0/6/«5»/True/5.0, текст <3, невидимий, >1500, не рядок → ReviewError (10 з 10)", errs == len(bad))
    try:
        rdb.submit(conn, t_ok, 5, "Чудовий товар", "А" * 41, NOW)
        ok = False
    except rdb.ReviewError:
        ok = True
    chk("submit: автор >40 символів → ReviewError", ok)
    chk("після 11 невдалих спроб токен ЖИВИЙ (не спалено)", rdb.form_info(conn, t_ok, NOW) is not None)
    chk("невідомий токен → ('invalid')", rdb.submit(conn, "Z" * 32, 5, "Чудовий товар", "Олена", NOW) == ("invalid", None))
    chk("прострочений токен → ('invalid')", rdb.submit(conn, t_ok, 5, "Чудовий товар", "Олена", NOW + timedelta(days=rdb.TOKEN_TTL_DAYS + 1)) == ("invalid", None))
    st, rid = rdb.submit(conn, t_ok, 5, "  Чудовий ‮товар,\r\nдитині сподобався  ", "  Олена   П. ", NOW)
    rec = rdb.get(conn, rid)
    chk("submit успішний: pending; bidi вирізано, пробіли згорнуто", st == "ok" and rec["status"] == "pending" and rec["text"] == "Чудовий товар,\nдитині сподобався" and rec["author"] == "Олена П." and rec["rating"] == 5)
    chk("повторне використання → ('used') і form_info → None", rdb.submit(conn, t_ok, 4, "Ще раз", "Хтось", NOW) == ("used", None) and rdb.form_info(conn, t_ok, NOW) is None)
    t2 = rdb.issue_token(conn, B1O, "300", NOW)
    st2, rid2 = rdb.submit(conn, t2, 4, "Нормальний товар", "", NOW)
    chk("порожній автор → «Покупець»", rdb.get(conn, rid2)["author"] == "Покупець")
    chk("pending не потрапляє в експорт", rdb.build_export(conn, NOW)["_meta"]["count"] == 0)

    # модерація
    chk("approve → published; reject повертає rejected; invited змінити не можна", rdb.moderate(conn, rid, "approve", "t", NOW) == "published" and rdb.moderate(conn, 999, "approve") is None)
    rdb.moderate(conn, rid2, "approve", "t", NOW)
    conn.commit()
    n = rdb.export_reviews(conn, now=NOW)
    doc = json.loads((TMP / "reviews.json").read_text(encoding="utf-8"))
    chk("reviews.json: _meta.count = 2, ключі — id товару, запис {rating,text,author,date}, date YYYY-MM-DD", n == 2 and doc["_meta"]["count"] == 2 and set(doc) == {"_meta", "251827", "300"} and set(doc["251827"][0]) == {"rating", "text", "author", "date"} and doc["251827"][0]["date"] == "2026-11-06")
    chk("в експорті нема email/токена/order_id", all(k not in json.dumps(doc, ensure_ascii=False) for k in ("b@e.com", "token", "site_A1", "380671112233")))
    # збірка сайту приймає файл без попереджень
    import build_site as bs  # noqa: E402
    bs._REVIEWS_FILE = str(TMP / "reviews.json")
    err = io.StringIO()
    with redirect_stderr(err):
        bs._load_reviews(now=NOW)
    loaded = {k: [x["rating"] for x in v] for k, v in bs._REVIEWS.items()}
    chk("збірка сайту (_load_reviews) приймає reviews.json: обидва відгуки, жодних попереджень про fail-closed", loaded == {"251827": [5], "300": [4]} and "fail-closed" not in err.getvalue())
    rdb.moderate(conn, rid2, "reject", "t", NOW)
    conn.commit()
    chk("reject знімає з експорту (count 1)", rdb.export_reviews(conn, now=NOW) == 1 and json.loads((TMP / "reviews.json").read_text(encoding="utf-8"))["_meta"]["count"] == 1)

# ── HTTP-обробники (без мережі): форма, прийом, ліміт
api.price_map = lambda: {"251827": {"name": "Тестовий набір", "price": 100}}


def call(handler_name, payload, ip="1.2.3.4"):
    out = []
    h = api.Handler.__new__(api.Handler)
    h._read_json = lambda: payload
    h._json = lambda code, obj, no_store=False: out.append((code, obj, no_store))
    h._client_ip = lambda: ip
    getattr(h, handler_name)()
    return out[0]


o3 = new_order("C1", [{"toysi_code": "251827", "name": "Тест", "qty": 1, "price": 100}])
with orders_db.get_connection() as conn:
    tok = rdb.issue_token(conn, o3, "251827")
    conn.commit()
chk("POST api/review/form: живий токен → 200, product_name із каталогу сайту, no-store", call("_handle_review_form", {"t": tok})[:2] == (200, {"product_id": "251827", "product_name": "Тестовий набір"}) and call("_handle_review_form", {"t": tok})[2])
chk("form: невідомий токен/без тіла/токен у query-стилі → 404 (не 500)", all(call("_handle_review_form", p)[0] == 404 for p in ({"t": "Q" * 32}, {}, {"t": 5}, {"token": tok})))
chk("submit: поля невалідні → 400 і токен ще живий", call("_handle_review_submit", {"t": tok, "rating": 9, "text": "Чудово", "author_name": "Олена"})[0] == 400 and call("_handle_review_form", {"t": tok})[0] == 200)
code, body, ns = call("_handle_review_submit", {"t": tok, "rating": 5, "text": "Чудовий набір, дякуємо", "author_name": "Олена"})
chk("submit: успіх → 200 {ok}, відповідь без токена", code == 200 and body == {"ok": True} and tok not in json.dumps(body))
chk("submit: повторно → 409; форма тепер 404; невідомий токен → 404", call("_handle_review_submit", {"t": tok, "rating": 5, "text": "Чудовий набір", "author_name": "О"})[0] == 409 and call("_handle_review_form", {"t": tok})[0] == 404
    and call("_handle_review_submit", {"t": "Q" * 32, "rating": 5, "text": "Чудовий набір", "author_name": "О"})[0] == 404)
api._REVIEW_LIMITER = api.RateLimiter(3, 60)
codes = [call("_handle_review_form", {"t": "Q" * 32}, ip="9.9.9.9")[0] for _ in range(5)]
chk("ліміт запитів на IP: після 3-х → 429", codes == [404, 404, 404, 429, 429] and call("_handle_review_submit", {"t": "Q" * 32}, ip="9.9.9.9")[0] == 429)
src = (HERE / "site_order_api.py").read_text(encoding="utf-8")
chk("токен не друкується в журнал (немає print із токеном у відгукових обробниках)", "print(f\"[site_order_api] review" in src and "{token}" not in src[src.index("def _handle_review_form"):src.index("def _client_ip")])

# не-об'єкт JSON у тілі → відповідь (404), а не виняток (аудит #1108)
for n_, body in enumerate(([1], "x", 123, None)):
    chk(f"тіло JSON {body!r} (не об'єкт): form → 404, submit → 404 (без винятку)", call("_handle_review_form", body, ip=f"7.7.7.{n_}")[0] == 404 and call("_handle_review_submit", body, ip=f"7.7.8.{n_}")[0] == 404)

# перевидача простроченого посилання
o9 = new_order("E1", [{"toysi_code": "500", "name": "Т5", "qty": 1, "price": 10}])
with orders_db.get_connection() as conn:
    tk1 = rdb.issue_token(conn, o9, "500", NOW)
    chk("перше посилання видано; друге (живе) — ні", bool(tk1) and rdb.issue_token(conn, o9, "500", NOW + timedelta(days=1)) is None)
    late = NOW + timedelta(days=rdb.TOKEN_TTL_DAYS + 1)
    tk2 = rdb.issue_token(conn, o9, "500", late)
    chk("прострочене посилання перевидається новим; старе мертве, нове живе", bool(tk2) and tk2 != tk1 and rdb.form_info(conn, tk1, late) is None and rdb.form_info(conn, tk2, late) is not None)
    rdb.submit(conn, tk2, 5, "Нормально, дякуємо", "Іра", late)
    chk("після використання (pending) перевидати не можна навіть через роки", rdb.issue_token(conn, o9, "500", NOW + timedelta(days=500)) is None)
    conn.commit()
NL = chr(10)
ESC, BEL = chr(27), chr(7)
chk("review_moderate._clean: ESC/керівні → «?», перенос рядка зберігається", rm._clean("a" + ESC + "[31mb" + BEL + "c" + NL + "d") == "a?[31mb?c" + NL + "d")

# ── CLI
o4 = new_order("D1", [{"toysi_code": "400", "name": "Т4", "qty": 1, "price": 10}])
buf = io.StringIO()
from contextlib import redirect_stdout  # noqa: E402
with redirect_stdout(buf):
    rc_elig = rm.main(["--eligible"])
    rc_issue = rm.main(["--issue", "site_D1", "400"])
    rc_issue2 = rm.main(["--issue", "site_D1", "400"])
out_txt = buf.getvalue()
chk("CLI --eligible бачить доставлене замовлення без посилання; --issue видає лише ОДНЕ посилання зі структурою #t=", "site_D1 товар 400" in out_txt and rc_issue == 0 and rc_issue2 == 1 and out_txt.count("https://plutustoys.com.ua/review.html#t=") == 1)
with orders_db.get_connection() as conn:
    pend = rdb.list_by_status(conn, "pending")
buf = io.StringIO()
with redirect_stdout(buf):
    rc_l = rm.main(["--list"])
chk("CLI --list (pending) працює без помилок", rc_l == 0)
print("\nFAIL:" if F else "\nOK: усі перевірки пройдені", *F, sep="\n")
sys.exit(1 if F else 0)

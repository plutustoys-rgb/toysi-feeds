#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_rt_cabinet.py — rt_cabinet.safe_link (аудит PR #871): режим читання не відкриває небезпечних посилань. Офлайн, без Playwright."""
import sys
from pathlib import Path
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).parent))
import rt_cabinet as rc  # noqa: E402
F = []
def chk(n, c):
    print(f"[{'OK ' if c else 'FAIL'}] {n}")
    if not c:
        F.append(n)
B = "https://royaltoys.com.ua"
for ok in ("/ua/my/", "/ua/my/orders/", "/ua/my/orders/?page=2", "/ua/my/orders/12/", "/ua/my/profile/", "/ua/dropshipping/"):
    chk(f"дозволено: {ok}", rc.safe_link(B + ok))
bad = ["/orders/12/del", "/orders/12/drop", "/my/orders/repeat/12", "/my/order/reorder/12", "/my/orders/pay/12", "/my/orders/confirm/12", "/my/orders/12/send", "/my/orders/12/close", "/my/orders/12/status/2",
       "/my/signout", "/my/logout/", "/my/wishlist/add/3", "/my/orders/12/annul", "/my/orders/create", "/my/orders/add", "/order/delete/12", "/cart/add/5", "/ua/auth/", "/my/orders/12/скасувати", "/my/orders/видалити", "/my/orders/отмена",
       "/my/orders/?action=remove", "/ua/my/?logout", "/ua/my/orders/cancelorder/12", "/ua/my/cart/removeitem/3", "/ua/my/orders/%64elete/12", "/ua/my/orders/disable/", "/my/orders/archive/", "/my/orders/return/", "/my/orders/unlink", "/ua/my/orders/?mode=delete", "/ua/my/orders/?go=logout", "/my/orders/?token=abc", "/my/orders/?do=cancel", "/my/orders/?status=2", "/blog/", "/ua/category/igrushki/"]
for b in bad:
    chk(f"заблоковано: {b}", not rc.safe_link(B + b))
chk("чужий хост і підрядок у query: заблоковано", not rc.safe_link("https://evil.tld/?r=royaltoys.com.ua/my/") and not rc.safe_link("https://royaltoys.com.ua.evil.tld/my/") and not rc.safe_link("http://royaltoys.com.ua/my/"))
chk("_norm лишає лише ?page=N", rc._norm(B + "/my/orders/?page=3&sort=x") == B + "/my/orders/?page=3" and rc._norm(B + "/my/orders/?sort=x") == B + "/my/orders/")
import os as _os, io as _io, contextlib as _cl
for k in ("RT_CABINET_EMAIL", "RT_CABINET_PHONE", "RT_CABINET_PASSWORD"):
    _os.environ.pop(k, None)
import dotenv as _dotenv
_dotenv.load_dotenv = lambda *a, **k: False      # тест не має підтягувати реальні RT_CABINET_* з локального .env (інакше запустив би справжній вхід)
_err = _io.StringIO()
with _cl.redirect_stderr(_err):
    _rc = rc.vps_login()
chk("--vps-login без RT_CABINET_PHONE/PASSWORD у середовищі → код 1, браузер не запускається, облікових даних у виводі нема", _rc == 1 and "RT_CABINET_EMAIL" in _err.getvalue())
src = Path(rc.__file__).read_text(encoding="utf-8")
chk("пароль ніколи не друкується: у файлі нема print з password/phone-змінними", not any(("password" in ln or "phone" in ln) and "print(" in ln and "RT_CABINET_" not in ln for ln in src.splitlines()))
B2 = "https://royaltoys.com.ua"
for pw_ in ("x~y", "a*b@c", "pa ss&w=rd", "Zażółć1", "abc123"):
    from urllib.parse import quote as _q, quote_plus as _qp
    chk(f"замок: пароль {pw_!r} у query (сирий/quote/quote_plus) → leaks", rc.leaks_secret(pw_, f"{B2}/ua/auth/email/?password={pw_}") and rc.leaks_secret(pw_, f"{B2}/x?p={_q(pw_, safe='')}") and rc.leaks_secret(pw_, f"{B2}/x?p={_qp(pw_)}"))
chk("замок: браузерне кодування `~`→%7E і `*` сирий (a*b%40c) ловиться через unquote", rc.leaks_secret("x~y", f"{B2}/x?p=x%7Ey") and rc.leaks_secret("a*b@c", f"{B2}/x?p=a*b%40c"))
chk("замок: пароль у fragment → leaks", rc.leaks_secret("secret1", f"{B2}/x#secret1"))
chk("замок: легітимне — короткий пароль «ua»/«auth» збігається зі шляхом, але не з query → НЕ leaks", not rc.leaks_secret("ua", f"{B2}/ua/auth/email/") and not rc.leaks_secret("auth", f"{B2}/ua/auth/email/") and not rc.leaks_secret("royal", f"{B2}/ua/auth/email/?page=2"))
chk("замок: тіло запиту на сторонній хост з паролем → leaks; без тіла/без пароля → ні; порожній секрет → ні", rc.leaks_secret("pw123456", "https://evil.tld/c", "email=a&password=pw123456") and not rc.leaks_secret("pw123456", "https://evil.tld/c", "email=a") and not rc.leaks_secret("", f"{B2}/x?p=1"))
chk("замок: бінарне (не UTF-8) тіло на сторонній хост не ламає перевірку; пароль у бінарному тілі ловиться; без пароля — ні", rc.leaks_secret("pw123456", "https://evil.tld/c", b"\xff\xfe\x00pw123456\x80") and not rc.leaks_secret("pw123456", "https://evil.tld/c", b"\xff\xfe\x00\x80"))
# --- оформлення замовлення (валідація без мережі) ---
import tempfile as _tf3, json as _j3
with _tf3.TemporaryDirectory() as td3:
    pdf = Path(td3) / "m.pdf"
    pdf.write_bytes(b"%PDF-1.4" + bytes([10]) + b"x" * 300)
    base = {"items": [{"vendor_code": "200513", "qty": 2, "cost": 155}], "recipient_name": "Іваненко Іван", "recipient_phone": "050 123-45-67", "ttn_number": "2045 1500 0000 00", "marking_pdf": str(pdf), "comment": "c"}
    ok = rc.validate_order(base)
    chk("validate_order: нормалізація телефону (+380…), № ТТН (14 цифр без пробілів), позиції", ok["recipient_phone"] == "+380501234567" and ok["ttn_number"] == "20451500000000" and ok["items"] == [{"vendor_code": "200513", "qty": 2, "cost": 155.0}])
    def bad(**kw):
        d = dict(base)
        d.update(kw)
        try:
            rc.validate_order(d)
            return False
        except rc.OrderError:
            return True
    chk("validate_order: відмови — порожні позиції, qty 0/100+/не число, один зі слова ПІБ, поганий телефон, ТТН не 14 цифр", bad(items=[]) and bad(items=[{"vendor_code": "1", "qty": 1}]) and bad(items=[{"vendor_code": "200513", "qty": 0}]) and bad(items=[{"vendor_code": "200513", "qty": 101}]) and bad(items=[{"vendor_code": "200513", "qty": "x"}]) and bad(recipient_name="Іваненко") and bad(recipient_phone="123") and bad(ttn_number="123") and bad(ttn_number="2045150000000a"))
    notpdf = Path(td3) / "n.pdf"
    notpdf.write_bytes(b"<html>" + b"x" * 300)
    tiny = Path(td3) / "t.pdf"
    tiny.write_bytes(b"%PDF")
    big = Path(td3) / "b.pdf"
    big.write_bytes(b"%PDF-1.4" + bytes([10]) + b"x" * (3 * 1024 * 1024))
    chk("validate_order: marking_pdf — не PDF / замалий / завеликий / нема файлу → відмова", bad(marking_pdf=str(notpdf)) and bad(marking_pdf=str(tiny)) and bad(marking_pdf=str(big)) and bad(marking_pdf=str(Path(td3) / "none.pdf")))
    nop = validate_order_no_pii = rc.validate_order({k: v for k, v in base.items() if k not in ("recipient_name", "recipient_phone")})
    chk("ПІБ і телефон необов'язкові: без них валідація проходить, у нормалізованому замовленні порожні (поля форми не заповнюються)", nop["recipient_name"] == "" and nop["recipient_phone"] == "")
    chk("require_recipient=True: без ПІБ/телефону -> відмова (RoyalToys не пропускає без них); з ними — проходить", bad(require_recipient=True, recipient_name="", recipient_phone="") and rc.validate_order(dict(base, require_recipient=True))["recipient_phone"] == "+380501234567")
    chk("якщо ПІБ/телефон вказано, але некоректні → відмова (не вказувати взагалі)", bad(recipient_name="Іваненко") and bad(recipient_phone="123"))
    chk("validate_order: cost обов'язкова, скінченна і > 0 (nan/inf/0/-1/нема → відмова); дублі артикулів → відмова", bad(items=[{"vendor_code": "200513", "qty": 1, "cost": float("nan")}]) and bad(items=[{"vendor_code": "200513", "qty": 1, "cost": float("inf")}]) and bad(items=[{"vendor_code": "200513", "qty": 1, "cost": 0}]) and bad(items=[{"vendor_code": "200513", "qty": 1, "cost": -5}]) and bad(items=[{"vendor_code": "200513", "qty": 1}]) and bad(items=[{"vendor_code": "200513", "qty": 1, "cost": 10}, {"vendor_code": "200513", "qty": 2, "cost": 10}]))
    chk("allow-list POST: лише спостережені шляхи, прив'язані до початку; /onestep/update, /onestep/payment, /x/cart/add, фінал /onestep — НЕ пропускаються", all(rc._ONESTEP_OK_RE.search(x) for x in ("/ua/cart/add", "/ua/cart/delete", "/ua/cart/save", "/ua/onestep/shipping", "/ua/data/shipping/15/warehouse", "/ua/orderaddon", "/cart/add")) and not any(rc._ONESTEP_OK_RE.search(x) for x in ("/ua/onestep/update", "/ua/onestep/payment", "/ua/onestep", "/x/ua/cart/add", "/ua/onestep/total", "/ua/cart/clear")))
chk("_money/_balance: «155 грн.», «1 234,50 грн», «Доступно 0.00» (з переносом рядка), «Доступно 1 250.5»", rc._money("Всього" + chr(10) + "155 грн.") == 155.0 and rc._money("1 234,50 грн") == 1234.5 and rc._balance("Доступно" + chr(10) + "0.00") == 0.0 and rc._balance("Доступно 1 250.5") == 1250.5 and rc._balance("нема") is None)
_os.environ.pop("RT_ORDER_SUBMIT", None)
try:
    rc.order_flow(base, submit=True)
    ok_sub = False
except rc.OrderError:
    ok_sub = True
chk("--submit-balance без RT_ORDER_SUBMIT=1 → відмова ДО будь-якої мережі", ok_sub)
print("\nFAIL:" if F else "\nOK: усі перевірки пройдені", *F, sep="\n")
sys.exit(1 if F else 0)

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
LiqPay Checkout (API v3) — формування даних+підпису для оплати карткою на власному
сайті plutustoys.com.ua та перевірка серверного колбека.

Алгоритм LiqPay (офіційна документація, стабільний):
  data      = base64( json({version:3, public_key, action:'pay', amount, currency,
                            description, order_id, [result_url, server_url, sandbox]}) )
  signature = base64( sha1( private_key + data + private_key ) )
Форма checkout POST-иться на https://www.liqpay.ua/api/3/checkout (поля data+signature).
Колбек: LiqPay POST-ить data+signature на server_url; підтверджуємо, перерахувавши підпис.

ЗАГЛУШКА / SANDBOX: поки власник не завів компанію та бойові ключі LiqPay, працюємо в
пісочниці (`sandbox:1` — LiqPay не списує реальні гроші) або взагалі без ключів
(`is_configured()==False`) — тоді checkout не будується, а API-шар віддає «оплата ще не
підключена». Ключі — з .env (LIQPAY_PUBLIC_KEY / LIQPAY_PRIVATE_KEY), як інші клієнти.
"""
import os
import json
import base64
import hashlib

from dotenv import load_dotenv

load_dotenv()

LIQPAY_PUBLIC_KEY = os.environ.get("LIQPAY_PUBLIC_KEY", "")
LIQPAY_PRIVATE_KEY = os.environ.get("LIQPAY_PRIVATE_KEY", "")
# За замовчуванням — пісочниця (безпечно). Реальні списання лише коли явно LIQPAY_SANDBOX=0
# І задані бойові ключі (їх дають у кабінеті LiqPay після реєстрації компанії).
LIQPAY_SANDBOX = os.environ.get("LIQPAY_SANDBOX", "1").strip() != "0"

CHECKOUT_URL = "https://www.liqpay.ua/api/3/checkout"
API_VERSION = 3
CURRENCY = "UAH"
# Статуси LiqPay, що означають отриману/зарезервовану оплату (підтверджуємо замовлення).
# 'sandbox' — успішний тестовий платіж у пісочниці; 'wait_accept' — гроші списано, чекають підтвердження продавцем.
PAID_STATUSES = {"success", "sandbox", "wait_accept", "subscribed"}


class LiqPayNotConfigured(Exception):
    """Немає ключів LiqPay — оплату карткою ще не підключено (треба зареєструвати
    компанію в LiqPay і додати LIQPAY_PUBLIC_KEY/LIQPAY_PRIVATE_KEY у .env)."""


def is_configured() -> bool:
    return bool(LIQPAY_PUBLIC_KEY and LIQPAY_PRIVATE_KEY)


def _b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


def _encode_data(params: dict) -> str:
    return _b64(json.dumps(params, ensure_ascii=False, sort_keys=True).encode("utf-8"))


def _signature(data_b64: str) -> str:
    if not LIQPAY_PRIVATE_KEY:
        raise LiqPayNotConfigured("не заданий LIQPAY_PRIVATE_KEY")
    raw = (LIQPAY_PRIVATE_KEY + data_b64 + LIQPAY_PRIVATE_KEY).encode("utf-8")
    return _b64(hashlib.sha1(raw).digest())


def build_checkout(order_id: str, amount, description: str,
                   result_url: str = "", server_url: str = "") -> dict:
    """Готує поля для форми оплати LiqPay. Кидає LiqPayNotConfigured, якщо ключів нема."""
    if not is_configured():
        raise LiqPayNotConfigured(
            "LiqPay ключі не задані — оплату карткою ще не підключено. Зареєструйте компанію "
            "в LiqPay та додайте LIQPAY_PUBLIC_KEY/LIQPAY_PRIVATE_KEY у .env"
        )
    try:
        amt = f"{float(amount):.2f}"
    except (TypeError, ValueError):
        raise ValueError(f"невалідна сума оплати: {amount!r}")
    if float(amt) <= 0:
        raise ValueError(f"сума оплати має бути додатна: {amount!r}")

    params = {
        "version": API_VERSION,
        "public_key": LIQPAY_PUBLIC_KEY,
        "action": "pay",
        "amount": amt,
        "currency": CURRENCY,
        "description": str(description)[:250],
        "order_id": str(order_id),
    }
    if result_url:
        params["result_url"] = result_url
    if server_url:
        params["server_url"] = server_url
    if LIQPAY_SANDBOX:
        params["sandbox"] = "1"

    data = _encode_data(params)
    return {
        "action_url": CHECKOUT_URL,
        "data": data,
        "signature": _signature(data),
        "sandbox": LIQPAY_SANDBOX,
    }


def verify_callback(data_b64: str, signature: str) -> dict:
    """Перевіряє серверний колбек LiqPay. Повертає dict із valid/paid/order_id/status/amount.
    `valid` — підпис зійшовся (запит справді від LiqPay); `paid` — і підпис валідний, і статус оплачений."""
    valid = False
    try:
        valid = bool(signature) and _signature(data_b64) == signature
    except LiqPayNotConfigured:
        valid = False

    payload = {}
    try:
        payload = json.loads(base64.b64decode(data_b64).decode("utf-8"))
        if not isinstance(payload, dict):
            payload = {}
    except Exception:
        payload = {}

    status = payload.get("status", "")
    return {
        "valid": valid,
        "paid": bool(valid and status in PAID_STATUSES),
        "order_id": payload.get("order_id"),
        "status": status,
        "amount": payload.get("amount"),
        "data": payload,
    }


if __name__ == "__main__":
    # Самотест алгоритму (з тестовими ключами; НЕ бойові). Перевіряє механіку sha1/base64,
    # детермінізм підпису, round-trip підпис↔перевірка, відсів підробленого підпису.
    import sys
    sys.stdout.reconfigure(encoding="utf-8")

    LIQPAY_PUBLIC_KEY = "sandbox_i00000000000"
    LIQPAY_PRIVATE_KEY = "sandbox_test_private_key_ZZZ"
    LIQPAY_SANDBOX = True

    ok = True

    # 1) підпис детермінований і за формулою base64(sha1(priv+data+priv))
    ck = build_checkout("PT-TEST-1", 199.0, "Тестове замовлення 🦊",
                        result_url="https://plutustoys.com.ua/thanks",
                        server_url="https://plutustoys.com.ua/api/liqpay/callback")
    manual = _b64(hashlib.sha1((LIQPAY_PRIVATE_KEY + ck["data"] + LIQPAY_PRIVATE_KEY).encode("utf-8")).digest())
    print("data     :", ck["data"][:48], "...")
    print("signature:", ck["signature"])
    assert ck["signature"] == manual, "підпис не збігається з ручним обчисленням"
    assert ck["sandbox"] is True

    # 2) вміст data декодується назад коректно (сума, order_id, sandbox)
    decoded = json.loads(base64.b64decode(ck["data"]).decode("utf-8"))
    assert decoded["amount"] == "199.00" and decoded["order_id"] == "PT-TEST-1"
    assert decoded["sandbox"] == "1" and decoded["currency"] == "UAH"
    print("decoded  :", {k: decoded[k] for k in ("amount", "order_id", "currency", "sandbox")})

    # 3) колбек: валідний підпис + статус 'sandbox' → paid; підроблений підпис → not valid
    cb_data = _b64(json.dumps({"status": "sandbox", "order_id": "PT-TEST-1", "amount": 199.0}).encode("utf-8"))
    cb_sig = _signature(cb_data)
    good = verify_callback(cb_data, cb_sig)
    bad = verify_callback(cb_data, "ZZZfake")
    print("callback good:", {k: good[k] for k in ("valid", "paid", "status", "order_id")})
    print("callback bad :", {k: bad[k] for k in ("valid", "paid")})
    assert good["valid"] and good["paid"] and good["order_id"] == "PT-TEST-1"
    assert (not bad["valid"]) and (not bad["paid"])

    # 4) статус 'failure' з валідним підписом → valid, але НЕ paid
    f_data = _b64(json.dumps({"status": "failure", "order_id": "PT-TEST-1"}).encode("utf-8"))
    f = verify_callback(f_data, _signature(f_data))
    assert f["valid"] and not f["paid"]

    print("САМОТЕСТ OK — алгоритм LiqPay коректний (кінцеву звірку з реальним sandbox-ключем зробимо після реєстрації компанії).")

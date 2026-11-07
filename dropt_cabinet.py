# -*- coding: utf-8 -*-
"""dropt_cabinet.py — кабінет Dropt (dropt.in.ua/ua/cabinet) для dropt_order_runner.py.

СТАН (07.11.2026): ЗАГЛУШКА-КОНТРАКТ. Інтерфейс, який викликає runner, зафіксований і протестований; сама робота в браузері (вхід через Google/Telegram → файл сесії Playwright, модалка checkout/modal,
«Зовнішня ТТН» + PDF ≤2 МБ, оплата з балансу) додається окремим PR після входу власника в кабінет. Доки final_implemented() = False, runner НЕ може працювати в реальному режимі
(apply_enabled() повертає «не підключено») — лише dry-run.

Контракт (його не міняти без правки runner і тестів):
  OrderError                        — відмова ДО кліку «Оформити» (валідація/ворота/сесія).
  final_implemented() -> bool       — True лише коли order_flow справді оформлює замовлення.
  ensure_session()                  — сесія кабінету жива (інакше OrderError з причиною; пароль не зберігаємо, автовходу нема).
  preflight(order) -> dict          — {"ready": bool, "problems": [..], "low_balance": bool, "balance": float|None, "total": float|None}: сесія, товари знайдені за артикулом, баланс ≥ сума закупівлі. Нічого не створює.
  order_flow(order, submit) -> dict — {"submitted": bool, "dropt_order_ref": str|None, "not_submitted_certain": bool, "low_balance": bool, "total_on_checkout": float|None, "balance_after": float|None, "problems": [..]}.
  order = {"items": [{"vendor_code", "qty", "cost", "sale_price"}], "ttn_number": "<наша ТТН НП>", "marking_pdf": "<шлях PDF ≤2 МБ>"}.
"""


class OrderError(Exception):
    pass


def final_implemented() -> bool:
    return False


def ensure_session() -> None:
    raise OrderError("вхід у кабінет Dropt не налаштовано (dropt_cabinet — заглушка до наступного PR)")


def preflight(order: dict) -> dict:
    raise OrderError("кабінет Dropt не підключено (dropt_cabinet — заглушка до наступного PR)")


def order_flow(order: dict, submit: bool = False) -> dict:
    raise OrderError("кабінет Dropt не підключено (dropt_cabinet — заглушка до наступного PR)")

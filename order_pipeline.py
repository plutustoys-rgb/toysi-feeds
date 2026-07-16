"""
order_pipeline.py — об'єднаний послідовний цикл: fetch -> save ->
confirm payment -> forward в межах ОДНОГО запуску, одного процесу,
без розриву між кроками.

ВИПРАВЛЕНО (2026-07-16, третій випадок недоходження замовлення вчасно
поспіль — 415858222/вузький фільтр status=pending, 100445626/норма
Toysi, 416114712/гонка таймерів, pt8): orders_watcher.py і
order_router.py досі виконувались як ДВА окремі systemd-сервіси на
ДВОХ окремих таймерах. Навіть після зсуву розкладу на 2 хв (pt8,
попередній фікс) лишався структурний ризик — два незалежні процеси,
запущені окремо, завжди можуть розійтися в часі знову з будь-якої
іншої причини (рестарт, різна тривалість виконання, майбутня зміна
розкладу). Об'єднання прибирає розрив як клас — "forward" тепер
виконується в ТОМУ САМОМУ процесі, одразу після "save", без жодного
вікна, в якому інший процес міг би щось прочитати між ними.

РОЗШИРЕНО (2026-07-16, за прямим проханням власниці — перевірити, чи
bank-check.timer має ту саму вразливість): так, той самий клас гонки
— bank_check.check_pending_prepayments() пише payment_confirmed
(mark_payment_confirmed()), а order_router.get_orders_ready_to_forward()
читає САМЕ це поле, щоб вирішити, чи передоплачене замовлення вже
готове до пересилки. Два незалежні таймери (bank-check кожні 15 хв,
order-router кожні 15 хв) могли розійтись так само, як
orders_watcher/order_router — щойно підтверджена оплата не встигла б
закомітитись до того, як order_router прочитав список готових до
пересилки, і чекала б аж до наступного циклу (до появи safety-net у
service_watchdog.py — тепер обмежено ~25-35 хв, а не невизначено
довго). Додано ТРЕТІМ кроком у ту саму послідовність — той самий
принцип, що й для orders_watcher/order_router: коли order_router
читає стан, bank_check уже гарантовано закомітив свій запис у ТОМУ Ж
процесі, без вікна для стороннього читання.

orders_watcher.poll_once(), bank_check.check_pending_prepayments() і
order_router.route_pending_orders() лишаються окремими, повторно
використовуваними функціями (код не дублюється) — цей файл лише
викликає їх послідовно, у правильному порядку.

Запуск:
    python order_pipeline.py
"""
import sys

import bank_check
import order_router
import orders_watcher

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def run_once() -> None:
    orders_watcher.poll_once()
    bank_check.check_pending_prepayments()
    order_router.route_pending_orders()


if __name__ == "__main__":
    run_once()

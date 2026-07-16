"""
order_pipeline.py — об'єднаний послідовний цикл: fetch -> save -> forward
в межах ОДНОГО запуску, одного процесу, без розриву між кроками.

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

orders_watcher.poll_once() і order_router.route_pending_orders()
лишаються окремими, повторно використовуваними функціями (код не
дублюється) — цей файл лише викликає їх послідовно.

Запуск:
    python order_pipeline.py
"""
import sys

import order_router
import orders_watcher

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def run_once() -> None:
    orders_watcher.poll_once()
    order_router.route_pending_orders()


if __name__ == "__main__":
    run_once()

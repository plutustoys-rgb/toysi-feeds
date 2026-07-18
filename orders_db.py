import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from typing import Optional

DB_PATH = "orders.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS orders (
    internal_order_id     TEXT PRIMARY KEY,   -- "{platform}_{order_id}", захист від дублів між Rozetka/Prom
    order_id              TEXT NOT NULL,
    platform              TEXT NOT NULL CHECK (platform IN ('rozetka', 'prom')),
    status                TEXT NOT NULL DEFAULT 'new',
    payment_method        TEXT NOT NULL CHECK (payment_method IN ('cod', 'prepaid')),
    payment_confirmed     INTEGER NOT NULL DEFAULT 0,
    customer_name         TEXT,
    phone                 TEXT,
    np_branch             TEXT,
    items                 TEXT NOT NULL,      -- JSON: [{"toysi_code":.., "name":.., "qty":.., "price":..}, ...]
    created_at            TEXT NOT NULL,
    forwarded_to_toysi_at TEXT,
    toysi_order_id        TEXT,               -- номер замовлення, який повернув Toysi order_create
    toysi_ttn             TEXT,               -- ТТН зі СТОРОНИ Toysi (order_status) — для НП заповнюється
                                               -- автоматично; для Укрпошти лише ПІСЛЯ ручного внесення в lk
    delivery_status       TEXT,
    carrier               TEXT NOT NULL DEFAULT 'nova_poshta' CHECK (carrier IN ('nova_poshta', 'ukrposhta')),
    ukrposhta_ttn         TEXT,               -- ТТН, яку МИ створили через ukrposhta_client.py (до внесення в Toysi)
    ukrposhta_sticker_path TEXT,              -- локальний шлях до PDF-етикетки Укрпошти
    checkbox_ettn_registered_at TEXT,         -- коли зареєстровано ЕТТН у Checkbox (order_status_tracker.py) —
                                               -- захист від повторної реєстрації (= дубль РЕАЛЬНОГО фіскального чека)
    checkbox_receipt_id   TEXT,               -- id чека з відповіді Checkbox — для звірки/ручного пошуку
    rozetka_ttn_pushed_at TEXT,                -- коли ТТН передано НАЗАД у Rozetka через OrderUpdateStatus
                                               -- (order_status_tracker.py) — захист від повторного PUT /orders/{id}
                                               -- на кожному циклі опитування (Rozetka сама починає трекінг після
                                               -- першого прикріплення ТТН, повторний виклик зайвий, не шкідливий,
                                               -- але марний мережевий запит щоразу)
    prom_delivered_pushed_at TEXT,             -- коли статус "delivered" передано в Prom (Auto-3, 2026-07-17,
                                               -- order_status_tracker.py, за живим трекінгом Нової Пошти,
                                               -- TrackingDocument.getStatusDocuments) — захист від повторного
                                               -- POST /orders/set_status на кожному циклі опитування
    prom_ttn_pushed_at    TEXT,               -- коли ЕН передано в Prom через POST /delivery/save_declaration_id
                                               -- (order_status_tracker.py, 2026-07-17) — до цього фіксу ЕН у Prom
                                               -- не реєструвався НІКОЛИ (declaration_number завжди лишався null),
                                               -- що унеможливлювало "Дешеву доставку" Новою Поштою для КОЖНОГО
                                               -- замовлення; захист від повторного POST щоцикл опитування
    UNIQUE (order_id, platform)
);

CREATE INDEX IF NOT EXISTS idx_orders_status ON orders (status);
CREATE INDEX IF NOT EXISTS idx_orders_payment_confirmed ON orders (payment_confirmed);
"""


@contextmanager
def get_connection(db_path: str = DB_PATH):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def _ensure_column(conn: sqlite3.Connection, table: str, column: str, ddl: str) -> None:
    """ALTER TABLE ADD COLUMN, ідемпотентно. CREATE TABLE IF NOT EXISTS у SCHEMA
    НЕ чіпає таблицю, яка вже існує (на VPS orders.db вже містить реальні
    замовлення) — тож нові колонки, додані до SCHEMA заднім числом, самі
    собою на існуючій БД не з'являться. Виклик нижче — щоб додавання
    carrier/ukrposhta_* (2026-07-10) не впало з "no such column" на вже
    розгорнутій БД."""
    existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
    if column not in existing:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {ddl}")


def init_db(db_path: str = DB_PATH) -> None:
    with get_connection(db_path) as conn:
        conn.executescript(SCHEMA)
        _ensure_column(
            conn, "orders", "carrier",
            "carrier TEXT NOT NULL DEFAULT 'nova_poshta' CHECK (carrier IN ('nova_poshta', 'ukrposhta'))",
        )
        _ensure_column(conn, "orders", "ukrposhta_ttn", "ukrposhta_ttn TEXT")
        _ensure_column(conn, "orders", "ukrposhta_sticker_path", "ukrposhta_sticker_path TEXT")
        _ensure_column(conn, "orders", "checkbox_ettn_registered_at", "checkbox_ettn_registered_at TEXT")
        _ensure_column(conn, "orders", "checkbox_receipt_id", "checkbox_receipt_id TEXT")
        _ensure_column(conn, "orders", "rozetka_ttn_pushed_at", "rozetka_ttn_pushed_at TEXT")
        # P0-6 (2026-07-17): коли востаннє надіслано алерт "Toysi зараз без
        # залишку" для цього замовлення — щоб order_router.py не спамив той
        # самий алерт щоцикл (кожні 15 хв), доки товар не з'явиться знову
        # чи замовлення не оброблять вручну. НЕ блокує повторні спроби
        # передачі (та сама ідемпотентність, що й checkbox_ettn_registered_at/
        # rozetka_ttn_pushed_at — перевіряємо прапорець, не пропускаємо
        # замовлення назавжди).
        _ensure_column(conn, "orders", "stock_alert_sent_at", "stock_alert_sent_at TEXT")
        _ensure_column(conn, "orders", "prom_delivered_pushed_at", "prom_delivered_pushed_at TEXT")
        _ensure_column(conn, "orders", "prom_ttn_pushed_at", "prom_ttn_pushed_at TEXT")


def order_exists(conn: sqlite3.Connection, order_id: str, platform: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM orders WHERE order_id = ? AND platform = ?", (order_id, platform)
    ).fetchone()
    return row is not None


def insert_order(conn: sqlite3.Connection, order: dict) -> bool:
    """
    Вставляє нове замовлення. Повертає False, якщо (order_id, platform) вже є в БД —
    виклик має бути ідемпотентним при повторному опитуванні (Крок 3 плану).
    """
    if order_exists(conn, order["order_id"], order["platform"]):
        return False

    internal_order_id = f"{order['platform']}_{order['order_id']}"

    conn.execute(
        """
        INSERT INTO orders (
            internal_order_id, order_id, platform, status, payment_method,
            payment_confirmed, customer_name, phone, np_branch, items,
            created_at, forwarded_to_toysi_at, toysi_order_id, toysi_ttn, delivery_status,
            carrier
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            internal_order_id,
            order["order_id"],
            order["platform"],
            order.get("status", "new"),
            order["payment_method"],
            int(order.get("payment_confirmed", False)),
            order.get("customer_name"),
            order.get("phone"),
            order.get("np_branch"),
            json.dumps(order["items"], ensure_ascii=False),
            order.get("created_at") or datetime.now().isoformat(timespec="seconds"),
            order.get("forwarded_to_toysi_at"),
            order.get("toysi_order_id"),
            order.get("toysi_ttn"),
            order.get("delivery_status"),
            order.get("carrier", "nova_poshta"),
        ),
    )
    return True


def _row_to_dict(row: sqlite3.Row) -> dict:
    result = dict(row)
    result["items"] = json.loads(result["items"])
    return result


def get_order(conn: sqlite3.Connection, internal_order_id: str) -> Optional[dict]:
    row = conn.execute(
        "SELECT * FROM orders WHERE internal_order_id = ?", (internal_order_id,)
    ).fetchone()
    return _row_to_dict(row) if row else None


def get_orders_by_status(conn: sqlite3.Connection, status: str) -> list:
    rows = conn.execute("SELECT * FROM orders WHERE status = ?", (status,)).fetchall()
    return [_row_to_dict(row) for row in rows]


def get_orders_awaiting_payment(conn: sqlite3.Connection) -> list:
    """Передоплачені замовлення, які ще чекають підтвердження bank_check.py (Крок 4)."""
    rows = conn.execute(
        "SELECT * FROM orders WHERE payment_method = 'prepaid' AND payment_confirmed = 0"
    ).fetchall()
    return [_row_to_dict(row) for row in rows]


def get_orders_ready_to_forward(conn: sqlite3.Connection) -> list:
    """
    Замовлення, готові до передачі Toysi (Крок 5): накладені — одразу;
    передоплачені — лише після підтвердження оплати. Свідомо НЕ фільтрує
    за полем `status` — воно містить вокабуляр платформи (напр. Prom
    повертає "pending", мок-дані Rozetka — "new"), тож `forwarded_to_toysi_at
    IS NULL` є єдиним надійним сигналом "ще не передано", незалежно від
    того, яке значення `status` виставила конкретна платформа.
    `toysi_error` виключено окремо, щоб не намагатись нескінченно
    передати замовлення з даними, які Toysi вже відхилив (Крок 5, п.4).

    `prom_cancelled_before_forward` (2026-07-17, реальний інцидент
    №415858222) — той самий принцип: order_router.py._check_prom_not_
    cancelled() виставляє цей статус, коли живий запит до Prom Orders
    API підтверджує скасування ПЕРЕД форвардом — без цього виключення
    те саме замовлення потрапляло б у кандидати знову на кожному циклі,
    попри вже надіслану ескалацію власнику.
    """
    rows = conn.execute(
        """
        SELECT * FROM orders
        WHERE forwarded_to_toysi_at IS NULL
          AND (status IS NULL OR status NOT IN ('toysi_error', 'prom_cancelled_before_forward'))
          AND (
              payment_method = 'cod'
              OR (payment_method = 'prepaid' AND payment_confirmed = 1)
          )
        """
    ).fetchall()
    return [_row_to_dict(row) for row in rows]


def get_active_toysi_orders(conn: sqlite3.Connection) -> list:
    """Замовлення, вже передані Toysi, чий статус доставки ще не термінальний
    (Крок 6) — саме ці order_status_tracker.py опитує далі."""
    rows = conn.execute(
        """
        SELECT * FROM orders
        WHERE forwarded_to_toysi_at IS NOT NULL
          AND toysi_order_id IS NOT NULL
          AND (delivery_status IS NULL OR delivery_status NOT IN ('cancelled', 'returned', 'expired'))
        """
    ).fetchall()
    return [_row_to_dict(row) for row in rows]


def mark_ukrposhta_shipment(conn: sqlite3.Connection, internal_order_id: str, ttn: str, sticker_path: str) -> None:
    """Записує ТТН і шлях до PDF-етикетки, отримані від ukrposhta_client.py.
    Toysi НЕ приймає ТТН через order_create (підтверджено емпірично
    2026-07-10: поле "ttn" у запиті ігнорується, order_status після цього
    повертає порожній TTN) — тому номер ТТН і PDF-етикетку далі треба
    внести в кабінет toysi.ua/lk ВРУЧНУ (дивись
    get_orders_awaiting_manual_ttn_entry()).

    Навмисно НЕ чіпає `status` — mark_forwarded_to_toysi() вже виставив
    status='forwarded_to_supplier' одразу перед цим викликом, і це лишається
    правдою (замовлення дійсно передане Toysi). "Очікує ручного внесення
    ТТН" — похідний стан (ukrposhta_ttn задано, toysi_ttn ще ні), а не
    окремий status, який довелось би вручну скидати після завершення
    ручної дії."""
    conn.execute(
        "UPDATE orders SET ukrposhta_ttn = ?, ukrposhta_sticker_path = ? WHERE internal_order_id = ?",
        (ttn, sticker_path, internal_order_id),
    )


def get_orders_awaiting_manual_ttn_entry(conn: sqlite3.Connection) -> list:
    """Замовлення Укрпошта, для яких ТТН/етикетка вже створені нашим
    ukrposhta_client.py (ukrposhta_ttn задано), але людина (чи Фаза 2 —
    браузерна автоматизація через claude-in-chrome) ще не внесла ТТН у
    кабінет toysi.ua/lk — тобто order_status_tracker.py ще не підтягнув
    його назад як toysi_ttn. Список сам собою "звужується" щойно ручна
    дія виконана — окремого статусу для цього не потрібно."""
    rows = conn.execute(
        """
        SELECT * FROM orders
        WHERE carrier = 'ukrposhta'
          AND ukrposhta_ttn IS NOT NULL
          AND (toysi_ttn IS NULL OR toysi_ttn = '')
        """
    ).fetchall()
    return [_row_to_dict(row) for row in rows]


def mark_stock_alert_sent(conn: sqlite3.Connection, internal_order_id: str) -> None:
    """Позначає, що алерт "Toysi зараз без залишку" для цього замовлення вже
    надіслано (P0-6, order_router.py) — не блокує повторні спроби передачі
    наступного циклу, лише запобігає повторному Telegram-алерту щоцикл."""
    conn.execute(
        "UPDATE orders SET stock_alert_sent_at = ? WHERE internal_order_id = ?",
        (datetime.now().isoformat(timespec="seconds"), internal_order_id),
    )


def clear_stock_alert(conn: sqlite3.Connection, internal_order_id: str) -> None:
    """Скидає прапорець алерту, коли залишок Toysi знову достатній — щоб
    ПОВТОРНИЙ дефіцит того самого замовлення (малоймовірно, але можливо:
    з'явився -> знову зник) знову подав сигнал, а не мовчав назавжди після
    першого разу."""
    conn.execute(
        "UPDATE orders SET stock_alert_sent_at = NULL WHERE internal_order_id = ?",
        (internal_order_id,),
    )


def mark_checkbox_ettn_registered(conn: sqlite3.Connection, internal_order_id: str, receipt_id: str = None) -> None:
    """Позначає, що ЕТТН для цього замовлення вже зареєстровано в Checkbox
    (order_status_tracker.py, checkbox_client.register_ettn()). КРИТИЧНО
    для ідемпотентності: order_status_tracker.py опитує замовлення
    ПЕРІОДИЧНО, поки їхній статус не термінальний — без цієї позначки
    кожен наступний цикл опитування створював би ДУБЛІКАТ РЕАЛЬНОГО
    фіскального чека на той самий ТТН.

    receipt_id — ідентифікатор чека з відповіді Checkbox (для звірки/ручного
    пошуку конкретного чека пізніше — timestamp сам по собі цього не дає)."""
    conn.execute(
        "UPDATE orders SET checkbox_ettn_registered_at = ?, checkbox_receipt_id = ? WHERE internal_order_id = ?",
        (datetime.now().isoformat(timespec="seconds"), receipt_id, internal_order_id),
    )


def mark_rozetka_ttn_pushed(conn: sqlite3.Connection, internal_order_id: str) -> None:
    """Позначає, що ТТН вже передано в Rozetka через OrderUpdateStatus
    (order_status_tracker.py, rozetka_client.update_order_status()) —
    захист від повторного PUT /orders/{id} на кожному циклі опитування,
    той самий підхід, що й mark_checkbox_ettn_registered()."""
    conn.execute(
        "UPDATE orders SET rozetka_ttn_pushed_at = ? WHERE internal_order_id = ?",
        (datetime.now().isoformat(timespec="seconds"), internal_order_id),
    )


def mark_prom_delivered_pushed(conn: sqlite3.Connection, internal_order_id: str) -> None:
    """Позначає, що статус "delivered" вже передано в Prom (Auto-3,
    order_status_tracker.py, orders_watcher.update_prom_order_status()) —
    захист від повторного POST /orders/set_status на кожному циклі
    опитування, той самий підхід, що й mark_rozetka_ttn_pushed()."""
    conn.execute(
        "UPDATE orders SET prom_delivered_pushed_at = ? WHERE internal_order_id = ?",
        (datetime.now().isoformat(timespec="seconds"), internal_order_id),
    )


def mark_prom_ttn_pushed(conn: sqlite3.Connection, internal_order_id: str) -> None:
    """Позначає, що ЕН вже передано в Prom через POST /delivery/save_declaration_id
    (order_status_tracker.py, orders_watcher.attach_prom_declaration_id()) —
    захист від повторного POST на кожному циклі опитування, той самий
    підхід, що й mark_rozetka_ttn_pushed()/mark_prom_delivered_pushed()."""
    conn.execute(
        "UPDATE orders SET prom_ttn_pushed_at = ? WHERE internal_order_id = ?",
        (datetime.now().isoformat(timespec="seconds"), internal_order_id),
    )


def mark_payment_confirmed(conn: sqlite3.Connection, internal_order_id: str) -> None:
    conn.execute(
        "UPDATE orders SET payment_confirmed = 1 WHERE internal_order_id = ?",
        (internal_order_id,),
    )


def mark_forwarded_to_toysi(conn: sqlite3.Connection, internal_order_id: str, toysi_order_id: str) -> None:
    conn.execute(
        """
        UPDATE orders
        SET forwarded_to_toysi_at = ?, toysi_order_id = ?, status = 'forwarded_to_supplier'
        WHERE internal_order_id = ?
        """,
        (datetime.now().isoformat(timespec="seconds"), toysi_order_id, internal_order_id),
    )


def update_delivery_status(
    conn: sqlite3.Connection,
    internal_order_id: str,
    toysi_ttn: str = None,
    delivery_status: str = None,
    status: str = None,
) -> None:
    fields, params = [], []
    if toysi_ttn is not None:
        fields.append("toysi_ttn = ?")
        params.append(toysi_ttn)
    if delivery_status is not None:
        fields.append("delivery_status = ?")
        params.append(delivery_status)
    if status is not None:
        fields.append("status = ?")
        params.append(status)
    if not fields:
        return
    params.append(internal_order_id)
    conn.execute(f"UPDATE orders SET {', '.join(fields)} WHERE internal_order_id = ?", params)


if __name__ == "__main__":
    init_db()
    print(f"[orders_db] Схему ініціалізовано: {DB_PATH}")

    with get_connection() as conn:
        demo = {
            "order_id": "DEMO-0001",
            "platform": "prom",
            "payment_method": "cod",
            "customer_name": "Демо Клієнт",
            "phone": "380501234567",
            "np_branch": "Київ, відділення №15",
            "items": [{"toysi_code": "11623", "name": "Демо товар", "qty": 1, "price": 450.0}],
        }
        if insert_order(conn, demo):
            print("[orders_db] Демо-запис додано")
        else:
            print("[orders_db] Демо-запис вже існує (повторний запуск)")

        print(get_order(conn, "prom_DEMO-0001"))

"""
prom_chat_db.py — сховище діалогів prom_chat_bot.py. Той самий підхід, що
й orders_db.py (SQLite, ідемпотентні ALTER TABLE, contextmanager).
"""

import sqlite3
from contextlib import contextmanager

DB_PATH = "prom_chat.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS chat_messages (
    id                      INTEGER PRIMARY KEY,  -- Prom message id (природний ключ — захист від повторної обробки)
    room_id                 TEXT NOT NULL,         -- UUID кімнати (для mark_message_read)
    room_ident              TEXT NOT NULL,         -- {user_id}_{company_id}_buyer (для send_message)
    user_name               TEXT,
    user_ident              TEXT,
    is_sender               INTEGER NOT NULL,      -- 1 = від нас, 0 = від покупця
    body                    TEXT,
    context_item_id         TEXT,                  -- id товару, якщо повідомлення прив'язане до картки
    date_sent                TEXT NOT NULL,
    fetched_at               TEXT NOT NULL,
    classification           TEXT,                  -- 'normal' | 'escalate' | NULL (наші власні повідомлення не класифікуються)
    classification_reasoning TEXT,
    response_status          TEXT,                  -- 'auto_replied' | 'escalated' | 'error' | NULL
    response_body            TEXT,                  -- що згенеровано/відправлено (якщо auto_replied)
    escalation_notified_at   TEXT
);

CREATE INDEX IF NOT EXISTS idx_chat_messages_room ON chat_messages (room_ident);
CREATE INDEX IF NOT EXISTS idx_chat_messages_response_status ON chat_messages (response_status);
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


def init_db(db_path: str = DB_PATH) -> None:
    with get_connection(db_path) as conn:
        conn.executescript(SCHEMA)


def message_already_seen(conn: sqlite3.Connection, message_id: int) -> bool:
    row = conn.execute("SELECT 1 FROM chat_messages WHERE id = ?", (message_id,)).fetchone()
    return row is not None


def insert_message(conn: sqlite3.Connection, row: dict) -> None:
    """INSERT OR IGNORE за природним ключем id — повторний polling того самого
    повідомлення (наприклад, якщо process впав між fetch і mark_read) не
    створює дубль і не переобробляє вже класифіковане повідомлення."""
    conn.execute(
        """
        INSERT OR IGNORE INTO chat_messages
            (id, room_id, room_ident, user_name, user_ident, is_sender, body,
             context_item_id, date_sent, fetched_at)
        VALUES (:id, :room_id, :room_ident, :user_name, :user_ident, :is_sender, :body,
                :context_item_id, :date_sent, :fetched_at)
        """,
        row,
    )


def update_response(conn: sqlite3.Connection, message_id: int, **fields) -> None:
    """Часткове оновлення (classification/response_status/response_body/...)
    після обробки повідомлення — лише передані поля."""
    if not fields:
        return
    set_clause = ", ".join(f"{k} = :{k}" for k in fields)
    fields["id"] = message_id
    conn.execute(f"UPDATE chat_messages SET {set_clause} WHERE id = :id", fields)


def get_response_status(conn: sqlite3.Connection, message_id: int) -> str | None:
    """response_status уже обробленого повідомлення, або None, якщо ще не
    оброблялось. Використовується, щоб не переобробляти (і не спамити
    Telegram повторно) escalate-повідомлення, які Prom і далі повертає як
    status=new, бо бот свідомо не викликає mark_message_read для них."""
    row = conn.execute(
        "SELECT response_status FROM chat_messages WHERE id = ?", (message_id,)
    ).fetchone()
    return row["response_status"] if row else None


def get_recent_room_history(conn: sqlite3.Connection, room_ident: str, limit: int = 10) -> list:
    """Останні `limit` повідомлень цієї кімнати з нашої БД (найновіше — останнім),
    для контексту діалогу при генерації відповіді."""
    rows = conn.execute(
        """
        SELECT is_sender, body, date_sent FROM chat_messages
        WHERE room_ident = ? AND body IS NOT NULL
        ORDER BY date_sent DESC LIMIT ?
        """,
        (room_ident, limit),
    ).fetchall()
    return list(reversed([dict(r) for r in rows]))

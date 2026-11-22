# -*- coding: utf-8 -*-
"""support_relay_watch.py — «пейджер» чату підтримки для того, хто веде чат (Shopify-code). Рішення власника 22.11.2026: КОЖНЕ нове повідомлення клієнта має будити того, хто веде чат (а не бути алертом власнику).

Як користуватись: запустити ОДИН РАЗ у Monitor із persistent=true:
    python C:/Users/smach/rozetka_agent/support_relay_watch.py
Скрипт раз на INTERVAL секунд викликає `support_relay.py list` (вузький SSH-канал) і друкує у stdout КОЖНЕ нове вхідне повідомлення окремим рядком вигляду
    НОВЕ ПОВІДОМЛЕННЯ КЛІЄНТА #id час чат N: текст…
Кожен надрукований рядок = подія Monitor = сесія прокидається. Уже бачені id не повторюються (стан у пам'яті процесу; після перезапуску один раз виводиться поточна черга).
Збій каналу (нема ключа, ssh недоступний) друкується ОДИН раз на серію збоїв: «КАНАЛ ЧАТУ НЕДОСТУПНИЙ …» (і ще раз, коли відновився), щоб Monitor не спамив.
Відповідати — `python support_relay.py reply <chat_id> "<текст>"`; кожна відповідь автоматично дублюється власнику в бота. Нічого, крім читання черги, цей скрипт не робить.
`--once` — один прохід і вихід (для тесту)."""
import re
import sys
import time

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import support_relay as sr  # noqa: E402

INTERVAL = 45
_LINE_RE = re.compile(r"^#(\d+)\s+(\S+)\s+чат\s+(\d+):\s*(.*)$")


def parse_list(out: str) -> list:
    """Вивід `list` → [(msg_id:int, ts, chat_id:int, text)]. Рядки не за форматом (напр. «(черга порожня…)») ігноруються."""
    res = []
    for line in (out or "").splitlines():
        m = _LINE_RE.match(line.strip())
        if m:
            res.append((int(m.group(1)), m.group(2), int(m.group(3)), m.group(4)))
    return res


def new_messages(seen: set, rows: list) -> list:
    """Повертає ще не бачені повідомлення (у порядку id) і додає їх id у seen. Повідомлення, що зникло з черги (на нього відповіли), з seen не видаляється: id унікальні."""
    fresh = [r for r in sorted(rows) if r[0] not in seen]
    for r in fresh:
        seen.add(r[0])
    return fresh


def format_event(row: tuple) -> str:
    mid, ts, chat, text = row
    return f"НОВЕ ПОВІДОМЛЕННЯ КЛІЄНТА #{mid} {ts} чат {chat}: {text}"


def step(seen: set, state: dict, fetch=None, emit=print) -> int:
    """Один прохід: прочитати чергу, надрукувати нові. state['down'] — чи вже повідомили про збій каналу. Повертає кількість нових."""
    rc, out = (fetch or (lambda: sr.run_remote("list")))()
    if rc != 0:
        if not state.get("down"):
            emit("КАНАЛ ЧАТУ НЕДОСТУПНИЙ (ssh/ключ): нові повідомлення клієнтів не відстежуються — перевірте `python support_relay.py list`; власник мусить запустити install_support_relay.ps1, якщо ключа ще нема")
            state["down"] = True
        return 0
    if state.get("down"):
        emit("канал чату відновився")
        state["down"] = False
    fresh = new_messages(seen, parse_list(out))
    for r in fresh:
        emit(format_event(r))
    return len(fresh)


def main(argv: list) -> int:
    once = "--once" in argv
    seen, state = set(), {}
    while True:
        step(seen, state)
        sys.stdout.flush()
        if once:
            return 0
        time.sleep(INTERVAL)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

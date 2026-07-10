"""
prom_chat_bot.py — автоматичний відповідач у чаті Prom.

АРХІТЕКТУРА
Webhook у Prom Chat API немає — перевірено напряму: повний перелік методів
публічного API (Orders/Products/Clients/Messages/Groups/Payment/Delivery/
OrderStatus/Chat) не містить жодного webhook/subscribe ендпоінта. Тому —
polling, той самий підхід, що й prom_catalog_sync.py/order_status_tracker.py
(systemd timer на VPS, не crontab).

"Messages" API (`/messages/list`) — окрема, СТАРІША система, перевірено:
повертає порожньо для цього акаунту. Реальний, активний канал — "Chat" API
(`/chat/*`) — підтверджено: `GET /chat/rooms` повернув реальну кімнату з
живою перепискою (той самий чат, що на storefront сторінках товару).

ВАРТІСТЬ — рішення 2026-07-11: Claude Max-підписка НЕ підходить для цього
бота, перевірено напряму по офіційній документації Claude Code
(code.claude.com/docs/en/legal-and-compliance), не припущення:
"Anthropic does not permit third-party developers to offer Claude.ai login
or to route requests through Free, Pro, or Max plan credentials on behalf
of their users" + OAuth "is intended exclusively for... ordinary use" —
цей бот саме "продукт на базі Claude, що діє від імені користувачів
(покупців)" і працює 24/7 без ручного запуску сесії, а не "звичайне
використання". Рекомендований headless-шлях (`claude -p --bare`) сам
технічно вимагає ANTHROPIC_API_KEY (пропускає OAuth). Тому економія
робиться інакше — двома шарами нижче:
1. Шаблонний шар БЕЗ жодного виклику LLM для типових питань (наявність
   конкретного товару, ціна, спосіб/термін доставки) — пряма підстановка
   даних із картки Prom/статичної політики магазину.
2. Лише для нетипових повідомлень — виклик Claude Haiku (дешевша модель,
   не Sonnet) для класифікації+відповіді.

1. GET /chat/messages_history?status=new&project=promua&sort=asc — нові
   вхідні повідомлення. Фільтруємо на is_sender=false, type=message (не
   свої, не "context"/"attachment"-записи).
2. Для кожного — спершу шаблонний шар (try_template_response, без LLM). Якщо
   не спрацював — один виклик Claude Haiku: класифікація (normal/escalate)
   і, якщо normal, одразу готова відповідь.
3. normal   -> POST /chat/send_message (автоматично, без очікування
   підтвердження — власник explicitly підтвердив повну автономність
   2026-07-11 після того, як йому запропонували безпечніший варіант із
   Telegram-підтвердженням і він свідомо обрав повну).
   escalate -> Telegram-сповіщення власнику з повним контекстом, ПОВІДОМЛЕННЯ
   НЕ ПОЗНАЧАЄТЬСЯ ПРОЧИТАНИМ І БОТ НЕ ВІДПОВІДАЄ — чекає на ручну відповідь
   власника напряму в кабінеті Prom.
4. Кожне повідомлення (вхідне й наше власне) логується в prom_chat.db.

Критерії escalate (єдиний, свідомо простий запобіжник за вимогою власника):
скарга, повернення/відмова від замовлення, будь-яка проблема із
замовленням/товаром — а також БУДЬ-ЩО, у чому Claude не впевнений (не
вигадувати обіцянки, яких немає в наданому контексті), і будь-яка технічна
помилка виклику Claude API (мережа, ліміт, вичерпаний баланс — трактуємо
як "не можемо класифікувати" = escalate, не мовчимо і не гадаємо).

Запуск:
    python prom_chat_bot.py
"""

import json
import os
import re
import sys
from datetime import datetime, timezone

import requests
from dotenv import load_dotenv

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from prom_chat_db import (
    get_connection, init_db, insert_message, update_response, get_response_status,
)
from telegram_notify import send_telegram_message

load_dotenv()

PROM_API_KEY = os.environ.get("PROM_API_KEY", "")
PROM_API_URL = "https://my.prom.ua/api/v1"

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"
# Haiku, свідомо не Sonnet — LLM тепер лише fallback для нетипових повідомлень,
# типові (наявність/ціна/доставка) відповідає шаблонний шар нижче без виклику API.
ANTHROPIC_MODEL   = os.environ.get("PROM_CHAT_BOT_MODEL", "claude-haiku-4-5-20251001")

REQUEST_TIMEOUT = 30
ROOM_HISTORY_LIMIT = 10
MAX_REPLY_LEN = 1900  # ліміт Prom — 2000 символів на body, лишаємо запас

PROJECT = "promua"

# ---------------------------------------------------------------------------
# Статичний контекст магазину — лише те, що реально задокументовано
# (звірка з живого фіда Toysi/логістикою, PlutusToys_avtonomiya_plan.md,
# розділ "Логістика — відповідь Вікторії (Toysi), 2026-07-09"). Свідомо
# НЕ включає нічого, що дає тверді обіцянки поза цим (точна дата доставки,
# знижки, гарантія понад стандартну) — за такі питання Claude має
# ескалувати, не вигадувати.
# ---------------------------------------------------------------------------
STORE_POLICY = """
Інформація про магазин PlutusToys (дитячі іграшки, маркетплейс Prom.ua):

ДОСТАВКА:
- Основний і повністю автоматизований канал — Нова Пошта. Замовлення,
  оформлені до ~13:00 у робочі дні, відправляються того ж дня.
- Відправляє склад постачальника (Київ), не власний склад продавця.
- Точний термін доставки залежить від відділення Нової Пошти отримувача —
  зазвичай 1-3 робочих дні після відправки; не давай точну обіцяну дату,
  якщо покупець просить гарантію "точно до [дата]" — це до ескалації.

ОПЛАТА: накладений платіж (оплата при отриманні на Новій Пошті) або
передоплата — обидва варіанти доступні.

ПОВЕРНЕННЯ: стандартне право повернення товару належної якості протягом
14 днів з моменту отримання (Закон України "Про захист прав споживачів",
дистанційна торгівля) — товар має бути в оригінальній упаковці, без
слідів використання. Якщо покупець ставить загальне питання про умови
повернення — можна дати цю відповідь. Якщо покупець хоче ФАКТИЧНО
оформити повернення/відмову від конкретного замовлення чи скаржиться на
товар — це ЗАВЖДИ ескалація, не відповідай сам.
""".strip()

SYSTEM_PROMPT = f"""Ти — асистент інтернет-магазину дитячих іграшок PlutusToys на маркетплейсі Prom.ua.
Відповідаєш покупцям у чаті Prom від імені магазину, українською мовою, коротко й по суті (2-4 речення, без зайвої формальності, але ввічливо).

{STORE_POLICY}

ТВОЯ ЗАДАЧА: для кожного вхідного повідомлення покупця визнач:
1. classification — "normal" чи "escalate".
2. Якщо "normal" — response: готовий текст відповіді покупцю.
3. Якщо "escalate" — response: null.

ЕСКАЛЮЙ (classification="escalate"), якщо повідомлення стосується:
- скарги на товар чи сервіс;
- повернення, відмови від замовлення, обміну;
- будь-якої проблеми із замовленням (не прийшло, не те відправили, брак тощо);
- ти НЕ впевнений у фактах для відповіді (наприклад, просять точну обіцянку
  доставки/знижку/щось поза наданим контекстом і даними товару) — краще
  ескалувати, ніж вигадати неправильну відповідь від імені магазину.

Для "normal" питань (наявність, ціна, характеристики товару, загальні
питання про доставку/оплату/повернення без активного запиту на повернення)
відповідай сам, спираючись ЛИШЕ на надані дані товару й політику магазину
вище — нічого не вигадуй понад це.

Формат відповіді — СУВОРО валідний JSON, без жодного тексту навколо:
{{"classification": "normal"|"escalate", "reasoning": "коротке пояснення чому", "response": "текст відповіді покупцю" або null}}"""


def _prom_headers() -> dict:
    return {"Authorization": f"Bearer {PROM_API_KEY}"}


def fetch_new_messages() -> list:
    """Нові вхідні повідомлення в усіх кімнатах чату (усі проєкти promua)."""
    response = requests.get(
        f"{PROM_API_URL}/chat/messages_history",
        headers=_prom_headers(),
        params={"status": "new", "project": PROJECT, "sort": "asc", "limit": 100},
        timeout=REQUEST_TIMEOUT,
    )
    response.raise_for_status()
    return response.json().get("data", {}).get("messages", [])


def fetch_room_history(room_ident: str, limit: int = ROOM_HISTORY_LIMIT) -> list:
    """Останні повідомлення кімнати НАПРЯМУ з Prom (не з локальної БД) —
    джерело правди, включно з повідомленнями до першого запуску бота."""
    response = requests.get(
        f"{PROM_API_URL}/chat/messages_history",
        headers=_prom_headers(),
        params={"room_ident": room_ident, "project": PROJECT, "sort": "desc", "limit": limit},
        timeout=REQUEST_TIMEOUT,
    )
    response.raise_for_status()
    messages = response.json().get("data", {}).get("messages", [])
    return list(reversed(messages))  # найстаріше -> найновіше


def resolve_product_context(context_item_id) -> dict | None:
    """Дані товару напряму з Prom (уже опублікована, актуальна картка —
    не Toysi-каталог, бо тут важлива саме та ціна/наявність, яку зараз
    бачить покупець на сторінці)."""
    if not context_item_id:
        return None
    try:
        response = requests.get(
            f"{PROM_API_URL}/products/{context_item_id}",
            headers=_prom_headers(),
            timeout=REQUEST_TIMEOUT,
        )
        response.raise_for_status()
        product = response.json().get("product")
        if not product:
            return None
        return {
            "name": product.get("name"),
            "price": product.get("price"),
            "currency": product.get("currency"),
            "presence": product.get("presence"),
            "quantity_in_stock": product.get("quantity_in_stock"),
            "description": (product.get("description") or "")[:1500],
        }
    except requests.exceptions.RequestException as e:
        print(f"[ChatBot] Не вдалось отримати дані товару {context_item_id}: {e}", file=sys.stderr)
        return None


# ---------------------------------------------------------------------------
# Шаблонний шар — БЕЗ жодного виклику LLM. Свідомо консервативний: працює
# лише коли ВЕСЬ нормалізований текст повідомлення (без розділових знаків,
# без "?") збігається з коротким явним списком типових формулювань, а не
# по ключових словах усередині довшого тексту — реальне повідомлення з
# практики ("Доброго дня!\nВ наявності?\nНа скільки літрів?\nЧи з шлангою
# рюкзак?") НЕ повинно потрапити сюди, бо містить питання про характеристики
# товару понад просту наявність, а на них шаблон відповісти не може.
# Спрацьовує лише коли є product_context (наявність/ціна прив'язані до
# конкретного товару) — без нього шаблонна відповідь не має сенсу.
# ---------------------------------------------------------------------------
STOCK_PHRASES = {
    "в наявності", "чи є в наявності", "є в наявності", "наявність",
    "чи в наявності", "це є в наявності", "товар в наявності",
    "чи є", "є в наявності товар", "чи є цей товар в наявності",
}
PRICE_PHRASES = {
    "яка ціна", "скільки коштує", "ціна", "почім", "по чому",
    "скільки це коштує", "яка вартість", "вартість", "яка ціна товару",
    "скільки коштує цей товар",
}
DELIVERY_PHRASES = {
    "яка доставка", "як доставка", "способи доставки", "доставка",
    "як відправляєте", "чим відправляєте", "яка пошта", "коли відправите",
    "коли відправляєте", "новою поштою відправляєте",
}

DELIVERY_ANSWER = (
    "Відправляємо Новою Поштою — замовлення, оформлені до ~13:00 у робочі "
    "дні, йдуть того ж дня. Далі термін залежить від відділення Нової "
    "Пошти отримувача, зазвичай 1-3 робочих дні. Оплата — накладений "
    "платіж або передоплата, на вибір."
)


def _normalize(text: str) -> str:
    return re.sub(r"[^\w\s]", "", text.lower(), flags=re.UNICODE).strip()


def try_template_response(customer_message: str, product_context: dict | None) -> str | None:
    """Пряма підстановка без LLM для явно типових питань. Повертає готовий
    текст відповіді, або None якщо повідомлення не збігається чітко —
    у цьому разі виклик іде далі, до Claude Haiku, а не вигадується тут."""
    normalized = _normalize(customer_message)
    if not normalized:
        return None

    if normalized in DELIVERY_PHRASES:
        return DELIVERY_ANSWER

    if not product_context:
        return None  # наявність/ціна без прив'язки до товару — не шаблонизуємо

    name = product_context.get("name") or "Цей товар"

    if normalized in STOCK_PHRASES:
        presence = product_context.get("presence")
        qty = product_context.get("quantity_in_stock")
        if presence == "available" and qty:
            return f"{name} — так, є в наявності, залишок {qty} шт."
        if presence == "available":
            return f"{name} — так, є в наявності."
        return f"{name} — на жаль, немає в наявності."

    if normalized in PRICE_PHRASES:
        price = product_context.get("price")
        currency = product_context.get("currency") or "UAH"
        if price:
            return f"{name} — ціна {price} {currency}."
        return None  # немає ціни в даних - краще не шаблонизувати, хай іде до LLM/ескалації

    return None


def classify_and_respond(customer_message: str, history: list, product_context: dict | None) -> dict:
    """Один виклик Claude — класифікація + (за потреби) готова відповідь.
    Будь-яка помилка (мережа, ліміт, вичерпаний баланс API) -> escalate,
    НЕ мовчазний пропуск і НЕ вигадана відповідь."""
    if not ANTHROPIC_API_KEY:
        return {
            "classification": "escalate",
            "reasoning": "ANTHROPIC_API_KEY не задано — автоматична відповідь технічно недоступна",
            "response": None,
        }

    context_parts = []
    if product_context:
        context_parts.append(
            "Товар, про який запитує покупець (актуальні дані з картки Prom):\n"
            + json.dumps(product_context, ensure_ascii=False, indent=2)
        )
    if history:
        history_text = "\n".join(
            f"{'Магазин' if m.get('is_sender') else 'Покупець'}: {m.get('body')}"
            for m in history if m.get("body")
        )
        context_parts.append(f"Історія цього діалогу (від старіших до новіших):\n{history_text}")

    user_content = "\n\n".join(context_parts + [f"Нове повідомлення покупця: {customer_message}"])

    try:
        response = requests.post(
            ANTHROPIC_API_URL,
            headers={
                "x-api-key": ANTHROPIC_API_KEY,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": ANTHROPIC_MODEL,
                "max_tokens": 600,
                "system": SYSTEM_PROMPT,
                "messages": [{"role": "user", "content": user_content}],
            },
            timeout=REQUEST_TIMEOUT,
        )
        response.raise_for_status()
        data = response.json()
        raw_text = "".join(
            block.get("text", "") for block in data.get("content", []) if block.get("type") == "text"
        ).strip()
        # Claude інколи обгортає JSON у ```json ... ``` попри пряму вимогу — знімаємо, якщо є.
        if raw_text.startswith("```"):
            raw_text = raw_text.strip("`")
            if raw_text.lower().startswith("json"):
                raw_text = raw_text[4:]
            raw_text = raw_text.strip()
        parsed = json.loads(raw_text)
        if parsed.get("classification") not in ("normal", "escalate"):
            raise ValueError(f"Неочікуване значення classification: {parsed.get('classification')!r}")
        return parsed
    except (requests.exceptions.RequestException, ValueError, json.JSONDecodeError, KeyError) as e:
        print(f"[ChatBot] Помилка класифікації через Claude API: {e}", file=sys.stderr)
        return {
            "classification": "escalate",
            "reasoning": f"Технічна помилка виклику Claude API: {e}",
            "response": None,
        }


def send_reply(room_ident: str, body: str) -> int | None:
    body = body[:MAX_REPLY_LEN]
    response = requests.post(
        f"{PROM_API_URL}/chat/send_message",
        headers=_prom_headers(),
        json={"room_ident": room_ident, "body": body, "project": PROJECT},
        timeout=REQUEST_TIMEOUT,
    )
    response.raise_for_status()
    data = response.json()
    if data.get("status") != "ok":
        raise RuntimeError(f"Prom send_message повернув помилку: {data}")
    return data.get("message_id")


def mark_read(message_id: int, room_id: str) -> None:
    response = requests.post(
        f"{PROM_API_URL}/chat/mark_message_read",
        headers=_prom_headers(),
        json={"message_id": message_id, "room_id": room_id},
        timeout=REQUEST_TIMEOUT,
    )
    response.raise_for_status()


def escalate(msg: dict, reasoning: str) -> None:
    """Сповіщення власнику — повідомлення СВІДОМО лишається непрочитаним
    (mark_read не викликається), щоб власник відповів сам напряму в
    кабінеті Prom, без плутанини, що бот уже щось відповів чи позначив."""
    text = (
        "🔔 Чат Prom — потрібна РУЧНА відповідь\n"
        f"Від: {msg.get('user_name') or msg.get('user_ident')}\n"
        f"Повідомлення: {msg.get('body')}\n"
        f"Причина ескалації: {reasoning}\n"
        "Відповісти напряму в кабінеті Prom (Товари та послуги -> Повідомлення/Чат) — "
        "бот НЕ відповідав і НЕ позначав прочитаним."
    )
    send_telegram_message(text)


def process_message(conn, msg: dict) -> None:
    message_id = msg["id"]
    room_ident = msg["room_ident"]
    room_id    = msg["room_id"]
    body       = msg.get("body") or ""

    print(f"[ChatBot] Обробляю повідомлення {message_id} від {msg.get('user_name')}: {body[:80]!r}")

    history = fetch_room_history(room_ident, limit=ROOM_HISTORY_LIMIT)
    # Контекст товару — з context-повідомлення в цій же історії (найновіше, якщо є декілька)
    product_context = None
    for h in reversed(history):
        if h.get("context_item_id"):
            product_context = resolve_product_context(h["context_item_id"])
            break

    template_reply = try_template_response(body, product_context)
    if template_reply is not None:
        decision = {"classification": "template", "reasoning": "типове питання — відповідь без LLM", "response": template_reply}
    else:
        decision = classify_and_respond(body, history[:-1], product_context)  # [:-1] — без самого нового повідомлення (воно вже передається окремо)

    if decision["classification"] == "escalate":
        escalate(msg, decision.get("reasoning", "не вказано"))
        update_response(
            conn, message_id,
            classification="escalate",
            classification_reasoning=decision.get("reasoning"),
            response_status="escalated",
            escalation_notified_at=datetime.now(timezone.utc).isoformat(),
        )
        print(f"[ChatBot] Ескальовано власнику: {decision.get('reasoning')}")
        return

    reply_text = decision.get("response") or ""
    if not reply_text.strip():
        # normal/template, але порожня відповідь — не мовчати без причини, теж ескалація
        escalate(msg, "Порожня відповідь від обробника (не escalate, але й немає тексту)")
        update_response(
            conn, message_id,
            classification="escalate",
            classification_reasoning="normal без response — трактовано як помилку",
            response_status="escalated",
            escalation_notified_at=datetime.now(timezone.utc).isoformat(),
        )
        return

    try:
        send_reply(room_ident, reply_text)
        mark_read(message_id, room_id)
        update_response(
            conn, message_id,
            classification=decision["classification"],
            classification_reasoning=decision.get("reasoning"),
            response_status="auto_replied",
            response_body=reply_text,
        )
        print(f"[ChatBot] Відповів автоматично ({decision['classification']}): {reply_text[:80]!r}")
    except requests.exceptions.RequestException as e:
        # Відповідь згенеровано, але надіслати/позначити не вдалось — ескалюємо,
        # щоб покупець не лишився без відповіді мовчки.
        print(f"[ChatBot] Помилка відправки відповіді: {e}", file=sys.stderr)
        escalate(msg, f"Відповідь згенеровано, але відправка через Prom API впала: {e}")
        update_response(
            conn, message_id,
            classification=decision["classification"],
            classification_reasoning=decision.get("reasoning"),
            response_status="error",
            response_body=reply_text,
        )


def main() -> None:
    if not PROM_API_KEY:
        print("[ChatBot] PROM_API_KEY не задано — зупиняюсь.", file=sys.stderr)
        sys.exit(1)

    init_db()

    print("[ChatBot] Перевіряю нові повідомлення в чаті Prom...")
    try:
        messages = fetch_new_messages()
    except requests.exceptions.RequestException as e:
        print(f"[ChatBot] Не вдалось отримати список повідомлень: {e}", file=sys.stderr)
        sys.exit(1)

    to_process = [
        m for m in messages
        if m.get("type") == "message" and not m.get("is_sender") and (m.get("body") or "").strip()
    ]
    print(f"[ChatBot] Нових повідомлень: {len(messages)}, з них від покупця й потребують обробки: {len(to_process)}")

    with get_connection() as conn:
        for msg in messages:
            # Логуємо ВСІ нові повідомлення (і від покупця, і наші власні,
            # і context/attachment-записи) — для повної історії, навіть
            # ті, що не потребують окремої обробки.
            insert_message(conn, {
                "id": msg["id"],
                "room_id": msg["room_id"],
                "room_ident": msg["room_ident"],
                "user_name": msg.get("user_name"),
                "user_ident": msg.get("user_ident"),
                "is_sender": 1 if msg.get("is_sender") else 0,
                "body": msg.get("body"),
                "context_item_id": msg.get("context_item_id"),
                "date_sent": msg.get("date_sent"),
                "fetched_at": datetime.now(timezone.utc).isoformat(),
            })

        for msg in to_process:
            # Escalate-повідомлення свідомо лишаються status=new на Prom (не
            # викликаємо mark_read — власник відповідає сам), тож без цієї
            # перевірки бот повторно сповіщав би Telegram щоп'ять хвилин про
            # те саме повідомлення аж до ручної відповіді власника.
            already = get_response_status(conn, msg["id"])
            if already is not None:
                print(f"[ChatBot] Повідомлення {msg['id']} вже оброблено раніше ({already}) — пропускаю.")
                continue
            try:
                process_message(conn, msg)
            except Exception as e:
                print(f"[ChatBot] Неочікувана помилка обробки {msg['id']}: {e}", file=sys.stderr)
                escalate(msg, f"Неочікувана помилка бота: {e}")
                update_response(conn, msg["id"], response_status="error", classification_reasoning=str(e))

    print("[ChatBot] Готово.")


if __name__ == "__main__":
    main()

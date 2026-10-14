# -*- coding: utf-8 -*-
"""feed_title_norm.py — нормалізація регістру назв у фідах Google/Meta/Bing (правило SEO-агента, 14.10.2026): прибирає «крик» ВЕЛИКИМИ літерами, не псуючи бренди, числа й одиниці.

Правила (SEO, дослівно за змістом):
  R0 НЕ чіпати: слова з латиницею чи цифрами (НИК-1, 24, 5см), будь-які слова ≤3 літер у капсі, одиниці виміру (см, мм, мл…), числа й розміри.
  R1 слово з великих КИРИЛИЧНИХ літер довжиною ≥4 → «Перша велика, решта малі» (МАХІ → Махі, ЛАБУБУ → Лабубу) — бренд/ім'я лишається впізнаваним.
  R2 виняток: кольори й загальні слова (РОЖЕВИЙ, ЗЕЛЕНА, МІКС, ЧОРНО-БІЛИЙ…) → нижній регістр: (рожевий).
  R3 якщо ≥60% літер назви великі → увесь кириличний текст у нижній регістр, перша літера велика, слова в лапках з великої першої (R0 лишається).
  R4 перша літера назви завжди велика. Довжина ≤150 без обрізання посеред слова.
Мова-незалежна (uk/ru): слова кольорів — єдиний словник обох мов. Чисте: без мережі й БД. `python feed_title_norm.py` друкує нічого; тест test_feed_title_norm.py.
"""
import re

UPPER_CYR = "АБВГҐДЕЄЖЗИІЇЙКЛМНОПРСТУФХЦЧШЩЬЮЯЁЫЭЪ"
LOWER_CYR = UPPER_CYR.lower()
_APOS = "'ʼ’`"
_CYR_RE = re.compile(r"[А-Яа-яІіЇїЄєҐґЁёЫыЭэЪъ]")
_LAT_DIG_RE = re.compile(r"[A-Za-z0-9]")
# токен = послідовність без пробілів/лапок/дужок/розділових; решта (роздільники) лишається як є
_TOKEN_RE = re.compile(r"[^\s\"«»“”()\[\],;:!?]+")

UNITS = {"см", "мм", "мл", "кг", "гр", "шт", "ел", "дет", "дм", "м", "л", "г", "кв", "див", "мес", "мес."}

_COLOR_STEMS = (
    # українські
    "рожев", "блакитн", "жовт", "зелен", "синій", "синя", "сині", "червон", "фіолетов", "помаранчев", "чорн", "біл", "бузков", "бірюзов", "сір", "малинов", "коричнев", "салатов",
    "мʼятн", "м'ятн", "кольоров", "міке", "мікс", "різнокольоров", "золот", "срібн", "бордов", "бежев", "персиков", "лимонн", "оранжев", "голуб", "лаймов", "неонов", "прозор",
    # російські
    "розов", "голубой", "жёлт", "желт", "зелён", "зелен", "синий", "красн", "фиолетов", "оранжев", "чёрн", "черн", "белый", "белая", "белое", "сиреневый", "бирюзов", "серый", "серая",
    "малинов", "коричнев", "салатов", "мятн", "цветн", "разноцветн", "золотой", "серебрян", "бордов", "бежев", "персиков", "лимонн", "прозрачн",
)
# ВЛАСНІ НАЗВИ (бренди, персонажі, країна): у назві, що вся капсом (R3), вони лишаються «Перша велика», а не йдуть у нижній регістр (правка SEO 14.10: «Майнкрафт», не «майнкрафт»).
# Список складено за живим каталогом Toysi (слова, які в звичайних назвах пишуться з великої й майже ніколи з малої); нове слово = один рядок тут + рядок у test_feed_title_norm.py.
PROPER_NAMES = {"майнкрафт", "лабубу", "санріо", "маріо", "ельза", "дісней", "мінні", "кітті", "україна", "технок", "автопром", "брейнрот", "стрейчапалз", "санта", "русалонька"}

# слова, що дійсно є кольором/загальним словом навіть коротші за ≥4 літери або без «основи»
_GENERIC = {"МІКС", "МИКС", "АСОРТІ", "АССОРТИ", "НАБІР", "НАБОР", "ВИДІВ", "ВИДОВ", "КОМПЛЕКТ"}


def _letters(s: str) -> str:
    return "".join(ch for ch in s if ch.isalpha())


def _is_upper_cyr_word(tok: str) -> bool:
    L = _letters(tok)
    return bool(L) and all(ch in UPPER_CYR for ch in L)


def _cap_first(word: str) -> str:
    # перша ЛІТЕРА велика, решта малі; апострофи й дефіси зберігаються
    out, done = [], False
    for ch in word.lower():
        if not done and ch.isalpha():
            out.append(ch.upper())
            done = True
        else:
            out.append(ch)
    return "".join(out)


def _is_color_or_generic(tok: str) -> bool:
    up = _letters(tok).upper()
    if up in _GENERIC:
        return True
    low = tok.lower().replace("ʼ", "'")
    parts = re.split(r"[-–]", low)          # ЧОРНО-БІЛИЙ → обидві частини кольорові
    def _col(p):
        p = re.sub(r"^[^а-яіїєґёыэъ]+|[^а-яіїєґёыэъ'ʼ]+$", "", p)
        return any(p.startswith(st) for st in _COLOR_STEMS) or p in {"чорно", "біло", "чёрно", "бело", "жовто", "жёлто", "сіро", "серо", "червоно", "красно", "синьо", "сине", "зелено"}
    return all(_col(p) for p in parts if p)


def _is_untouchable(tok: str) -> bool:
    """R0: латиниця/цифри, ≤3 літер у капсі, одиниці виміру."""
    if _LAT_DIG_RE.search(tok):
        return True
    L = _letters(tok)
    if len(L) <= 3:
        return True
    if tok.lower().strip(".") in UNITS:
        return True
    return False


def _upper_share(title: str) -> float:
    letters = [ch for ch in title if ch.isalpha() and _CYR_RE.match(ch)]
    if not letters:
        return 0.0
    return sum(1 for ch in letters if ch in UPPER_CYR) / len(letters)


def normalize_title(title: str, limit: int = 150) -> str:
    """Повертає назву з нормалізованим регістром (див. правила вгорі). Без кириличних великих слів або порожню — без змін (крім R4 і ліміту)."""
    t = (title or "").strip()
    if not t:
        return t
    all_caps_mode = _upper_share(t) >= 0.60 and len(_letters(t)) >= 8     # R3 (короткі назви не вважаємо «криком»)

    def repl(m):
        tok = m.group(0)
        if not _CYR_RE.search(tok):
            return tok
        if _LAT_DIG_RE.search(tok):          # R0
            return tok
        if all_caps_mode:                    # R3: увесь кириличний текст у нижній регістр (R0 вище лишив латиницю/цифри), крім власних назв
            return _cap_first(tok) if _letters(tok).lower() in PROPER_NAMES else tok.lower()
        if not _is_upper_cyr_word(tok):
            return tok
        if _is_untouchable(tok):
            return tok.lower() if tok.lower().strip(".") in UNITS else tok       # одиниці «СМ» → «см»; інші короткі лишаємо
        if _is_color_or_generic(tok):        # R2
            return tok.lower()
        return _cap_first(tok)               # R1

    out = _TOKEN_RE.sub(repl, t)
    if all_caps_mode:
        # слова в лапках з великої першої (R3)
        out = re.sub(r"([\"«“])([^\"»”]+)([\"»”])", lambda m: m.group(1) + " ".join(_cap_first(w) if w.islower() and len(_letters(w)) > 3 else w for w in m.group(2).split(" ")) + m.group(3), out)
    # R4: перша літера велика
    for i, ch in enumerate(out):
        if ch.isalpha():
            out = out[:i] + ch.upper() + out[i + 1:]
            break
    if len(out) > limit:                      # не обрізати посеред слова
        cut = out[:limit]
        if out[limit] != " " and " " in cut:
            cut = cut[:cut.rfind(" ")]
        out = cut.rstrip(" ,;:-")
    return out

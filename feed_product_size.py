# -*- coding: utf-8 -*-
"""feed_product_size.py — Google-фід: g:size для м'яких іграшок (запит SEO 20.11.2026).

ЧОМУ: Merchant Center, звіт «Обновите описания товаров и добавьте информацию, которую ищут клиенты», колонка «Добавить в описание» = «Размер» в усіх рядках категорії
«Мягкие игрушки в виде животных». Розмір є в назві й описі («24 см»), але як поле Google його не бачить. Google приймає `size` і для не-одягу (одне значення, вільний текст).

ПРАВИЛО (виміряно 20.11 на живому фіді: 1 041 м'яка іграшка → 832 (79,9 %) отримують розмір, одиниця — унікальний g:id):
  • «м'яка іграшка» = назва або категорія містить «м'яка/м'які іграшка/іграшки» (укр) чи «мягкая игрушка» (рос);
  • розмір береться ДОСЛІВНО (лише цифри з тексту, «cm» → «см»; нічого не вигадуємо й не рахуємо):
      1) з НАЗВИ — рівно один різний розмір («24 см», «36 х 20 см», «20-25 см»); два різні (напр. «60 см (за стандартом - 85 см)») → нічого;
      2) інакше з ОПИСУ — лише число з міткою розміру САМОЇ ІГРАШКИ («розмір іграшки», «висота іграшки», «довжина іграшки», «висота виробу», «без упаковки»,
         «розмір, зазначений у назві»); «в упаковці» НІКОЛИ (це габарити коробки); різні значення → нічого. «Різні» — за набором чисел без порядку (30х18х13 = 13 x 30 x 18).
  • нічого певного → тег не пишемо (товар без g:size, як було).
`FEED_SOFT_TOY_SIZE=0` у .env VPS вимикає без релізу. Лише Google-фід (`_build_xml`: укр, рос, RT/KV); Meta/Bing цього поля не отримують.
"""
import os
import re

_NUM = r"\d{1,3}(?:[.,]\d)?"
_X = r"[xхXХ×*]"
_R_DIM = re.compile(rf"(?<![\d.,])({_NUM})\s*{_X}\s*({_NUM})(?:\s*{_X}\s*({_NUM}))?\s*(?:см|cm)\b", re.I)
_R_RNG = re.compile(rf"(?<![\d.,])({_NUM})\s*[-–—]\s*({_NUM})\s*(?:см|cm)\b", re.I)
_R_ONE = re.compile(rf"(?<![\d.,xXхХ×*])({_NUM})\s*(?:см|cm)\b", re.I)
_ITEM_LAB = re.compile(
    rf"(?:розмір(?:и)?\s+іграшки|висота\s+іграшки|довжина\s+іграшки|висота\s+виробу|без\s+упаковки|розмір,\s*зазначений\s+у\s+назві"
    rf"|размер(?:ы)?\s+игрушки|высота\s+игрушки|длина\s+игрушки|высота\s+изделия|размер,\s*указанный\s+в\s+названии)"
    rf"\s*[-–—:()]*\s*(?:приблизно|близько|примерно|около)?\s*({_NUM}(?:\s*{_X}\s*{_NUM}){{0,2}})\s*(?:см|cm)\b", re.I)
_SOFT = re.compile(r"(?:м'як\w*\s+іграшк|мягк\w*\s+игруш)", re.I)
_APOS = str.maketrans({"ʼ": "'", "’": "'", "`": "'", "‘": "'"})


def enabled() -> bool:
    return os.environ.get("FEED_SOFT_TOY_SIZE", "1").strip() != "0"


def is_soft_toy(title, category_name="") -> bool:
    return bool(_SOFT.search(f"{title or ''} {category_name or ''}".translate(_APOS)))


def _key(s: str):
    return tuple(sorted(float(x.replace(",", ".")) for x in re.findall(r"\d+(?:[.,]\d)?", s)))


def _fmt(raw: str) -> str:
    nums = re.findall(r"\d+(?:[.,]\d)?", raw)
    sep = "-" if (re.search(r"[-–—]", raw) and len(nums) == 2 and not re.search(_X, raw)) else " x "
    return sep.join(nums) + " см"


def _title_sizes(text: str) -> list:
    out, spans = [], []
    for rx in (_R_DIM, _R_RNG):
        for m in rx.finditer(text):
            out.append(m.group(0))
            spans.append(m.span())
    for m in _R_ONE.finditer(text):
        if not any(a <= m.start() < b for a, b in spans):
            out.append(m.group(0))
    return out


def extract(title, description="", category_name="") -> str | None:
    """«24 см» / «36 x 20 см» / «20-25 см» або None. Лише для м'яких іграшок; лише дослівно з назви/опису; неоднозначно → None."""
    if not enabled() or not is_soft_toy(title, category_name):
        return None
    ts = _title_sizes(title or "")
    keys = {_key(s) for s in ts}
    if len(keys) > 1:
        return None
    if ts:
        return _fmt(ts[0])
    found = [m.group(1) for m in _ITEM_LAB.finditer(description or "")]
    if found and len({_key(f) for f in found}) == 1:
        return _fmt(found[0])
    return None

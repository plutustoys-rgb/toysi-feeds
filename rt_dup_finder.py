"""
rt_dup_finder.py — консервативний пошук дублів RoyalToys (rt-) ↔ Toysi для поля dup_of.

Пише rt_dup_map.json {"rt-<id>": "<id товару Toysi>"} (читає rt_supplier_export.load_dup_map → поле dup_of у supplier_rt.json).
Принцип: КРАЩЕ ПРОПУСТИТИ дубль, ніж позначити різні товари дублями (різні модель/колір/розмір/комплектація = НЕ дубль).

АЛГОРИТМ (метод і калібрування порогу — у rt_dup_report_2026-10-20.md):
 1. Бренд: normalize_vendor (з compare_royaltoys_toysi) + ручна таблиця аліасів ALIAS_GROUPS. Кандидат Toysi — лише ТОГО Ж бренду.
 2. Кандидат Toysi має бути тим, що сайт реально показує: stock>0, є фото й назва, не заборонений (forbidden_products), не UNO, не «Уцінка».
 3. Схожість назв — ОДНА метрика wjac: зважений на IDF коефіцієнт Жаккара над основами слів (normalize_name з compare_royaltoys_toysi;
    без артикулів, слів бренду, кольорів і слів-модифікаторів; IDF — за всіма RT+Toysi назвами). Звичайний Жаккар давав систематичні хибні збіги
    на шаблонних назвах («Картина за номерами … 40х50 см»), IDF знижує вагу шаблонних слів.
 4. Жорсткі перевірки (будь-яка суперечність → НЕ дубль): (a) множини чисел у назвах збігаються (артикули й цифри vendor_code не рахуються);
    (b) кольори не суперечать і вказані з обох боків або з жодного (колір лише з одного боку = не підтверджено → НЕ дубль);
    (c) слова-модифікатори/«тип товару»/одно-літерні позначки (Великий/Міні/А/Б, алмазна мозаїка ≠ картина) збігаються.
 5. Правило dup:  (артикул виробника RT є токеном у назві Toysi І числа збігаються І кольори не суперечать і кандидат один)
                  АБО (wjac ≥ SIM_T І всі перевірки пройдені І такий кандидат РІВНО ОДИН І назва Toysi не «пуста»: ≥ MIN_TOKENS_T інформативних слів).
 6. «Явно кращий за другий» = другого пристойного кандидата немає взагалі. Зворотна неоднозначність: якщо один товар Toysi «претендується»
    кількома rt-картками — не віддаємо його жодній.
Запуск:  python rt_dup_finder.py            (тягне живі каталоги, пише rt_dup_map.json)
         python rt_dup_finder.py --dry      (лише підсумок, файл не чіпає)
"""
from __future__ import annotations

import os
os.environ.setdefault("AUDIT_NO_TELEGRAM", "1")
import argparse
import json
import math
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

from compare_royaltoys_toysi import normalize_name, normalize_vendor   # перевикористано (нормалізація назв/бренду)

HERE = Path(__file__).resolve().parent
OUT_FILE = HERE / "rt_dup_map.json"

SIM_T = 0.75          # поріг схожості (калібрування: ручна вибірка, див. звіт)
MIN_TOKENS_T = 3      # «пуста» (загальна) назва Toysi («Набір інструментів», «Каска») — менше 3 інформативних слів: схожістю не підтверджуємо

# ---- (1) бренд: ручна таблиця аліасів (кожну групу перевірено по зразках назв товарів) ----
ALIAS_GROUPS = [
    ["ranokcreative", "ранок", "ранокcreative"],
    ["идейка", "ідейка", "ideyka"],
    ["dodotoys", "dodo", "додо"],
    ["зірка", "zirka"],
    ["mtoys", "мтoys"],                 # у Toysi «мтoys» = кирилична «м» + латиниця (так у фіді)
    ["limotoy", "limotoys"],
    ["fungames", "fungame"],
    ["сувенірдекор", "сувенирдекор"],
    ["юника", "юніка", "unika"],
    ["ellott", "ellot"],
    ["технок", "technoktoys"],
    ["максимус", "maximus"],
    ["bamsic", "бамсик", "bamsik"],
    ["defa", "defatoys"],
    ["joy", "joytoy"],
    ["bohui", "bohuitoys"],
    ["shantoujinxing", "shantoujinxingplastics"],
    ["mic", "міс"],
    ["strateg", "starteg"],             # «Starteg» у Toysi — друкарська помилка бренду Strateg
    ["ubumblebees", "ubee"],            # Ubee = Ubumblebees (збіги назв підтверджено вручну)
]
ALIAS = {k: g[0] for g in ALIAS_GROUPS for k in g}


def brand_key(v: str) -> str:
    k = normalize_vendor(v or "")
    return ALIAS.get(k, k)


# ---- слова-кольори / модифікатори ----
COLORS = {
    "червон": "red", "red": "red", "син": "blue", "blue": "blue", "блакит": "lblue", "зелен": "green", "green": "green", "жовт": "yellow", "yellow": "yellow",
    "біл": "white", "white": "white", "чорн": "black", "black": "black", "рожев": "pink", "pink": "pink", "фіолет": "violet", "violet": "violet", "purple": "violet",
    "пурпур": "violet", "помаранч": "orange", "orange": "orange", "оранж": "orange", "сір": "grey", "grey": "grey", "gray": "grey", "коричн": "brown",
    "brown": "brown", "бежев": "beige", "beige": "beige", "золот": "gold", "gold": "gold", "срібл": "silver", "срібн": "silver", "silver": "silver",
    "бірюз": "turq", "голуб": "lblue", "бордов": "bordo", "малинов": "raspb", "лаванд": "lavender", "салатов": "lime", "персик": "peach", "peach": "peach",
    "crimson": "raspb", "turquoise": "turq", "cream": "cream", "кремов": "cream", "м'ят": "mint", "мят": "mint", "mint": "mint", "бузков": "lilac",
    "лілов": "lilac", "кораловий": "coral", "мінт": "mint", "хакі": "khaki", "прозор": "clear", "різнокольор": "multi", "мульти": "multi", "мікс": "multi",
}
_COLOR_ITEMS = [(p.replace("’", "'").replace("ʼ", "'"), k) for p, k in COLORS.items()]
CODE_COL = {"peach": "peach", "crimson": "raspb", "turquoise": "turq", "cream": "cream", "mint": "mint", "lime": "lime", "lilac": "lilac", "coral": "coral",
            "khaki": "khaki", "red": "red", "blue": "blue*", "green": "green", "yellow": "yellow", "white": "white", "black": "black", "pink": "pink",
            "violet": "violet", "purple": "violet", "orange": "orange", "grey": "grey", "gray": "grey", "brown": "brown", "beige": "beige", "gold": "gold",
            "silver": "silver"}


def _norm_apos(s: str) -> str:
    return s.lower().replace("’", "'").replace("ʼ", "'")


def _is_color_word(w: str) -> bool:
    return any(w.startswith(p) and (len(p) >= 4 or len(w) <= len(p) + 4) for p, _ in _COLOR_ITEMS)


def colors_of(name: str) -> set:
    out = set()
    for w in re.findall(r"[a-zа-яіїєґ']+", _norm_apos(name)):
        for p, k in _COLOR_ITEMS:
            if w.startswith(p) and (len(p) >= 4 or len(w) <= len(p) + 4):
                out.add(k)
                break
    return out


def rt_colors(vendor_code: str, name: str) -> set:
    """Колір RT: зі слів назви; якщо їх нема — з англ. слова в дужках vendor_code, напр. «Y9093(Blue)» (blue* = синій АБО блакитний)."""
    c = colors_of(name)
    if c:
        return c
    out = set()
    for g in re.findall(r"\(([^)]*)\)", vendor_code or ""):
        for w in re.findall(r"[a-z]+", g.lower()):
            if w in CODE_COL:
                out.add(CODE_COL[w])
    return out


def col_relation(rc: set, tc: set) -> str:
    """'both_none' | 'equal' | 'one_sided' | 'conflict'."""
    if not rc and not tc:
        return "both_none"
    if not rc or not tc:
        return "one_sided"
    if "blue*" in rc:
        hit = tc & {"blue", "lblue"}
        rc = (rc - {"blue*"}) | (hit if hit else {"blue"})
    return "equal" if rc == tc else "conflict"


VARIANT = {"великий": "L", "велика": "L", "великі": "L", "великих": "L", "малий": "S", "мала": "S", "малі": "S", "маленький": "S", "міні": "mini",
           "mini": "mini", "максі": "maxi", "maxi": "maxi", "середній": "M", "середня": "M", "xl": "xl", "xxl": "xxl", "англ": "en", "англійською": "en",
           "eng": "en", "рос": "ru", "російською": "ru", "рус": "ru", "уцінка": "md", "уцінений": "md", "підрамника": "noframe", "premium": "prem",
           "deluxe": "dlx", "pro": "pro", "plus": "plus", "лайт": "lite", "lite": "lite", "black": "blk", "mega": "mega", "мікс": "mix",
           "асортименті": "mix", "асорті": "mix", "комплект": "set", "світло": "light", "світлий": "light", "темно": "dark", "темний": "dark",
           "неоновий": "neon", "неонові": "neon", "металік": "metal",
           # видання-модифікатори (Shopify-code 20.10: «Елліас для вечірок» ≠ «Елліас»): лише з одного боку = НЕ дубль
           "вечірок": "party", "вечірка": "party", "вечірки": "party", "party": "party", "дорожня": "travel", "дорожній": "travel", "дорожні": "travel", "travel": "travel",
           "подарункова": "gift", "подарунковий": "gift", "подарункове": "gift", "подарункові": "gift", "юніор": "junior", "junior": "junior", "юніорів": "junior"}
TYPE_STEMS = (("алмазн", "T:diamond"), ("діамантов", "T:diamond"), ("кришталев", "T:crystal"), ("картин", "T:paint"), ("розпис", "T:paint"),
              ("пазл", "T:puzzle"), ("плакат", "T:poster"), ("наліпк", "T:sticker"), ("розфарбов", "T:color"), ("пальчик", "T:finger"),
              ("акрилов", "T:acryl"), ("гуашев", "T:gouache"), ("магніт", "T:magnet"), ("каркасн", "T:frame"), ("надувн", "T:inflat"),
              ("електрон", "T:elec"), ("інтерактивн", "T:interact"))


def variants_of(name: str) -> set:
    """Слова-модифікатори варіанта + «тип товару» + одно-літерні позначки («Відерко Б» ≠ «Відерце А»): множини з обох боків мають збігатися."""
    words = re.findall(r"[a-zа-яіїєґ']+", _norm_apos(name))
    out = {VARIANT[w] for w in words if w in VARIANT}
    for w in words:
        for st, k in TYPE_STEMS:
            if w.startswith(st):
                out.add(k)
                break
    out |= {"L:" + m for m in re.findall(r"(?<![\w'])([A-ZА-ЯІЇЄҐ])(?![\w'])", name)}
    return out


# ---- числа й артикули ----
SIZE_RE = re.compile(r"^\d+(?:[.,]\d+)?[хx*×]\d+")
UNITS = r"(?:мл|л|г|гр|кг|см|мм|м|шт|ел|елем|дет|ст|мг|мач|вт|в|mm|cm|ml|kg|g|l|pcs|шарів|кол)"
_LET = "A-Za-zА-Яа-яІіЇїЄєҐґ"
CYR2LAT = str.maketrans("авекмнорстхі", "abekmhopctxi")


def is_article_token(tok: str) -> bool:
    """Токен-артикул (FGS76, 0014TXK, 01540/01/02, 39404-9), а не змістовне число/розмір/номер моделі (S2, NIK-3, 100мл, 30х40)."""
    t = tok.strip(".,;:()[]\"«»'")
    if not t or not re.search(r"\d", t):
        return False
    tl = t.lower()
    if SIZE_RE.match(tl) or re.fullmatch(r"\d+(?:[.,]\d+)?" + UNITS, tl) or re.fullmatch(r"\d+[a-zа-яіїє]\d+", tl):
        return False
    if re.fullmatch(r"\d{1,4}(?:[.,]\d+)?", t):
        return False
    if re.fullmatch(r"\d{5,}", t) or re.fullmatch(r"\d{4,}(?:[/\-]\d+)+", t):
        return True
    if len(t) < 4 or re.search(rf"[{_LET}]{{4,}}", t) or len(re.findall(r"\d", t)) < 2:
        return False
    return bool(re.search(rf"[{_LET}]", t))


def code_digit_runs(vc: str) -> set:
    return {r.lstrip("0") or "0" for r in re.findall(r"\d+", vc or "") if len(r) >= 3}


def numbers_of(name: str, drop_runs=frozenset()) -> set:
    """Множина числових значень назви, крім артикулів і цифр з vendor_code RT (drop_runs)."""
    nums = set()
    for tok in name.split():
        if is_article_token(tok):
            continue
        for r in re.findall(r"\d+(?:[.,]\d+)?", tok):
            r = r.replace(",", ".")
            r = r.rstrip("0").rstrip(".") if "." in r else (str(int(r)) if r.isdigit() else r)
            if r not in drop_runs:
                nums.add(r)
    return nums


def codekey(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower().translate(CYR2LAT))


def code_tokens_of_text(text: str) -> set:
    """Токени тексту, схожі на артикул (≥4 символи, є цифра), у нормалізованому вигляді + склеєні «ME 1200»→«me1200»."""
    toks = re.findall(rf"[{_LET}0-9][{_LET}0-9\-/.]*", text or "")
    out = set()
    for i, t in enumerate(toks):
        k = codekey(t)
        if len(k) >= 4 and re.search(r"\d", k):
            out.add(k)
        if i + 1 < len(toks) and re.search(r"\d", toks[i + 1]) and re.fullmatch(r"[a-z]{1,4}", codekey(t)):
            k2 = codekey(t + toks[i + 1])
            if len(k2) >= 4:
                out.add(k2)
    return out


def rt_codes(vendor_code: str) -> set:
    base = re.sub(r"\(.*?\)", "", vendor_code or "")
    ks = set(code_tokens_of_text(base))
    k = codekey(base)
    if len(k) >= 4 and re.search(r"\d", k):
        ks.add(k)
    return ks


def stem(w: str) -> str:
    return w[:5] if len(w) > 5 else w


def tokens_for_sim(name: str, brand_words=frozenset()) -> set:
    """normalize_name → без артикулів, слів бренду, кольорів і модифікаторів (їх перевіряємо окремо) і числових токенів → основи (5 символів)."""
    base = " ".join(t for t in name.split() if not is_article_token(t))
    out = set()
    for w in normalize_name(base):
        if re.fullmatch(r"[\d.,х*×x]+(?:[a-zа-яіїє]{0,3})", w) or w in brand_words or w in VARIANT or len(w) <= 1 or _is_color_word(w):
            continue
        out.add(stem(w))
    return out


def toysi_eligible(t: dict) -> bool:
    """Картка Toysi, яку сайт реально показує (site/build_site.py): є в наявності, фото й назва, не заборонена, не UNO, не «Уцінка»."""
    from forbidden_products import is_forbidden
    from trademark_filter import is_uno_trademark_blocked
    try:
        if int(t.get("stock") or 0) <= 0:
            return False
    except (TypeError, ValueError):
        return False
    if not t.get("pictures") or not t.get("name"):
        return False
    if re.search(r"уц[еі]нк", (t.get("category_name") or "") + " " + t["name"], re.I):
        return False
    return not is_forbidden(t) and not is_uno_trademark_blocked(t["name"], t.get("vendor") or "")


class Matcher:
    """Індекс Toysi + IDF по всіх RT+Toysi назвах. evaluate(rt_item) → список кандидатів того ж бренду з усіма перевірками."""

    def __init__(self, rt: dict, ty: dict, rt_sids: list):
        self.rt, self.ty = rt, ty
        self.tby = defaultdict(list)
        self.tel = [t for t in ty.values() if toysi_eligible(t)]
        for t in self.tel:
            bw = set(normalize_name(t.get("vendor") or ""))
            t["_bw"], t["_tok"] = bw, tokens_for_sim(t["name"], bw)
            t["_codes"], t["_col"], t["_var"] = code_tokens_of_text(t["name"]), colors_of(t["name"]), variants_of(t["name"])
            self.tby[brand_key(t["vendor"])].append(t)
        df, n = Counter(), 0
        for t in self.tel:
            df.update(t["_tok"]); n += 1
        for sid in rt_sids:
            r = rt[sid[3:]]
            df.update(tokens_for_sim(r.get("name_ua") or r["name"], set(normalize_name(r.get("vendor") or "")))); n += 1
        self._df, self._n = df, n

    def idf(self, w: str) -> float:
        return math.log(self._n / self._df.get(w, 1))

    def wjac(self, a: set, b: set) -> float:
        """ЄДИНА метрика схожості назв: IDF-зважений Жаккар над основами слів."""
        if not a or not b:
            return 0.0
        den = sum(self.idf(w) for w in a | b)
        return sum(self.idf(w) for w in a & b) / den if den > 0 else 0.0

    def evaluate(self, sid: str, keep_min: float = 0.1):
        r = self.rt[sid[3:]]
        cands = self.tby.get(brand_key(r.get("vendor") or ""))
        if not cands:
            return None
        nm = r.get("name_ua") or r["name"]
        vc = r.get("vendor_code") or ""
        drop = code_digit_runs(vc)
        bw = set(normalize_name(r.get("vendor") or ""))
        rtok, rnum = tokens_for_sim(nm, bw), numbers_of(nm, drop)
        rcol, rvar, rc = rt_colors(vc, nm), variants_of(nm), rt_codes(vc)
        out, best_any = [], 0.0
        for t in cands:
            s = self.wjac(rtok, t["_tok"] - bw)
            best_any = max(best_any, s)
            art = bool(rc & t["_codes"])
            if s < keep_min and not art:
                continue
            tnum = numbers_of(t["name"], drop)
            out.append(dict(tid=t["id"], sim=s, art=art, num_ok=(rnum == tnum), nums=sorted(rnum & tnum), col=col_relation(rcol, t["_col"]),
                            var_ok=(rvar == t["_var"]), tn=len(t["_tok"])))
        out.sort(key=lambda x: -x["sim"])
        return dict(sid=sid, bk=brand_key(r.get("vendor") or ""), best_any=best_any, cands=out)


def _hard(c: dict) -> bool:
    return c["num_ok"] and c["var_ok"] and c["col"] in ("equal", "both_none")


def _hard_art(c: dict) -> bool:
    return c["num_ok"] and c["var_ok"] and c["col"] != "conflict"


def decide(ev: dict, sim_t: float = SIM_T, min_tokens: int = MIN_TOKENS_T) -> dict:
    """ev {sid: evaluate()} → {sid: (tid, route, sim)}."""
    claims = {}
    for sid, e in ev.items():
        arts = [c for c in e["cands"] if c["art"] and _hard_art(c)]
        if len(arts) == 1:
            claims[sid] = (arts[0]["tid"], "art", arts[0]["sim"])
            continue
        if arts:
            continue
        ok = [c for c in e["cands"] if _hard(c) and c["sim"] >= sim_t]
        if len(ok) == 1 and ok[0]["tn"] >= min_tokens:
            claims[sid] = (ok[0]["tid"], "sim", ok[0]["sim"])
    cnt = Counter(v[0] for v in claims.values())            # один товар Toysi «претендують» кілька rt- → не віддаємо нікому
    return {k: v for k, v in claims.items() if cnt[v[0]] == 1}


def find_dups(rt: dict, ty: dict, rt_sids: list, sim_t: float = SIM_T):
    m = Matcher(rt, ty, rt_sids)
    ev = {}
    for sid in rt_sids:
        e = m.evaluate(sid)
        if e:
            ev[sid] = e
    return decide(ev, sim_t), ev, m


def published_rt_ids(rt: dict) -> list:
    import rt_supplier_export as ex
    ids = ex.select_ids(rt, ex.load_pilot_ids(), ex.load_selection())
    return [s for s in ids if s[3:] in rt and ex.build_item(s, rt[s[3:]], ["-"]) is not None]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true", help="не писати rt_dup_map.json")
    args = ap.parse_args(argv)
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass
    import royaltoys_parser as rp
    from parser import fetch_toysi_catalog
    rt, ty = rp.fetch_royaltoys_catalog(), fetch_toysi_catalog()
    if not rt or len(ty) < 25_000:
        print("[rt_dup] каталог RT/Toysi порожній або усічений — файл не чіпаю", file=sys.stderr)
        return 1
    dup, ev, _m = find_dups(rt, ty, published_rt_ids(rt))
    print(f"[rt_dup] RT у брендах перетину: {len(ev)}; дублів: {len(dup)} ({Counter(v[1] for v in dup.values())})")
    if not args.dry:
        tmp = OUT_FILE.with_suffix(".json.tmp")
        tmp.write_text(json.dumps({k: dup[k][0] for k in sorted(dup)}, ensure_ascii=False, indent=0), encoding="utf-8")
        tmp.replace(OUT_FILE)
        print(f"[rt_dup] записано {OUT_FILE.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

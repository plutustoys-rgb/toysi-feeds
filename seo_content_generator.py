"""seo_content_generator.py — Фаза 2 SEO-описів: шаблонний rule-based генератор на масштаб.

Задача Cowork (STATUS.md найновіше-18/20). Фаза 1 (#260) — 30 «золотих» описів вручну
(Cowork), затверджені власником. Фаза 2 (цей файл) — генерує унікальні описи для РЕШТИ
каталогу з РЕАЛЬНИХ атрибутів Toysi (назва/категорія/бренд/країна/розмір), пише у той самий
`seo_content.db` (seo_content_db.py). БЕЗ Anthropic API (власник не поповнює баланс).

ГРАМАТИЧНА БЕЗПЕКА (урок демо «якісну тварини»): НІКОЛИ не відмінюємо змінні (назву/
категорію/бренд/країну) — ставимо їх фактами в НАЗИВНОМУ або лейблами: «Категорія: X»,
«Виробник — Y», «Країна походження — Z». Користь категорії — ПОВНІ заздалегідь написані
речення в називному. Варіативність — детермінована за hash(sku) (унікально per-SKU, але
відтворювано). Duplicate-content розв'язується: текст різний і від Toysi, і між товарами.

БЕЗПЕКА КОНТЕНТУ: лише реальні атрибути; без суперлативів (найкращий/№1 — урок #252); без
вигаданих вік/безпека/матеріал-тверджень; без чужих ТМ у похвальному контексті.

ГЕЙТ: генерує з approved=0 (НЕ публікується, поки власник не перегляне sample і не
затвердить). НЕ перезаписує approved=1 (золотий пілот Cowork). Пропускає SKU, чий
source_hash уже збігається (idempotent — не регенеруємо без зміни Toysi).
"""
import argparse
import hashlib
import html
import json
import re
import sys
from pathlib import Path

from parser import fetch_toysi_catalog
from generate_prom_feed_top import select_top_items
import seo_content_db as db

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

SEO_SOURCE = "template_v1"
DEFAULT_LIMIT = 200
BANNED_COUNTRY = ("рф", "росія", "россия", "russia")

# Користь категорії — ПОВНІ речення в називному (жодного відмінювання змінних). Ключ —
# підрядок назви Toysi-категорії (регістронезалежно). Кілька варіантів → варіативність за sku.
CATEGORY_BENEFIT = {
    "пазл":        ["Пазли розвивають логіку, увагу та дрібну моторику.",
                    "Складання пазла тренує посидючість і просторове мислення."],
    "конструктор": ["Конструктор розвиває дрібну моторику та просторову уяву.",
                    "Складання деталей тренує логіку й терпіння дитини."],
    "лял":         ["Ляльки заохочують рольові ігри та розвивають соціальні навички.",
                    "Гра з лялькою розвиває уяву та емпатію."],
    "пупс":        ["Пупси заохочують турботливу гру та розвивають емпатію.",
                    "Гра з пупсом розвиває дбайливість і уяву дитини."],
    "машин":       ["Іграшкова техніка розвиває уяву та координацію рухів.",
                    "Динамічна гра з машинками тренує моторику."],
    "антистрес":   ["Іграшка-антистрес допомагає зняти напругу та зосередитись.",
                    "М’яка фактура заспокоює і тренує дрібну моторику пальців."],
    "сквіш":       ["Сквіш повільно відновлює форму, заспокоює й тренує пальчики.",
                    "М’який сквіш — приємний антистрес для рук."],
    "творч":       ["Набір для творчості розвиває креативність і посидючість.",
                    "Творчі заняття тренують уяву та дрібну моторику."],
    "малюванн":    ["Малювання розвиває креативність, увагу та дрібну моторику.",
                    "Творчий процес тренує уяву й посидючість дитини."],
    "ліпленн":     ["Ліплення розвиває дрібну моторику та фантазію.",
                    "Робота з масою для ліплення тренує пальчики й уяву."],
    "тварин":      ["Фігурки тварин знайомлять дитину зі світом природи.",
                    "Гра з тваринками розвиває мовлення та уяву."],
    "настільн":    ["Настільна гра — чудове дозвілля для родини та друзів.",
                    "Гра розвиває логіку, увагу й уміння грати за правилами."],
    "м'як":        ["М’яка іграшка дарує затишок і розвиває тактильні відчуття.",
                    "М’який друг заспокоює й супроводжує дитину в іграх і сні."],
    "мяк":         ["М’яка іграшка дарує затишок і розвиває тактильні відчуття.",
                    "М’який друг заспокоює й супроводжує дитину в іграх і сні."],
    "ведмед":      ["М’який ведмедик дарує затишок і розвиває тактильні відчуття.",
                    "Плюшевий друг заспокоює й супроводжує дитину."],
    "музичн":      ["Музична іграшка розвиває слух і чуття ритму.",
                    "Звуки й мелодії знайомлять дитину зі світом музики."],
    "розвива":     ["Розвивальна іграшка тренує увагу, логіку та дрібну моторику.",
                    "Гра стимулює пізнавальний інтерес і мислення дитини."],
    "навчальн":    ["Навчальний набір у грі знайомить дитину з новими знаннями.",
                    "Заняття розвивають пам’ять, увагу та логіку."],
    "кубик":       ["Кубики розвивають дрібну моторику, логіку та уяву малюка.",
                    "Складання й сортування кубиків тренує координацію."],
    "фігурк":      ["Ігрові фігурки розвивають уяву та сюжетно-рольову гру.",
                    "Колекційні фігурки заохочують творчі сценарії гри."],
    "пісок":       ["Ігри з піском розвивають дрібну моторику та сенсорику.",
                    "Кінетичний пісок заспокоює й тренує пальчики."],
    "вод":         ["Іграшка для води робить купання веселим і безпечним.",
                    "Водні ігри розвивають сенсорику й координацію."],
    "спорт":       ["Спортивна іграшка заохочує активний рух і координацію.",
                    "Активна гра розвиває спритність і витривалість."],
    "самокат":     ["Самокат розвиває рівновагу, координацію та любов до руху.",
                    "Активні прогулянки на самокаті зміцнюють дитину."],
}
GENERIC_BENEFIT = ["Іграшка дарує дитині радість і години захопливої гри.",
                   "Якісна іграшка для цікавого й розвивального дозвілля."]
CLOSERS = ["Гарний вибір для розвитку та дозвілля.",
           "Підійде і для гри вдома, і в подарунок.",
           "Якісна іграшка для щоденних ігор дитини.",
           "Чудовий варіант для дитячого дозвілля та подарунка."]


def _pick(lst: list, sku: str, salt: str = "") -> str:
    idx = int(hashlib.sha256((str(sku) + salt).encode("utf-8")).hexdigest(), 16) % len(lst)
    return lst[idx]


def _benefit(category_name: str, sku: str) -> str:
    c = (category_name or "").lower()
    for key, variants in CATEGORY_BENEFIT.items():
        if key in c:
            return _pick(variants, sku, "b")
    return _pick(GENERIC_BENEFIT, sku, "b")


def _dimensions(params) -> str:
    d = {k: v for k, v in (params or [])}
    L = d.get("Довжина без упаковки (см)") or d.get("Довжина в упаковці (см)")
    W = d.get("Ширина без упаковки (см)") or d.get("Ширина в упаковці (см)")
    H = d.get("Висота без упаковки (см)") or d.get("Висота в упаковці (см)")
    return f"Розмір: {L}×{W}×{H} см." if (L and W and H) else ""


def _clean_name(name: str) -> str:
    return re.sub(r"\s+", " ", (name or "")).strip()


def build_seo(item: dict) -> dict:
    """Грамотний SEO-контент з реальних атрибутів. Повертає {seo_title, seo_meta_description,
    seo_long_html}. НАЗВУ не змінюємо (використовуємо як факт), тож пошук конкурентів
    (find_best_competitor за назвою) не зачіпається."""
    sku = str(item.get("id"))
    name = _clean_name(item.get("name"))
    category = (item.get("category_name") or "").strip()
    vendor = (item.get("vendor") or "").strip()
    country = (item.get("country") or "").strip()
    benefit = _benefit(category, sku)
    closer = _pick(CLOSERS, sku, "c")

    facts = []
    if vendor:
        facts.append(f"Виробник — {vendor}.")
    if country and country.lower() not in BANNED_COUNTRY:
        facts.append(f"Країна походження — {country}.")
    dims = _dimensions(item.get("params"))
    if dims:
        facts.append(dims)

    # Грамотно-безпечний каркас (усе в називному / лейблами; змінні не відмінюються).
    # html.escape ЛИШЕ для HTML-варіанту: назва/бренд Toysi може містити «<»/«&», що зламало б
    # розмітку сторінки. title/meta лишаються сирими (це plain-text поля; «&quot;» там — сміття).
    def _p(*texts):
        return "".join(f"<p>{html.escape(t)}</p>" for t in texts if t)
    body_bits = []
    if category:
        body_bits.append(f"Категорія: {category}.")
    body_bits.append(benefit)
    if facts:
        body_bits.append(" ".join(facts))
    body_bits.append(closer)
    long_html = _p(f"{name}.", " ".join(body_bits))

    title = name[:255]
    meta = f"{name} — {category}. {benefit}"[:155] if category else f"{name}. {benefit}"[:155]
    return {"seo_title": title, "seo_meta_description": meta, "seo_long_html": long_html}


def generate(limit: int = DEFAULT_LIMIT, sample_path: str = None) -> dict:
    """Генерує SEO для топ-SKU (select_top_items), пише в seo_content.db з approved=0 (гейт).
    ПРОПУСКАЄ: SKU, вже approved=1 (золотий пілот — не чіпаємо); SKU з тим самим source_hash
    (idempotent). Повертає статистику. sample_path — експорт «сире Toysi vs згенероване» на огляд."""
    catalog = fetch_toysi_catalog()
    if not catalog:
        print("[seo-gen] Каталог Toysi порожній — зупиняюсь.", file=sys.stderr)
        return {"generated": 0}
    top = select_top_items(catalog)

    # Спершу гарантуємо, що золотий пілот (approved=1) у БД — інакше, якщо генератор першим
    # наповнить свіжу БД, фідовий bootstrap (gated на count==0) назавжди пропустить пілот.
    db.load_approved_prom_overrides()

    existing = db.load_all_meta()   # {sku: {approved, source_hash}}
    stats = {"generated": 0, "skipped_approved": 0, "skipped_unchanged": 0, "errors": 0}
    samples = []
    for sku, item in top.items():
        sku = str(sku)
        if stats["generated"] >= limit:
            break
        cur = existing.get(sku)
        if cur and cur.get("approved"):
            stats["skipped_approved"] += 1
            continue
        try:
            src_hash = db.compute_source_hash(item)
            if cur and cur.get("source_hash") == src_hash and src_hash:
                stats["skipped_unchanged"] += 1
                continue
            seo = build_seo(item)
            db.upsert(sku, source_hash=src_hash, source=SEO_SOURCE, approved=0, **seo)
        except Exception as e:   # один битий товар не має валити весь прогін
            stats["errors"] += 1
            print(f"[seo-gen] SKU {sku} пропущено ({type(e).__name__}: {e})", file=sys.stderr)
            continue
        stats["generated"] += 1
        if sample_path and len(samples) < 30:
            raw = re.sub(r"<[^>]+>", " ", str(item.get("description", "")))[:200].strip()
            gen = re.sub(r"<[^>]+>", " ", seo["seo_long_html"]).strip()
            samples.append(f"SKU {sku} [{item.get('category_name','')}]\n  СИРЕ: {raw}\n  SEO:  {gen}\n")

    if sample_path and samples:
        Path(sample_path).write_text("\n".join(samples), encoding="utf-8")
        print(f"[seo-gen] Sample ({len(samples)}) → {sample_path}")
    print(f"[seo-gen] Згенеровано {stats['generated']} (approved=0, гейт), пропущено: "
          f"{stats['skipped_approved']} затверджених + {stats['skipped_unchanged']} без зміни.")
    return stats


def _pilot_skus() -> set:
    """SKU рукописного золотого пілота — шаблон їх НЕ чіпає (золото має пріоритет)."""
    try:
        data = json.loads(db.PILOT_BATCH.read_text(encoding="utf-8"))
        return {str(it.get("sku")) for it in data.get("items", []) if it.get("sku")}
    except (OSError, ValueError):
        return set()


def export_batch(limit: int, out_path: str, generated_at: str) -> int:
    """Генерує ЗАТВЕРДЖЕНИЙ committed batch-файл (meta.approved=true) для топ-`limit` SKU,
    ВИКЛЮЧАЮЧИ золотий пілот. Файл детермінований (items sorted by sku) → без git-churn поки
    каталог не змінився. Деплой — тим самим bootstrap-шляхом, що й пілот (без SSH). Викликати
    ЛИШЕ після style-approval власника. Повертає кількість items."""
    catalog = fetch_toysi_catalog()
    if not catalog:
        raise RuntimeError("Каталог Toysi порожній — експорт скасовано.")
    top = select_top_items(catalog)
    skip = _pilot_skus()
    items = []
    for sku, item in top.items():
        if len(items) >= limit:
            break
        sku = str(sku)
        if sku in skip:
            continue
        try:
            seo = build_seo(item)
        except Exception as e:
            print(f"[seo-gen] export: SKU {sku} пропущено ({type(e).__name__}: {e})", file=sys.stderr)
            continue
        items.append({"sku": sku, **seo})
    items.sort(key=lambda it: it["sku"])
    payload = {
        "meta": {"approved": True, "source": SEO_SOURCE, "generated_at": generated_at,
                 "note": "Фаза 2, шаблонний генератор. Затверджено власником (стиль) 2026-08-13. Виключає золотий пілот."},
        "items": items,
    }
    Path(out_path).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[seo-gen] Експортовано {len(items)} approved описів → {out_path}")
    return len(items)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--limit", type=int, default=DEFAULT_LIMIT, help=f"Скільки SKU за прогін (дефолт {DEFAULT_LIMIT}).")
    ap.add_argument("--sample", metavar="FILE", help="Експортувати «сире vs SEO» у файл на огляд власнику.")
    ap.add_argument("--export-batch", metavar="FILE",
                    help="Згенерувати ЗАТВЕРДЖЕНИЙ committed batch (approved=true) для деплою. Лише після style-approval.")
    ap.add_argument("--generated-at", default="", help="Дата для meta.generated_at (для детермінованого файлу).")
    args = ap.parse_args()
    if args.export_batch:
        export_batch(limit=args.limit, out_path=args.export_batch, generated_at=args.generated_at)
    else:
        generate(limit=args.limit, sample_path=args.sample)


if __name__ == "__main__":
    main()

"""
Точне SKU-рівня порівняння: скільки товарів з нашого топ-970 асортименту
Toysi (feeds/prom_feed_top.xml) реально є в каталозі RoyalToys.

Жоден з двох постачальників не дає штрих-код (EAN) у фіді (RoyalToys —
підтверджено: 0 тегів <barcode> у 18738 offer), а vendorCode обох
постачальників — це їхній ВЛАСНИЙ внутрішній номер товару (перевірено:
у Toysi vendorCode == внутрішній id, не збігається з жодним кодом
виробника), тож пряме зіставлення кодів між постачальниками неможливе.

Тому "SKU-рівня збіг" тут означає: для кожного товару Toysi шукаємо
конкретний відповідний товар у каталозі RoyalToys — той самий бренд +
достатньо схожа назва (а не просто "в цій категорії є хоч щось").
Це набагато точніше за оцінку по категоріях (звіт 2026-07-04), яка лише
рахувала, чи RoyalToys продає щось у тій самій товарній категорії.
"""
import os
import re
import sys
from collections import defaultdict

import xml.etree.ElementTree as ET

import royaltoys_parser as rp

TOYSI_TOP_FEED = "feeds/prom_feed_top.xml"
MATCH_THRESHOLD = 0.55

# Спільна папка PlutusToys_avtonomiya (Cowork і automation-сесія читають/пишуть
# той самий фізичний файл на цій Windows-машині) — той самий підхід, що й у
# TELEGRAM_OUTBOX_FILE у telegram_outbox_processor.py.
REPORT_OUTPUT_PATH = os.environ.get(
    "ROYALTOYS_SKU_REPORT_PATH",
    r"C:\Users\smach\Claude\Projects\PlutusToys_avtonomiya\royaltoys_comparison_2026-07-06_sku.md",
)

_STOPWORDS = {
    "для", "з", "та", "і", "в", "на", "від", "до", "по", "шт", "см", "мм",
    "набір", "набор", "игрушка", "іграшка", "игрушки", "іграшки",
}


def normalize_name(name: str) -> set:
    name = name.lower()
    name = re.sub(r"[«»\"'()\[\],.\-–—/]", " ", name)
    words = [w for w in name.split() if w and w not in _STOPWORDS]
    return set(words)


def normalize_vendor(vendor: str) -> str:
    return re.sub(r"[^a-zа-яіїєґ0-9]", "", vendor.lower())


def load_toysi_top(path: str):
    tree = ET.parse(path)
    root = tree.getroot()
    items = []
    for offer in root.findall(".//offer"):
        vendor = offer.findtext("vendor", "").strip()
        name = offer.findtext("name", "").strip()
        price = offer.findtext("price", "").strip()
        vendor_code = offer.findtext("vendorCode", "").strip()
        items.append({
            "vendor_code": vendor_code,
            "vendor": vendor,
            "name": name,
            "price": price,
        })
    return items


def name_similarity(a: set, b: set) -> float:
    if not a or not b:
        return 0.0
    inter = len(a & b)
    union = len(a | b)
    jaccard = inter / union if union else 0.0
    return jaccard


def best_match(toysi_item, royaltoys_by_vendor, seq_cache):
    vkey = normalize_vendor(toysi_item["vendor"])
    candidates = royaltoys_by_vendor.get(vkey)
    if not candidates:
        return None, 0.0, "no_vendor"

    toysi_words = normalize_name(toysi_item["name"])
    best_score = 0.0
    best_cand = None
    for cand in candidates:
        cand_words = seq_cache.get(cand["id"])
        if cand_words is None:
            # Toysi ("name") віддає назви українською (lang=ukr) — порівнюємо
            # з name_ua RoyalToys, а не з їхнім основним (російськомовним) name
            cand_words = normalize_name(cand["name_ua"] or cand["name"])
            seq_cache[cand["id"]] = cand_words
        score = name_similarity(toysi_words, cand_words)
        if score > best_score:
            best_score = score
            best_cand = cand

    if best_score < MATCH_THRESHOLD:
        return best_cand, best_score, "vendor_no_match"

    return best_cand, best_score, "matched"


def main():
    print("Завантаження топ-970 Toysi...", file=sys.stderr)
    try:
        toysi_items = load_toysi_top(TOYSI_TOP_FEED)
    except FileNotFoundError:
        print(
            f"Не знайдено {TOYSI_TOP_FEED} — цей файл згенерований і не в git "
            "(feeds/*.xml у .gitignore, живе лише в гілці feed-data). "
            "Спочатку запусти: python generate_prom_feed_top.py",
            file=sys.stderr,
        )
        return
    print(f"Toysi топ-асортимент: {len(toysi_items)} товарів", file=sys.stderr)

    print("Завантаження каталогу RoyalToys...", file=sys.stderr)
    rt_catalog = rp.fetch_royaltoys_catalog()
    if not rt_catalog:
        print("Не вдалося завантажити каталог RoyalToys — перевір ROYALTOYS_YML_URL", file=sys.stderr)
        return

    rt_by_vendor = defaultdict(list)
    for item in rt_catalog.values():
        vkey = normalize_vendor(item["vendor"])
        if vkey:
            rt_by_vendor[vkey].append(item)

    toysi_vendors = {normalize_vendor(i["vendor"]) for i in toysi_items if i["vendor"]}
    rt_vendors = set(rt_by_vendor.keys())
    vendors_present = toysi_vendors & rt_vendors
    vendors_missing = toysi_vendors - rt_vendors

    seq_cache = {}
    matched = []
    unmatched_no_vendor = []
    unmatched_vendor_no_name = []

    for item in toysi_items:
        cand, score, status = best_match(item, rt_by_vendor, seq_cache)
        if status == "matched":
            matched.append((item, cand, score))
        elif status == "no_vendor":
            unmatched_no_vendor.append(item)
        else:
            unmatched_vendor_no_name.append((item, cand, score))

    total = len(toysi_items)
    if total == 0:
        print(f"{TOYSI_TOP_FEED} порожній (0 товарів) — порівнювати нічого", file=sys.stderr)
        return
    n_matched = len(matched)
    n_no_vendor = len(unmatched_no_vendor)
    n_vendor_no_name = len(unmatched_vendor_no_name)

    by_vendor_stats = defaultdict(lambda: [0, 0])  # vendor -> [total, matched]
    for item in toysi_items:
        by_vendor_stats[item["vendor"] or "(без бренду)"][0] += 1
    for item, cand, score in matched:
        by_vendor_stats[item["vendor"] or "(без бренду)"][1] += 1

    lines = []
    lines.append("# RoyalToys vs Toysi — точне SKU-рівня порівняння (2026-07-06)")
    lines.append("")
    lines.append(
        "Цей звіт замінює попередню ОЦІНКУ за категоріями "
        "(`royaltoys_comparison_2026-07-04.md`, побудовану без доступу до прайсу "
        "RoyalToys, лише за публічними назвами категорій). Тепер є справжній "
        "прайс-фід RoyalToys (авторизований YML-експорт, 18 738 товарів), і "
        "порівняння зроблено товар-до-товару, а не категорія-до-категорії."
    )
    lines.append("")
    lines.append("## Методологія (і чому не пряме зіставлення кодів)")
    lines.append("")
    lines.append(
        "- **Жоден з двох постачальників не дає штрих-код (EAN)** у фіді. "
        "У RoyalToys перевірено: 0 тегів `<barcode>` серед 18 738 товарів."
    )
    lines.append(
        "- **`vendorCode` — це внутрішній номер кожного постачальника**, не код "
        "виробника: у Toysi vendorCode збігається з внутрішнім id товару, тому "
        "зіставляти коди Toysi з кодами RoyalToys напряму безглуздо — це два "
        "незалежні простори нумерації."
    )
    lines.append(
        "- Тому товар вважається **знайденим** у RoyalToys, якщо: (1) бренд "
        "(`vendor`) збігається (нормалізовано: без регістру/пробілів), і "
        "(2) схожість назви (Jaccard за словами, без стоп-слів) "
        f"≥ {MATCH_THRESHOLD:.2f}."
    )
    lines.append(
        "- Це набагато точніше за звіт 2026-07-04, де \"збіг\" означав лише "
        "\"RoyalToys продає щось у тій самій публічній категорії\" — там "
        "рахувалися категорії, а не конкретні товари."
    )
    lines.append("")
    lines.append("## Підсумкові цифри")
    lines.append("")
    lines.append(f"- Топ-асортимент Toysi (база порівняння): **{total}** товарів")
    lines.append(
        f"- **Знайдено точний відповідник у RoyalToys: {n_matched} "
        f"({n_matched/total*100:.1f}%)**"
    )
    lines.append(
        f"- Бренду немає в каталозі RoyalToys взагалі: {n_no_vendor} "
        f"({n_no_vendor/total*100:.1f}%)"
    )
    lines.append(
        f"- Бренд є в RoyalToys, але конкретного товару не знайдено "
        f"(схожість назви < {MATCH_THRESHOLD:.2f}): {n_vendor_no_name} "
        f"({n_vendor_no_name/total*100:.1f}%)"
    )
    lines.append("")
    lines.append(
        f"Брендів з топ-970, які присутні в RoyalToys: **{len(vendors_present)}** "
        f"з {len(toysi_vendors)} унікальних брендів."
    )
    lines.append("")
    lines.append("## Розбивка по брендах (топ-20 за обсягом у нашому асортименті)")
    lines.append("")
    lines.append("| Бренд | Товарів у топ-970 | Знайдено в RoyalToys | Покриття |")
    lines.append("|---|---|---|---|")
    for vendor, (tot, mat) in sorted(by_vendor_stats.items(), key=lambda x: -x[1][0])[:20]:
        pct = mat / tot * 100 if tot else 0
        lines.append(f"| {vendor} | {tot} | {mat} | {pct:.0f}% |")
    lines.append("")
    lines.append("## Бренди топ-970, яких взагалі немає в RoyalToys")
    lines.append("")
    missing_vendor_counts = sorted(
        ((v, by_vendor_stats[v][0]) for v in by_vendor_stats
         if normalize_vendor(v) in vendors_missing),
        key=lambda x: -x[1],
    )
    if missing_vendor_counts:
        lines.append("| Бренд | Товарів у топ-970 |")
        lines.append("|---|---|")
        for v, tot in missing_vendor_counts:
            lines.append(f"| {v} | {tot} |")
    else:
        lines.append("Немає — усі бренди топ-970 присутні в каталозі RoyalToys.")
    lines.append("")
    lines.append("## Приклади знайдених відповідників (перші 15)")
    lines.append("")
    lines.append("| Toysi: назва | RoyalToys: назва | Бренд | Схожість |")
    lines.append("|---|---|---|---|")
    for item, cand, score in matched[:15]:
        t_name = item["name"][:60].replace("|", "/")
        r_name = (cand["name_ua"] or cand["name"])[:60].replace("|", "/")
        lines.append(f"| {t_name} | {r_name} | {item['vendor']} | {score:.2f} |")
    lines.append("")
    lines.append(
        "## Висновок щодо резервного постачальника\n\n"
        f"З нашого поточного топ-970 асортименту Toysi RoyalToys **напряму "
        f"закриває {n_matched} товарів ({n_matched/total*100:.1f}%)** — це "
        "точна цифра на рівні конкретних товарів, а не оцінка по категоріях. "
        "Решта або належить брендам, яких RoyalToys не продає взагалі, або "
        "це той самий бренд, але інша конкретна модель/варіант."
    )

    report = "\n".join(lines)
    with open(REPORT_OUTPUT_PATH, "w", encoding="utf-8") as f:
        f.write(report)
    print(f"Звіт записано: {REPORT_OUTPUT_PATH}", file=sys.stderr)
    print(f"Matched: {n_matched}/{total} ({n_matched/total*100:.1f}%)", file=sys.stderr)


if __name__ == "__main__":
    main()

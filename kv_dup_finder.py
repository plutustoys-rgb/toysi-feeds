# -*- coding: utf-8 -*-
"""kv_dup_finder.py — консервативний пошук дублів KIDDISVIT (kv-) ↔ Toysi і kv- ↔ RoyalToys (запит SEO 17.11.2026 + рішення власника: «показувати KIDDISVIT», тінню стає RT; Toysi↔KIDDISVIT — чинне «варіант 1 резерв»: живу Toysi показуємо, kv — тінь).

Пише:
  kv_dup_map.json     {"kv-<id>": "<id товару Toysi>"}      → kv_supplier_export.load_dup_map → поле `dup_of` у supplier_kv.json (як `dup_of` в supplier_rt.json);
  rt_kv_dup_map.json  {"rt-<id>": "kv-<id>"}                → rt_supplier_export.load_kv_dup_map → поле `kv_dup_of` у supplier_rt.json (зворотне посилання для RT-«тіні» → kv).
Алгоритм — РІВНО той самий, що rt_dup_finder (IDF-зважений Жаккар над основами слів, жорсткі перевірки чисел/кольорів/модифікаторів, артикул-маршрут, «один і лише один кандидат», зворотна неоднозначність → нікому): тут kv-картка грає роль «rt-картки», а пулом кандидатів є Toysi, а далі RT.
Принцип: КРАЩЕ ПРОПУСТИТИ дубль, ніж позначити різні товари дублями (різна модель/колір/розмір/комплектація = НЕ дубль).
Додатковий ЗАХИСТ для пар kv↔RT (аудит назв надто «широкий» на серіях Pop Top/Dinoster: на живих даних 17.11 8 із 337 пар мали різні серії): якщо в kv-елементі є `rrp` (РРЦ), пара приймається лише коли ціна RT збігається з РРЦ KIDDISVIT (RT продає за РРЦ KIDDISVIT: 91 % пар, ±1,5 ₴) АБО габарити упаковки збігаються (±3 см). Нема rrp у файлі → без цього захисту.
Джерело kv-списку: --kv-json (за замовчуванням supplier_kv.json на VPS) або будь-який JSON {"kv-<id>"|"<id>": {name, vendor, vendor_code}} (назви/бренд/артикул публічні, грошей не треба).
Запуск:  python kv_dup_finder.py [--kv-json шлях] [--dry]    (тягне живі каталоги Toysi і RT; нема/усічений каталог → файли не чіпає)"""
from __future__ import annotations

import os
os.environ.setdefault("AUDIT_NO_TELEGRAM", "1")
import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

import rt_dup_finder as rdf

HERE = Path(__file__).resolve().parent
KV_OUT = HERE / "kv_dup_map.json"
RT_OUT = HERE / "rt_kv_dup_map.json"
_PID = re.compile(r"(?:kv-)?([0-9]+)")


def load_kv_items(path: Path) -> dict:
    """{pid: {name_ua, name, vendor, vendor_code}} із JSON (формат supplier_kv.json {"items": {...}} або плоский {id: {...}})."""
    raw = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    items = raw.get("items") if isinstance(raw, dict) and isinstance(raw.get("items"), dict) else raw
    out = {}
    for k, v in (items or {}).items():
        m = _PID.fullmatch(str(k))
        if not m or not isinstance(v, dict) or str(k).startswith("kvu-") or not (v.get("name") or v.get("name_ua")):
            continue
        out[m.group(1)] = {"name_ua": v.get("name_ua") or v["name"], "name": v.get("name") or v["name_ua"], "vendor": v.get("vendor") or "", "vendor_code": str(v.get("vendor_code") or v.get("sku") or ""),
                           "rrp": v.get("rrp"), "dims_cm": v.get("dims_cm")}
    return out


def rt_pool(rt: dict) -> dict:
    """Каталог RT у форматі «Toysi-подібного» пулу для Matcher: назва — українська (name_ua), id — без префікса."""
    pool = {}
    for rid, v in rt.items():
        nm = v.get("name_ua") or v.get("name")
        if nm:
            pool[str(rid)] = {**v, "id": str(rid), "name": nm}
    return pool


def _num(x):
    try:
        f = float(str(x).replace(",", "."))
    except (TypeError, ValueError):
        return None
    return f if f > 0 else None


def rt_oracle_ok(kv_item: dict, rt_item: dict) -> bool:
    """Незалежна від назви перевірка пари kv↔RT: ціна RT ≈ РРЦ KIDDISVIT (±1,5 ₴) або габарити збігаються (±3 см). Нема РРЦ у kv-елементі → True (захист вимкнено)."""
    rrp = _num(kv_item.get("rrp"))
    if rrp is None:
        return True
    p = _num(rt_item.get("price"))
    if p is not None and abs(p - rrp) <= 1.5:
        return True
    kd, rd = kv_item.get("dims_cm"), rt_item.get("dims_cm")
    if isinstance(rd, str):
        try:
            rd = json.loads(rd)
        except ValueError:
            rd = None
    try:
        kd, rd = sorted(float(x) for x in kd), sorted(float(x) for x in rd)
    except (TypeError, ValueError):
        return False
    return len(kd) == len(rd) == 3 and all(abs(a - b) <= 3.0 for a, b in zip(kd, rd))


def find_kv_dups(kv: dict, toysi: dict, rt: dict):
    """(kv→Toysi {sid: tid}, kv→RT {sid: rid}). Кожен товар пулу віддається найбільше одній kv-картці (інакше нікому)."""
    sids = [f"kv-{pid}" for pid in sorted(kv, key=int)]
    d_t, _ev, _m = rdf.find_dups(kv, toysi, sids)
    pool = rt_pool(rt)
    d_r, _ev2, _m2 = rdf.find_dups(kv, pool, sids)
    return {s: v[0] for s, v in d_t.items()}, {s: v[0] for s, v in d_r.items() if rt_oracle_ok(kv[s[3:]], pool[str(v[0])])}


def build_maps(kv: dict, toysi: dict, rt: dict):
    """(kv_dup_map {"kv-<id>": "<toysi id>"}, rt_kv_dup_map {"rt-<id>": "kv-<id>"}). Якщо kv-картка збігається і з Toysi, і з RT — обидва зв'язки лишаються (рішення, яку показувати, — на сайті за наявністю)."""
    kt, kr = find_kv_dups(kv, toysi, rt)
    kv_map = {k: str(v) for k, v in sorted(kt.items())}
    rt_map = {f"rt-{rid}": sid for sid, rid in sorted(kr.items())}
    return kv_map, rt_map


def _write(path: Path, data: dict) -> None:
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=0), encoding="utf-8")
    tmp.replace(path)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--kv-json", default=str(HERE / "supplier_kv.json"))
    ap.add_argument("--dry", action="store_true", help="не писати файли")
    args = ap.parse_args(argv)
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass
    import royaltoys_parser as rp
    from parser import fetch_toysi_catalog
    kv = load_kv_items(Path(args.kv_json))
    toysi, rt = fetch_toysi_catalog(), rp.fetch_royaltoys_catalog()
    if len(kv) < 500 or len(toysi) < 25_000 or len(rt) < 5_000:
        print(f"[kv_dup] каталог KIDDISVIT/Toysi/RT порожній або усічений ({len(kv)}/{len(toysi)}/{len(rt)}) — файли не чіпаю", file=sys.stderr)
        return 1
    kv_map, rt_map = build_maps(kv, toysi, rt)
    print(f"[kv_dup] kv-карток: {len(kv)}; дублів Toysi: {len(kv_map)}; дублів RT: {len(rt_map)}")
    if not args.dry:
        _write(KV_OUT, kv_map)
        _write(RT_OUT, rt_map)
        print(f"[kv_dup] записано {KV_OUT.name}, {RT_OUT.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

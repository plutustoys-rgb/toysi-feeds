# -*- coding: utf-8 -*-
"""kandydaty_backfill_fee.py — разовий backfill числового поля `fee` у ВІДКРИТИХ записах реєстру кандидатів КОДВ.

ЧОМУ: PR #621 додав поле `fee` (комісія NovaPay / еквайринг RozetkaPay числом), але старі open-записи автоматично не
донаповнюються — sync NovaPay бачить лише нові ТТН (запит головного бухгалтера 2026-10-06, напр. `novapay_registry:20451539150728`,
168 ₴ з summary «винагорода НП 0» без копійок). Скрипт бере дані з АРХІВНИХ реєстрів (локально, без мережі) і дописує `fee`
(та для NovaPay — перебудовує summary тим самим compact_summary). Нічого іншого в записі не чіпає.

ЗАПУСК: python kandydaty_backfill_fee.py            # dry-run: лише показує, що змінилось би
        python kandydaty_backfill_fee.py --apply    # записує в _vidkryti_kandydaty.json
"""
import argparse
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import kandydaty_registry


def novapay_fees() -> dict:
    """{ттн: {fee, row}} з УСІХ архівних реєстрів NovaPay."""
    import novapay_registry_kandydaty as npk
    out = {}
    for r in npk._rows_from(npk._all_registries()):
        ttn = str(r.get("ttn") or "").strip()
        if ttn and r.get("commission") is not None:
            out[ttn] = r
    return out


def rozetkapay_fees() -> dict:
    """{'order:acquiring'|'order:storno': fee} з усіх архівних реєстрів RozetkaPay (як у sync_registry: acquiring = |комісія|,
    storno = повернена комісія)."""
    import glob
    import os
    from pathlib import Path
    import rozetkapay_registry_kandydaty as rpk
    out = {}
    for f in glob.glob(str(rpk.DOCS_DIR / "*" / "RozetkaPay" / "*.xlsx")):
        if os.path.basename(f).startswith("~$"):
            continue
        try:
            rows = rpk._parse_registry(Path(f))
        except Exception as e:  # noqa: BLE001
            print(f"[BackfillFee] {os.path.basename(f)}: не прочитано ({e})", file=sys.stderr)
            continue
        for r in rows:
            c = r.get("commission")
            if not isinstance(c, (int, float)) or not r.get("order_id"):
                continue
            kind = "storno" if rpk._is_storno(r) else "acquiring"
            out[f"{r['order_id']}:{kind}"] = abs(c)
    return out


def plan(reg: dict, np_rows: dict, rp_fees: dict) -> list:
    """[(full_key, new_fee, new_summary|None)] для open-записів без `fee` (або без неї у NovaPay-summary)."""
    changes = []
    for full_key, e in reg.items():
        if e.get("status") != "open" or e.get("fee") is not None:
            continue
        src, key = e.get("source"), str(e.get("key"))
        if src == "novapay_registry" and key in np_rows:
            r = np_rows[key]
            import novapay_registry_kandydaty as npk
            plat, order = npk._bare_order(r.get("internal_order_id"))
            summary = kandydaty_registry.compact_summary([
                f"COD НЕ в книзі, ТТН {key}", f"прийнято {r.get('amount_received')}",
                f"винагорода НП {r.get('commission')}",
                f"зараховано {r.get('amount_net')}" if r.get("amount_net") is not None else None,
                f"зам. {order} ({plat})", f"дата {r.get('date')}"])
            changes.append((full_key, float(r["commission"]), summary))
        elif src == "rozetkapay_registry" and key in rp_fees:
            changes.append((full_key, float(rp_fees[key]), None))
    return changes


def main(apply: bool = False) -> int:
    reg = kandydaty_registry._load_registry()
    changes = plan(reg, novapay_fees(), rozetkapay_fees())
    for full_key, fee, summary in changes:
        print(f"[BackfillFee] {full_key}: fee={fee}" + (f"; summary → {summary}" if summary else ""))
    open_no_fee = [k for k, e in reg.items() if e.get("status") == "open" and e.get("fee") is None
                   and e.get("source") in ("novapay_registry", "rozetkapay_registry")]
    left = [k for k in open_no_fee if k not in {c[0] for c in changes}]
    print(f"[BackfillFee] open без fee (NovaPay/RozetkaPay): {len(open_no_fee)}; буде заповнено: {len(changes)}; "
          f"не знайдено в архівах: {len(left)} {left}")
    if apply and changes:
        for full_key, fee, summary in changes:
            reg[full_key]["fee"] = fee
            if summary:
                reg[full_key]["summary"] = summary
        kandydaty_registry._save_registry(reg)
        print("[BackfillFee] записано.")
    elif changes:
        print("[BackfillFee] dry-run: нічого не записано (--apply — записати).")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    sys.exit(main(ap.parse_args().apply))

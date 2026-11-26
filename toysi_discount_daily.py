# -*- coding: utf-8 -*-
"""toysi_discount_daily.py — щоденний знімок ПЕРСОНАЛЬНИХ цін Toysi з кабінету і доставка на VPS (слово власника 26.11.2026, «п.2»).

Що: toysi_discount_scan.scan() (збережена сесія Toysi ЦЬОГО ПК, headless, повільно) -> перевірка повноти -> атомарне копіювання на VPS
/opt/plutustoys/toysi_discounts.json (scp у .tmp, потім mv) -> competitor_pricing.scanned_discount_pct читає (код-sync VPS 15 хв).
Чому на ПК, а не на VPS: сесія Toysi (storageState) лише тут; переносити її на VPS не потрібно. Файл діє TOYSI_SCAN_MAX_AGE_DAYS=3 доби —
вимкнений на кілька днів ноутбук не ламає ціни (правило-фолбек), health_check бачить LastTaskResult != 0.
Нічого не купує/не міняє на Toysi. Файл містить закупівельні ціни — у git не потрапляє (.gitignore).
Запуск: python toysi_discount_daily.py [--no-upload] [--delay 2.5]   Код виходу 0 = завантажено; 1 = збій (повний обхід не вдався/VPS недоступний)."""
import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent
OUT = BASE / "toysi_discounts.json"
TMP_OUT = BASE / "toysi_discounts.new.json"
MAX_ZERO_SHARE = 0.20        # 25.11: 1 002/24 304 = 4 %
MIN_VS_PREVIOUS = 0.85       # скан не менший за 85 % попереднього
MIN_ITEMS = 15000            # повний обхід 25.11 = 24 304; менше -> обхід урізаний, не публікуємо
VPS = "root@45.94.157.4"
KEY = str(Path.home() / ".ssh" / "plutustoys_vps")
REMOTE = "/opt/plutustoys/toysi_discounts.json"


def upload(src: Path) -> None:
    ssh = ["-i", KEY, "-o", "BatchMode=yes", "-o", "ConnectTimeout=20"]
    subprocess.run(["scp", *ssh, str(src), f"{VPS}:{REMOTE}.tmp"], check=True, timeout=300)
    subprocess.run(["ssh", *ssh, VPS, f"chmod 600 {REMOTE}.tmp && mv {REMOTE}.tmp {REMOTE}"], check=True, timeout=60)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-upload", action="store_true")
    ap.add_argument("--delay", type=float, default=2.5)
    ap.add_argument("--upload-existing", action="store_true", help="не сканувати, лише надіслати наявний toysi_discounts.json")
    a = ap.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if a.upload_existing:
        src = OUT
    else:
        import toysi_discount_scan
        toysi_discount_scan.scan(a.delay, 0, TMP_OUT)
        src = TMP_OUT
    items = json.loads(src.read_text(encoding="utf-8"))["items"]
    n = len(items)
    if n < MIN_ITEMS:
        print(f"[toysi_daily] ЗБІЙ: у скані {n} < {MIN_ITEMS} товарів — обхід урізаний, на VPS НЕ відправляю", file=sys.stderr)
        return 1
    zero = sum(1 for v in items.values() if v.get("price") == v.get("base"))
    if zero / n > MAX_ZERO_SHARE:  # зміна розмітки (одна ціна замість двох) дала б disc=0 скрізь -> ціни вгору по всьому каталогу
        print(f"[toysi_daily] ЗБІЙ: частка товарів без знижки {zero}/{n} = {zero / n:.0%} > {MAX_ZERO_SHARE:.0%} — схоже на зміну розмітки, НЕ відправляю", file=sys.stderr)
        return 1
    if not a.upload_existing and OUT.exists():
        try:
            prev_n = len(json.loads(OUT.read_text(encoding="utf-8"))["items"])
        except (OSError, ValueError, KeyError, TypeError):
            prev_n = 0
        if prev_n and n < prev_n * MIN_VS_PREVIOUS:
            print(f"[toysi_daily] ЗБІЙ: у скані {n} товарів проти {prev_n} попереднього (< {MIN_VS_PREVIOUS:.0%}) — НЕ відправляю", file=sys.stderr)
            return 1
    if not a.upload_existing:
        os.replace(TMP_OUT, OUT)
        src = OUT
    if a.no_upload:
        print(f"[toysi_daily] готово локально: {n} товарів (без відправки)")
        return 0
    upload(src)
    print(f"[toysi_daily] ОК: {n} товарів -> {VPS}:{REMOTE}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

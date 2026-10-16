# -*- coding: utf-8 -*-
"""gmc_account_settings.py — знімок НАЛАШТУВАНЬ акаунта Google Merchant Center через Merchant API (лише читання; запит SEO 16.10.2026: «хто моніторить GMC і чи всі налаштування зроблені»).

Той самий сервіс-акаунт і ключ, що в gmc_status_report (scope content, доступ «лише читання» достатній). Збирає: сам акаунт, programs (які програми ввімкнені: free listings / shopping ads …), shippingSettings, onlineReturnPolicies,
automaticImprovements («Автоматичні оновлення товарів»), homepage, businessInfo, список джерел даних (dataSources: id, мова, країна, тип). Кожен ендпоінт окремо: збій одного не скасовує інших; у файлі видно код відповіді.
ВИХІД: reports/gmc/gmc_account_settings_<дата>.json (приватна тека) + `diff_settings(старий, новий)` для щоденного прапорця «налаштування змінились».
CLI: python gmc_account_settings.py   # зібрати і записати"""
import json
import os
import sys
from datetime import datetime
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = Path(__file__).resolve().parent
ACCOUNT_ID = os.environ.get("GMC_ACCOUNT_ID", "5824264949")
OUT_DIR = Path(os.environ.get("GMC_REPORT_DIR", str(HERE / "reports" / "gmc")))
BASE = "https://merchantapi.googleapis.com"
ENDPOINTS = {
    "account": f"/accounts/v1/accounts/{{id}}",
    "programs": f"/accounts/v1/accounts/{{id}}/programs",
    "shippingSettings": f"/accounts/v1/accounts/{{id}}/shippingSettings",
    "onlineReturnPolicies": f"/accounts/v1/accounts/{{id}}/onlineReturnPolicies",
    "automaticImprovements": f"/accounts/v1/accounts/{{id}}/automaticImprovements",
    "homepage": f"/accounts/v1/accounts/{{id}}/homepage",
    "businessInfo": f"/accounts/v1/accounts/{{id}}/businessInfo",
    "dataSources": f"/datasources/v1/accounts/{{id}}/dataSources",
}


def fetch_all(session, account_id: str = ACCOUNT_ID) -> dict:
    """{ім'я: {"status": код, "data": відповідь | "error": текст}} для кожного ендпоінта (без винятків назовні)."""
    out = {}
    for name, path in ENDPOINTS.items():
        url = BASE + path.format(id=account_id)
        try:
            r = session.get(url, timeout=60)
            code = getattr(r, "status_code", 200)
            try:
                body = r.json()
            except Exception:  # noqa: BLE001
                body = {}
            if code >= 400:
                msg = (body.get("error") or {}).get("message", "") if isinstance(body, dict) else ""
                out[name] = {"status": code, "error": str(msg)[:300]}
            else:
                out[name] = {"status": code, "data": body}
        except Exception as e:  # noqa: BLE001
            out[name] = {"status": 0, "error": f"{type(e).__name__}: {str(e)[:150]}"}
    return out


def diff_settings(old: dict, new: dict) -> list:
    """Список змін між двома знімками (лише успішні розділи): «розділ: змінився / з'явився / зник»."""
    out = []
    for name in sorted(set(old) | set(new)):
        o, n = (old.get(name) or {}), (new.get(name) or {})
        od, nd = o.get("data"), n.get("data")
        if od is None and nd is None:
            continue
        if od is None:
            out.append(f"{name}: з'явився")
        elif nd is None:
            out.append(f"{name}: перестав читатися (код {n.get('status')})")
        elif od != nd:
            out.append(f"{name}: змінився")
    return out


def main() -> int:
    sys.path.insert(0, str(HERE))
    import gmc_status_report as g
    if not g.KEY_FILE.exists():
        print(f"[gmc_account_settings] нема ключа {g.KEY_FILE}")
        return 0
    res = fetch_all(g.make_session())
    if not any(v.get("status") == 200 for v in res.values()):
        print("[gmc_account_settings] жоден розділ не прочитано (мережа/доступ): знімок не записано, щоб не отруїти порівняння", file=sys.stderr)
        return 0
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    today = f"{datetime.now():%Y-%m-%d}"
    path = OUT_DIR / f"gmc_account_settings_{today}.json"
    prevs = sorted(p for p in OUT_DIR.glob("gmc_account_settings_*.json") if p.stem.replace("gmc_account_settings_", "") < today)
    changes = []
    if prevs:
        try:
            changes = diff_settings(json.loads(prevs[-1].read_text(encoding="utf-8")).get("sections", {}), res)
        except (OSError, ValueError):
            changes = []
        if changes:
            (OUT_DIR / f"gmc_settings_changes_{today}.txt").write_text(f"Зміни налаштувань GMC проти {prevs[-1].stem.replace('gmc_account_settings_', '')}:\n" + "\n".join("  " + c for c in changes) + "\n", encoding="utf-8")
    path.write_text(json.dumps({"account_id": ACCOUNT_ID, "at": datetime.now().isoformat(timespec="seconds"), "sections": res}, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"[gmc_account_settings] {path.name}: " + ", ".join(f"{k}={v['status']}" for k, v in res.items()))
    return 0


if __name__ == "__main__":
    sys.exit(main())

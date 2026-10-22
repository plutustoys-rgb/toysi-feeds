#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_control_panel_role_env.py — панель виставляє PLUTUS_ROLE у середовищі дочірньої сесії (22.10: детекція ролі за текстом
Shopify-code не впоралась; панель ЗНАЄ, кого піднімає, тож роль задається напряму).

ІНВАРІАНТИ: для кожного агента панелі, що має роль recall.py, _role_env дає PLUTUS_ROLE=<роль з _CONFIGS>; невідомий агент —
без PLUTUS_ROLE; власне os.environ панелі НЕ змінюється (змінна лише в копії для дитини); усі ролі з _ROLE_ENV існують у recall._CONFIGS.
"""
import os
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
import control_panel as cp
import recall

F = []


def chk(name, cond):
    print(f"[{'OK ' if cond else 'FAIL'}] {name}")
    if not cond:
        F.append(name)


os.environ.pop("PLUTUS_ROLE", None)
chk("усі ролі панелі існують у recall._CONFIGS", all(r in recall._CONFIGS for r in cp._ROLE_ENV.values()))
for name, role in cp._ROLE_ENV.items():
    chk(f"{name} -> PLUTUS_ROLE={role}", cp._role_env({"name": name}).get("PLUTUS_ROLE") == role)
chk("невідомий агент — без PLUTUS_ROLE", "PLUTUS_ROLE" not in cp._role_env({"name": "Хтось"}))
chk("os.environ панелі не змінено", "PLUTUS_ROLE" not in os.environ)
os.environ["PLUTUS_ROLE"] = "seo"
chk("успадкований чужий PLUTUS_ROLE у невідомого агента лишається як є (не перезаписуємо мовчки)",
    cp._role_env({"name": "Хтось"}).get("PLUTUS_ROLE") == "seo")
chk("а для відомого агента ПЕРЕЗАПИСУЄТЬСЯ його ролью", cp._role_env({"name": "Код"}).get("PLUTUS_ROLE") == "kod")
del os.environ["PLUTUS_ROLE"]

sys.exit(1 if F else 0)

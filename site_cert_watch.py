#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""site_cert_watch.py — щоденний сторож TLS-сертифіката plutustoys.com.ua (VPS, systemd-таймер site-cert-watch.timer, 04:17).

НАВІЩО (18.10.2026, власник: «якщо є можливість контролювати автоматично?» — перед підняттям HSTS до року): Let's Encrypt видає сертифікат на 90 днів, звичайне оновлення certbot робить за ≤30 днів до кінця.
Якщо оновлення тихо зламається (таймер не стартує, Apache-правка закрила /.well-known, нема місця), протермінований сертифікат при HSTS = відвідувачі не відкриють сайт зовсім (клацнути «все одно відкрити» браузер не дає).
Цей скрипт НЕ залежить від того, чи «прокинеться» якийсь агент: крутиться на сервері сам.
  1. Читає, скільки днів лишилось у сертифіката, який реально бачать відвідувачі (HTTPS-з'єднання з доменом, з перевіркою ланцюга).
  2. Лишилось ≥ RENEW_BELOW_DAYS (60) → усе гаразд, нічого не робить (тиша).
  3. Менше → САМ оновлює ТІЛЬКИ наш сертифікат (`certbot renew --cert-name <домен> --force-renewal`; інші сертифікати сервера не чіпає; deploy-hook у конфігу оновлення робить apachectl graceful) й перечитує.
  4. Після спроби лишилось < WARN_DAYS (30) → тротлений Telegram-сигнал ⚠️; < CRIT_DAYS (14), протермінований або сайт по HTTPS недоступний → 🚨.
Запас: оновлення за 60 днів до кінця = щонайменше 60 днів на виправлення, якщо щось зламалось; сигнал ще за 30.
Код виходу: 0 — усе гаразд/самовилікувалось; 1 — оновити не вдалось (лишилось < RENEW_BELOW_DAYS); 2 — HTTPS недоступний/сертифікат протермінований."""
import os
import socket
import ssl
import subprocess
import sys
import time

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

DOMAIN = os.environ.get("SITE_CERT_DOMAIN", "plutustoys.com.ua")
HOST = os.environ.get("SITE_CERT_HOST", DOMAIN)
RENEW_BELOW_DAYS = 60
WARN_DAYS = 30
CRIT_DAYS = 14
CERTBOT = os.environ.get("CERTBOT_BIN", "certbot")
ALERT_COOLDOWN_SEC = 12 * 3600


def days_left(host=None, port=443, now=None):
    """Скільки діб лишилось у сертифіката, який віддає сервер (з перевіркою ланцюга й імені). Протермінований → -1.0; нема з'єднання/інша TLS-помилка → None."""
    host = host or HOST
    try:
        ctx = ssl.create_default_context()
        with socket.create_connection((host, port), timeout=15) as sock:
            with ctx.wrap_socket(sock, server_hostname=host) as s:
                cert = s.getpeercert()
        end = ssl.cert_time_to_seconds(cert["notAfter"])
        return (end - (now if now is not None else time.time())) / 86400.0
    except ssl.SSLCertVerificationError as e:
        if "expired" in str(e).lower():
            return -1.0
        return None
    except (OSError, ssl.SSLError, KeyError, ValueError):
        return None


def renew_cmd():
    """Команда оновлення ЛИШЕ нашого сертифіката (--cert-name), примусово (скоро-кінець)."""
    return [CERTBOT, "renew", "--cert-name", DOMAIN, "--force-renewal", "--non-interactive"]


def renew():
    try:
        r = subprocess.run(renew_cmd(), capture_output=True, text=True, timeout=600)
    except (OSError, subprocess.SubprocessError) as e:
        print(f"[cert-watch] certbot не запустився: {type(e).__name__}: {e}", file=sys.stderr)
        return False
    if r.returncode != 0:
        print(f"[cert-watch] certbot завершився з кодом {r.returncode}: {(r.stderr or r.stdout)[-400:]}", file=sys.stderr)
    return r.returncode == 0


def alert(key, text):
    try:
        from telegram_notify import send_throttled_alert
        send_throttled_alert(key, text, cooldown_sec=ALERT_COOLDOWN_SEC)
    except Exception as e:  # noqa: BLE001 — сигнал best-effort, не валить сторож
        print(f"[cert-watch] сигнал не надіслано: {type(e).__name__}", file=sys.stderr)


def main():
    d = days_left()
    if d is None:
        alert("site_cert_unreachable", f"🚨 site-cert-watch: не вдалося перевірити HTTPS-сертифікат {HOST} (нема з'єднання на :443 або ланцюг не проходить перевірку). Сайт для відвідувачів може бути недоступний — перевір Apache/DNS/certbot.")
        print(f"[cert-watch] {HOST}: HTTPS недоступний або сертифікат не проходить перевірку", file=sys.stderr)
        return 2
    if d >= RENEW_BELOW_DAYS:
        print(f"[cert-watch] {HOST}: сертифікат діє ще {d:.0f} дн. — гаразд")
        return 0
    print(f"[cert-watch] {HOST}: лишилось {d:.1f} дн. (< {RENEW_BELOW_DAYS}) — оновлюю {DOMAIN}")
    ok = renew()
    after = days_left()
    if after is not None and after >= RENEW_BELOW_DAYS:
        print(f"[cert-watch] оновлено: тепер діє ще {after:.0f} дн.")
        return 0
    left = after if after is not None else d
    print(f"[cert-watch] оновлення не допомогло (certbot ok={ok}); лишилось {left:.1f} дн.", file=sys.stderr)
    if left < 0 or left < CRIT_DAYS:
        alert("site_cert_crit", f"🚨 site-cert-watch: сертифікат {HOST} {'ПРОТЕРМІНОВАНИЙ' if left < 0 else f'закінчується через {left:.0f} дн.'}, автооновлення не спрацювало. Зайди на VPS: journalctl -u site-cert-watch, /var/log/letsencrypt/letsencrypt.log.")
        return 2 if left < 0 else 1
    if left < WARN_DAYS:
        alert("site_cert_warn", f"⚠️ site-cert-watch: сертифікат {HOST} закінчується через {left:.0f} дн., автооновлення не спрацювало. Перевір certbot (journalctl -u site-cert-watch).")
    return 1


if __name__ == "__main__":
    sys.exit(main())

#!/bin/bash
# deploy_systemd_units.sh — синхронізує systemd-юніти PlutusToys з репо в /etc/systemd/system:
# копіює нові/змінені *.service/*.timer з кореня репо, daemon-reload, вмикає нові таймери, і
# ЗНІМАЄ юніти, що ЗНИКЛИ з репо (міграція — напр. коли social-poster.{service,timer} замінили
# на розділені fb/ig). Керує ЛИШЕ юнітами, які САМ встановив (список у .deployed_units) плюс
# наявними в репо — чужі/системні юніти НІКОЛИ не чіпає. Викликається з vps_code_sync.sh ПІСЛЯ
# успішного git merge. Потребує root (інакше м'яко пропускає — лишає ручний деплой).
#
# БЕЗПЕКА: інкрементальний ризик низький — аудит-гейтований репо ВЖЕ виконує довільний код як
# root через таймери; реальний контроль — обов'язковий незалежний аудит перед мержем. Тут ще
# захист від масового зняття (порожній набір репо → аборт) і від чужих юнітів (лише .deployed_
# units + корінь репо). Env-оверрайди PLUTUS_* — виключно для тестів.
set -u
REPO="${PLUTUS_REPO:-/opt/plutustoys}"
DEST="${PLUTUS_SYSTEMD_DIR:-/etc/systemd/system}"
STATE="$REPO/.deployed_units"
SYSTEMCTL="${PLUTUS_SYSTEMCTL:-systemctl}"

if [ "$(id -u)" != 0 ] && [ -z "${PLUTUS_ALLOW_NONROOT:-}" ]; then
    echo "[units] не root — пропускаю застосування юнітів (код синхронізовано; юніти — вручну)."
    exit 0
fi

desired="$(cd "$REPO" 2>/dev/null && ls -1 ./*.service ./*.timer 2>/dev/null | sed 's#^\./##' | sort -u)"

# Захист від масового зняття: якщо в репо раптом НУЛЬ юнітів (збій glob/checkout) — не чіпаємо нічого.
if [ -z "$desired" ]; then
    echo "[units] у репо не знайдено жодного юніта — пропускаю (захист від масового зняття)."
    exit 0
fi

changed=0
# 1) копіюємо нові/змінені
for u in $desired; do
    if ! cmp -s "$REPO/$u" "$DEST/$u" 2>/dev/null; then
        if cp "$REPO/$u" "$DEST/$u"; then
            echo "[units] оновлено $u"; changed=1
        else
            echo "[units] ПОПЕРЕДЖЕННЯ: не вдалось скопіювати $u — лишаю стару версію." >&2
        fi
    fi
done

# 2) знімаємо юніти, які МИ ставили (є в .deployed_units), але яких уже нема в репо
if [ -f "$STATE" ]; then
    while IFS= read -r old; do
        [ -z "$old" ] && continue
        if ! printf '%s\n' $desired | grep -qxF "$old"; then
            if [ -f "$DEST/$old" ]; then
                case "$old" in *.timer) "$SYSTEMCTL" disable --now "$old" >/dev/null 2>&1 ;; esac
                rm -f "$DEST/$old" && { echo "[units] знято (зник з репо) $old"; changed=1; }
            fi
        fi
    done < "$STATE"
fi

if [ "$changed" = 1 ]; then
    "$SYSTEMCTL" daemon-reload
    # 3) вмикаємо всі таймери репо (ідемпотентно; сервіси активують самі таймери)
    for u in $desired; do
        case "$u" in *.timer) "$SYSTEMCTL" enable --now "$u" >/dev/null 2>&1 && echo "[units] enable $u" ;; esac
    done
fi

# 4) фіксуємо поточний набір як стан (для майбутнього виявлення зниклих)
printf '%s\n' $desired > "$STATE"
echo "[units] синхронізовано юнітів: $(printf '%s\n' $desired | grep -c .) (змін цього прогону: $changed)."

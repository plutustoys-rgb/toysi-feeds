#!/usr/bin/env bash
# PreToolUse(Bash) guard.
# ДВІ функції:
#  1) Інтерактив: блокує `gh pr merge` у репо toysi-feeds, поки нема маркера пройденого аудиту
#     (<repo-root>/.audit_ok). Правило власника (2026-08-17): мержити ЛИШЕ після аудиту, завжди.
#  2) Headless (env PLUTUS_AGENT_HEADLESS=1 — сесія монітора agent_watch, автономний Код із Bash):
#     блокує БУДЬ-ЯКИЙ спосіб змержити/запушити в master (не лише `gh pr merge`, а й REST-merge через
#     `gh api .../merge` та `git push ... master/main`) — незалежно від .audit_ok. Автономний Код лише
#     ГОТУЄ PR; аудит+мерж робить інтерактивна сесія/власник (загартовано за аудитом PR #353, 2026-08-21).
# jq тут НЕМА → парсимо python.
in=$(cat)
# Дешевий фаст-пас: якщо у вводі нема жодного ризик-слова — одразу пропускаємо (без python).
case "$in" in
  *merge*|*push*) : ;;
  *) exit 0 ;;
esac
# Точний розбір команди (jq відсутній, python є) + нормалізація пробілів (проти `gh  pr  merge`).
cmd=$(printf '%s' "$in" | python -c "import sys,json; print(json.load(sys.stdin).get('tool_input',{}).get('command',''))" 2>/dev/null)
norm=$(printf '%s' "$cmd" | tr -s ' \t' ' ')

# (2) HEADLESS — широкий блок мержу/пушу-в-master для автономної монітор-сесії.
if [ -n "$PLUTUS_AGENT_HEADLESS" ]; then
  case "$norm" in
    *"gh pr merge"* | *"gh api"*merge* | *"git push"*master* | *"git push"*main* | *"pulls/"*"/merge"*)
      printf '%s' '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"deny","permissionDecisionReason":"МЕРЖ/ПУШ У MASTER ЗАБЛОКОВАНО: це headless-сесія монітора (agent_watch). Автономний Код лише ГОТУЄ PR (гілка+push у feature-гілку+gh pr create). Незалежний аудит і мерж/пуш у master робить інтерактивна сесія або власник. Залиш PR відкритим."}}'
      exit 0 ;;
  esac
fi

# (1) Інтерактивний гейт .audit_ok — лише для `gh pr merge`.
case "$norm" in
  *"gh pr merge"*) : ;;
  *) exit 0 ;;
esac
# Каталог репо: з `cd ...rozetka_agent` у команді, інакше поточний.
dir=$(printf '%s' "$cmd" | grep -oE 'cd "?[^"&;|]*rozetka_agent' | head -1 | sed 's/^cd "\?//')
[ -z "$dir" ] && dir="$PWD"
url=$(git -C "$dir" remote get-url origin 2>/dev/null)
root=$(git -C "$dir" rev-parse --show-toplevel 2>/dev/null)
case "$url" in
  *toysi-feeds*) : ;;
  *) exit 0 ;;   # інший репо — не втручаємось
esac
deny() {
  printf '%s' "{\"hookSpecificOutput\":{\"hookEventName\":\"PreToolUse\",\"permissionDecision\":\"deny\",\"permissionDecisionReason\":\"$1\"}}"
  exit 0
}
if [ -z "$root" ] || [ ! -f "$root/.audit_ok" ]; then
  deny "МЕРЖ ЗАБЛОКОВАНО (toysi-feeds): за правилом власника мержити ЛИШЕ ПІСЛЯ пройденого незалежного аудиту (CLAUDE.md). У корені репо нема маркера .audit_ok. Порядок: (1) провести незалежну аудит-сесію; (2) коли висновок ЧИСТИЙ — створити файл rozetka_agent/.audit_ok з номером PR усередині (echo 529 > .audit_ok); (3) тоді мерж ТОГО САМОГО номера; (4) прибрати .audit_ok після мержу. Загальне <так роби>/<активуй> власника НЕ є дозволом на мерж."
fi
# ПРИВ'ЯЗКА до номера PR (2026-09-14, закриває TOCTOU: старий гейт перевіряв лише факт існування
# файлу — маркер, лишений після аудиту PR A, міг би мовчки дозволити мерж НЕаудированого PR B, якщо
# видалення маркера між "мерж" і "прибрати" затрималось/забулось). .audit_ok тепер має містити
# ЛИШЕ номер PR (без нового рядка теж ок). Порожній/нечисловий вміст = старий стиль маркера (до цього
# фікса) — сумісність, пропускаємо як раніше, без прив'язки.
marker=$(cat "$root/.audit_ok" 2>/dev/null | tr -d '[:space:]')
case "$marker" in
  ''|*[!0-9]*) exit 0 ;;   # старий/порожній маркер — сумісність зі створеними до цього фікса
esac
pr_num=$(printf '%s' "$norm" | grep -oE 'pr merge [0-9]+' | grep -oE '[0-9]+' | head -1)
if [ -z "$pr_num" ]; then
  deny "МЕРЖ ЗАБЛОКОВАНО: .audit_ok прив'язаний до PR #$marker, але команда не називає номер PR явно (gh pr merge БЕЗ номера). Вкажи номер явно: gh pr merge $marker --merge --delete-branch — щоб гейт міг звірити, що мержиш саме аудійований PR."
fi
if [ "$pr_num" != "$marker" ]; then
  deny "МЕРЖ ЗАБЛОКОВАНО: .audit_ok прив'язаний до PR #$marker (той, що пройшов аудит), а команда мержить PR #$pr_num — це ІНШИЙ PR, аудиту не проходив. Проведи для нього окремий аудит і окремий маркер."
fi
exit 0   # номер збігається з аудійованим PR → дозволяємо

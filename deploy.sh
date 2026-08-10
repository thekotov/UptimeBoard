#!/usr/bin/env bash
# UptimeBoard — деплой и пересборка стека (postgres + redis + api + worker + frontend).
#
# По умолчанию: git pull → сборка образов → перезапуск контейнеров.
# Миграции БД (alembic upgrade head) применяются автоматически при старте api.
#
# Использование:
#   ./deploy.sh                 полный деплой: git pull + build + up -d
#   ./deploy.sh --no-pull       не делать git pull (собрать уже выкачанный код)
#   ./deploy.sh --no-build      только перезапустить контейнеры, без пересборки
#   ./deploy.sh --prune         почистить висящие (dangling) образы после сборки
#   ./deploy.sh --logs          после запуска показать логи (Ctrl+C для выхода)
#   ./deploy.sh api frontend    работать только с указанными сервисами
#   ./deploy.sh -h | --help     эта справка
set -euo pipefail

# Работаем из каталога скрипта, откуда бы его ни запустили.
cd "$(dirname "$0")"

# ---- разбор аргументов ----
PULL=1; BUILD=1; PRUNE=0; LOGS=0
SERVICES=()
while [ $# -gt 0 ]; do
  case "$1" in
    --no-pull)  PULL=0 ;;
    --no-build) BUILD=0 ;;
    --prune)    PRUNE=1 ;;
    --logs)     LOGS=1 ;;
    -h|--help)  sed -n '2,14p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    -*)         echo "Неизвестная опция: $1 (см. --help)" >&2; exit 1 ;;
    *)          SERVICES+=("$1") ;;
  esac
  shift
done

# ---- оформление вывода ----
if [ -t 1 ]; then B=$'\033[1m'; G=$'\033[32m'; Y=$'\033[33m'; R=$'\033[31m'; N=$'\033[0m'
else B=""; G=""; Y=""; R=""; N=""; fi
say()  { echo "${B}==>${N} $*"; }
warn() { echo "${Y}!  $*${N}"; }
die()  { echo "${R}Ошибка:${N} $*" >&2; exit 1; }

# ---- проверки окружения ----
[ -f docker-compose.yml ] || die "docker-compose.yml не найден — запускай из корня UptimeBoard."
[ -f .env ] || warn ".env не найден — сервисы поднимутся на значениях по умолчанию."
command -v docker >/dev/null 2>&1 || die "docker не установлен."
docker info >/dev/null 2>&1 || die "docker-демон недоступен (нужны права root / sudo?)."

# docker compose v2 ('docker compose') или v1 ('docker-compose')
if docker compose version >/dev/null 2>&1; then COMPOSE="docker compose"
elif command -v docker-compose >/dev/null 2>&1; then COMPOSE="docker-compose"
else die "не найден ни 'docker compose', ни 'docker-compose'."; fi

say "Compose: ${COMPOSE}"
if [ ${#SERVICES[@]} -gt 0 ]; then say "Сервисы: ${SERVICES[*]}"; else say "Сервисы: все"; fi

# ---- git pull ----
if [ "$PULL" -eq 1 ]; then
  if [ -d .git ]; then
    say "git pull (--ff-only)…"
    git pull --ff-only \
      || die "git pull не удался (локальные правки или расхождение веток). Разреши вручную или запусти с --no-pull."
  else
    warn "не git-репозиторий — пропускаю git pull."
  fi
fi

# ---- сборка образов ----
if [ "$BUILD" -eq 1 ]; then
  say "Сборка образов (--pull для свежих базовых)…"
  $COMPOSE build --pull "${SERVICES[@]}"
fi

# ---- запуск / пересоздание ----
say "Запуск контейнеров (up -d)…"
$COMPOSE up -d --remove-orphans "${SERVICES[@]}"

# ---- уборка ----
if [ "$PRUNE" -eq 1 ]; then
  say "Удаляю висящие образы…"
  docker image prune -f >/dev/null || true
fi

say "${G}Готово.${N} Миграции БД применяются автоматически при старте api (alembic upgrade head)."
echo
$COMPOSE ps

if [ "$LOGS" -eq 1 ]; then
  echo
  say "Логи (Ctrl+C для выхода):"
  $COMPOSE logs -f --tail=50 "${SERVICES[@]}"
fi

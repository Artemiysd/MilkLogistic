#!/usr/bin/env bash
# Запуск MilkLogistic на macOS / Linux.
#   ./start.sh          — реальный бэкенд + routing + db (+ OSRM, если граф готов)
#   ./start.sh --mock   — вместо бэкенда заглушка API
#   ./stop.sh           — остановить всё
set -euo pipefail
cd "$(dirname "$0")"

step() { printf "\n>> %s\n" "$1"; }
ok() { printf "   [OK] %s\n" "$1"; }
fail() { printf "   [!!] %s\n" "$1" >&2; }

MOCK=0
[ "${1:-}" = "--mock" ] && MOCK=1

step "Проверяю Docker"
if ! command -v docker >/dev/null 2>&1; then
    fail "Docker не найден. Установите Docker Desktop: https://www.docker.com/products/docker-desktop/"
    exit 1
fi
if ! docker info >/dev/null 2>&1; then
    fail "Docker Desktop не запущен. Откройте приложение и повторите."
    exit 1
fi
ok "$(docker --version)"

if [ ! -f .env ]; then
    cp .env.example .env
    ok "создал .env из .env.example"
fi

FILES=(-f docker-compose.yml)
PROFILE=()
if [ "$MOCK" -eq 0 ]; then
    FILES+=(-f docker-compose.backend.yml)
fi
if [ -f infra/osrm/data/grodno.osrm ]; then
    PROFILE=(--profile geo)
    ok "граф OSRM найден — поднимаю свой"
else
    ok "графа OSRM нет — использую публичный (см. infra/osrm/prepare.sh)"
fi

step "Поднимаю стек"
docker compose "${FILES[@]}" "${PROFILE[@]}" up -d --build

step "Жду ответа сервисов"
api_ok=0
for _ in $(seq 1 30); do
    if curl -fsS http://localhost:8000/api/health >/dev/null 2>&1; then api_ok=1; break; fi
    sleep 2
done
if [ "$api_ok" -eq 1 ]; then ok "API отвечает"; else fail "API не отвечает: docker compose logs api"; fi

if curl -fsS http://localhost:8100/api/health >/dev/null 2>&1; then
    ok "сервис маршрутизации отвечает"
else
    fail "routing не отвечает: docker compose logs routing"
fi

step "Готово"
echo ""
echo "   Интерфейс ........... http://localhost:8080"
echo "   API / Swagger ....... http://localhost:8000/docs"
echo "   Проверка расчётов ... http://localhost:8000/"
echo "   Маршрутизация ....... http://localhost:8100/api/health"
echo ""

if curl -fsS http://localhost:8100/api/health 2>/dev/null | grep -q '"reachable": *true'; then
    ok "OSRM доступен"
else
    echo "   [!!] OSRM недоступен. Для офлайн-режима: ./infra/osrm/prepare.sh, затем ./start.sh"
fi

if [ "$MOCK" -eq 0 ] && [ "${NO_BROWSER:-0}" != "1" ]; then
    if command -v open >/dev/null 2>&1; then open http://localhost:8080
    elif command -v xdg-open >/dev/null 2>&1; then xdg-open http://localhost:8080
    fi
fi

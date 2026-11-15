#!/usr/bin/env bash
# Остановка стека. Данные и образы сохраняются, запуск снова — ./start.sh
set -euo pipefail
cd "$(dirname "$0")"

if ! command -v docker >/dev/null 2>&1; then
    echo "Docker не найден" >&2
    exit 1
fi

docker compose --profile geo --profile tools down
echo ""
echo "Остановлено. Запуск снова: ./start.sh"

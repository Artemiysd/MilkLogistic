#!/usr/bin/env bash
# Проверка окружения: docker, данные, матрица, API, OSRM.
set -uo pipefail
cd "$(dirname "$0")"

ok=0
bad=0

pass() { printf "  [OK]   %s\n" "$1"; ok=$((ok + 1)); }
fail() { printf "  [FAIL] %s\n" "$1" >&2; bad=$((bad + 1)); }

echo ""
echo "1. Docker"
if command -v docker >/dev/null 2>&1; then pass "docker установлен"; else fail "docker не найден"; fi
if docker info >/dev/null 2>&1; then pass "docker daemon запущен"; else fail "docker daemon не запущен"; fi

echo ""
echo "2. Данные"
[ -f data/logistics_data.json ] && pass "logistics_data.json на месте" || fail "нет data/logistics_data.json"
[ -f data/matrix_cache.json ] && pass "matrix_cache.json на месте" || fail "нет data/matrix_cache.json"
if [ -f data/matrix_cache.json ] && grep -q '"size":215' data/matrix_cache.json; then
    pass "матрица 215x215"
elif [ -f data/matrix_cache.json ]; then
    fail "размер матрицы не 215"
fi
if [ -f data/logistics_data.json ] && grep -q '"demand": *63630' data/logistics_data.json; then
    pass "сумма груза 63 630 кг"
fi

echo ""
echo "3. API"
if curl -fsS http://localhost:8000/api/health >/dev/null 2>&1; then pass "GET /api/health"; else fail "API не отвечает"; fi
if curl -fsS http://localhost:8080/api/health >/dev/null 2>&1; then pass "GET /api/health через nginx"; else fail "nginx/фронт не отвечает"; fi

echo ""
echo "4. Маршрутизация и OSRM"
routing=$(curl -fsS http://localhost:8100/api/health 2>/dev/null || true)
if [ -n "$routing" ]; then
    pass "сервис маршрутизации отвечает"
    if printf "%s" "$routing" | grep -q '"reachable": *true'; then
        pass "OSRM доступен"
    else
        fail "OSRM недоступен (OSRM_BASE_URL в .env)"
    fi
else
    fail "routing не отвечает"
fi

echo ""
echo "Итог: $ok ок, $bad проблем"
[ "$bad" -eq 0 ] || exit 1

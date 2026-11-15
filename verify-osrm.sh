#!/usr/bin/env bash
# Проверка своего OSRM и связки routing -> OSRM (macOS / Linux).
#   ./verify-osrm.sh
set -uo pipefail
cd "$(dirname "$0")"

bad=0
pass() { printf "  [OK]   %s\n" "$1"; }
fail() { printf "  [FAIL] %s\n" "$1" >&2; bad=$((bad + 1)); }

echo ""
echo "1. OSRM (порт 5000)"
if curl -fsS "http://localhost:5000/route/v1/driving/23.828288,53.708928;23.9116412,53.6753356?overview=false" >/dev/null 2>&1; then
    pass "маршрут склад -> точка 1 считается"
else
    fail "OSRM не отвечает (docker compose --profile geo up -d)"
fi

echo ""
echo "2. Сервис маршрутизации (порт 8100)"
health=$(curl -fsS http://localhost:8100/api/health 2>/dev/null || true)
if [ -z "$health" ]; then
    fail "routing не отвечает"
elif printf "%s" "$health" | grep -q '"reachable": *true'; then
    pass "routing видит OSRM"
else
    fail "routing не видит OSRM — проверьте OSRM_BASE_URL в .env"
fi

echo ""
echo "3. Расчёт маршрута через API"
response=$(curl -fsS -X POST http://localhost:8100/api/calculate-route \
    -H "Content-Type: application/json" \
    -d '{"points":[{"id":"depot","lat":53.708928,"lon":23.828288},{"id":"shop","lat":53.6753356,"lon":23.9116412}]}' 2>/dev/null || true)

if [ -z "$response" ]; then
    fail "calculate-route не ответил"
elif command -v python3 >/dev/null 2>&1; then
    printf "%s" "$response" | python3 -c '
import json, sys
data = json.load(sys.stdin)
if data.get("code") != "Ok":
    print("  [FAIL] code=%s" % data.get("code"), file=sys.stderr)
    sys.exit(1)
print("  [OK]   %s км, %s мин, точек геометрии: %s" % (
    round(data["distance_m"] / 1000, 2),
    round(data["duration_s"] / 60),
    len((data.get("geometry") or {}).get("coordinates", [])),
))
' || bad=$((bad + 1))
else
    pass "calculate-route ответил (python3 нет, подробности пропущены)"
fi

echo ""
if [ "$bad" -eq 0 ]; then
    echo "Итог: всё ок"
else
    echo "Итог: проблем — $bad"
    exit 1
fi

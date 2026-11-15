#!/usr/bin/env bash
# Подготовка своего OSRM для Гродно (macOS / Linux). Нужен Docker.
#   ./infra/osrm/prepare.sh
#   BBOX=23.60,53.55,24.10,53.90 ./infra/osrm/prepare.sh
set -euo pipefail
cd "$(dirname "$0")"

DATA_DIR="$(pwd)/data"
mkdir -p "$DATA_DIR"

BBOX="${BBOX:-23.60,53.55,24.10,53.90}"
IMAGE="${OSRM_IMAGE:-osrm/osrm-backend:v5.25.0}"
OSMIUM_IMAGE="${OSMIUM_IMAGE:-stefda/osmium-tool}"
PBF="$DATA_DIR/belarus-latest.osm.pbf"

if [ ! -f "$PBF" ]; then
    echo "Скачиваю Беларусь (~335 МБ)..."
    curl -L --fail --progress-bar -o "$PBF" \
        "https://download.geofabrik.de/europe/belarus-latest.osm.pbf"
fi

SIZE=$(wc -c < "$PBF" | tr -d ' ')
if [ "$SIZE" -lt 100000000 ]; then
    echo "Файл подозрительно маленький ($SIZE байт) — удалите его и повторите" >&2
    exit 1
fi

echo "1/4 Нарезаю область Гродно (bbox $BBOX)..."
docker run --rm -v "$DATA_DIR:/data" "$OSMIUM_IMAGE" \
    osmium extract -b "$BBOX" -o /data/grodno.osm.pbf --overwrite /data/belarus-latest.osm.pbf

echo "2/4 osrm-extract..."
docker run --rm -v "$DATA_DIR:/data" "$IMAGE" osrm-extract -p /opt/car.lua /data/grodno.osm.pbf

echo "3/4 osrm-partition..."
docker run --rm -v "$DATA_DIR:/data" "$IMAGE" osrm-partition /data/grodno.osrm

echo "4/4 osrm-customize..."
docker run --rm -v "$DATA_DIR:/data" "$IMAGE" osrm-customize /data/grodno.osrm

echo ""
echo "Готово. Дальше:"
echo "  1) в .env: OSRM_BASE_URL=http://osrm:5000"
echo "  2) docker compose -f docker-compose.yml -f docker-compose.backend.yml --profile geo up -d"

"""
Сборка дорожной матрицы расстояний и времени для всех точек доставки.

Источник — OSRM /table (документация: https://project-osrm.org/docs/v26.5.0/http).
Публичный demo-сервер отдаёт не больше 100 координат в матрице за запрос,
поэтому матрица 215x215 собирается блоками 100x100 (9 запросов) с паузой между ними.

Результат кладётся в data/matrix_cache.json и дальше используется бэкендом
без обращения к сети: на защите интернет не нужен.

Примеры:
    py infra/matrix/build_matrix.py
    py infra/matrix/build_matrix.py --provider haversine          # офлайн-фолбэк
    py infra/matrix/build_matrix.py --base-url http://localhost:5000  # свой OSRM
    py infra/matrix/build_matrix.py --verify                     # сверить пару точек через /route
"""

from __future__ import annotations

import argparse
import json
import math
import random
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DATA = ROOT / "data" / "logistics_data.json"
DEFAULT_OVERRIDES = ROOT / "data" / "geocode_overrides.json"
DEFAULT_OUT = ROOT / "data" / "matrix_cache.json"
DEFAULT_BASE_URL = "https://router.project-osrm.org"

BLOCK = 100          # лимит demo-сервера на размер блока
PAUSE_SEC = 0.6      # пауза между запросами, чтобы не злить публичный сервер
RETRIES = 3
EARTH_R_M = 6371000.0
DETOUR = 1.30        # коэффициент извилистости для haversine-фолбэка


# ---------------------------------------------------------------- данные

def load_points(data_path: Path, overrides_path: Path) -> list[dict]:
    with open(data_path, encoding="utf-8") as f:
        data = json.load(f)

    overrides = {}
    if overrides_path.exists():
        with open(overrides_path, encoding="utf-8") as f:
            for item in json.load(f).get("overrides", []):
                overrides[item["point_id"]] = item

    depot = data["depot"]
    points = [
        {
            "index": 0,
            "id": depot["id"],
            "kind": "depot",
            "name": depot.get("name", "Склад"),
            "address": depot["address"],
            "lat": depot["coords"]["lat"],
            "lon": depot["coords"]["lon"],
        }
    ]

    skipped = []
    for point in data.get("delivery_points", []):
        fix = overrides.get(point["id"])
        coords = fix["coords"] if fix else point.get("coords")
        if not coords:
            skipped.append({"id": point["id"], "reason": "no coords"})
            continue
        points.append(
            {
                "index": len(points),
                "id": point["id"],
                "kind": "delivery",
                "name": point.get("name", ""),
                "address": point.get("address", ""),
                "lat": coords["lat"],
                "lon": coords["lon"],
                "geocode_quality": "override" if fix else "source",
            }
        )

    if skipped:
        print(f"  пропущено точек без координат: {len(skipped)}", file=sys.stderr)
    return points


# ---------------------------------------------------------------- сеть

def haversine_m(a: dict, b: dict) -> float:
    dlat = math.radians(b["lat"] - a["lat"])
    dlon = math.radians(b["lon"] - a["lon"])
    s = (
        math.sin(dlat / 2) ** 2
        + math.cos(math.radians(a["lat"]))
        * math.cos(math.radians(b["lat"]))
        * math.sin(dlon / 2) ** 2
    )
    return 2 * EARTH_R_M * math.asin(math.sqrt(s))


def osrm_table(base_url: str, points: list[dict], sources: list[int], dests: list[int]) -> dict:
    coords = ";".join(f"{p['lon']:.7f},{p['lat']:.7f}" for p in points)
    query = urllib.parse.urlencode(
        {
            "sources": ";".join(map(str, sources)),
            "destinations": ";".join(map(str, dests)),
            "annotations": "distance,duration",
        },
        safe=";",
    )
    url = f"{base_url}/table/v1/driving/{coords}?{query}"

    last_error = None
    for attempt in range(1, RETRIES + 1):
        try:
            with urllib.request.urlopen(url, timeout=60) as response:
                payload = json.loads(response.read().decode("utf-8"))
            if payload.get("code") != "Ok":
                raise RuntimeError(f"OSRM code={payload.get('code')} {payload.get('message','')}")
            return payload
        except (urllib.error.URLError, RuntimeError, TimeoutError) as exc:
            last_error = exc
            wait = 1.5 * attempt
            print(f"    попытка {attempt}/{RETRIES} не удалась: {exc}. Пауза {wait:.1f}s")
            time.sleep(wait)
    raise RuntimeError(f"OSRM /table недоступен: {last_error}")


def blocks(count: int, size: int) -> list[list[int]]:
    return [list(range(start, min(start + size, count))) for start in range(0, count, size)]


# ---------------------------------------------------------------- сборка

def build_osrm(points: list[dict], base_url: str) -> tuple[list, list, int, dict]:
    n = len(points)
    distances = [[0.0] * n for _ in range(n)]
    durations = [[0.0] * n for _ in range(n)]
    fallback_cells = 0
    requests = 0

    src_blocks = blocks(n, BLOCK)
    dst_blocks = blocks(n, BLOCK)
    total = len(src_blocks) * len(dst_blocks)
    started = time.time()

    for si, sources in enumerate(src_blocks, start=1):
        for di, dests in enumerate(dst_blocks, start=1):
            print(
                f"  блок {requests + 1}/{total}: источники {sources[0]}..{sources[-1]}, "
                f"назначения {dests[0]}..{dests[-1]}",
                flush=True,
            )
            payload = osrm_table(base_url, points, sources, dests)
            requests += 1

            for row, i in enumerate(sources):
                for col, j in enumerate(dests):
                    d = payload["distances"][row][col]
                    t = payload["durations"][row][col]
                    if d is None or t is None:
                        # дороги между точками нет (остров, закрытая зона) — считаем по прямой
                        d = haversine_m(points[i], points[j]) * DETOUR
                        t = d / (30.0 * 1000 / 3600)
                        fallback_cells += 1
                    distances[i][j] = round(float(d), 1)
                    durations[i][j] = round(float(t), 1)
            if requests < total:
                time.sleep(PAUSE_SEC)

    meta = {"requests": requests, "elapsed_sec": round(time.time() - started, 1)}
    return distances, durations, fallback_cells, meta


def build_haversine(points: list[dict]) -> tuple[list, list, int, dict]:
    n = len(points)
    distances = [[0.0] * n for _ in range(n)]
    durations = [[0.0] * n for _ in range(n)]
    for i in range(n):
        for j in range(n):
            if i == j:
                continue
            d = haversine_m(points[i], points[j]) * DETOUR
            distances[i][j] = round(d, 1)
            durations[i][j] = round(d / (30.0 * 1000 / 3600), 1)
    return distances, durations, 0, {"requests": 0, "elapsed_sec": 0.0}


# ---------------------------------------------------------------- проверки

def stats(matrix: list[list[float]]) -> dict:
    flat = [v for i, row in enumerate(matrix) for j, v in enumerate(row) if i != j]
    flat.sort()
    n = len(flat)
    return {
        "cells": n,
        "min_m": flat[0] if n else None,
        "median_m": flat[n // 2] if n else None,
        "max_m": flat[-1] if n else None,
        "avg_m": round(sum(flat) / n, 1) if n else None,
    }


def check_symmetry(matrix: list[list[float]]) -> float:
    n = len(matrix)
    worst = 0.0
    for i in range(0, n, 7):
        for j in range(0, n, 7):
            a, b = matrix[i][j], matrix[j][i]
            if a and b:
                worst = max(worst, abs(a - b) / max(a, b))
    return round(worst, 4)


def verify_route(base_url: str, points: list[dict], distances: list[list[float]], count: int = 3) -> list[dict]:
    rng = random.Random(42)
    results = []
    n = len(points)
    for _ in range(count):
        i, j = rng.randrange(n), rng.randrange(n)
        if i == j:
            continue
        coords = f"{points[i]['lon']:.7f},{points[i]['lat']:.7f};{points[j]['lon']:.7f},{points[j]['lat']:.7f}"
        url = f"{base_url}/route/v1/driving/{coords}?overview=false"
        try:
            with urllib.request.urlopen(url, timeout=30) as response:
                payload = json.loads(response.read().decode("utf-8"))
            route = payload["routes"][0]
            results.append(
                {
                    "from": points[i]["id"],
                    "to": points[j]["id"],
                    "table_m": distances[i][j],
                    "route_m": round(route["distance"], 1),
                    "delta_m": round(distances[i][j] - route["distance"], 1),
                }
            )
        except Exception as exc:  # noqa: BLE001 - проверка не должна ломать сборку
            results.append({"from": points[i]["id"], "to": points[j]["id"], "error": str(exc)})
        time.sleep(PAUSE_SEC)
    return results


# ---------------------------------------------------------------- main

def main() -> int:
    parser = argparse.ArgumentParser(description="Сборка матрицы расстояний OSRM")
    parser.add_argument("--data", type=Path, default=DEFAULT_DATA)
    parser.add_argument("--overrides", type=Path, default=DEFAULT_OVERRIDES)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--provider", choices=["osrm", "haversine"], default="osrm")
    parser.add_argument("--verify", action="store_true", help="сверить часть ячеек через /route")
    args = parser.parse_args()

    print(f"Данные: {args.data}")
    points = load_points(args.data, args.overrides)
    print(f"Точек с координатами: {len(points)} (включая склад)")

    if args.provider == "osrm":
        print(f"Провайдер: OSRM {args.base_url}")
        distances, durations, fallback_cells, meta = build_osrm(points, args.base_url.rstrip('/'))
    else:
        print("Провайдер: haversine (офлайн-фолбэк)")
        distances, durations, fallback_cells, meta = build_haversine(points)

    dist_stats = stats(distances)
    checks = {
        "diagonal_zero": all(distances[i][i] == 0 for i in range(len(points))),
        "symmetry_max_rel_diff": check_symmetry(distances),
        "fallback_cells": fallback_cells,
    }
    if args.verify and args.provider == "osrm":
        checks["route_samples"] = verify_route(args.base_url.rstrip('/'), points, distances)

    payload = {
        "version": 1,
        "provider": args.provider,
        "base_url": args.base_url if args.provider == "osrm" else None,
        "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "points": points,
        "size": len(points),
        "distance_m": distances,
        "duration_s": durations,
        "stats": {"distance": dist_stats, "requests": meta["requests"], "elapsed_sec": meta["elapsed_sec"]},
        "checks": checks,
    }

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    size_mb = args.out.stat().st_size / 1e6

    print()
    print(f"Готово: {args.out} ({size_mb:.2f} МБ)")
    print(f"  запросов: {meta['requests']}, время: {meta['elapsed_sec']} c")
    print(f"  расстояния: min {dist_stats['min_m']} м, медиана {dist_stats['median_m']} м, max {dist_stats['max_m']} м")
    print(f"  фолбэк-ячеек (нет дороги): {fallback_cells}")
    print(f"  диагональ нулевая: {checks['diagonal_zero']}, макс. асимметрия: {checks['symmetry_max_rel_diff']}")
    for sample in checks.get("route_samples", []):
        print(f"  сверка {sample}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

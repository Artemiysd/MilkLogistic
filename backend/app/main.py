"""
API MilkLogistic: данные, планирование маршрутов, геометрия и HTML-отчёты.

    GET  /                          страница проверки (не фронтенд)
    GET  /api/health
    GET  /api/data
    POST /api/optimize              {"drivers": 16, "include_geometry": true}
    GET  /api/runs                  последние прогоны
    GET  /api/runs/{id}             JSON прогона
    GET  /api/runs/{id}/report      HTML-отчёт: карта, маршруты, расшифровка
    GET  /api/runs/{id}/geojson     линии маршрутов
    POST /api/demo/seed
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from . import __version__
from .planner_adapter import expand_vehicles, load_json, run_plan
from .report import render_index, render_report

DATA_FILE = Path(os.environ.get("DATA_FILE", "/data/logistics_data.json"))
OVERRIDES_FILE = Path(os.environ.get("OVERRIDES_FILE", "/data/geocode_overrides.json"))
MATRIX_CACHE = Path(os.environ.get("MATRIX_CACHE", "/data/matrix_cache.json"))
RUNS_DIR = Path(os.environ.get("RUNS_DIR", "/data/runs"))
ROUTING_URL = os.environ.get("ROUTING_URL", "")

app = FastAPI(
    title="MilkLogistic API",
    version=__version__,
    description="Планирование доставки по реальной матрице OSRM + отчёты для проверки",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class OptimizeRequest(BaseModel):
    drivers: int | None = Field(default=None, ge=1, le=100, description="Сколько водителей доступно сегодня")
    shift_end: str = Field(default="17:00", description="Конец смены, HH:MM")
    loading_minutes: int = Field(default=30, ge=0, le=180)
    include_geometry: bool = Field(default=True, description="Считать линии маршрутов через сервис маршрутизации")


def _data() -> dict:
    if not DATA_FILE.exists():
        raise HTTPException(500, f"Нет файла данных {DATA_FILE}")
    data = load_json(DATA_FILE)
    # Ручные правки координат (битые геокоды) — те же, что применяет сборщик матрицы.
    if OVERRIDES_FILE.exists():
        fixes = {
            item["point_id"]: item
            for item in load_json(OVERRIDES_FILE).get("overrides", [])
        }
        for point in data.get("delivery_points", []):
            fix = fixes.get(point.get("id"))
            if fix:
                point["coords"] = fix["coords"]
                point["geocode_quality"] = fix.get("quality", "manual")
    return data


def _matrix() -> dict:
    if not MATRIX_CACHE.exists():
        raise HTTPException(
            503,
            f"Нет матрицы {MATRIX_CACHE}. Соберите её: docker compose --profile tools run --rm matrix",
        )
    return load_json(MATRIX_CACHE)


@app.get("/api/health")
def health() -> dict:
    data = _data()
    points = data.get("delivery_points", [])
    vehicles = expand_vehicles(data.get("vehicles", []))
    matrix_ready = MATRIX_CACHE.exists()
    matrix_size = None
    matrix_provider = None
    if matrix_ready:
        cache = load_json(MATRIX_CACHE)
        matrix_size = cache.get("size")
        matrix_provider = cache.get("provider")
    return {
        "status": "ok" if matrix_ready else "degraded",
        "version": __version__,
        "mode": "real",
        "planner": "logistics.RoutePlanner",
        "routing_url": ROUTING_URL or None,
        "points": len(points),
        "addresses": len({p["address"].strip().lower() for p in points}),
        "vehicles": len(vehicles),
        "demand_kg": sum(float(p.get("demand", 0)) for p in points),
        "matrix": {"ready": matrix_ready, "size": matrix_size, "provider": matrix_provider},
    }


@app.get("/api/data")
def get_data() -> dict:
    data = _data()
    return {
        "depot": data["depot"],
        "vehicles": expand_vehicles(data.get("vehicles", [])),
        "vehicle_types": data.get("vehicles", []),
        "delivery_points": data.get("delivery_points", []),
        "constraints": data.get("constraints", {}),
    }


@app.post("/api/optimize")
def optimize(body: OptimizeRequest | None = None) -> dict:
    body = body or OptimizeRequest()
    data = _data()
    cache = _matrix()
    try:
        result = run_plan(
            data,
            cache,
            drivers=body.drivers,
            shift_end=body.shift_end,
            loading_minutes=body.loading_minutes,
            routing_url=ROUTING_URL,
            include_geometry=body.include_geometry,
        )
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc

    try:
        RUNS_DIR.mkdir(parents=True, exist_ok=True)
        (RUNS_DIR / f"{result['run_id']}.json").write_text(
            json.dumps(result, ensure_ascii=False), encoding="utf-8"
        )
    except OSError:
        pass  # результат важнее сохранения
    return result


def _runs() -> list[dict]:
    if not RUNS_DIR.exists():
        return []
    items = sorted(RUNS_DIR.glob("*.json"), key=lambda path: path.stat().st_mtime, reverse=True)
    return [load_json(path) for path in items[:10]]


@app.get("/api/runs")
def list_runs() -> dict:
    runs = _runs()
    return {"count": len(runs), "runs": runs}


@app.get("/api/runs/{run_id}")
def get_run(run_id: str) -> dict:
    path = RUNS_DIR / f"{run_id}.json"
    if not path.exists():
        raise HTTPException(404, "Прогон не найден")
    return load_json(path)


@app.get("/api/runs/{run_id}/report", response_class=HTMLResponse)
def get_report(run_id: str) -> str:
    path = RUNS_DIR / f"{run_id}.json"
    if not path.exists():
        raise HTTPException(404, "Прогон не найден")
    return render_report(load_json(path))


@app.get("/api/runs/{run_id}/geojson")
def get_geojson(run_id: str) -> dict:
    path = RUNS_DIR / f"{run_id}.json"
    if not path.exists():
        raise HTTPException(404, "Прогон не найден")
    run = load_json(path)
    features = []
    for route in run.get("routes", []):
        geometry = route.get("geometry")
        if not geometry:
            continue
        features.append(
            {
                "type": "Feature",
                "properties": {
                    "route": route.get("route_number"),
                    "vehicle_id": route.get("vehicle_id"),
                    "distance_m": route.get("distance_m"),
                    "load_kg": route.get("load_kg"),
                    "stops": len(route.get("stops", [])),
                },
                "geometry": geometry,
            }
        )
    return {"type": "FeatureCollection", "features": features}


@app.post("/api/demo/seed")
def demo_seed() -> dict:
    data = _data()
    return {
        "status": "ok",
        "points": len(data.get("delivery_points", [])),
        "vehicles": len(expand_vehicles(data.get("vehicles", []))),
        "message": "Данные читаются из DATA_FILE, матрица — из MATRIX_CACHE",
    }


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    """Страница проверки: запустить расчёт и открыть отчёт. Это бэкенд, не фронтенд."""
    try:
        health_payload = health()
    except HTTPException as exc:
        health_payload = {"status": f"error: {exc.detail}"}
    return render_index(_runs(), health_payload)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.main:app", host="0.0.0.0", port=8000)

"""
API MilkLogistic: данные, планирование маршрутов, результаты прогонов.

    GET  /api/health
    GET  /api/data
    POST /api/optimize        {"drivers": 16, "shift_end": "17:00"}
    GET  /api/runs/{run_id}
    POST /api/demo/seed
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from . import __version__
from .planner_adapter import expand_vehicles, load_json, run_plan

DATA_FILE = Path(os.environ.get("DATA_FILE", "/data/logistics_data.json"))
MATRIX_CACHE = Path(os.environ.get("MATRIX_CACHE", "/data/matrix_cache.json"))
RUNS_DIR = Path(os.environ.get("RUNS_DIR", "/data/runs"))

app = FastAPI(title="MilkLogistic API", version=__version__, description="Планирование доставки по реальной матрице OSRM")
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


def _data() -> dict:
    if not DATA_FILE.exists():
        raise HTTPException(500, f"Нет файла данных {DATA_FILE}")
    return load_json(DATA_FILE)


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


@app.get("/api/runs/{run_id}")
def get_run(run_id: str) -> dict:
    path = RUNS_DIR / f"{run_id}.json"
    if not path.exists():
        raise HTTPException(404, "Прогон не найден")
    return load_json(path)


@app.post("/api/demo/seed")
def demo_seed() -> dict:
    data = _data()
    return {
        "status": "ok",
        "points": len(data.get("delivery_points", [])),
        "vehicles": len(expand_vehicles(data.get("vehicles", []))),
        "message": "Данные читаются из DATA_FILE, матрица — из MATRIX_CACHE",
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.main:app", host="0.0.0.0", port=8000)

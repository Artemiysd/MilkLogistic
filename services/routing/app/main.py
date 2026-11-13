"""
Сервис маршрутизации MilkLogistic.

Задача сервиса: «координаты -> реальные расстояния и время по дорогам».
Никакой логики распределения точек и порядка объезда здесь нет:
этим занимается алгоритм. Сервис только общается с OSRM и отдаёт результат.

    POST /api/calculate-route   порядок точек -> distance/duration/legs/geometry
    POST /api/calculate-table   матрица расстояний и времени N x N
    GET  /api/health            живость сервиса и OSRM
"""

from __future__ import annotations

import time

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from . import osrm as osrm_client
from .config import settings
from .models import (
    HealthResponse,
    RouteRequest,
    RouteResponse,
    TableRequest,
    TableResponse,
)

app = FastAPI(
    title="MilkLogistic Routing Service",
    version=settings.version,
    description="Слой OSRM: координаты -> дорожные расстояния, время и геометрия.",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


def _ensure_size(count: int) -> None:
    if count > settings.max_points:
        raise HTTPException(
            status_code=413,
            detail={
                "code": "TooManyPoints",
                "message": (
                    f"{count} точек больше лимита {settings.max_points}. "
                    "Для полного парка используйте /api/calculate-table и кэш матрицы."
                ),
            },
        )


@app.get("/", include_in_schema=False)
def root() -> dict:
    return {
        "service": "routing",
        "version": settings.version,
        "provider": settings.osrm_base_url,
        "docs": "/docs",
        "endpoints": [
            "/api/health",
            "/api/calculate-route",
            "/api/calculate-table",
        ],
    }


@app.get("/api/health", response_model=HealthResponse)
def health() -> HealthResponse:
    osrm_state = osrm_client.ping()
    return HealthResponse(
        status="ok" if osrm_state["reachable"] else "degraded",
        version=settings.version,
        osrm=osrm_state,
        cache={
            "route": osrm_client._route_cache.stats(),
            "table": osrm_client._table_cache.stats(),
        },
        limits={
            "max_points": settings.max_points,
            "chunk_size": settings.chunk_size,
            "timeout_sec": settings.osrm_timeout_s,
            "min_interval_sec": settings.osrm_min_interval_s,
        },
    )


@app.post(
    "/api/calculate-route",
    response_model=RouteResponse,
    summary="Маршрут по заданному порядку точек",
    responses={422: {"description": "Маршрут не найден"}, 502: {"description": "Ошибка OSRM"}, 504: {"description": "OSRM недоступен"}},
)
def calculate_route(body: RouteRequest) -> RouteResponse:
    _ensure_size(len(body.points))
    started = time.monotonic()
    try:
        result = osrm_client.calculate_route(
            body.points,
            profile=body.profile,
            return_geometry=body.return_geometry,
            overview=body.overview,
            continue_straight=body.continue_straight,
            costs=body.costs,
        )
    except osrm_client.OsrmError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.to_dict()) from exc

    result["elapsed_ms"] = int((time.monotonic() - started) * 1000)
    return RouteResponse(**result)


@app.post(
    "/api/calculate-table",
    response_model=TableResponse,
    summary="Матрица расстояний и времени",
    responses={502: {"description": "Ошибка OSRM"}, 504: {"description": "OSRM недоступен"}},
)
def calculate_table(body: TableRequest) -> TableResponse:
    _ensure_size(len(body.points))
    try:
        result = osrm_client.calculate_table(body.points, profile=body.profile)
    except osrm_client.OsrmError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.to_dict()) from exc
    return TableResponse(**result)


@app.post("/api/calculate-matrix", include_in_schema=False)
def calculate_matrix_alias(body: TableRequest) -> TableResponse:
    """Псевдоним: алгоритму привычнее слово matrix."""
    return calculate_table(body)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.main:app", host="0.0.0.0", port=8100, reload=False)

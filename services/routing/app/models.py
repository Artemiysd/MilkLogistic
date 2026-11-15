"""Контракты сервиса маршрутизации (Pydantic)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class PointIn(BaseModel):
    id: str | None = Field(default=None, description="Идентификатор точки из накладной")
    lat: float = Field(ge=-90, le=90, description="Широта, WGS84")
    lon: float = Field(ge=-180, le=180, description="Долгота, WGS84")


class RouteRequest(BaseModel):
    points: list[PointIn] = Field(min_length=2, description="Порядок точек = порядок объезда")
    profile: Literal["driving", "walking", "cycling"] = "driving"
    return_geometry: bool = True
    overview: Literal["full", "simplified", "false"] = "full"
    costs: Literal["table", "route"] = Field(
        default="table",
        description=(
            "table — стоимость участков из /table, то есть из той же матрицы, "
            "по которой считает алгоритм (итог совпадает с матрицей до метра). "
            "route — из /route, быстрее на один запрос, но возможны расхождения."
        ),
    )
    continue_straight: bool = Field(
        default=False,
        description=(
            "false — каждый участок считается как кратчайший путь между парами точек "
            "и совпадает с /table. true — OSRM запрещает развороты на точках (суммы "
            "перестают биться с матрицей)."
        ),
    )


class LegOut(BaseModel):
    from_index: int
    to_index: int
    from_id: str | None = None
    to_id: str | None = None
    distance_m: float
    duration_s: float


class RouteResponse(BaseModel):
    code: str = "Ok"
    source: str = "osrm"
    provider: str
    distance_m: float
    duration_s: float
    legs: list[LegOut]
    geometry: dict | None = None
    costs_source: str = "table"
    points_count: int
    requests: int
    elapsed_ms: int
    cached: bool = False


class TableRequest(BaseModel):
    points: list[PointIn] = Field(min_length=2)
    profile: Literal["driving", "walking", "cycling"] = "driving"


class TableStats(BaseModel):
    min_m: float | None = None
    median_m: float | None = None
    max_m: float | None = None
    unreachable_pairs: int = 0


class TableResponse(BaseModel):
    code: str = "Ok"
    source: str = "osrm"
    provider: str
    size: int
    ids: list[str | None]
    distance_m: list[list[float | None]]
    duration_s: list[list[float | None]]
    stats: TableStats
    requests: int
    elapsed_ms: int
    cached: bool = False


class HealthResponse(BaseModel):
    status: Literal["ok", "degraded"]
    version: str
    osrm: dict
    cache: dict
    limits: dict

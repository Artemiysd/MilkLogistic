"""
Мост между планировщиком (logistics/) и нашим API.

Что делает:
  * читает data/logistics_data.json и data/matrix_cache.json;
  * подставляет реальные дорожные расстояния вместо генератора (geo_random);
  * переводит результат PlanningResult в JSON-контракт API.

Ключи матрицы — нормализованные адреса: у планировщика остановка это физический
адрес, а не отдельная заявка.
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

# В Docker PLANNER_ROOT=/app, локально — корень репозитория.
ROOT = Path(os.environ.get("PLANNER_ROOT") or Path(__file__).resolve().parents[2])
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from logistics.data_loader import build_stops, format_clock, normalize_address  # noqa: E402
from logistics.models import PlanningResult  # noqa: E402
from logistics.planner import PlannerSettings, RoutePlanner  # noqa: E402
from logistics.routing import TravelTimeProvider  # noqa: E402


class MatrixTravelProvider(TravelTimeProvider):
    """Дорожная матрица OSRM в терминах планировщика: минуты и километры по адресам."""

    def __init__(self, cache: dict):
        self.points = cache["points"]
        self.distance_m = cache["distance_m"]
        self.duration_s = cache["duration_s"]
        self.provider = cache.get("provider", "unknown")
        self.built_at = cache.get("built_at")

        self.index_by_address: dict[str, int] = {}
        for point in self.points:
            self.index_by_address.setdefault(normalize_address(point["address"]), int(point["index"]))

        self.unknown_keys: set[str] = set()
        self.fallback_minutes = int(os.environ.get("MATRIX_FALLBACK_MINUTES", "15"))
        self.fallback_km = float(os.environ.get("MATRIX_FALLBACK_KM", "8.0"))

    def _index(self, key: str) -> int | None:
        return self.index_by_address.get(normalize_address(key))

    def km(self, origin: str, destination: str, origin_street: str = "", destination_street: str = "") -> float:
        if normalize_address(origin) == normalize_address(destination):
            return 0.0
        i, j = self._index(origin), self._index(destination)
        if i is None or j is None:
            self.unknown_keys.add(origin if i is None else destination)
            return self.fallback_km
        return round(self.distance_m[i][j] / 1000.0, 3)

    def minutes(self, origin: str, destination: str, origin_street: str = "", destination_street: str = "") -> int:
        if normalize_address(origin) == normalize_address(destination):
            return 0
        i, j = self._index(origin), self._index(destination)
        if i is None or j is None:
            self.unknown_keys.add(origin if i is None else destination)
            return self.fallback_minutes
        return max(1, int(round(self.duration_s[i][j] / 60.0)))


def load_json(path: str | Path) -> dict:
    with open(path, encoding="utf-8") as file:
        return json.load(file)


def expand_vehicles(vehicle_types: list[dict]) -> list[dict]:
    vehicles = []
    for vehicle_type in vehicle_types:
        for number in range(1, int(vehicle_type.get("count", 1)) + 1):
            vehicles.append(
                {
                    "id": f"{vehicle_type['id']}-{number}",
                    "type_id": vehicle_type["id"],
                    "capacity": vehicle_type["capacity"],
                    "restricted_to_small_streets": vehicle_type.get(
                        "restricted_to_small_streets", False
                    ),
                }
            )
    return vehicles


def baseline_distance_m(data: dict, travel) -> float:
    """Пробег «как в накладной»: склад -> адреса по порядку -> склад.

    Считаем тем же провайдером, что и план: для известных адресов это
    дорожные километры OSRM, для неизвестных — честная запасная оценка.
    Дубли адресов сохраняются: это отдельные заявки в порядке накладной.
    """
    depot = str(data.get("depot", {}).get("address", ""))
    addresses = [
        normalize_address(point.get("address", ""))
        for point in data.get("delivery_points", [])
        if normalize_address(point.get("address", ""))
    ]
    if not addresses:
        return 0.0

    total = travel.km(depot, addresses[0]) if depot else 0.0
    for first, second in zip(addresses, addresses[1:]):
        total += travel.km(first, second)
    total += travel.km(addresses[-1], depot) if depot else 0.0
    return round(total * 1000.0, 1)


def _route_to_dict(route, index: int, min_ratio: float = 0.9) -> dict:
    stops = []
    for sequence, visit in enumerate(route.stops, start=1):
        stop = visit.stop
        stops.append(
            {
                "seq": sequence,
                "point_id": stop.point_ids[0] if stop.point_ids else None,
                "point_ids": stop.point_ids,
                "address": stop.address,
                "street": stop.street,
                "demand_kg": stop.demand_kg,
                "arrive": format_clock(visit.arrival_minutes),
                "leave": format_clock(visit.service_end_minutes),
                "wait_minutes": visit.wait_minutes,
                "travel_minutes": visit.travel_minutes,
                "travel_m": round(visit.travel_km * 1000, 1),
                "orders": [
                    {
                        "id": point.get("id"),
                        "name": point.get("name"),
                        "demand_kg": point.get("demand"),
                    }
                    for point in stop.points
                ],
                "coords": _coords_of(stop.points),
            }
        )
    min_load_kg = int(round(route.vehicle.capacity_kg * min_ratio))
    return {
        "route_number": index,
        "vehicle_id": route.vehicle.vehicle_id,
        "vehicle_type": route.vehicle.vehicle_type,
        "vehicle_capacity": route.vehicle.capacity_kg,
        "driver": route.driver_number,
        "trip_no": route.trip_no,
        "load_kg": route.load_kg,
        "load_ratio": round(route.utilization, 4),
        "min_load_kg": min_load_kg,
        "underloaded": bool(min_ratio > 0 and route.load_kg < min_load_kg),
        "distance_m": round(route.total_km * 1000, 1),
        "work_minutes": route.working_minutes,
        "end_time": format_clock(route.finish_minutes),
        "warnings": list(route.warnings),
        "stops": stops,
    }


def _coords_of(points: list[dict]) -> dict | None:
    for point in points:
        coords = point.get("coords")
        if coords:
            return {"lat": coords["lat"], "lon": coords["lon"]}
    return None


def _true_unassigned_stops(data: dict, result: PlanningResult) -> list:
    """
    Реально неразвезённые адреса.

    Планировщик складывает в unassigned_stops только те адреса, что не влезли
    в машины. Рейсы, которым не хватило водителя, он молча выбрасывает, поэтому
    считаем сами: все адреса минус те, что есть в готовых маршрутах.
    """
    try:
        all_stops = build_stops(data)
    except Exception:  # noqa: BLE001 - если данные не разобрались, доверяем планировщику
        return list(result.unassigned_stops)

    assigned_keys = {visit.stop.key for route in result.routes for visit in route.stops}
    return [stop for stop in all_stops if stop.key not in assigned_keys]


def _violations(
    result: PlanningResult,
    max_workday_minutes: int,
    min_load_ratio: float,
    shift_end_minutes: int,
    unassigned_stops: list,
) -> list[dict]:
    violations: list[dict] = []
    for route in result.routes:
        if route.working_minutes > max_workday_minutes:
            violations.append(
                {
                    "type": "workday_exceeded",
                    "vehicle_id": route.vehicle.vehicle_id,
                    "value": route.working_minutes,
                    "limit": max_workday_minutes,
                    "message": "Рейс длиннее рабочего дня",
                }
            )
        if route.finish_minutes > shift_end_minutes:
            violations.append(
                {
                    "type": "late_return",
                    "vehicle_id": route.vehicle.vehicle_id,
                    "value": route.finish_minutes,
                    "limit": shift_end_minutes,
                    "message": f"Возврат на базу позже {format_clock(shift_end_minutes)}",
                }
            )
        if min_load_ratio > 0 and route.utilization < min_load_ratio:
            violations.append(
                {
                    "type": "min_load_violation",
                    "vehicle_id": route.vehicle.vehicle_id,
                    "value": round(route.utilization, 4),
                    "limit": min_load_ratio,
                    "message": "Загрузка ниже минимальной",
                }
            )
        for warning in route.warnings:
            violations.append(
                {
                    "type": "route_warning",
                    "vehicle_id": route.vehicle.vehicle_id,
                    "message": warning,
                }
            )
    for stop in unassigned_stops:
        violations.append(
            {
                "type": "unassigned_address",
                "point_id": stop.point_ids[0] if stop.point_ids else None,
                "value": stop.demand_kg,
                "message": f"Не развезено: {stop.address} ({stop.demand_kg} кг)",
            }
        )
    return violations


def _post_json(url: str, payload: dict, timeout: float) -> dict:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        url, data=body, headers={"Content-Type": "application/json", "Accept": "application/json"}
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def _geometry_for_route(route: dict, depot: dict, routing_url: str, timeout: float) -> dict | None:
    """Линия маршрута по дорогам: склад -> остановки -> склад."""
    points = []
    depot_coords = depot.get("coords")
    if not depot_coords:
        return None
    points.append({"id": "depot", "lat": depot_coords["lat"], "lon": depot_coords["lon"]})

    for stop in route["stops"]:
        coords = stop.get("coords")
        if not coords:
            return None
        points.append(
            {
                "id": stop.get("point_id") or f"stop-{stop['seq']}",
                "lat": coords["lat"],
                "lon": coords["lon"],
            }
        )
    points.append({"id": "depot", "lat": depot_coords["lat"], "lon": depot_coords["lon"]})

    payload = {"points": points, "return_geometry": True, "costs": "table"}
    response = _post_json(f"{routing_url.rstrip('/')}/api/calculate-route", payload, timeout)
    geometry = response.get("geometry")
    if not geometry:
        return None
    # 5 знаков ~ 1 метр: файл меньше, на глаз не отличить
    geometry["coordinates"] = [[round(lon, 5), round(lat, 5)] for lon, lat in geometry["coordinates"]]
    return geometry


def _get_json(url: str, timeout: float) -> dict:
    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def _routing_ready(routing_url: str, timeout: float = 6.0) -> tuple[bool, str]:
    """Быстрая проверка: жив ли сервис маршрутизации и видит ли он OSRM."""
    try:
        health = _get_json(f"{routing_url.rstrip('/')}/api/health", timeout)
    except Exception as exc:  # noqa: BLE001 - причина важна только в тексте
        return False, f"сервис маршрутизации недоступен: {exc}"
    osrm = health.get("osrm") or {}
    if not osrm.get("reachable"):
        error = (osrm.get("error") or {}).get("message") or "OSRM не отвечает"
        return False, f"OSRM недоступен: {error}"
    return True, ""


def attach_geometry(
    routes: list[dict], depot: dict, routing_url: str, timeout: float = 15.0
) -> list[str]:
    """Добавляет routes[i]['geometry'] и возвращает предупреждения."""
    if not routing_url:
        return ["Геометрия не запрошена: не задан ROUTING_URL"]

    ready, reason = _routing_ready(routing_url)
    if not ready:
        for route in routes:
            route["geometry"] = None
            route["geometry_source"] = None
        return [f"Геометрия пропущена — {reason}"]

    warnings: list[str] = []

    def work(route: dict) -> tuple[dict, dict | None, str | None]:
        try:
            return route, _geometry_for_route(route, depot, routing_url, timeout), None
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
            return route, None, f"маршрут {route['route_number']}: {exc}"

    failed = 0
    with ThreadPoolExecutor(max_workers=4) as pool:
        for route, geometry, error in pool.map(work, routes):
            if error:
                failed += 1
                if failed <= 2:
                    warnings.append("Не удалось получить геометрию: " + error)
            route["geometry"] = geometry
            route["geometry_source"] = "osrm" if geometry else None

    if failed:
        warnings.append(f"Без геометрии маршрутов: {failed} из {len(routes)}")
    return warnings


def run_plan(
    data: dict,
    cache: dict,
    drivers: int | None = None,
    shift_end: str = "17:00",
    loading_minutes: int = 30,
    routing_url: str | None = None,
    include_geometry: bool = True,
    enforce_min_load: bool = False,
) -> dict:
    """Запускает планировщик Даника на реальной матрице и возвращает ответ API."""
    started = time.monotonic()
    constraints = dict(data.get("constraints", {}))
    if enforce_min_load:
        constraints["enforce_min_load"] = True
        data = {**data, "constraints": constraints}
    provider = MatrixTravelProvider(cache)

    settings = PlannerSettings(
        shift_start=str(constraints.get("workday_start", "08:00")),
        shift_end=shift_end,
        loading_minutes=int(loading_minutes),
        unloading_minutes=int(constraints.get("unload_time_minutes", 30)),
        max_workday_hours=float(constraints.get("max_workday_hours", 8)),
        min_load_ratio=float(constraints.get("min_load_ratio", 0.9)),
    )
    planner = RoutePlanner(settings, travel_provider=provider)
    result: PlanningResult = planner.build(data, available_vehicles=drivers)

    max_workday_minutes = int(round(settings.max_workday_hours * 60))
    shift_end_minutes = settings.end_minutes
    routes = [
        _route_to_dict(route, index, settings.min_load_ratio)
        for index, route in enumerate(result.routes, start=1)
    ]
    unassigned_stops = _true_unassigned_stops(data, result)

    distance_m = round(sum(route["distance_m"] for route in routes), 1)
    baseline_m = baseline_distance_m(data, provider)
    demand_kg = result.total_demand_kg
    unassigned_kg = round(sum(stop.demand_kg for stop in unassigned_stops), 1)
    assigned_kg = round(demand_kg - unassigned_kg, 1)
    capacity_kg = sum(route.vehicle.capacity_kg for route in result.routes) or 0
    avg_load = (
        round(sum(route["load_ratio"] for route in routes) / len(routes), 4) if routes else 0.0
    )

    warnings = list(result.warnings)
    if provider.unknown_keys:
        warnings.append(
            "Часть адресов не нашлась в матрице, взяты оценочные значения: "
            + ", ".join(sorted(provider.unknown_keys)[:5])
        )

    if include_geometry:
        warnings.extend(
            attach_geometry(
                routes,
                data.get("depot", {}),
                routing_url or os.environ.get("ROUTING_URL", ""),
            )
        )

    return {
        "run_id": uuid.uuid4().hex[:12],
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "mock": False,
        "solver": "logistics.RoutePlanner + OSRM matrix",
        "depot": data.get("depot"),
        "options": {
            "drivers": drivers,
            "shift_start": settings.shift_start,
            "shift_end": settings.shift_end,
            "unload_minutes": settings.unloading_minutes,
            "loading_minutes": settings.loading_minutes,
            "min_load_ratio": settings.min_load_ratio,
            "enforce_min_load": enforce_min_load,
            "max_workday_hours": settings.max_workday_hours,
            "matrix_provider": provider.provider,
            "matrix_built_at": provider.built_at,
            "include_geometry": include_geometry,
            "routing_url": routing_url or os.environ.get("ROUTING_URL"),
        },
        "routes": routes,
        "review": {
            "min_load_ratio": settings.min_load_ratio,
            "underloaded_routes": [
                route["route_number"] for route in routes if route.get("underloaded")
            ],
            "underloaded_count": sum(1 for route in routes if route.get("underloaded")),
            "needs_decision": any(route.get("underloaded") for route in routes),
        },
        "totals": {
            "distance_m": distance_m,
            "vehicles_used": len(routes),
            "demand_kg": demand_kg,
            "assigned_kg": assigned_kg,
            "unassigned_kg": unassigned_kg,
            "capacity_kg": capacity_kg,
            "utilization": round(demand_kg / capacity_kg, 4) if capacity_kg else 0.0,
            "avg_load_ratio": avg_load,
            "orders_total": result.all_points_count,
            "addresses_total": result.all_stops_count,
            "geometry_routes": sum(1 for route in routes if route.get("geometry")),
        },
        "baseline": {
            "distance_m": baseline_m,
            "source": "порядок накладной (склад -> точки -> склад)",
        },
        "savings": {
            "distance_pct": round((1 - distance_m / baseline_m) * 100, 2) if baseline_m else None,
            "distance_m": round(baseline_m - distance_m, 1),
        },
        "unassigned": [
            {
                "address": stop.address,
                "demand_kg": stop.demand_kg,
                "priority": stop.priority,
                "point_ids": stop.point_ids,
            }
            for stop in unassigned_stops
        ],
        "violations": _violations(
            result, max_workday_minutes, settings.min_load_ratio, shift_end_minutes, unassigned_stops
        ),
        "warnings": warnings,
        "elapsed_ms": int((time.monotonic() - started) * 1000),
    }

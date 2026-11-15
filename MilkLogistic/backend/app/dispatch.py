"""Выдача маршрутов водителям и планирование дополнительного заезда.

Здесь две операции, которые связывают расчёт с реальной сменой:

* ``dispatch_run`` — раздать рейсы из готового прогона конкретным водителям
  (создаются записи в базе, у каждого водителя появляется своя ссылка);
* ``build_extra_plan`` — «доп. заезд»: взять адреса, которые не влезли в первую
  волну (и/или новые заявки из свежей выгрузки 1С), посадить их на машины,
  освободившиеся раньше остальных, и посчитать вторую волну с честным временем.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from logistics.data_loader import format_clock, parse_clock

from .db import DriverDB
from .datasets import DatasetError, build_matrix
from .planner_adapter import run_plan

# Машина считается свободной для второй волны, если вернулась минимум за час до конца смены.
FREE_BEFORE_MINUTES = 16 * 60


def _route_payload(route: dict[str, Any], run: dict[str, Any]) -> dict[str, Any]:
    return {
        "route_number": route.get("route_number"),
        "vehicle_id": route.get("vehicle_id"),
        "vehicle_type": route.get("vehicle_type"),
        "vehicle_capacity": route.get("vehicle_capacity"),
        "load_kg": route.get("load_kg"),
        "distance_m": route.get("distance_m"),
        "work_minutes": route.get("work_minutes"),
        "end_time": route.get("end_time"),
        "geometry": route.get("geometry"),
        "depot": run.get("depot"),
        "run_created_at": run.get("created_at"),
        "stops": route.get("stops", []),
    }


def dispatch_run(
    run: dict[str, Any],
    drivers: list[dict[str, Any]],
    store: DriverDB,
    mapping: dict[int, str] | None = None,
) -> list[dict[str, Any]]:
    """Создаёт задания водителям по рейсам прогона.

    ``mapping`` — необязательный словарь {номер рейса: id водителя}. Без него
    водители подбираются по закреплённой машине, затем по порядку.
    """
    mapping = mapping or {}
    by_id = {driver["id"]: driver for driver in drivers}
    used: set[str] = set()
    assignments = []

    for route in run.get("routes", []):
        number = int(route.get("route_number", 0))
        driver = None
        if number in mapping and mapping[number] in by_id:
            driver = by_id[mapping[number]]
        else:
            driver = next(
                (
                    item
                    for item in drivers
                    if item["id"] not in used
                    and item.get("vehicle_id")
                    and item["vehicle_id"] == route.get("vehicle_id")
                ),
                None,
            )
        if driver is None:
            # Не отдаём маршрут водителю с чужой машиной: лучше создать недостающего.
            driver = next(
                (item for item in drivers if item["id"] not in used and not item.get("vehicle_id")),
                None,
            )
        if driver is None:
            break
        used.add(driver["id"])
        assignment = _create_assignment(store, run, route, driver)
        assignments.append(assignment)
    return assignments


def _create_assignment(
    store: DriverDB, run: dict[str, Any], route: dict[str, Any], driver: dict[str, Any]
) -> dict[str, Any]:
    return store.create_assignment(
        run_id=str(run.get("run_id")),
        dataset_id=run.get("dataset_id"),
        route_number=int(route.get("route_number", 0)),
        driver_id=driver["id"],
        vehicle_id=route.get("vehicle_id"),
        payload=_route_payload(route, run),
    )


def dispatch_summary(
    store: DriverDB,
    assignments: list[dict[str, Any]] | None = None,
    drivers: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    assignments = assignments if assignments is not None else store.list_assignments()
    drivers = drivers if drivers is not None else store.list_drivers()
    by_driver: dict[str, dict[str, Any]] = {}
    for assignment in assignments:
        item = by_driver.setdefault(
            assignment["driver_id"],
            {"driver_id": assignment["driver_id"], "assignments": 0, "delivered": 0, "total": 0},
        )
        item["assignments"] += 1
        item["delivered"] += assignment["progress"]["delivered"]
        item["total"] += assignment["progress"]["total"]
    return {
        "drivers": drivers,
        "assignments": assignments,
        "stats": {
            "drivers": len(drivers),
            "assignments": len(assignments),
            "delivered": sum(item["progress"]["delivered"] for item in assignments),
            "stops": sum(item["progress"]["total"] for item in assignments),
        },
        "by_driver": list(by_driver.values()),
    }


def _free_vehicles(run: dict[str, Any], *, before_minutes: int = FREE_BEFORE_MINUTES) -> list[dict[str, Any]]:
    vehicles: list[dict[str, Any]] = []
    seen: set[str] = set()
    for route in run.get("routes", []):
        finish = parse_clock(route.get("end_time"))
        vehicle_id = str(route.get("vehicle_id") or "")
        if finish is None or finish > before_minutes or not vehicle_id or vehicle_id in seen:
            continue
        seen.add(vehicle_id)
        capacity = int(route.get("vehicle_capacity") or 0)
        if capacity <= 0:
            continue
        vehicles.append(
            {
                "id": vehicle_id,
                "name": route.get("vehicle_type") or vehicle_id,
                "capacity": capacity,
                "count": 1,
                "restricted_to_small_streets": capacity <= 1500,
                "_finish": finish,
            }
        )
    vehicles.sort(key=lambda item: item["_finish"])
    return vehicles


def build_extra_plan(
    run: dict[str, Any],
    data: dict[str, Any],
    routing_url: str | None,
    *,
    start_time: str = "13:00",
    extra_data: dict[str, Any] | None = None,
    max_vehicles: int | None = None,
    shift_end: str = "17:00",
    fallback_matrix: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Планирует вторую волну: остаток первой + (опционально) новые заявки 1С."""
    leftover_ids = {
        str(point_id)
        for item in run.get("unassigned", [])
        for point_id in item.get("point_ids", [])
    }
    data_by_id = {str(point.get("id")): point for point in data.get("delivery_points", [])}
    points = [data_by_id[point_id] for point_id in leftover_ids if point_id in data_by_id]

    added = 0
    if extra_data:
        for point in extra_data.get("delivery_points", []):
            points.append(point)
            added += 1

    if not points:
        raise ValueError(
            "Нет остатка для дополнительного заезда: все адреса первой волны развезены"
        )

    vehicles = _free_vehicles(run)
    if max_vehicles:
        vehicles = vehicles[:max_vehicles]
    if not vehicles:
        raise ValueError(
            "Нет машин, освободившихся до 16:00. Дополнительный заезд планировать не на чем"
        )

    start_minutes = parse_clock(start_time)
    if start_minutes is None:
        raise ValueError(f"Некорректное время старта: {start_time}")
    end_minutes = parse_clock(shift_end) or 17 * 60
    if start_minutes >= end_minutes:
        raise ValueError("Старт доп. заезда позже окончания смены")

    derived = {
        "depot": data.get("depot"),
        "vehicles": [
            {key: value for key, value in vehicle.items() if not key.startswith("_")}
            for vehicle in vehicles
        ],
        "delivery_points": points,
        "constraints": {
            **data.get("constraints", {}),
            "workday_start": format_clock(start_minutes),
            "min_load_ratio": 0,
            "one_trip_per_vehicle": True,
        },
        "meta": {
            "source": "extra-wave",
            "parent_run_id": run.get("run_id"),
            "added_points": added,
        },
    }

    matrix = None
    try:
        matrix = build_matrix(derived, routing_url)
    except DatasetError:
        if fallback_matrix is None:
            raise
        matrix = fallback_matrix
    result = run_plan(
        derived,
        matrix,
        drivers=len(vehicles),
        shift_end=shift_end,
        routing_url=routing_url,
        include_geometry=True,
    )
    result["wave"] = 2
    result["parent_run_id"] = run.get("run_id")
    result["dataset_id"] = run.get("dataset_id")
    result["extra"] = {
        "start_time": format_clock(start_minutes),
        "leftover_addresses": len(run.get("unassigned", [])),
        "planned_addresses": len(points) - added,
        "added_from_dataset": added,
        "vehicles": [vehicle["id"] for vehicle in vehicles],
        "source": "остаток первой волны" + (" + новая выгрузка 1С" if added else ""),
    }
    if not added:
        result["warnings"].append(
            "Доп. заезд спланирован только по остатку первой волны. "
            "Загрузите новую выгрузку 1С, чтобы добавить заявки"
        )
    return result


def now_clock() -> str:
    return datetime.now().strftime("%H:%M")

from __future__ import annotations

import csv
import json
from pathlib import Path

from .data_loader import format_clock
from .models import PlanningResult


def plan_to_dict(result: PlanningResult) -> dict:
    routes = []
    for route_index, route in enumerate(result.routes, start=1):
        stops = []
        for sequence, visit in enumerate(route.stops, start=1):
            stop = visit.stop
            stops.append({
                "sequence": sequence,
                "address": stop.address,
                "street": stop.street,
                "weight_kg": stop.demand_kg,
                "arrival": format_clock(visit.arrival_minutes),
                "unloading_start": format_clock(visit.service_start_minutes),
                "unloading_end": format_clock(visit.service_end_minutes),
                "waiting_minutes": visit.wait_minutes,
                "orders": [
                    {"id": point.get("id"), "name": point.get("name"), "weight_kg": point.get("demand")}
                    for point in stop.points
                ],
            })
        routes.append({
            "route_number": route_index,
            "vehicle_id": route.vehicle.vehicle_id,
            "vehicle_type": route.vehicle.vehicle_type,
            "capacity_kg": route.vehicle.capacity_kg,
            "load_kg": route.load_kg,
            "utilization_percent": round(route.utilization * 100, 1),
            "working_minutes": route.working_minutes,
            "return_time": format_clock(route.finish_minutes),
            "travel_minutes_estimated": route.travel_minutes,
            "warnings": route.warnings,
            "stops": stops,
        })
    return {
        "summary": {
            "orders_total": result.all_points_count,
            "physical_addresses_total": result.all_stops_count,
            "weight_total_kg": result.total_demand_kg,
            "weight_assigned_kg": result.assigned_demand_kg,
            "weight_unassigned_kg": result.unassigned_weight_kg,
            "vehicles_used": result.vehicles_used,
            "planning_mode": result.planner_mode,
        },
        "routes": routes,
        "unassigned_stops": [
            {
                "address": stop.address,
                "weight_kg": stop.demand_kg,
                "priority": stop.priority,
                "required_vehicle_type": stop.required_type,
                "orders": [
                    {"id": point.get("id"), "name": point.get("name"), "weight_kg": point.get("demand")}
                    for point in stop.points
                ],
            }
            for stop in result.unassigned_stops
        ],
        "warnings": result.warnings,
    }


def export_plan(result: PlanningResult, folder: str | Path = "output") -> list[Path]:
    target = Path(folder).expanduser()
    target.mkdir(parents=True, exist_ok=True)
    json_path = target / "plan.json"
    csv_path = target / "routes.csv"
    summary_path = target / "vehicles.csv"

    payload = plan_to_dict(result)
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    with csv_path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.writer(file, delimiter=";")
        writer.writerow(["Номер маршрута", "Автомобиль", "Порядок", "Адрес", "Улица", "Прибытие", "Начало разгрузки", "Конец разгрузки", "Ожидание, мин", "Вес, кг", "ID заявок", "Получатели"])
        for route in result.routes:
            for index, visit in enumerate(route.stops, start=1):
                stop = visit.stop
                writer.writerow([
                    route.vehicle.vehicle_id,
                    route.vehicle.label,
                    index,
                    stop.address,
                    stop.street,
                    format_clock(visit.arrival_minutes),
                    format_clock(visit.service_start_minutes),
                    format_clock(visit.service_end_minutes),
                    visit.wait_minutes,
                    stop.demand_kg,
                    ", ".join(str(p.get("id", "")) for p in stop.points),
                    "; ".join(str(p.get("name", "")) for p in stop.points),
                ])

    with summary_path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.writer(file, delimiter=";")
        writer.writerow(["Автомобиль", "Тип", "Грузоподъёмность, кг", "Загрузка, кг", "Загрузка, %", "Адресов", "Время работы, мин", "Возврат", "Предупреждения"])
        for route in result.routes:
            writer.writerow([
                route.vehicle.vehicle_id,
                route.vehicle.vehicle_type,
                route.vehicle.capacity_kg,
                route.load_kg,
                round(route.utilization * 100, 1),
                len(route.stops),
                route.working_minutes,
                format_clock(route.finish_minutes),
                " | ".join(route.warnings),
            ])
    return [json_path, csv_path, summary_path]

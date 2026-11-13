from __future__ import annotations

import json
import math
import re
from collections import defaultdict
from pathlib import Path
from typing import Any

from .models import DeliveryStop, Vehicle


def parse_clock(value: str | int | float | None) -> int | None:
    """Преобразует 09:30, 9-00 или 09.00 в минуты от начала суток."""
    if value is None or str(value).strip() == "":
        return None
    if isinstance(value, (int, float)):
        # Числовые значения в исходном JSON трактуем как часы только при дробной форме,
        # чтобы не угадывать семантику непонятных числовых времён.
        raise ValueError(f"Время должно быть строкой HH:MM, получено {value!r}")
    match = re.fullmatch(r"\s*(\d{1,2})\s*[-:.]\s*(\d{1,2})\s*", str(value))
    if not match:
        raise ValueError(f"Не удалось разобрать время {value!r}. Используйте HH:MM, например 09:30")
    hour, minute = int(match.group(1)), int(match.group(2))
    if hour > 23 or minute > 59:
        raise ValueError(f"Некорректное время {value!r}")
    return hour * 60 + minute


def format_clock(minutes: int | float) -> str:
    value = int(round(minutes))
    return f"{(value // 60) % 24:02d}:{value % 60:02d}"


def normalize_address(address: str) -> str:
    return " ".join(str(address).split()).strip()


def _normal(value: Any) -> str:
    return " ".join(str(value or "").casefold().split()).strip().replace("ё", "е")


def _is_restricted(street: str, address: str, restricted_streets: list[str]) -> tuple[bool, str]:
    street_norm = _normal(street)
    address_norm = _normal(address)
    for restricted in restricted_streets:
        candidate = _normal(restricted)
        if not candidate:
            continue
        # Разрешаем как точное имя улицы, так и варианты вроде «ул. Советская».
        if candidate == street_norm or candidate in street_norm or candidate in address_norm:
            return True, str(restricted).strip()
    return False, street.strip()


def validate_problem(data: Any) -> dict[str, Any]:
    """Проверяет входной контракт до запуска оптимизатора."""
    if not isinstance(data, dict):
        raise ValueError("Корень JSON должен быть объектом {...}")

    required = {"depot", "vehicles", "delivery_points"}
    missing = required - set(data)
    if missing:
        raise ValueError("В JSON отсутствуют обязательные разделы: " + ", ".join(sorted(missing)))

    if not isinstance(data["depot"], dict) or not normalize_address(data["depot"].get("address", "")):
        raise ValueError("В разделе depot необходимо указать непустое поле address")
    if not isinstance(data["vehicles"], list) or not data["vehicles"]:
        raise ValueError("Раздел vehicles должен быть непустым массивом")
    if not isinstance(data["delivery_points"], list) or not data["delivery_points"]:
        raise ValueError("Раздел delivery_points должен быть непустым массивом")

    constraints = data.setdefault("constraints", {})
    if not isinstance(constraints, dict):
        raise ValueError("Раздел constraints должен быть объектом")
    constraints.setdefault("min_load_ratio", 0.90)
    constraints.setdefault("max_workday_hours", 8)
    constraints.setdefault("one_trip_per_vehicle", True)
    constraints.setdefault("unload_time_minutes", 30)
    constraints.setdefault("restricted_streets", [])
    constraints.setdefault("restricted_vehicle_capacity", 1500)

    try:
        ratio = float(constraints["min_load_ratio"])
        max_hours = float(constraints["max_workday_hours"])
        unload = float(constraints["unload_time_minutes"])
        restricted_capacity = float(constraints["restricted_vehicle_capacity"])
    except (TypeError, ValueError) as exc:
        raise ValueError("Проверьте числовые ограничения в разделе constraints") from exc
    if not 0 < ratio <= 1:
        raise ValueError("constraints.min_load_ratio должен быть больше 0 и не больше 1")
    if max_hours <= 0 or unload < 0 or restricted_capacity <= 0:
        raise ValueError("Некорректные ограничения: рабочее время, разгрузка или грузоподъёмность")
    if constraints["one_trip_per_vehicle"] is not True:
        raise ValueError("Текущая версия поддерживает только один рейс на автомобиль (one_trip_per_vehicle=true)")
    if not isinstance(constraints["restricted_streets"], list):
        raise ValueError("constraints.restricted_streets должен быть массивом строк")

    vehicle_ids: set[str] = set()
    valid_vehicle_count = 0
    for index, vehicle in enumerate(data["vehicles"], start=1):
        if not isinstance(vehicle, dict):
            raise ValueError(f"vehicles[{index - 1}] должен быть объектом")
        vehicle_id = str(vehicle.get("id", f"vehicle_{index}")).strip()
        if vehicle_id in vehicle_ids:
            raise ValueError(f"Повторяется ID типа автомобиля: {vehicle_id}")
        vehicle_ids.add(vehicle_id)
        try:
            capacity = float(vehicle.get("capacity", 0))
            count = int(vehicle.get("count", 0))
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Некорректная грузоподъёмность/количество машин в vehicles[{index - 1}]") from exc
        if not math.isfinite(capacity) or capacity <= 0 or count < 0:
            raise ValueError(f"В vehicles[{index - 1}] capacity должен быть > 0, count — >= 0")
        valid_vehicle_count += count
    if valid_vehicle_count == 0:
        raise ValueError("По описанию vehicles нет ни одного доступного автомобиля (сумма count = 0)")

    point_ids: set[str] = set()
    for index, point in enumerate(data["delivery_points"], start=1):
        if not isinstance(point, dict):
            raise ValueError(f"delivery_points[{index - 1}] должен быть объектом")
        point_id = str(point.get("id", "")).strip()
        if not point_id:
            raise ValueError(f"У доставки №{index} отсутствует id")
        if point_id in point_ids:
            raise ValueError(f"Повторяется ID доставки: {point_id}")
        point_ids.add(point_id)
        if not normalize_address(point.get("address", "")):
            raise ValueError(f"У точки {point_id} не указан address")
        try:
            demand = float(point.get("demand", 0))
        except (TypeError, ValueError) as exc:
            raise ValueError(f"У точки {point_id} некорректный demand") from exc
        if not math.isfinite(demand) or demand <= 0:
            raise ValueError(f"У точки {point_id} demand должен быть положительным числом")
        if point.get("delivery_window") not in (None, ""):
            parse_clock(point["delivery_window"])
        priority = point.get("priority")
        if priority is not None and not isinstance(priority, (bool, int, float)):
            raise ValueError(f"У точки {point_id} поле priority должно быть true/false или числом")
        vehicle_type = point.get("vehicle_type")
        if vehicle_type is not None and not str(vehicle_type).strip():
            raise ValueError(f"У точки {point_id} поле vehicle_type должно быть непустой строкой")
    return data


def load_problem(path: str | Path) -> dict[str, Any]:
    file_path = Path(path).expanduser()
    try:
        with file_path.open("r", encoding="utf-8-sig") as file:
            data = json.load(file)
    except json.JSONDecodeError as exc:
        raise ValueError(f"JSON повреждён: строка {exc.lineno}, столбец {exc.colno}: {exc.msg}") from exc
    except UnicodeDecodeError as exc:
        raise ValueError("Не удалось прочитать файл как UTF-8. Сохраните JSON в кодировке UTF-8") from exc
    return validate_problem(data)


def build_stops(data: dict[str, Any], busy_until_by_address: dict[str, int] | None = None) -> list[DeliveryStop]:
    busy_until_by_address = busy_until_by_address or {}
    constraints = data.get("constraints", {})
    restricted_streets = list(constraints.get("restricted_streets", []))
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    address_display: dict[str, str] = {}

    for point in data.get("delivery_points", []):
        address = normalize_address(point.get("address", ""))
        key = _normal(address)
        grouped[key].append(point)
        address_display.setdefault(key, address)

    stops: list[DeliveryStop] = []
    for key, points in grouped.items():
        demand = sum(int(round(float(point["demand"]))) for point in points)
        if demand <= 0:
            raise ValueError(f"У адреса {address_display[key]!r} нулевая или отрицательная заявка")

        street_values = [str(point.get("street") or "").strip() for point in points if point.get("street")]
        street = street_values[0] if street_values else ""
        restricted_matches = [_is_restricted(value, address_display[key], restricted_streets) for value in street_values]
        address_match = _is_restricted(street, address_display[key], restricted_streets)
        restricted = address_match[0] or any(match[0] for match in restricted_matches)
        if address_match[0]:
            street = address_match[1]
        elif restricted:
            street = next(match[1] for match in restricted_matches if match[0])

        time_values = [parse_clock(point.get("delivery_window")) for point in points if point.get("delivery_window")]
        time_values = [value for value in time_values if value is not None]
        priority = any(bool(point.get("priority")) for point in points)
        type_values = [str(point.get("vehicle_type")).strip() for point in points if point.get("vehicle_type")]
        required_type = type_values[0] if type_values else None
        stops.append(
            DeliveryStop(
                key=address_display[key],
                address=address_display[key],
                street=street,
                demand_kg=demand,
                points=points,
                earliest_service_minutes=min(time_values) if time_values else None,
                restricted=restricted,
                busy_until=busy_until_by_address.get(address_display[key], busy_until_by_address.get(key)),
                priority=priority,
                required_type=required_type,
            )
        )
    return stops


def expand_fleet(data: dict[str, Any]) -> list[Vehicle]:
    fleet: list[Vehicle] = []
    restricted_capacity = int(data.get("constraints", {}).get("restricted_vehicle_capacity", 1500))
    for item in data.get("vehicles", []):
        count = int(item.get("count", 0))
        capacity = int(round(float(item.get("capacity", 0))))
        if count < 0 or capacity <= 0:
            raise ValueError(f"Некорректное описание машины: {item}")
        base_id = str(item.get("id", "vehicle"))
        vehicle_type = str(item.get("type") or item.get("name") or base_id.rsplit("_", 1)[0])
        can_enter_restricted = bool(item.get("restricted_to_small_streets", False)) or capacity <= restricted_capacity
        for index in range(1, count + 1):
            fleet.append(
                Vehicle(
                    vehicle_id=f"{base_id}-{index}",
                    vehicle_type=vehicle_type,
                    capacity_kg=capacity,
                    restricted_to_small_streets=can_enter_restricted,
                )
            )
    if not fleet:
        raise ValueError("В исходных данных нет доступного транспорта")
    return fleet

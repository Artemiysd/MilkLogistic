from __future__ import annotations

from .data_loader import format_clock
from .models import PlanningResult
from .planner import PlannerSettings


def _duration(minutes: int) -> str:
    return f"{minutes // 60} ч {minutes % 60:02d} мин"


def _bar(value: float, width: int = 18) -> str:
    filled = max(0, min(width, round(value * width)))
    return "█" * filled + "░" * (width - filled)


def _print_path_chain(route) -> None:
    """Цепочка пути: БАЗА -> адрес -> адрес -> ... -> БАЗА, с переносом длинных строк."""
    if not route.stops:
        return
    departure = format_clock(route.stops[0].arrival_minutes - route.stops[0].travel_minutes)
    parts = [f"БАЗА (выезд {departure})"]
    for visit in route.stops:
        km = f", {visit.travel_km:.1f} км" if visit.travel_km else ""
        parts.append(f"{visit.stop.address} приб. {format_clock(visit.arrival_minutes)}{km}")
    parts.append(f"БАЗА (возврат {format_clock(route.finish_minutes)})")

    prefix = "ПУТЬ: "
    indent = " " * len(prefix)
    line = prefix
    for position, part in enumerate(parts):
        token = part if position == 0 else f" -> {part}"
        if len(line) + len(token) > 108 and position > 0:
            print(line)
            line = indent + token.strip()
        else:
            line += token
    print(line)


def print_summary(result: PlanningResult, settings: PlannerSettings | None = None) -> None:
    min_load = settings.min_load_ratio if settings else 0.90
    max_work = settings.max_workday_minutes if settings else 480
    shift_end = settings.end_minutes if settings else 17 * 60
    print("\n" + "=" * 108)
    print("ИТОГ ПЛАНИРОВАНИЯ ДОСТАВОК")
    print("=" * 108)
    print(f"Заявок / торговых объектов : {result.all_points_count}")
    print(f"Физических адресов         : {result.all_stops_count}")
    print(f"Общий груз                 : {result.total_demand_kg:,.0f} кг".replace(",", " "))
    print(f"Распределено               : {result.assigned_demand_kg:,.0f} кг".replace(",", " "))
    if result.unassigned_stops:
        print(f"НЕ РАСПРЕДЕЛЕНО            : {len(result.unassigned_stops)} адресов "
              f"({result.unassigned_weight_kg} кг) — см. пункт 2 / экспорт")
    print(f"Использовано автомобилей   : {result.vehicles_used}")
    total_km = sum(route.total_km for route in result.routes)
    if total_km:
        print(f"Суммарный пробег (оценка)  : {total_km:,.1f} км".replace(",", " "))
    print("\nРАСПРЕДЕЛЕНИЕ ПО МАШИНАМ (рейсы по водителям)")
    print("-" * 116)
    print(f"{'№':<3} {'Вод':>4} {'Машина / тип':<32} {'Загрузка':>17} {'%':>7}  {'Адресов':>7} {'Возврат':>8} {'Работа':>12}  Статус")
    print("-" * 116)
    for index, route in enumerate(result.routes, start=1):
        v = route.vehicle
        pct = route.utilization
        time_bad = route.working_minutes > max_work or route.finish_minutes > shift_end
        load_ok = pct + 1e-9 >= min_load and route.load_kg <= v.capacity_kg
        status = "ВНИМАНИЕ" if (time_bad or not load_ok or route.warnings) else "OK"
        driver = f"{route.driver_number}р{route.trip_no}" if route.driver_number else "-"
        print(
            f"{index:<3} {driver:>4} {v.label:<32} {route.load_kg:>6} / {v.capacity_kg:<6} "
            f"{pct:>6.1%}  {len(route.stops):>7} {format_clock(route.finish_minutes):>8} "
            f"{_duration(route.working_minutes):>12}  {status}"
        )
        print(f"    [{_bar(pct)}]")
        for warning in route.warnings:
            print(f"    ! {warning}")
    print("-" * 108)
    under = sum(1 for route in result.routes if route.utilization + 1e-9 < min_load)
    over = sum(1 for route in result.routes if route.load_kg > route.vehicle.capacity_kg)
    time_bad = sum(1 for route in result.routes if route.working_minutes > max_work or route.finish_minutes > shift_end)
    assigned_ok = result.assigned_points_count == result.all_points_count and result.assigned_demand_kg == result.total_demand_kg
    print("ПРОВЕРКИ")
    print(f"  • Все заявки распределены: {'ДА' if assigned_ok else 'НЕТ'} ({result.assigned_points_count}/{result.all_points_count})")
    print(f"  • Недогруженных (ниже {min_load:.0%}): {under}; перегруженных: {over}")
    print(f"  • Рейсов с превышением рабочего времени/смены: {time_bad}")
    for warning in result.warnings:
        print(f"  • {warning}")


def print_route_details(result: PlanningResult) -> None:
    for index, route in enumerate(result.routes, start=1):
        vehicle = route.vehicle
        access = "допущен на ограниченные улицы" if vehicle.restricted_to_small_streets else "обычный доступ"
        driver = f"Водитель {route.driver_number}, рейс {route.trip_no}" if route.driver_number else "Водитель -"
        print("\n" + "=" * 108)
        print(f"МАРШРУТ №{index} | {driver} | {vehicle.label} | {access}")
        print(
            f"Загрузка: {route.load_kg} / {vehicle.capacity_kg} кг ({route.utilization:.1%}) | "
            f"Адресов: {len(route.stops)} | Возврат: {format_clock(route.finish_minutes)}"
        )
        km_line = f"Пробег (оценка): {route.total_km:.1f} км" if route.total_km else "Пробег: нет данных (не задан км-провайдер)"
        lunch_line = f" | Обед: {route.lunch_minutes} мин" if route.lunch_minutes else ""
        print(f"Общее рабочее время: {_duration(route.working_minutes)} | В пути (оценка): {_duration(route.travel_minutes)} | {km_line}{lunch_line}")
        _print_path_chain(route)
        print("-" * 108)
        print(f"{'№':<4} {'Приб.':<7} {'Разгрузка':<12} {'Ожид.':>6} {'Км':>6} {'Вес':>6}  Адрес | заявки")
        for stop_index, visit in enumerate(route.stops, start=1):
            stop = visit.stop
            km_text = f"{visit.travel_km:.1f}" if visit.travel_km else "-"
            orders = "; ".join(
                f"{str(point.get('name') or 'Без названия')} {float(point.get('demand', 0)):g} кг [{point.get('id', '?')}]"
                for point in stop.points
            )
            print(
                f"{stop_index:<4} {format_clock(visit.arrival_minutes):<7} "
                f"{format_clock(visit.service_start_minutes)}-{format_clock(visit.service_end_minutes)} "
                f"{visit.wait_minutes:>4} м {km_text:>6} {stop.demand_kg:>6}  {stop.address} | {orders}"
            )
        print(f"Возврат на базу: {format_clock(route.finish_minutes)}")
        for warning in route.warnings:
            print(f"  ! {warning}")

    if result.unassigned_stops:
        print("\n" + "=" * 108)
        print(f"НЕ РАЗВЕЗЕНО: {len(result.unassigned_stops)} адресов на {result.unassigned_weight_kg} кг "
              "(не хватило грузоподъёмности или допуска машины)")
        print("-" * 108)
        for stop in result.unassigned_stops:
            tags = []
            if stop.priority:
                tags.append("ПРИОРИТЕТ")
            if stop.required_type:
                tags.append(f"нужна машина: {stop.required_type}")
            tag_text = f" [{', '.join(tags)}]" if tags else ""
            orders = ", ".join(str(point.get("id", "")) for point in stop.points)
            print(f"  - {stop.address} — {stop.demand_kg} кг{tag_text} | заявки: {orders}")

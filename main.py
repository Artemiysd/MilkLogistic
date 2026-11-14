from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

from logistics.data_loader import (
    build_stops,
    expand_fleet,
    format_clock,
    load_problem,
    normalize_address,
    parse_clock,
)
from logistics.export import export_plan
from logistics.geo_random import KM_MAX, KM_MIN, DEFAULT_SPEED_KMH, KmTravelTimeProvider
from logistics.logger import log, session_start
from logistics.planner import PlannerSettings, PlanningError, RoutePlanner
from logistics.reporting import print_route_details, print_summary

ROOT = Path(__file__).resolve().parent
DEFAULT_DATA = ROOT / "data" / "full_demo.json"

MENU = """
1. Сводка распределения по машинам
2. Маршрутные листы (подробно, с километражем)
3. Точки из JSON (таблица, с поиском)
4. Машины из JSON (парк и флот)
5. Число водителей -> пересчёт
6. Занятость точки -> перерасчёт
7. Загрузить другой JSON
8. Экспорт: plan.json, routes.csv, vehicles.csv
9. Ограничения и допущения
0. Выход
"""


# ---------------------------------------------------------------- загрузка

def resolve_data_path(raw_path: str) -> Path:
    raw = raw_path.strip().strip('"').strip("'")
    if not raw:
        return DEFAULT_DATA
    candidate = Path(raw).expanduser()
    options = [candidate] if candidate.is_absolute() else [
        Path.cwd() / candidate,
        ROOT / candidate,
        ROOT / "data" / candidate,
    ]
    for option in options:
        if option.is_file():
            return option.resolve()
    tried = "\n".join(f"  - {p}" for p in options)
    raise FileNotFoundError(f"Файл не найден: {raw}\nПроверены пути:\n{tried}")


def ask_json_path() -> Path | None:
    print("\nЗАГРУЗКА ВХОДНЫХ ДАННЫХ")
    print("JSON с разделами: depot, vehicles, delivery_points, constraints.")
    print("Можно перетащить файл в окно консоли. Enter — демо-файл full_demo.json.")
    while True:
        try:
            raw = input(f"Путь к JSON [{DEFAULT_DATA.name}]: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nЗагрузка отменена.")
            return None
        try:
            return resolve_data_path(raw)
        except FileNotFoundError as error:
            print(f"\n{error}\nПопробуйте ещё раз.\n")


def ask_driver_count(fleet_size: int) -> int:
    """Число водителей сегодня: не меньше 1."""
    print("\nВОДИТЕЛИ")
    print("Каждый водитель за смену может сделать несколько рейсов подряд:")
    print("вернулся на базу — взял следующую машину, пока суммарная работа <= 8 ч (без обеда).")
    while True:
        try:
            raw = input(f"Сколько водителей сегодня? [Enter — все {fleet_size}]: ").strip()
        except (EOFError, KeyboardInterrupt):
            raise SystemExit("\nЗагрузка отменена.")
        if not raw:
            return fleet_size
        try:
            count = int(raw)
        except ValueError:
            print("Введите целое число, например 12.")
            continue
        if count < 1:
            print("Должен быть хотя бы один водитель.")
            continue
        if count > fleet_size:
            print(f"В автопарке {fleet_size} машин — лишние водители останутся без работы, "
                  "но это допустимо. Введите число или Enter.")
            continue
        return count


def make_planner(data: dict[str, Any], drive_minutes: int, load_minutes: int) -> RoutePlanner:
    constraints = data.get("constraints", {})
    settings = PlannerSettings(
        shift_start="08:00",
        shift_end="17:00",
        loading_minutes=load_minutes,
        unloading_minutes=int(constraints.get("unload_time_minutes", 30)),
        default_drive_minutes=max(1, drive_minutes),
        max_workday_hours=float(constraints.get("max_workday_hours", 8)),
        min_load_ratio=float(constraints.get("min_load_ratio", 0.90)),
    )
    # Километраж между адресами генерируется (детерминированно), пока нет реальной дорожной матрицы.
    return RoutePlanner(settings, travel_provider=KmTravelTimeProvider(speed_kmh=DEFAULT_SPEED_KMH))


def plan_with_drivers(
    data: dict[str, Any],
    planner: RoutePlanner,
    drivers: int,
    busy_until_by_address: dict[str, int] | None = None,
):
    return planner.build(data, busy_until_by_address=busy_until_by_address, available_vehicles=drivers)


# ---------------------------------------------------------------- просмотр данных

def show_points(data: dict[str, Any]) -> None:
    points = data.get("delivery_points", [])
    print(f"\nТОЧКИ ДОСТАВКИ ИЗ JSON: всего {len(points)}")
    query = input("Фильтр по адресу/названию/ID (Enter — показать первые 30): ").strip().casefold()
    rows = [
        p for p in points
        if not query or query in str(p.get("id", "")).casefold()
        or query in str(p.get("name", "")).casefold()
        or query in normalize_address(p.get("address", "")).casefold()
    ]
    limit = 30 if not query else len(rows)
    if not rows:
        print("Ничего не найдено.")
        return
    print(f"{'ID':<12} {'Название':<26} {'Адрес':<34} {'кг':>6}  Окно")
    print("-" * 92)
    for point in rows[:limit]:
        address = normalize_address(point.get("address", ""))
        name = str(point.get("name") or "-")[:25]
        window = str(point.get("delivery_window") or "-")
        print(f"{str(point.get('id', '')):<12} {name:<26} {address[:33]:<34} {point.get('demand', 0):>6}  {window}")
    if len(rows) > limit:
        print(f"... и ещё {len(rows) - limit} строк. Введите фильтр, чтобы сузить список.")


def show_vehicles(data: dict[str, Any], drivers: int) -> None:
    print(f"\nПАРК ИЗ JSON ({len(data.get('vehicles', []))} типов)")
    print(f"{'ID':<16} {'Тип':<18} {'кг':>8} {'шт':>4}  Узкие улицы")
    print("-" * 64)
    for vehicle in data.get("vehicles", []):
        capacity = int(round(float(vehicle.get("capacity", 0))))
        small = vehicle.get("restricted_to_small_streets", False) or capacity <= int(
            data.get("constraints", {}).get("restricted_vehicle_capacity", 1500)
        )
        print(
            f"{str(vehicle.get('id', '')):<16} {str(vehicle.get('type') or vehicle.get('name') or '-')[:17]:<18} "
            f"{capacity:>8} {int(vehicle.get('count', 0)):>4}  {'да' if small else 'нет'}"
        )
    fleet = expand_fleet(data)
    print(f"\nРазвёрнутый флот: {len(fleet)} машин, "
          f"грузоподъёмность {sum(v.capacity_kg for v in fleet):,} кг".replace(",", " "))
    print(f"Водителей: {drivers} — каждый может сделать несколько рейсов за смену (лимит работы 8 ч без обеда); "
          "рейсы, не влезающие в смену, остаются неразвезёнными.")


# ---------------------------------------------------------------- сценарий занятости

def find_point(data: dict[str, Any], point_id: str) -> tuple[str, str] | None:
    for point in data.get("delivery_points", []):
        if str(point.get("id")) == point_id.strip():
            return normalize_address(point.get("address", "")), str(point.get("name", point_id))
    return None


def find_visit(result, address: str):
    for route_index, route in enumerate(result.routes, start=1):
        for stop_index, visit in enumerate(route.stops, start=1):
            if normalize_address(visit.stop.address).casefold() == normalize_address(address).casefold():
                return route_index, stop_index, route, visit
    return None


def print_replan_diff(before, after, address: str, busy_until: int) -> None:
    old = find_visit(before, address)
    new = find_visit(after, address)
    print("\nРЕЗУЛЬТАТ ПЕРЕРАСЧЁТА")
    print(f"Адрес: {address}")
    print(f"Точка доступна для разгрузки с {format_clock(busy_until)}")
    if old and new:
        print(f"До:    машина {old[0]}, позиция {old[1]}, прибытие {format_clock(old[3].arrival_minutes)}")
        print(f"После: машина {new[0]}, позиция {new[1]}, прибытие {format_clock(new[3].arrival_minutes)}")
        if old[0] == new[0]:
            print("Машина не изменилась; проверен порядок посещения адресов.")
        else:
            print("Адрес переназначен другому автомобилю.")
        print(f"Ожидание в новом расписании: {new[3].wait_minutes} мин")
        print(f"Возврат машины: {format_clock(new[2].finish_minutes)}")
    else:
        print("Не удалось сопоставить точку в старом и новом маршруте.")
    print("Примечание: километраж генерируется, точные ETA появятся после подключения матрицы дорог.")


def simulate_busy(data: dict[str, Any], planner: RoutePlanner, baseline, drivers: int):
    print("\nЗАГРУЖЕННОСТЬ ТОРГОВОЙ ТОЧКИ")
    print("Введите ID доставки из JSON (например point_51).")
    point_id = input("ID точки: ").strip()
    found = find_point(data, point_id)
    if not found:
        print("Такой ID не найден в загруженном JSON.")
        return baseline
    address, name = found
    print(f"Выбрано: {name} — {address}")
    try:
        busy_until = parse_clock(input("До какого времени точка занята? (HH:MM): ").strip())
        if busy_until is None:
            raise ValueError("Время не задано")
        updated = plan_with_drivers(data, planner, drivers, busy_until_by_address={address: busy_until})
    except (ValueError, PlanningError) as error:
        print(f"Перерасчёт не выполнен: {error}")
        return baseline
    print_replan_diff(baseline, updated, address, busy_until)
    log("ПЕРЕСЧЁТ", f"Точка {point_id} ({address}) занята до {format_clock(busy_until)} — план пересчитан")
    print_summary(updated, planner.settings)
    if input("Показать подробные маршруты? [д/н]: ").strip().casefold() in {"д", "да", "y", "yes"}:
        print_route_details(updated)
    return updated


# ---------------------------------------------------------------- сводки

def show_input_info(data: dict[str, Any], path: Path, planner: RoutePlanner, drivers: int) -> None:
    stops = build_stops(data)
    fleet = expand_fleet(data)
    total_weight = sum(stop.demand_kg for stop in stops)
    total_capacity = sum(vehicle.capacity_kg for vehicle in fleet)
    print("\n" + "=" * 88)
    print("ВХОДНЫЕ ДАННЫЕ ПРОВЕРЕНЫ")
    print("=" * 88)
    print(f"Файл: {path}")
    print(f"Заявок: {len(data['delivery_points'])} | физических адресов: {len(stops)}")
    print(f"Водителей: {drivers} | машин в парке: {len(fleet)} | "
          f"грузоподъёмность: {total_capacity:,} кг".replace(",", " "))
    print("Водитель за смену может сделать несколько рейсов: вернулся на базу — взял следующую машину.")
    print(f"Суммарный груз: {total_weight:,} кг".replace(",", " "))
    if total_weight > total_capacity:
        print("ВНИМАНИЕ: вес заявок превышает грузоподъёмность парка — часть уйдёт в НЕ РАЗВЕЗЕНО.")
    print(f"Смена: {planner.settings.shift_start}–{planner.settings.shift_end}, обед 13:00–14:00 — "
          f"лимит работы {planner.settings.max_workday_hours:g} ч (без учёта обеда)")
    print(f"Погрузка: {planner.settings.loading_minutes} мин | "
          f"разгрузка: {planner.settings.unloading_minutes} мин на адрес")
    print(f"Минимальная загрузка машины: {planner.settings.min_load_ratio:.0%} — рейс с меньшей загрузкой не выезжает")
    print(f"Километраж генерируется: адрес {KM_MIN:g}–{KM_MAX:g} км от базы, скорость {DEFAULT_SPEED_KMH:g} км/ч")


def show_constraints(data: dict[str, Any], planner: RoutePlanner) -> None:
    constraints = data.get("constraints", {})
    print("\nОГРАНИЧЕНИЯ ПЛАНИРОВАНИЯ")
    print(f"Минимальная загрузка автомобиля: {planner.settings.min_load_ratio:.0%}")
    print(f"Смена: {planner.settings.shift_start}–{planner.settings.shift_end}, обед 13:00–14:00 (1 ч); "
          f"работа не более {planner.settings.max_workday_hours:g} ч без учёта обеда")
    print("Если развезти всё нельзя — частичная доставка: максимум веса, приоритетные точки первыми")
    print("Точка может требовать тип машины: поле vehicle_type у delivery_points")
    print(f"Погрузка: {planner.settings.loading_minutes} мин | разгрузка: {planner.settings.unloading_minutes} мин на адрес")
    print("Машина выполняет один рейс; водитель за смену может сменить несколько машин:")
    print("вернулся на базу — взял следующую, пока суммарная работа <= 8 ч и возврат до конца смены.")
    print("Рейс с загрузкой ниже 90% не выезжает; рейс, не влезающий в смену, не отправляется.")
    print(f"Ограниченные улицы: {', '.join(constraints.get('restricted_streets', [])) or 'не заданы'} "
          f"(только транспорт до {constraints.get('restricted_vehicle_capacity', 1500)} кг / с флагом restricted_to_small_streets)")
    print(f"Километраж генерируется: {KM_MIN:g}–{KM_MAX:g} км от базы, скорость {DEFAULT_SPEED_KMH:g} км/ч (детерминированно).")
    print("delivery_window — самое раннее время приёмки; время до 08:00 считается доступностью к началу смены.")
    print("Цели: уложиться в окно доставки, сократить суммарный пробег и число задействованных машин.")


# ---------------------------------------------------------------- меню

def run_menu(data: dict[str, Any], data_path: Path, planner: RoutePlanner, baseline, drivers: int) -> int:
    current = baseline
    while True:
        print("\n" + "-" * 60)
        print(f"ПЛАНИРОВЩИК | файл: {data_path.name} | водителей: {drivers}")
        print(MENU.strip())
        try:
            choice = input("Выбор: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nВыход.")
            return 0

        if choice == "1":
            print_summary(current, planner.settings)
        elif choice == "2":
            print_route_details(current)
        elif choice == "3":
            show_points(data)
        elif choice == "4":
            show_vehicles(data, drivers)
        elif choice == "5":
            result = change_drivers(data, planner)
            if result is not None:
                drivers, current = result
        elif choice == "6":
            current = simulate_busy(data, planner, current, drivers)
        elif choice == "7":
            reloaded = load_another_json(planner)
            if reloaded is not None:
                data, data_path, planner, drivers, current = reloaded
                print_summary(current, planner.settings)
        elif choice == "8":
            folder = input("Папка для файлов [output]: ").strip() or "output"
            try:
                paths = export_plan(current, folder)
                log("ЭКСПОРТ", "Сохранены файлы: " + ", ".join(p.name for p in paths) + f" -> {folder}")
                print("Экспорт завершён:")
                for path in paths:
                    print(f"  {path}")
            except OSError as error:
                print(f"Ошибка экспорта: {error}")
        elif choice == "9":
            show_constraints(data, planner)
        elif choice == "0":
            log("СЕССИЯ", "Работа завершена пользователем")
            print("Работа завершена.")
            return 0
        else:
            print("Неизвестная команда. Выберите пункт от 0 до 9.")


def change_drivers(data: dict[str, Any], planner: RoutePlanner):
    fleet_size = len(expand_fleet(data))
    drivers = ask_driver_count(fleet_size)
    try:
        result = plan_with_drivers(data, planner, drivers)
    except PlanningError as error:
        print(f"Пересчёт не выполнен: {error}")
        return None
    print(f"Пересчитано для {drivers} водителей.")
    log("ВОДИТЕЛИ", f"Пересчёт для {drivers} водителей")
    print_summary(result, planner.settings)
    return drivers, result


def load_another_json(planner: RoutePlanner):
    new_path = ask_json_path()
    if new_path is None:
        return None
    try:
        new_data = load_problem(new_path)
        new_planner = make_planner(
            new_data,
            planner.settings.default_drive_minutes,
            planner.settings.loading_minutes,
        )
        new_drivers = ask_driver_count(len(expand_fleet(new_data)))
        print("JSON загружен. Пересчитываю распределение...")
        new_result = plan_with_drivers(new_data, new_planner, new_drivers)
    except (OSError, ValueError, PlanningError) as error:
        print(f"Не удалось построить план: {error}")
        return None
    show_input_info(new_data, new_path, new_planner, new_drivers)
    return new_data, new_path, new_planner, new_drivers, new_result


# ---------------------------------------------------------------- запуск

def make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Планировщик доставки: JSON, проверка данных, распределение по машинам, расписание и пробег"
    )
    parser.add_argument("--data", help="Путь к JSON (иначе программа попросит файл в консоли)")
    parser.add_argument("--drivers", type=int, help="Число водителей (иначе спросит в консоли)")
    parser.add_argument("--drive-minutes", type=int, default=5,
                        help="Запасная оценка времени одного участка без км-генератора, мин (по умолчанию 5)")
    parser.add_argument("--load-minutes", type=int, default=30, help="Погрузка на базе, мин (по умолчанию 30)")
    parser.add_argument("--details", action="store_true", help="Сразу показать все маршруты и завершить работу")
    parser.add_argument("--export", help="Сразу сохранить план в указанную папку и завершить работу")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = make_parser().parse_args(argv)
    session_start()
    if args.drive_minutes < 1 or args.load_minutes < 0:
        print("Ошибка: время движения должно быть >= 1 минуты, погрузка — >= 0.")
        return 2

    if args.data:
        try:
            data_path = resolve_data_path(args.data)
        except FileNotFoundError as error:
            print(f"Ошибка: {error}")
            return 2
    else:
        data_path = ask_json_path()
        if data_path is None:
            return 0

    try:
        data = load_problem(data_path)
        log("ЗАГРУЗКА", f"Файл {data_path.name}: {len(data['delivery_points'])} заявок, {len(data['vehicles'])} типов машин — валидация пройдена")
        planner = make_planner(data, args.drive_minutes, args.load_minutes)
        fleet_size = len(expand_fleet(data))
        drivers = args.drivers if args.drivers else ask_driver_count(fleet_size)
        if drivers < 1:
            print("Ошибка: должен быть хотя бы один водитель.")
            return 2
        log("ВОДИТЕЛИ", f"Смена укомплектована: {drivers} водителей (машина после рейса выбывает, водитель берёт следующую)")
        show_input_info(data, data_path, planner, drivers)
        print("\nВыполняется расчёт распределения и маршрутов...")
        result = plan_with_drivers(data, planner, drivers)
    except (OSError, ValueError, PlanningError) as error:
        log("ОШИБКА", str(error))
        print(f"\nОШИБКА: {error}")
        return 2

    print("Расчёт завершён. Подробный журнал: logs/logs.txt")
    log(
        "ИТОГ",
        f"План готов: {len(result.routes)} рейсов, водителей "
        f"{len({r.driver_number for r in result.routes})}, пробег "
        f"{sum(r.total_km for r in result.routes):.1f} км, не развезено {len(result.unassigned_stops)} адресов",
    )
    print_summary(result, planner.settings)

    if args.details:
        print_route_details(result)
    if args.export:
        try:
            for path in export_plan(result, args.export):
                print(f"Сохранено: {path}")
        except OSError as error:
            print(f"Ошибка экспорта: {error}")
            return 3
    if args.details or args.export:
        return 0
    return run_menu(data, data_path, planner, result, drivers)


if __name__ == "__main__":
    raise SystemExit(main())

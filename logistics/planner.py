from __future__ import annotations

import itertools
import math
from dataclasses import dataclass, replace

from .data_loader import build_stops, expand_fleet, format_clock, parse_clock
from .logger import log
from .models import DeliveryStop, PlanningResult, StopVisit, Vehicle, VehicleRoute
from .routing import FallbackTravelTimeProvider, TravelTimeProvider, improve_route_order, order_stops


class PlanningError(RuntimeError):
    pass


@dataclass(frozen=True)
class PlannerSettings:
    shift_start: str = "08:00"
    shift_end: str = "17:00"
    lunch_start: str = "13:00"
    lunch_minutes: int = 60
    loading_minutes: int = 30
    unloading_minutes: int = 30
    default_drive_minutes: int = 7
    max_workday_hours: float = 8.0
    min_load_ratio: float = 0.90

    @property
    def start_minutes(self) -> int:
        value = parse_clock(self.shift_start)
        assert value is not None
        return value

    @property
    def end_minutes(self) -> int:
        value = parse_clock(self.shift_end)
        assert value is not None
        return value

    @property
    def lunch_start_minutes(self) -> int:
        value = parse_clock(self.lunch_start)
        assert value is not None
        return value

    @property
    def max_workday_minutes(self) -> int:
        return int(round(self.max_workday_hours * 60))


class RoutePlanner:
    def __init__(self, settings: PlannerSettings | None = None, travel_provider: TravelTimeProvider | None = None):
        self.settings = settings or PlannerSettings()
        self.travel = travel_provider or FallbackTravelTimeProvider(self.settings.default_drive_minutes)

    def build(
        self,
        data: dict,
        busy_until_by_address: dict[str, int] | None = None,
        available_vehicles: int | None = None,
    ) -> PlanningResult:
        source_constraints = data.get("constraints", {})
        min_ratio = float(source_constraints.get("min_load_ratio", self.settings.min_load_ratio))
        max_hours = float(source_constraints.get("max_workday_hours", self.settings.max_workday_hours))
        unload_minutes = int(source_constraints.get("unload_time_minutes", self.settings.unloading_minutes))
        settings = PlannerSettings(
            shift_start=self.settings.shift_start,
            shift_end=self.settings.shift_end,
            loading_minutes=self.settings.loading_minutes,
            unloading_minutes=unload_minutes,
            default_drive_minutes=self.settings.default_drive_minutes,
            max_workday_hours=max_hours,
            min_load_ratio=min_ratio,
        )
        stops = build_stops(data, busy_until_by_address)
        fleet = expand_fleet(data)
        total_demand = sum(stop.demand_kg for stop in stops)
        all_points_count = len(data.get("delivery_points", []))
        log(
            "ПЛАН",
            f"Расчёт: {all_points_count} заявок, {len(stops)} адресов, {len(fleet)} машин, "
            f"водителей {available_vehicles if available_vehicles else 'по числу рейсов'}, груз {total_demand} кг",
        )

        # 1) Сначала пытаемся развезти всё с полной загрузкой машин (90-100%).
        # 2) Если невозможно (груза больше, чем влезает, или есть требования к типу машины) —
        #    частичная доставка: максимум развезённого веса, приоритетные точки обслуживаются первыми.
        assignment = None
        unassigned: list[DeliveryStop] = []
        if not any(stop.required_type for stop in stops):
            assignment = self._find_feasible_assignment(stops, fleet, settings.min_load_ratio)
            if assignment is not None:
                log("ПЛАН", f"Полная развозка возможна: {len(assignment)} рейсов, загрузка 90-100%")
        if assignment is None:
            settings = replace(settings, min_load_ratio=0.0)  # недогруз ожидаем, не warning
            assignment, unassigned = self._best_effort_assignment(stops, fleet)
            log(
                "ПЛАН",
                f"Полная развозка невозможна — частичная доставка: {len(assignment)} рейсов, "
                f"не развезено {len(unassigned)} адресов",
            )

        # Рейсы -> водители: водитель может сделать несколько рейсов за смену
        # (вернулся на базу — взял следующую машину), пока суммарная работа не превысит
        # лимит и возврат не выйдет за конец смены. Переработку не строим.
        planned_trips = [(vehicle, vehicle_stops) for vehicle, vehicle_stops in assignment if vehicle_stops]
        executed: list[VehicleRoute] = []
        unassigned_from_trips: list[DeliveryStop] = []
        if planned_trips:
            estimates = []
            for vehicle, vehicle_stops in planned_trips:
                estimate = self._schedule_route(vehicle, vehicle_stops, data, settings)
                estimates.append((estimate.working_minutes, len(vehicle_stops), vehicle, vehicle_stops))
            # Длинные рейсы раздаём первым — короткие потом вставляем в остаток смены.
            estimates.sort(key=lambda item: (-item[0], -item[1]))

            driver_count = available_vehicles if available_vehicles else len(estimates)
            driver_free_at = [settings.start_minutes] * driver_count
            driver_work = [0] * driver_count
            driver_lunch = [False] * driver_count
            last_vehicle: dict[int, Vehicle] = {}

            pending = [(vehicle, vehicle_stops) for _, _, vehicle, vehicle_stops in estimates]
            while pending:
                progress = False
                for trip in list(pending):
                    vehicle, vehicle_stops = trip
                    # Рейс сам длиннее смены: его никто не выполнит без переработки — не отправляем.
                    base = self._schedule_route(vehicle, vehicle_stops, data, settings)
                    if base.working_minutes > settings.max_workday_minutes:
                        pending.remove(trip)
                        unassigned_from_trips.extend(vehicle_stops)
                        progress = True
                        log(
                            "ПЛАН",
                            f"Рейс на {vehicle.vehicle_id} не отправлен: длиннее смены "
                            f"({base.working_minutes // 60} ч {base.working_minutes % 60:02d} мин) — "
                            f"{len(vehicle_stops)} адрес(ов) на {sum(s.demand_kg for s in vehicle_stops)} кг в НЕ РАЗВЕЗЕНО",
                        )
                        continue
                    for driver in sorted(range(driver_count), key=lambda i: driver_free_at[i]):
                        candidate = self._schedule_route(
                            vehicle,
                            vehicle_stops,
                            data,
                            settings,
                            start_minutes=driver_free_at[driver],
                            lunch_taken=driver_lunch[driver],
                        )
                        fits_work = driver_work[driver] + candidate.working_minutes <= settings.max_workday_minutes
                        fits_shift = candidate.finish_minutes <= settings.end_minutes
                        if fits_work and fits_shift:
                            candidate.driver_number = driver + 1
                            candidate.trip_no = sum(
                                1 for route in executed if route.driver_number == driver + 1
                            ) + 1
                            driver_free_at[driver] = candidate.finish_minutes
                            driver_work[driver] += candidate.working_minutes
                            driver_lunch[driver] = driver_lunch[driver] or candidate.lunch_minutes > 0
                            executed.append(candidate)
                            pending.remove(trip)
                            progress = True
                            previous = last_vehicle.get(driver)
                            if previous is not None:
                                log(
                                    "ВОДИТЕЛЬ",
                                    f"Водитель {driver + 1} сдал {previous.vehicle_id} "
                                    "(машина использована — больше не выезжает) и взял "
                                    f"{vehicle.vehicle_id} для рейса {candidate.trip_no}",
                                )
                            last_vehicle[driver] = vehicle
                            log(
                                "РЕЙС",
                                f"Водитель {driver + 1}, рейс {candidate.trip_no}: {vehicle.label}, "
                                f"{len(vehicle_stops)} адрес(ов), {candidate.load_kg} кг "
                                f"({candidate.utilization:.0%}), работа "
                                f"{candidate.working_minutes // 60} ч {candidate.working_minutes % 60:02d} мин, "
                                f"возврат {format_clock(candidate.finish_minutes)}",
                            )
                            break
                if not progress:
                    break

        unassigned_from_trips.extend(unassigned)
        unassigned = unassigned_from_trips
        routes = executed
        routes.sort(key=lambda route: (route.driver_number, route.trip_no))
        if unassigned and not routes:
            log("ПЛАН", "Ни один рейс не отправлен: смена не вмещает ни один маршрут")
        elif unassigned:
            log("ПЛАН", f"Осталось неразвезёнными: {len(unassigned)} адресов, {sum(s.demand_kg for s in unassigned)} кг")
        result = PlanningResult(
            routes=routes,
            all_points_count=all_points_count,
            all_stops_count=len(stops),
            total_demand_kg=total_demand,
            unassigned_stops=unassigned,
        )
        if unassigned:
            result.planner_mode = "best_effort"
        result.warnings.extend(self._global_validation(result, stops, fleet, settings))
        return result

    @staticmethod
    def _vehicle_can_serve(vehicle: Vehicle, stop: DeliveryStop) -> bool:
        """Жёсткие допуски: узкие улицы и требование к типу машины из JSON."""
        if stop.restricted and not vehicle.restricted_to_small_streets:
            return False
        if stop.required_type and vehicle.vehicle_type.casefold() != stop.required_type.casefold():
            return False
        return True

    def _best_effort_assignment(
        self,
        stops: list[DeliveryStop],
        fleet: list[Vehicle],
    ) -> tuple[list[tuple[Vehicle, list[DeliveryStop]]], list[DeliveryStop]]:
        """Частичная доставка: цель — развезти как можно больше.

        Порядок: priority-точки, затем точки с окном приёмки, затем остальные;
        внутри группы — более тяжёлые первыми. Каждой точке — машина с наименьшим
        подходящим остатком (best-fit), перегруз запрещён.
        """
        if not fleet:
            return [], list(stops)
        remaining = {v.vehicle_id: v.capacity_kg for v in fleet}
        by_id = {v.vehicle_id: v for v in fleet}
        assigned: dict[str, list[DeliveryStop]] = {v.vehicle_id: [] for v in fleet}
        unassigned: list[DeliveryStop] = []

        def group(stop: DeliveryStop) -> int:
            if stop.priority:
                return 0
            if stop.earliest_service_minutes is not None:
                return 1
            return 2

        for stop in sorted(stops, key=lambda s: (group(s), -s.demand_kg)):
            candidates = [
                vid for vid, capacity in remaining.items()
                if capacity >= stop.demand_kg and self._vehicle_can_serve(by_id[vid], stop)
            ]
            if not candidates:
                unassigned.append(stop)
                continue
            chosen = min(
                candidates,
                key=lambda vid: (remaining[vid] - stop.demand_kg, by_id[vid].capacity_kg),
            )
            assigned[chosen].append(stop)
            remaining[chosen] -= stop.demand_kg
        return [
            (by_id[vid], vehicle_stops)
            for vid, vehicle_stops in assigned.items()
            if vehicle_stops
        ], unassigned

    def _find_feasible_assignment(
        self,
        stops: list[DeliveryStop],
        fleet: list[Vehicle],
        min_ratio: float,
    ) -> list[tuple[Vehicle, list[DeliveryStop]]] | None:
        restricted_stops = [stop for stop in stops if stop.restricted]
        total_demand = sum(stop.demand_kg for stop in stops)
        min_units = max(1, math.ceil(total_demand / max(v.capacity_kg for v in fleet)))

        # Ищем сначала минимальное количество машин; при равенстве предпочитаем меньший резерв ёмкости.
        candidate_sets: list[tuple[tuple[int, int], tuple[Vehicle, ...]]] = []
        for count in range(min_units, len(fleet) + 1):
            for combo in itertools.combinations(fleet, count):
                if restricted_stops and not any(v.restricted_to_small_streets for v in combo):
                    continue
                capacity_sum = sum(v.capacity_kg for v in combo)
                minimum_sum = sum(math.ceil(v.capacity_kg * min_ratio) for v in combo)
                if capacity_sum < total_demand or minimum_sum > total_demand:
                    continue
                candidate_sets.append(((count, capacity_sum - total_demand), combo))
            if candidate_sets:
                break

        candidate_sets.sort(key=lambda item: item[0])
        for _, combo in candidate_sets:
            if restricted_stops:
                small_candidates = [v for v in combo if v.restricted_to_small_streets]
                for small_vehicle in small_candidates:
                    result = self._try_assign_with_small_vehicle(
                        stops, list(combo), small_vehicle, min_ratio
                    )
                    if result is not None:
                        return result
            else:
                result = self._pack_bins(stops, list(combo), min_ratio)
                if result is not None:
                    return result
        return None

    def _try_assign_with_small_vehicle(
        self,
        stops: list[DeliveryStop],
        vehicles: list[Vehicle],
        small_vehicle: Vehicle,
        min_ratio: float,
    ) -> list[tuple[Vehicle, list[DeliveryStop]]] | None:
        mandatory = [stop for stop in stops if stop.restricted]
        regular = [stop for stop in stops if not stop.restricted]
        mandatory_weight = sum(stop.demand_kg for stop in mandatory)
        small_capacity = small_vehicle.capacity_kg
        min_small_load = math.ceil(small_capacity * min_ratio)
        if mandatory_weight > small_capacity:
            return None

        min_extra = max(0, min_small_load - mandatory_weight)
        max_extra = small_capacity - mandatory_weight
        # Сначала стараемся не добавлять на малую машину точки с заданным временем выгрузки.
        untimed = [stop for stop in regular if stop.earliest_service_minutes is None]
        selected_extra = self._select_extra_stops(untimed, min_extra, max_extra)
        if selected_extra is None:
            selected_extra = self._select_extra_stops(regular, min_extra, max_extra)
        if selected_extra is None:
            return None

        selected_keys = {stop.key for stop in selected_extra}
        small_stops = mandatory + selected_extra
        remaining = [stop for stop in regular if stop.key not in selected_keys]
        normal_vehicles = [vehicle for vehicle in vehicles if vehicle.vehicle_id != small_vehicle.vehicle_id]
        packed = self._pack_bins(remaining, normal_vehicles, min_ratio)
        if packed is None:
            return None
        return [(small_vehicle, small_stops)] + packed

    @staticmethod
    def _select_extra_stops(stops: list[DeliveryStop], min_weight: int, max_weight: int) -> list[DeliveryStop] | None:
        if min_weight <= 0:
            return []
        states: dict[int, tuple[int, ...]] = {0: ()}
        for index, stop in enumerate(stops):
            weight = stop.demand_kg
            snapshot = list(states.items())
            for current_weight, selected in snapshot:
                new_weight = current_weight + weight
                if new_weight <= max_weight and new_weight not in states:
                    states[new_weight] = selected + (index,)
        candidates = [
            (len(selected), -weight, selected)
            for weight, selected in states.items()
            if min_weight <= weight <= max_weight
        ]
        if not candidates:
            return None
        _, _, chosen = min(candidates)
        return [stops[index] for index in chosen]

    def _pack_bins(
        self,
        stops: list[DeliveryStop],
        vehicles: list[Vehicle],
        min_ratio: float,
    ) -> list[tuple[Vehicle, list[DeliveryStop]]] | None:
        if not vehicles:
            return [] if not stops else None
        remaining = list(stops)
        remaining_weight = sum(stop.demand_kg for stop in remaining)
        vehicles_sorted = sorted(vehicles, key=lambda v: v.capacity_kg, reverse=True)
        assignments: list[tuple[Vehicle, list[DeliveryStop]]] = []

        if remaining_weight > sum(v.capacity_kg for v in vehicles_sorted):
            return None
        if remaining_weight < sum(math.ceil(v.capacity_kg * min_ratio) for v in vehicles_sorted):
            return None

        for index, vehicle in enumerate(vehicles_sorted):
            future = vehicles_sorted[index + 1 :]
            future_capacity = sum(v.capacity_kg for v in future)
            future_minimum = sum(math.ceil(v.capacity_kg * min_ratio) for v in future)
            lower = max(math.ceil(vehicle.capacity_kg * min_ratio), remaining_weight - future_capacity)
            upper = min(vehicle.capacity_kg, remaining_weight - future_minimum)
            if lower > upper:
                return None

            combined_capacity = vehicle.capacity_kg + future_capacity
            ideal = vehicle.capacity_kg * remaining_weight / combined_capacity
            # Разгрузка занимает фиксированное время на адрес, поэтому число остановок
            # балансируем почти равномерно между машинами, а не пропорционально тоннажу.
            ideal_count = len(remaining) / (len(vehicles_sorted) - index)
            chosen = self._subset_sum_near(remaining, lower, upper, ideal, ideal_count)
            if chosen is None:
                return None
            selected_indices, selected_weight = chosen
            selected_index_set = set(selected_indices)
            chosen_stops = [stop for i, stop in enumerate(remaining) if i in selected_index_set]
            assignments.append((vehicle, chosen_stops))
            remaining = [stop for i, stop in enumerate(remaining) if i not in selected_index_set]
            remaining_weight -= selected_weight

        if remaining:
            return None
        return assignments

    @staticmethod
    def _subset_sum_near(
        stops: list[DeliveryStop],
        lower: int,
        upper: int,
        target: float,
        target_count: float,
    ) -> tuple[tuple[int, ...], int] | None:
        # Для одного и того же тоннажа сохраняем набор с количеством адресов,
        # наиболее близким к ожидаемому для этой машины.
        states: dict[int, tuple[int, ...]] = {0: ()}
        for index, stop in enumerate(stops):
            weight = stop.demand_kg
            snapshot = list(states.items())
            for current_weight, selected in snapshot:
                new_weight = current_weight + weight
                if new_weight > upper:
                    continue
                candidate = selected + (index,)
                previous = states.get(new_weight)
                if previous is None or (abs(len(candidate) - target_count), len(candidate)) < (abs(len(previous) - target_count), len(previous)):
                    states[new_weight] = candidate
        candidates = [weight for weight in states if lower <= weight <= upper]
        if not candidates:
            return None
        # Число адресов важнее тоннажа: у каждого адреса фиксированная разгрузка,
        # поэтому переработка зависит именно от количества остановок (90-100% по весу
        # гарантируют границы lower/upper).
        chosen_weight = min(
            candidates,
            key=lambda weight: (
                abs(len(states[weight]) - target_count),
                abs(weight - target),
                -weight,
            ),
        )
        return states[chosen_weight], chosen_weight

    def _schedule_route(
        self,
        vehicle: Vehicle,
        stops: list[DeliveryStop],
        data: dict,
        settings: PlannerSettings,
        start_minutes: int | None = None,
        lunch_taken: bool = False,
    ) -> VehicleRoute:
        """Строит один рейс от заданного момента времени.

        lunch_taken=True — водитель уже пообедал в предыдущем рейсе, пауза не вставляется.
        """
        start = settings.start_minutes if start_minutes is None else start_minutes
        lunch_start = settings.lunch_start_minutes
        lunch_end = lunch_start + settings.lunch_minutes
        if lunch_taken:
            lunch_start = lunch_end = 24 * 60  # окно обеда закрыто

        def through_lunch(start_ts: int, duration: int) -> tuple[int, int, int]:
            """(фактическое начало, конец, сколько минут обеда внутри участка)."""
            if start_ts >= lunch_end or start_ts + duration <= lunch_start:
                return start_ts, start_ts + duration, 0
            if start_ts >= lunch_start:
                # участок пришёлся на обед: фактически начнётся после обеда
                return lunch_end, lunch_end + duration, lunch_end - start_ts
            # участок начался до обеда и пересекает его: обед целиком внутри
            return start_ts, start_ts + duration + settings.lunch_minutes, settings.lunch_minutes

        depot_key = str(data.get("depot", {}).get("address", "DEPO"))
        ordered = order_stops(
            stops,
            self.travel,
            depot_key,
            start,
            settings.loading_minutes,
            settings.unloading_minutes,
        )
        # Локальный поиск порядка: раньше возврат на базу — без переработки и с меньшим пробегом.
        ordered = improve_route_order(
            ordered,
            depot_key,
            self.travel,
            start,
            settings.loading_minutes,
            settings.unloading_minutes,
        )
        current_time = start + settings.loading_minutes
        current_location = depot_key
        current_street = ""
        visits: list[StopVisit] = []
        total_travel = 0
        total_km = 0.0
        total_lunch = 0
        route_warnings: list[str] = []
        km_of = getattr(self.travel, "km", None)

        for stop in ordered:
            drive = self.travel.minutes(current_location, stop.key, current_street, stop.street)
            leg_km = km_of(current_location, stop.key, current_street, stop.street) if km_of else 0.0
            arrival, _, road_lunch = through_lunch(current_time, drive)
            ready = arrival
            if stop.earliest_service_minutes is not None and stop.earliest_service_minutes >= start:
                ready = max(ready, stop.earliest_service_minutes)
            if stop.busy_until is not None:
                ready = max(ready, stop.busy_until)
            service_start, service_end, service_lunch = through_lunch(ready, settings.unloading_minutes)
            wait = max(0, ready - arrival)
            visits.append(
                StopVisit(
                    stop=stop,
                    arrival_minutes=arrival,
                    service_start_minutes=service_start,
                    service_end_minutes=service_end,
                    travel_minutes=drive,
                    wait_minutes=wait,
                    travel_km=leg_km,
                )
            )
            if stop.busy_until is not None and wait > 0:
                route_warnings.append(
                    f"{stop.address}: ожидание освобождения точки {wait} мин"
                )
            current_time = service_end
            current_location = stop.key
            current_street = stop.street
            total_travel += drive
            total_km += leg_km
            total_lunch += road_lunch + service_lunch

        return_drive = self.travel.minutes(current_location, depot_key, current_street, "")
        return_km = km_of(current_location, depot_key, current_street, "") if km_of else 0.0
        _, return_arrival, return_lunch = through_lunch(current_time, return_drive)
        total_travel += return_drive
        total_km += return_km
        total_lunch += return_lunch
        finish = return_arrival
        working_minutes = finish - start - total_lunch
        load = sum(stop.demand_kg for stop in stops)
        if load > vehicle.capacity_kg:
            route_warnings.append("ПЕРЕГРУЗ: превышена грузоподъёмность")
        if load < math.ceil(vehicle.capacity_kg * settings.min_load_ratio):
            route_warnings.append("Недогруз: загрузка ниже установленного минимума")
        if working_minutes > settings.max_workday_minutes:
            route_warnings.append(
                f"Рабочее время {working_minutes // 60} ч {working_minutes % 60:02d} мин "
                f"превышает лимит {settings.max_workday_hours:g} ч"
            )
        if finish > settings.end_minutes:
            route_warnings.append(
                f"Возврат в {format_clock(finish)} позже окончания смены {settings.shift_end}"
            )
        return VehicleRoute(
            vehicle=vehicle,
            stops=visits,
            load_kg=load,
            travel_minutes=total_travel,
            working_minutes=working_minutes,
            finish_minutes=finish,
            warnings=route_warnings,
            total_km=round(total_km, 1),
            lunch_minutes=total_lunch,
        )

    @staticmethod
    def _global_validation(
        result: PlanningResult,
        source_stops: list[DeliveryStop],
        fleet: list[Vehicle],
        settings: PlannerSettings,
    ) -> list[str]:
        warnings: list[str] = []
        assigned_stops = [visit.stop for route in result.routes for visit in route.stops]
        assigned_keys = [stop.key for stop in assigned_stops]

        if result.unassigned_stops:
            warnings.append(
                f"Частичная доставка: не распределено {len(result.unassigned_stops)} адресов "
                f"на {result.unassigned_weight_kg} кг — не хватает грузоподъёмности или допуска машины"
            )
        elif result.assigned_points_count != result.all_points_count:
            warnings.append(
                f"Покрытие заявок не пройдено: назначено {result.assigned_points_count} из {result.all_points_count}"
            )
        if len(assigned_keys) != len(set(assigned_keys)):
            warnings.append("Один или несколько физических адресов назначены более чем одной машине")
        if result.assigned_demand_kg != result.total_demand_kg:
            warnings.append("Сумма назначенного груза не совпадает с исходной заявкой")

        restricted = {stop.key for stop in source_stops if stop.restricted}
        for route in result.routes:
            route_keys = {visit.stop.key for visit in route.stops}
            if restricted.intersection(route_keys) and not route.vehicle.restricted_to_small_streets:
                warnings.append(f"Нарушено ограничение улиц: машина {route.vehicle.vehicle_id} не допущена на узкие улицы")
            if route.load_kg > route.vehicle.capacity_kg:
                warnings.append(f"Перегруз автомобиля {route.vehicle.vehicle_id}")
            if route.load_kg < math.ceil(route.vehicle.capacity_kg * settings.min_load_ratio):
                warnings.append(f"Недогруз автомобиля {route.vehicle.vehicle_id}: {route.load_kg} кг")

        unassigned = {stop.key for stop in source_stops} - set(assigned_keys)
        if unassigned:
            warnings.append(f"Не распределено физических адресов: {len(unassigned)}")
        if len(result.routes) > len(fleet):
            warnings.append("Количество рейсов превышает количество доступных машин")
        if any(not route.stops for route in result.routes):
            warnings.append("Обнаружен пустой рейс")

        # Время — отдельный тип нарушения: оно не отменяет корректность тоннажного распределения.
        time_violations = sum(
            1 for route in result.routes
            if route.working_minutes > settings.max_workday_minutes or route.finish_minutes > settings.end_minutes
        )
        if time_violations:
            warnings.append(
                f"Временные ограничения нарушены в {time_violations} из {len(result.routes)} рейсов; "
                "проверьте фактическую матрицу времени движения и число адресов на рейс"
            )

        if not warnings:
            warnings.append("Все базовые проверки распределения пройдены")
        return warnings

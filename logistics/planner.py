from __future__ import annotations

import itertools
import math
from dataclasses import dataclass, replace

from .data_loader import build_stops, expand_fleet, format_clock, parse_clock
from .logger import log
from .models import DeliveryStop, PlanningResult, StopVisit, Vehicle, VehicleRoute
from .routing import (
    FallbackTravelTimeProvider,
    TravelTimeProvider,
    improve_route_order,
    improve_route_order_2opt,
    order_stops,
    route_schedule_minutes,
)


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
    enforce_min_load: bool = False

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
            enforce_min_load=bool(source_constraints.get("enforce_min_load", self.settings.enforce_min_load)),
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
        # Разгрузка фиксирована на адрес, поэтому у рейса есть предел числа адресов по времени смены:
        # работа = погрузка + N * (разгрузка + средний перегон) <= лимит смены.
        depot_key = str(data.get("depot", {}).get("address", "DEPO"))
        leg_samples = [self.travel.minutes(depot_key, stop.key, "", stop.street) for stop in stops]
        avg_leg = sum(leg_samples) / len(leg_samples) if leg_samples else float(settings.default_drive_minutes)
        max_stops_per_trip = max(
            1,
            int((settings.max_workday_minutes - settings.loading_minutes) // (settings.unloading_minutes + avg_leg)),
        )
        log(
            "ПЛАН",
            f"Лимит адресов на рейс по времени смены: {max_stops_per_trip} "
            f"(средний перегон {avg_leg:.1f} мин, разгрузка {settings.unloading_minutes} мин/адрес)",
        )
        assignment = None
        unassigned: list[DeliveryStop] = []
        if not any(stop.required_type for stop in stops):
            assignment = self._find_feasible_assignment(
                stops, fleet, settings.min_load_ratio, max_stops=max_stops_per_trip
            )
            if assignment is not None:
                log("ПЛАН", f"Полная развозка возможна: {len(assignment)} рейсов, загрузка 90-100%")
        if assignment is None:
            settings = replace(settings, min_load_ratio=0.0)  # недогруз ожидаем, не warning
            greedy, greedy_unassigned = self._best_effort_assignment(stops, fleet, settings=settings, data=data)
            greedy, greedy_unassigned = self._improve_trip_weights(greedy, greedy_unassigned, data, settings)

            alt, alt_unassigned = self._fill_by_capacity(
                stops, fleet, data, settings, max_stops=None
            )

            def packed_weight(pack: list[tuple[Vehicle, list[DeliveryStop]]]) -> int:
                return sum(s.demand_kg for _, trip_stops in pack for s in trip_stops)

            def underloaded(pack: list[tuple[Vehicle, list[DeliveryStop]]]) -> int:
                return sum(
                    1 for v, trip_stops in pack
                    if sum(s.demand_kg for s in trip_stops) < math.ceil(v.capacity_kg * min_ratio)
                )

            greedy_score = (packed_weight(greedy), -underloaded(greedy), -len(greedy))
            alt_score = (packed_weight(alt), -underloaded(alt), -len(alt)) if alt else (-1, 0, 0)
            # Приоритет критерия «загрузка не менее 90%»: выбираем набор без недогруженных
            # рейсов, если он развозит не меньше 90% веса лучшего по весу варианта.
            use_alt = alt is not None and (
                (underloaded(alt) < underloaded(greedy) and packed_weight(alt) >= 0.9 * packed_weight(greedy))
                or alt_score > greedy_score
            )
            if use_alt:
                assignment, unassigned = alt, alt_unassigned
                log(
                    "ПЛАН",
                    f"Стратегия «90-100%»: {len(assignment)} рейсов, "
                    f"развезено {packed_weight(assignment)} кг",
                )
            else:
                assignment, unassigned = greedy, greedy_unassigned
            delivered = packed_weight(assignment)
            log(
                "ПЛАН",
                f"Полная развозка невозможна — частичная доставка: {len(assignment)} рейсов, "
                f"развезено {delivered} кг, не развезено {len(unassigned)} адресов",
            )

        # Правило «машина не выезжает меньше 90%»: сначала пробуем переложить
        # заявки из недогруженных рейсов в соседние, чтобы не гонять полупустую машину.
        assignment, rebalanced = self._rebalance_underloaded(assignment, data, settings, min_ratio)
        if rebalanced:
            log("ПЛАН", f"Перебалансировка: {rebalanced} адрес(ов) перенесено из недогруженных рейсов")

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

            # Без явного лимита водителей доступен весь парк: иначе на рейс,
            # который не успел получить водителя, заявки уходили в «не развезено».
            driver_count = available_vehicles if available_vehicles else len(fleet)
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

            # Второй рейс: водитель, освободившийся рано, берёт простаивающую машину
            # (каждая машина — по-прежнему один рейс) и развозит остаток, пока хватает
            # смены. Так простой водителя не пропадает, а лёгкие адреса, не попавшие
            # в первые рейсы по критерию загрузки, всё равно доставляются.
            used_vehicle_ids = {route.vehicle.vehicle_id for route in executed}
            pool = list(unassigned)
            for vehicle in sorted(
                (v for v in fleet if v.vehicle_id not in used_vehicle_ids),
                key=lambda v: -v.capacity_kg,
            ):
                if not pool:
                    break
                driver = max(
                    range(driver_count),
                    key=lambda i: min(
                        settings.end_minutes - driver_free_at[i],
                        settings.max_workday_minutes - driver_work[i],
                    ),
                )
                slack = min(
                    settings.end_minutes - driver_free_at[driver],
                    settings.max_workday_minutes - driver_work[driver],
                )
                if slack < settings.loading_minutes + settings.unloading_minutes:
                    break
                extra = self._build_second_trip(
                    vehicle,
                    pool,
                    data,
                    settings,
                    driver_free_at[driver],
                    driver_lunch[driver],
                    driver_work[driver],
                )
                if extra is None:
                    continue
                extra.driver_number = driver + 1
                extra.trip_no = sum(1 for r in executed if r.driver_number == driver + 1) + 1
                driver_free_at[driver] = extra.finish_minutes
                driver_work[driver] += extra.working_minutes
                driver_lunch[driver] = driver_lunch[driver] or extra.lunch_minutes > 0
                executed.append(extra)
                delivered = {visit.stop.key for visit in extra.stops}
                pool = [s for s in pool if s.key not in delivered]
                log(
                    "РЕЙС",
                    f"Водитель {driver + 1}, рейс {extra.trip_no}: {vehicle.label}, "
                    f"второй рейс на свободной машине, {len(extra.stops)} адрес(ов), "
                    f"{extra.load_kg} кг, работа {extra.working_minutes // 60} ч "
                    f"{extra.working_minutes % 60:02d} мин, возврат {format_clock(extra.finish_minutes)}",
                )
            unassigned = pool

        # Жёсткое правило «не выпускать машину с загрузкой ниже 90%»:
        # такие рейсы снимаются, их заявки честно уходят в «не развезено»,
        # а логист решает — отправить всё равно или пересчитать/догрузить.
        if settings.enforce_min_load and min_ratio > 0:
            kept: list[VehicleRoute] = []
            for route in executed:
                minimum = math.ceil(route.vehicle.capacity_kg * min_ratio)
                if route.load_kg < minimum:
                    unassigned_from_trips.extend(visit.stop for visit in route.stops)
                    log(
                        "ПЛАН",
                        f"Рейс {route.vehicle.vehicle_id} снят: загрузка {route.load_kg} кг "
                        f"< {minimum} кг (90%) — решает логист",
                    )
                else:
                    kept.append(route)
            executed = kept

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

    def _rebalance_underloaded(
        self,
        assignment: list[tuple[Vehicle, list[DeliveryStop]]],
        data: dict,
        settings: PlannerSettings,
        min_ratio: float,
    ) -> tuple[list[tuple[Vehicle, list[DeliveryStop]]], int]:
        """Убирает недогруженные рейсы: переносит их заявки в другие машины.

        Правило «машина не выезжает меньше 90%» проверяется до расписания:
        лёгкие заявки из недогруженного рейса по одной переезжают в те машины,
        где есть место по весу и времени. Рейс, который опустел, из плана уходит;
        рейс, который добрал норму, остаётся.
        """
        min_ratio = min_ratio or 0.0
        if min_ratio <= 0 or not assignment:
            return assignment, 0

        trips: list[list] = [[vehicle, list(stops)] for vehicle, stops in assignment if stops]
        moved = 0

        def load(stops: list[DeliveryStop]) -> int:
            return sum(stop.demand_kg for stop in stops)

        def has_norm(vehicle: Vehicle, stops: list[DeliveryStop]) -> bool:
            return load(stops) >= math.ceil(vehicle.capacity_kg * min_ratio)

        for _ in range(4):
            sparse = [trip for trip in trips if not has_norm(trip[0], trip[1])]
            if not sparse:
                break
            changed = False
            for vehicle, stops in list(sparse):
                for stop in sorted(list(stops), key=lambda item: item.demand_kg):
                    candidates = []
                    for target_vehicle, target_stops in trips:
                        if target_vehicle is vehicle:
                            continue
                        if load(target_stops) + stop.demand_kg > target_vehicle.capacity_kg:
                            continue
                        if not self._vehicle_can_serve(target_vehicle, stop):
                            continue
                        if not self._trip_fits_shift(target_stops + [stop], data, settings):
                            continue
                        free = target_vehicle.capacity_kg - load(target_stops) - stop.demand_kg
                        candidates.append((free, target_vehicle.vehicle_id, target_stops))
                    if not candidates:
                        continue
                    candidates.sort()
                    _, _, target_stops = candidates[0]
                    target_stops.append(stop)
                    stops.remove(stop)
                    moved += 1
                    changed = True
                    if not stops or has_norm(vehicle, stops):
                        break
            if not changed:
                break

        return [(vehicle, stops) for vehicle, stops in trips if stops], moved

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
        settings: PlannerSettings | None = None,
        data: dict | None = None,
    ) -> tuple[list[tuple[Vehicle, list[DeliveryStop]]], list[DeliveryStop]]:
        """Частичная доставка: цель — развезти как можно больше.

        Порядок: priority-точки, затем точки с окном приёмки, затем остальные;
        внутри группы — более тяжёлые первыми. Каждой точке — машина с наименьшим
        подходящим остатком (best-fit), перегруз запрещён. Если заданы settings
        и data, дополнительно проверяется, что рейс влезает в смену: разгрузка
        фиксирована на адрес, поэтому лимит именно по времени, а не только по весу.
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
            # Best-fit по остатку, затем ближайший запас вместимости.
            candidates.sort(key=lambda vid: (remaining[vid] - stop.demand_kg, by_id[vid].capacity_kg))
            chosen = candidates[0]
            if settings is not None and data is not None:
                chosen = next(
                    (
                        vid for vid in candidates
                        if self._trip_fits_shift(assigned[vid] + [stop], data, settings)
                    ),
                    None,
                )
            if chosen is None:
                unassigned.append(stop)
                continue
            assigned[chosen].append(stop)
            remaining[chosen] -= stop.demand_kg
        return [
            (by_id[vid], vehicle_stops)
            for vid, vehicle_stops in assigned.items()
            if vehicle_stops
        ], unassigned

    def _improve_trip_weights(
        self,
        assignment: list[tuple[Vehicle, list[DeliveryStop]]],
        unassigned: list[DeliveryStop],
        data: dict,
        settings: PlannerSettings,
    ) -> tuple[list[tuple[Vehicle, list[DeliveryStop]]], list[DeliveryStop]]:
        """Догрузка в частичном режиме: лёгкую заявку в рейсе меняем на более тяжёлую из неразвезённых.

        Лимит смены — число остановок (разгрузка фиксирована на адрес), поэтому обмен
        строго 1:1: количество адресов в рейсе не растёт, время проверяется честно
        по матрице. Результат: больше развезённый вес и выше загрузка машины
        (критерий 90-100% выполняется там, где это физически возможно).
        Приоритетные, узкоуличные и требующие определённый тип машины заявки
        из рейса не изымаются.
        """
        if not assignment or not unassigned:
            return assignment, unassigned

        trips: list[list] = [[vehicle, list(stops)] for vehicle, stops in assignment]
        pool: list[DeliveryStop] = sorted(unassigned, key=lambda s: -s.demand_kg)

        def fixed(stop: DeliveryStop) -> bool:
            return bool(stop.priority or stop.required_type or stop.restricted)

        swaps = 0
        gained_kg = 0
        for _pass in range(3):
            improved = False
            for trip in trips:
                vehicle, stops = trip
                if not stops:
                    continue
                load = sum(s.demand_kg for s in stops)
                removable = sorted(
                    (s for s in stops if not fixed(s)),
                    key=lambda s: s.demand_kg,
                )
                for out_stop in removable:
                    capacity_after = vehicle.capacity_kg - (load - out_stop.demand_kg)
                    tested = 0
                    for cand in pool:
                        if cand.demand_kg <= out_stop.demand_kg:
                            continue  # pool отсортирован по убыванию веса: дальше только легче
                        if cand.demand_kg > capacity_after:
                            continue
                        if not self._vehicle_can_serve(vehicle, cand):
                            continue
                        tested += 1
                        if tested > 6:
                            break
                        new_stops = [s for s in stops if s.key != out_stop.key] + [cand]
                        if self._trip_fits_shift(new_stops, data, settings):
                            stops.remove(out_stop)
                            stops.append(cand)
                            pool.remove(cand)
                            pool.append(out_stop)
                            gained_kg += cand.demand_kg - out_stop.demand_kg
                            swaps += 1
                            load = sum(s.demand_kg for s in stops)
                            improved = True
                            break
            if not improved:
                break

        if swaps:
            log(
                "ПЛАН",
                f"Догрузка тяжёлыми заявками: {swaps} обмен(ов), развезённый вес вырос на {gained_kg} кг",
            )
        return [(vehicle, stops) for vehicle, stops in trips], pool

    def _fill_by_capacity(
        self,
        stops: list[DeliveryStop],
        fleet: list[Vehicle],
        data: dict,
        settings: PlannerSettings,
        max_stops: int | None = None,
    ) -> tuple[list[tuple[Vehicle, list[DeliveryStop]]], list[DeliveryStop]]:
        """Стратегия «каждая машина 90-100%»: набираем рейсы по очереди от большой
        машины к малой, каждая — до полной/почти полной загрузки по весу в пределах
        лимита адресов и честного времени смены. Машина, которую не удалось добить
        до 90%, не используется (сокращение транспорта), её заявки остаются
        в неразвезённых. Машина с допуском на узкие улицы используется обязательно,
        если на ней есть обязательные заявки.
        """
        remaining = list(stops)
        if not remaining or not fleet:
            return [], list(remaining)

        def group(stop: DeliveryStop) -> int:
            if stop.priority:
                return 0
            if stop.earliest_service_minutes is not None:
                return 1
            return 2

        assignment: list[tuple[Vehicle, list[DeliveryStop]]] = []
        small_fleet = [v for v in fleet if v.restricted_to_small_streets]
        big_fleet = sorted(
            (v for v in fleet if not v.restricted_to_small_streets),
            key=lambda v: -v.capacity_kg,
        )
        for vehicle in big_fleet + small_fleet:
            if not remaining:
                break
            mandatory: list[DeliveryStop] = []
            if vehicle.restricted_to_small_streets:
                mandatory = sorted(
                    (s for s in remaining if s.restricted and self._vehicle_can_serve(vehicle, s)),
                    key=lambda s: -s.demand_kg,
                )
            trip = self._fill_vehicle(
                remaining, vehicle, mandatory, max_stops, data, settings, group
            )
            if trip is None:
                continue  # 90% набрать не удалось — машина остаётся в гараже
            used = {s.key for s in trip}
            remaining = [s for s in remaining if s.key not in used]
            assignment.append((vehicle, trip))

        # Догрузка: в рейсы со свободным весом и лимитом адресов добавляем
        # самые тяжёлые из остатков (больше развезённого веса).
        progress = True
        while progress and remaining:
            progress = False
            for vehicle, trip in assignment:
                if max_stops is not None and len(trip) >= max_stops:
                    continue
                spare = vehicle.capacity_kg - sum(s.demand_kg for s in trip)
                if spare <= 0:
                    continue
                for stop in sorted(remaining, key=lambda s: (-s.demand_kg, s.key)):
                    if stop.demand_kg > spare or not self._vehicle_can_serve(vehicle, stop):
                        continue
                    if self._trip_fits_shift(trip + [stop], data, settings):
                        trip.append(stop)
                        remaining.remove(stop)
                        progress = True
                        break
        return assignment, remaining

    def _fill_vehicle(
        self,
        remaining: list[DeliveryStop],
        vehicle: Vehicle,
        mandatory: list[DeliveryStop],
        max_stops: int | None,
        data: dict,
        settings: PlannerSettings,
        group,
    ) -> list[DeliveryStop] | None:
        """Набирает один рейс: тяжёлые заявки первыми, пока влезают по весу и времени.

        Возвращает None, если до 90% загрузки набрать не удалось (машина не используется).
        Для машины с обязательными заявками на узкие улицы нижняя граница не проверяется:
        доставить их важнее критерия загрузки.
        """
        mandatory = [
            s for s in mandatory
            if self._vehicle_can_serve(vehicle, s)
        ]
        mandatory_weight = sum(s.demand_kg for s in mandatory)
        if mandatory_weight > vehicle.capacity_kg:
            return None
        trip: list[DeliveryStop] = list(mandatory)
        load = mandatory_weight
        taken = {s.key for s in trip}
        ordered = sorted(
            (
                s for s in remaining
                if s.key not in taken and self._vehicle_can_serve(vehicle, s)
            ),
            key=lambda s: (group(s), -s.demand_kg, s.key),
        )
        for stop in ordered:
            if max_stops is not None and len(trip) >= max_stops:
                break
            if load + stop.demand_kg > vehicle.capacity_kg:
                continue
            if self._trip_fits_shift(trip + [stop], data, settings):
                trip.append(stop)
                load += stop.demand_kg
        lower = 0 if mandatory else math.ceil(vehicle.capacity_kg * 0.9)
        if load < lower:
            return None
        return trip

    def _trip_fits_shift(
        self,
        stops: list[DeliveryStop],
        data: dict,
        settings: PlannerSettings,
    ) -> bool:
        """Грубая проверка (жадный порядок без локального поиска): рейс влезает в смену?"""
        if not stops:
            return True
        depot_key = str(data.get("depot", {}).get("address", "DEPO"))
        ordered = order_stops(
            stops,
            self.travel,
            depot_key,
            settings.start_minutes,
            settings.loading_minutes,
            settings.unloading_minutes,
        )
        finish, work = route_schedule_minutes(
            ordered,
            depot_key,
            self.travel,
            settings.start_minutes,
            settings.loading_minutes,
            settings.unloading_minutes,
            settings.lunch_start_minutes,
            settings.lunch_minutes,
        )
        return work <= settings.max_workday_minutes and finish <= settings.end_minutes

    def _find_feasible_assignment(
        self,
        stops: list[DeliveryStop],
        fleet: list[Vehicle],
        min_ratio: float,
        max_stops: int | None = None,
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
                        stops, list(combo), small_vehicle, min_ratio, max_stops=max_stops
                    )
                    if result is not None:
                        return result
            else:
                result = self._pack_bins(stops, list(combo), min_ratio, max_stops=max_stops)
                if result is not None:
                    return result
        return None

    def _try_assign_with_small_vehicle(
        self,
        stops: list[DeliveryStop],
        vehicles: list[Vehicle],
        small_vehicle: Vehicle,
        min_ratio: float,
        max_stops: int | None = None,
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
        if max_stops is not None and len(small_stops) > max_stops:
            return None
        remaining = [stop for stop in regular if stop.key not in selected_keys]
        normal_vehicles = [vehicle for vehicle in vehicles if vehicle.vehicle_id != small_vehicle.vehicle_id]
        packed = self._pack_bins(remaining, normal_vehicles, min_ratio, max_stops=max_stops)
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
        max_stops: int | None = None,
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
            chosen = self._subset_sum_near(remaining, lower, upper, ideal, ideal_count, max_stops=max_stops)
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
        max_stops: int | None = None,
    ) -> tuple[tuple[int, ...], int] | None:
        # Для одного и того же тоннажа сохраняем набор с количеством адресов,
        # наиболее близким к ожидаемому для этой машины.
        states: dict[int, tuple[int, ...]] = {0: ()}
        for index, stop in enumerate(stops):
            weight = stop.demand_kg
            snapshot = list(states.items())
            for current_weight, selected in snapshot:
                new_weight = current_weight + weight
                if new_weight > upper or (max_stops is not None and len(selected) >= max_stops):
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

    def _build_second_trip(
        self,
        vehicle: Vehicle,
        pool: list[DeliveryStop],
        data: dict,
        settings: PlannerSettings,
        start_minutes: int,
        lunch_taken: bool,
        work_used: int,
    ) -> VehicleRoute | None:
        """Второй рейс на свободной машине: набирает остаток заявок в остаток смены водителя.

        Машина не перегружается, время считается по фактической матрице с учётом
        обеда и уже отработанных водителем часов.
        """
        ordered = sorted(
            pool,
            key=lambda s: (0 if s.priority else 1, -s.demand_kg, s.key),
        )
        stops: list[DeliveryStop] = []
        load = 0
        for stop in ordered:
            if not self._vehicle_can_serve(vehicle, stop):
                continue
            if load + stop.demand_kg > vehicle.capacity_kg:
                continue
            trial = stops + [stop]
            route = self._schedule_route(
                vehicle,
                trial,
                data,
                settings,
                start_minutes=start_minutes,
                lunch_taken=lunch_taken,
            )
            if (
                work_used + route.working_minutes <= settings.max_workday_minutes
                and route.finish_minutes <= settings.end_minutes
            ):
                stops.append(stop)
                load += stop.demand_kg
        if not stops:
            return None
        return self._schedule_route(
            vehicle,
            stops,
            data,
            settings,
            start_minutes=start_minutes,
            lunch_taken=lunch_taken,
        )

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
        # Цель учитывает окно обеда (кроме случая, когда обед уже взят в предыдущем рейсе).
        ordered = improve_route_order(
            ordered,
            depot_key,
            self.travel,
            start,
            settings.loading_minutes,
            settings.unloading_minutes,
            lunch_start_minutes=None if lunch_taken else settings.lunch_start_minutes,
            lunch_minutes=settings.lunch_minutes,
        )
        # 2-opt убирает «крестовые» перегоны, которые перестановкой одной точки не лечатся.
        ordered = improve_route_order_2opt(
            ordered,
            depot_key,
            self.travel,
            start,
            settings.loading_minutes,
            settings.unloading_minutes,
            lunch_start_minutes=None if lunch_taken else settings.lunch_start_minutes,
            lunch_minutes=settings.lunch_minutes,
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
            # through_lunch возвращает (начало участка, конец участка, обед внутри):
            # прибытие — это КОНЕЦ перегона, а не его начало.
            _, arrival, road_lunch = through_lunch(current_time, drive)
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
        if result.assigned_demand_kg != result.total_demand_kg and not result.unassigned_stops:
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

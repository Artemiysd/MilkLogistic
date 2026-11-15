from __future__ import annotations

from typing import Protocol

from .models import DeliveryStop


class TravelTimeProvider(Protocol):
    """Абстракция над матрицей времени в пути (позже сюда подключается Яндекс)."""

    def minutes(self, origin: str, destination: str, origin_street: str = "", destination_street: str = "") -> int:
        ...


class MatrixTravelTimeProvider:
    """Провайдер по готовой матрице минут. Её может отдавать адаптер Яндекс.Карт."""

    def __init__(self, matrix: dict[str, dict[str, int]], fallback_minutes: int = 7):
        self.matrix = matrix
        self.fallback_minutes = max(1, int(fallback_minutes))

    def minutes(self, origin: str, destination: str, origin_street: str = "", destination_street: str = "") -> int:
        if origin == destination:
            return 0
        # Матрица направленная: время A->B не обязано совпадать с B->A.
        value = self.matrix.get(origin, {}).get(destination)
        return max(1, int(value)) if value is not None else self.fallback_minutes


class FallbackTravelTimeProvider:
    """Временная оценка, пока нет координат и матрицы маршрутов."""

    def __init__(self, default_minutes: int = 7):
        self.default_minutes = max(1, int(default_minutes))

    def minutes(self, origin: str, destination: str, origin_street: str = "", destination_street: str = "") -> int:
        if origin == destination:
            return 0
        # Не выдаём расстояния за реальные: используется единая тестовая оценка.
        return self.default_minutes


def choose_next_stop(
    current_location: str,
    current_street: str,
    current_minutes: int,
    remaining: list[DeliveryStop],
    travel: TravelTimeProvider,
    shift_start_minutes: int,
) -> DeliveryStop:
    """Выбирает следующую точку с учётом дороги, времени открытия и загруженности."""
    best: tuple[tuple[float, int, str], DeliveryStop] | None = None
    for stop in remaining:
        drive = travel.minutes(current_location, stop.key, current_street, stop.street)
        arrival = current_minutes + drive
        ready_at = arrival

        # Если точка открылась до начала смены, к началу рейса она уже доступна.
        if stop.earliest_service_minutes is not None and stop.earliest_service_minutes >= shift_start_minutes:
            ready_at = max(ready_at, stop.earliest_service_minutes)
        if stop.busy_until is not None:
            ready_at = max(ready_at, stop.busy_until)

        wait = max(0, ready_at - arrival)
        # После времени открытия опоздания нет: это нижняя граница, а не точный слот.
        # Ожидание дорогое: алгоритм сначала обслужит доступные точки.
        score = drive + wait * 1.15
        # Tie-breaker: не дробим кластер улицы, когда временная цена одинакова.
        street_penalty = 0 if current_street and current_street == stop.street else 1
        key = (score, street_penalty, stop.address.casefold())
        if best is None or key < best[0]:
            best = (key, stop)
    assert best is not None
    return best[1]


def order_stops(
    stops: list[DeliveryStop],
    travel: TravelTimeProvider,
    depot_key: str,
    start_minutes: int,
    loading_minutes: int,
    service_minutes: int = 30,
) -> list[DeliveryStop]:
    remaining = list(stops)
    ordered: list[DeliveryStop] = []
    current_location = depot_key
    current_street = ""
    current_time = start_minutes + loading_minutes
    while remaining:
        selected = choose_next_stop(
            current_location,
            current_street,
            current_time,
            remaining,
            travel,
            start_minutes,
        )
        ordered.append(selected)
        drive = travel.minutes(current_location, selected.key, current_street, selected.street)
        arrival = current_time + drive
        ready_at = arrival
        if selected.earliest_service_minutes is not None and selected.earliest_service_minutes >= start_minutes:
            ready_at = max(ready_at, selected.earliest_service_minutes)
        if selected.busy_until is not None:
            ready_at = max(ready_at, selected.busy_until)
        current_time = ready_at + service_minutes  # предварительная оценка для выбора следующей точки
        current_location = selected.key
        current_street = selected.street
        remaining.remove(selected)
    return ordered


def _advance_through_lunch(current: int, duration: int, lunch_start: int | None, lunch_minutes: int) -> int:
    """Конец участка, который начался в `current`, с учётом паузы на обед.

    Обед не входит в рабочее время: если участок накрывает окно обеда,
    к длительности добавляется пауза (или участок стартует после обеда).
    """
    if not lunch_minutes or lunch_start is None:
        return current + duration
    lunch_end = lunch_start + lunch_minutes
    if current + duration <= lunch_start or current >= lunch_end:
        return current + duration
    if current >= lunch_start:
        return lunch_end + duration
    return current + duration + lunch_minutes  # обед целиком внутри участка


def _simulate_route(
    sequence: list[DeliveryStop],
    depot_key: str,
    travel: TravelTimeProvider,
    start_minutes: int,
    loading_minutes: int,
    service_minutes: int,
    lunch_start_minutes: int | None,
    lunch_minutes: int,
) -> tuple[int, int]:
    """Прогон маршрута: (возврат на базу, суммарные паузы на обед)."""
    current = start_minutes + loading_minutes
    pauses = 0

    def advance(moment: int, duration: int) -> int:
        nonlocal pauses
        end = _advance_through_lunch(moment, duration, lunch_start_minutes, lunch_minutes)
        pauses += end - moment - duration
        return end

    location = depot_key
    street = ""
    for stop in sequence:
        drive = travel.minutes(location, stop.key, street, stop.street)
        arrival = advance(current, drive)
        ready = arrival
        if stop.earliest_service_minutes is not None and stop.earliest_service_minutes >= start_minutes:
            ready = max(ready, stop.earliest_service_minutes)
        if stop.busy_until is not None:
            ready = max(ready, stop.busy_until)
        current = advance(ready, service_minutes)
        location = stop.key
        street = stop.street
    finish = advance(current, travel.minutes(location, depot_key, street, ""))
    return finish, pauses


def route_finish_minutes(
    sequence: list[DeliveryStop],
    depot_key: str,
    travel: TravelTimeProvider,
    start_minutes: int,
    loading_minutes: int,
    service_minutes: int = 30,
    lunch_start_minutes: int | None = None,
    lunch_minutes: int = 0,
) -> int:
    """Время возврата на базу: путь + ожидания окон + разгрузки + пауза на обед."""
    finish, _ = _simulate_route(
        sequence, depot_key, travel, start_minutes, loading_minutes,
        service_minutes, lunch_start_minutes, lunch_minutes,
    )
    return finish


def route_schedule_minutes(
    sequence: list[DeliveryStop],
    depot_key: str,
    travel: TravelTimeProvider,
    start_minutes: int,
    loading_minutes: int,
    service_minutes: int = 30,
    lunch_start_minutes: int | None = None,
    lunch_minutes: int = 0,
) -> tuple[int, int]:
    """(возврат на базу, чистая работа без обеда) для заданного порядка точек."""
    finish, pauses = _simulate_route(
        sequence, depot_key, travel, start_minutes, loading_minutes,
        service_minutes, lunch_start_minutes, lunch_minutes,
    )
    return finish, finish - start_minutes - pauses


def route_legs_minutes(sequence: list[DeliveryStop], depot_key: str, travel: TravelTimeProvider) -> int:
    """Сумма минут в пути: база -> точки -> база для заданного порядка."""
    if not sequence:
        return 0
    total = travel.minutes(depot_key, sequence[0].key, "", sequence[0].street)
    for previous, stop in zip(sequence, sequence[1:]):
        total += travel.minutes(previous.key, stop.key, previous.street, stop.street)
    total += travel.minutes(sequence[-1].key, depot_key, sequence[-1].street, "")
    return total


def improve_route_order(
    ordered: list[DeliveryStop],
    depot_key: str,
    travel: TravelTimeProvider,
    start_minutes: int,
    loading_minutes: int,
    service_minutes: int = 30,
    max_passes: int = 20,
    lunch_start_minutes: int | None = None,
    lunch_minutes: int = 0,
) -> list[DeliveryStop]:
    """Локальный поиск (or-opt): перенос одной точки на другое место.

    Цель — раньше вернуться на базу: меньше минут в пути и меньше ожидания окон,
    чтобы укладываться в смену без переработки. Цель учитывает паузу на обед.
    """
    best = list(ordered)

    def finish_cost(sequence: list[DeliveryStop]) -> int:
        return route_finish_minutes(
            sequence,
            depot_key,
            travel,
            start_minutes,
            loading_minutes,
            service_minutes,
            lunch_start_minutes,
            lunch_minutes,
        )

    best_total = finish_cost(best)
    for _ in range(max_passes):
        improved = False
        for source in range(len(best)):
            for target in range(len(best)):
                if source == target:
                    continue
                candidate = best[:]
                stop = candidate.pop(source)
                candidate.insert(target, stop)
                total = finish_cost(candidate)
                if total < best_total:
                    best, best_total = candidate, total
                    improved = True
        if not improved:
            break
    return best

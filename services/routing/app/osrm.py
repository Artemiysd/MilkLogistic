"""
Клиент OSRM: запросы, повторы, троттлинг, кэш, нарезка на блоки.

Почему не «просто fetch на каждый чих»:
  * публичный demo-сервер отдаёт максимум 100 координат в /table;
  * он же не любит много параллельных запросов;
  * на защите интернет может пропасть, поэтому ответы кэшируются в памяти,
    а ошибки отдаются внятным JSON, а не 500 без текста.

Документация API: https://project-osrm.org/docs/v26.5.0/http
"""

from __future__ import annotations

import json
import math
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import OrderedDict
from typing import Any, Iterator

from .config import settings


class OsrmError(Exception):
    """Ошибка OSRM, которую можно показать клиенту как есть."""

    def __init__(self, code: str, message: str, status: int = 502):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status

    def to_dict(self) -> dict:
        return {
            "code": self.code,
            "message": self.message,
            "provider": settings.osrm_base_url,
        }


class LRUCache:
    """Маленький потокобезопасный LRU: без зависимостей и без Redis."""

    def __init__(self, size: int):
        self._size = max(1, size)
        self._data: OrderedDict[Any, Any] = OrderedDict()
        self._lock = threading.Lock()
        self.hits = 0
        self.misses = 0

    def get(self, key: Any) -> Any | None:
        with self._lock:
            if key in self._data:
                self._data.move_to_end(key)
                self.hits += 1
                return self._data[key]
            self.misses += 1
            return None

    def put(self, key: Any, value: Any) -> None:
        with self._lock:
            self._data[key] = value
            self._data.move_to_end(key)
            while len(self._data) > self._size:
                self._data.popitem(last=False)

    def stats(self) -> dict:
        with self._lock:
            return {"entries": len(self._data), "size": self._size, "hits": self.hits, "misses": self.misses}


_route_cache = LRUCache(settings.cache_size)
_table_cache = LRUCache(4)

_throttle_lock = threading.Lock()
_last_request_at = 0.0


def _throttle() -> None:
    """Не больше одного запроса за OSRM_MIN_INTERVAL_SEC — бережём публичный сервер."""
    global _last_request_at
    if settings.osrm_min_interval_s <= 0:
        return
    with _throttle_lock:
        wait = settings.osrm_min_interval_s - (time.monotonic() - _last_request_at)
        if wait > 0:
            time.sleep(wait)
        _last_request_at = time.monotonic()


def _coords(points: list) -> str:
    return ";".join(f"{p.lon:.7f},{p.lat:.7f}" for p in points)


def _request_json(url: str) -> dict:
    last_error: Exception | None = None

    for attempt in range(1, settings.osrm_retries + 1):
        _throttle()
        request = urllib.request.Request(
            url,
            headers={
                "Accept": "application/json",
                "User-Agent": "MilkLogistic-Routing/1.0 (hackathon)",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=settings.osrm_timeout_s) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", "replace")
            code, message = _parse_error(body, exc.code)
            if exc.code in (429, 500, 502, 503, 504) and attempt < settings.osrm_retries:
                time.sleep(0.8 * attempt)
                last_error = OsrmError(code, message, 502)
                continue
            raise OsrmError(code, message, 502 if exc.code >= 500 else exc.code) from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last_error = exc
            if attempt < settings.osrm_retries:
                time.sleep(0.8 * attempt)
                continue
            reason = str(exc) or exc.__class__.__name__
            raise OsrmError(
                "OsrmUnavailable",
                f"OSRM недоступен ({settings.osrm_base_url}): {reason}",
                504,
            ) from exc

        if payload.get("code") != "Ok":
            raise OsrmError(
                str(payload.get("code", "Error")),
                str(payload.get("message", "OSRM вернул ошибку")),
                502,
            )
        return payload

    raise OsrmError("OsrmUnavailable", f"OSRM недоступен: {last_error}", 504)


def _parse_error(body: str, status: int) -> tuple[str, str]:
    try:
        payload = json.loads(body)
        return (
            str(payload.get("code", f"HTTP{status}")),
            str(payload.get("message", body[:300])),
        )
    except json.JSONDecodeError:
        return f"HTTP{status}", body[:300] or f"OSRM ответил {status}"


def chunk_ranges(count: int, size: int | None = None) -> Iterator[tuple[int, int]]:
    """
    Диапазоны [start, end] по size точек с перекрытием в одну точку.
    Перекрытие нужно, чтобы склеить участки маршрута без разрыва.
    """
    size = max(2, size or settings.chunk_size)
    if count < 2:
        return
    start = 0
    while start < count - 1:
        end = min(start + size - 1, count - 1)
        yield start, end
        if end >= count - 1:
            break
        start = end


def _blocks(count: int, size: int) -> list[list[int]]:
    return [list(range(s, min(s + size, count))) for s in range(0, count, size)]


# ------------------------------------------------------------------ /route

def calculate_route(
    points: list,
    profile: str = "driving",
    return_geometry: bool = True,
    overview: str = "full",
    continue_straight: bool = False,
    costs: str = "table",
) -> dict:
    """
    costs="table"  — расстояния участков берутся из /table, то есть из той же матрицы,
                     по которой считает алгоритм. Итог маршрута всегда совпадает с матрицей.
    costs="route"  — расстояния берутся из ответа /route (быстрее на один запрос,
                     но может отличаться от матрицы на десятые доли процента).
    """
    key = (
        "route",
        profile,
        return_geometry,
        overview,
        continue_straight,
        costs,
        tuple((p.id, round(p.lat, 5), round(p.lon, 5)) for p in points),
    )
    cached = _route_cache.get(key)
    if cached is not None:
        return {**cached, "cached": True}

    started = time.monotonic()
    legs: list[dict] = []
    coordinates: list[list[float]] = []
    distance_m = 0.0
    duration_s = 0.0
    requests = 0
    result_costs_source = "route" if costs == "route" else "table"

    for start, end in chunk_ranges(len(points)):
        chunk = points[start : end + 1]
        query = {
            "overview": overview if return_geometry else "false",
            "geometries": "geojson",
            "steps": "false",
            "alternatives": "false",
            # без этого OSRM по умолчанию продолжает движение через точку,
            # участки перестают совпадать с матрицей /table (проверено на 214 точках)
            "continue_straight": "true" if continue_straight else "false",
        }
        url = (
            f"{settings.osrm_base_url}/route/v1/{profile}/{_coords(chunk)}"
            f"?{urllib.parse.urlencode(query)}"
        )
        payload = _request_json(url)
        requests += 1

        if not payload.get("routes"):
            raise OsrmError("NoRoute", "OSRM не нашёл маршрут между точками", 422)

        route = payload["routes"][0]

        # Стоимость участков: по умолчанию из /table — той же матрицы, по которой
        # работает алгоритм. Тогда итог маршрута совпадает с матрицей до метра.
        leg_costs: list[tuple[float, float]] | None = None
        if costs == "table" and len(chunk) >= 2:
            try:
                table = _request_json(
                    f"{settings.osrm_base_url}/table/v1/{profile}/{_coords(chunk)}"
                    "?annotations=distance,duration"
                )
                requests += 1
                leg_costs = [
                    (float(table["distances"][k][k + 1]), float(table["durations"][k][k + 1]))
                    for k in range(len(chunk) - 1)
                    if table["distances"][k][k + 1] is not None
                    and table["durations"][k][k + 1] is not None
                ]
                if len(leg_costs) != len(chunk) - 1:
                    leg_costs = None
            except OsrmError:
                leg_costs = None

        for offset, leg in enumerate(route.get("legs", [])):
            if leg_costs is not None:
                leg_distance, leg_duration = leg_costs[offset]
                costs_source = "table"
            else:
                leg_distance, leg_duration = float(leg["distance"]), float(leg["duration"])
                costs_source = "route"
            leg_distance = round(leg_distance, 1)
            leg_duration = round(leg_duration, 1)
            distance_m += leg_distance
            duration_s += leg_duration
            legs.append(
                {
                    "from_index": start + offset,
                    "to_index": start + offset + 1,
                    "from_id": chunk[offset].id,
                    "to_id": chunk[offset + 1].id,
                    "distance_m": leg_distance,
                    "duration_s": leg_duration,
                }
            )
            result_costs_source = costs_source

        if return_geometry and overview != "false":
            segment = route.get("geometry", {}).get("coordinates", [])
            if coordinates and segment:
                segment = segment[1:]  # первая точка уже есть в предыдущем участке
            coordinates.extend(segment)

    result = {
        "code": "Ok",
        "source": "osrm",
        "provider": settings.osrm_base_url,
        "distance_m": round(distance_m, 1),
        "duration_s": round(duration_s, 1),
        "legs": legs,
        "costs_source": result_costs_source,
        "geometry": (
            {"type": "LineString", "coordinates": coordinates} if return_geometry and coordinates else None
        ),
        "points_count": len(points),
        "requests": requests,
        "elapsed_ms": int((time.monotonic() - started) * 1000),
        "cached": False,
    }
    _route_cache.put(key, result)
    return result


# ------------------------------------------------------------------ /table

def calculate_table(points: list, profile: str = "driving") -> dict:
    key = ("table", profile, tuple((p.id, round(p.lat, 5), round(p.lon, 5)) for p in points))
    cached = _table_cache.get(key)
    if cached is not None:
        return {**cached, "cached": True}

    started = time.monotonic()
    n = len(points)
    distances: list[list[float | None]] = [[None] * n for _ in range(n)]
    durations: list[list[float | None]] = [[None] * n for _ in range(n)]
    coords = _coords(points)
    requests = 0
    unreachable = 0

    for sources in _blocks(n, settings.chunk_size):
        for dests in _blocks(n, settings.chunk_size):
            query = urllib.parse.urlencode(
                {
                    "sources": ";".join(map(str, sources)),
                    "destinations": ";".join(map(str, dests)),
                    "annotations": "distance,duration",
                },
                safe=";",
            )
            payload = _request_json(f"{settings.osrm_base_url}/table/v1/{profile}/{coords}?{query}")
            requests += 1

            for row, i in enumerate(sources):
                for col, j in enumerate(dests):
                    d = payload["distances"][row][col]
                    t = payload["durations"][row][col]
                    if d is None or t is None:
                        unreachable += 1
                        continue
                    distances[i][j] = round(float(d), 1)
                    durations[i][j] = round(float(t), 1)

    flat = sorted(
        distances[i][j] for i in range(n) for j in range(n) if i != j and distances[i][j] is not None
    )
    result = {
        "code": "Ok",
        "source": "osrm",
        "provider": settings.osrm_base_url,
        "size": n,
        "ids": [p.id for p in points],
        "distance_m": distances,
        "duration_s": durations,
        "stats": {
            "min_m": flat[0] if flat else None,
            "median_m": flat[len(flat) // 2] if flat else None,
            "max_m": flat[-1] if flat else None,
            "unreachable_pairs": unreachable,
        },
        "requests": requests,
        "elapsed_ms": int((time.monotonic() - started) * 1000),
        "cached": False,
    }
    _table_cache.put(key, result)
    return result


# ------------------------------------------------------------------ health

_health_cache: dict[str, Any] = {"at": 0.0, "value": None}
_health_lock = threading.Lock()


def ping() -> dict:
    """Проверка живости OSRM через короткий /route. Кэшируется на HEALTH_CACHE_SEC."""
    with _health_lock:
        if time.monotonic() - _health_cache["at"] < settings.health_cache_s and _health_cache["value"]:
            return _health_cache["value"]

    url = (
        f"{settings.osrm_base_url}/route/v1/driving/23.828288,53.708928;23.9116412,53.6753356"
        "?overview=false"
    )
    started = time.monotonic()
    try:
        payload = _request_json(url)
        value = {
            "reachable": True,
            "base_url": settings.osrm_base_url,
            "latency_ms": int((time.monotonic() - started) * 1000),
            "distance_m": round(float(payload["routes"][0]["distance"]), 1),
            "error": None,
        }
    except OsrmError as exc:
        value = {
            "reachable": False,
            "base_url": settings.osrm_base_url,
            "latency_ms": int((time.monotonic() - started) * 1000),
            "distance_m": None,
            "error": exc.to_dict(),
        }

    with _health_lock:
        _health_cache.update({"at": time.monotonic(), "value": value})
    return value


def haversine_m(a, b) -> float:
    """Запасной расчёт по прямой — если дороги между точками нет."""
    r = 6371000.0
    dlat = math.radians(b.lat - a.lat)
    dlon = math.radians(b.lon - a.lon)
    s = (
        math.sin(dlat / 2) ** 2
        + math.cos(math.radians(a.lat)) * math.cos(math.radians(b.lat)) * math.sin(dlon / 2) ** 2
    )
    return 2 * r * math.asin(math.sqrt(s))

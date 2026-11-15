"""
Заглушка API для сборки инфраструктуры и работы фронтенда до готовности бэкенда.

Задача файла — отдавать ответы строго по контракту schema.json, чтобы:
  * фронтенд рисовал карту и таблицы, не дожидаясь бэкенда;
  * Docker-сеть, nginx, порты и healthcheck были проверены заранее;
  * подмена на реальный бэкенд была заменой одного сервиса в compose.

Это НЕ алгоритм и НЕ бизнес-логика: маршруты здесь делятся последовательно,
без оптимизации. В каждом ответе /api/optimize стоит "mock": true.
"""

import json
import math
import os
import time
import uuid
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

PORT = int(os.environ.get("PORT", "8000"))
DATA_FILE = os.environ.get("DATA_FILE", "/data/logistics_data.json")
OVERRIDES_FILE = os.environ.get("OVERRIDES_FILE", "/data/geocode_overrides.json")

EARTH_R_M = 6371000.0


def haversine_m(a, b):
    dlat = math.radians(b["lat"] - a["lat"])
    dlon = math.radians(b["lon"] - a["lon"])
    s = (
        math.sin(dlat / 2) ** 2
        + math.cos(math.radians(a["lat"]))
        * math.cos(math.radians(b["lat"]))
        * math.sin(dlon / 2) ** 2
    )
    return 2 * EARTH_R_M * math.asin(math.sqrt(s))


def load_data():
    with open(DATA_FILE, encoding="utf-8") as f:
        data = json.load(f)

    overrides = {}
    if os.path.exists(OVERRIDES_FILE):
        with open(OVERRIDES_FILE, encoding="utf-8") as f:
            for item in json.load(f).get("overrides", []):
                overrides[item["point_id"]] = item

    for point in data.get("delivery_points", []):
        fix = overrides.get(point["id"])
        if fix:
            point["coords"] = fix["coords"]
            point["geocode_quality"] = fix.get("quality", "manual")

    data.setdefault("constraints", {})
    return data


def expand_vehicles(data):
    vehicles = []
    for vtype in data.get("vehicles", []):
        for i in range(1, int(vtype.get("count", 1)) + 1):
            vehicles.append(
                {
                    "id": f"{vtype['id']}_{i}",
                    "type_id": vtype["id"],
                    "capacity": vtype["capacity"],
                    "restricted_to_small_streets": vtype.get(
                        "restricted_to_small_streets", False
                    ),
                }
            )
    return vehicles


def iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%S")


def assign_points(points, vehicles, constraints):
    """
    Best-fit decreasing по грузоподъёмности — самая простая укладка, при которой
    не превышается вместимость машин. Не оптимизация, а именно укладка.
    """
    restricted = set(constraints.get("restricted_streets", []))
    small_cap = float(constraints.get("restricted_vehicle_capacity", 1500))

    remaining = {v["id"]: float(v["capacity"]) for v in vehicles}
    assigned = {v["id"]: [] for v in vehicles}
    violations = []

    def can_enter(vehicle, point):
        if point.get("street") in restricted:
            return vehicle["capacity"] <= small_cap or vehicle.get(
                "restricted_to_small_streets", False
            )
        return True

    for point in sorted(points, key=lambda p: -float(p.get("demand", 0))):
        demand = float(point.get("demand", 0))
        candidates = [
            v for v in vehicles if can_enter(v, point) and remaining[v["id"]] >= demand
        ]
        if not candidates:
            violations.append(
                {
                    "type": "unassigned_point",
                    "point_id": point["id"],
                    "message": "Точку некуда положить: свободной вместимости нет",
                }
            )
            continue
        # самый тесный подходящий автомобиль
        vehicle = min(candidates, key=lambda v: remaining[v["id"]])
        remaining[vehicle["id"]] -= demand
        assigned[vehicle["id"]].append(point)

    return assigned, violations


def order_stops(points, depot):
    """Nearest neighbour от склада — чтобы mock не рисовал зигзаги по карте."""
    left = list(points)
    ordered = []
    current = depot
    while left:
        nxt = min(left, key=lambda p: haversine_m(current, p["coords"]))
        ordered.append(nxt)
        left.remove(nxt)
        current = nxt["coords"]
    return ordered


def mock_optimize(data, body):
    """Укладка по вместимости + nearest neighbour. Без VRP-оптимизации."""
    points = [p for p in data["delivery_points"] if p.get("coords")]
    vehicles = expand_vehicles(data)
    constraints = data.get("constraints", {})
    unload = int(constraints.get("unload_time_minutes", 30))
    start_at = datetime(2026, 1, 1, 8, 0, 0)

    assigned, violations = assign_points(points, vehicles, constraints)
    routes = []
    total_distance = 0.0

    for vehicle in vehicles:
        chunk = order_stops(assigned[vehicle["id"]], data["depot"]["coords"])
        if not chunk:
            continue

        depot = data["depot"]["coords"]
        clock = start_at
        prev = depot
        leg_m = 0.0
        stops = []
        load = 0.0

        for seq, point in enumerate(chunk, start=1):
            distance = haversine_m(prev, point["coords"])
            leg_m += distance
            clock += timedelta(minutes=distance / 1000.0 / 25.0 * 60.0)
            arrive = clock
            clock += timedelta(minutes=unload)
            load += float(point.get("demand", 0))
            stops.append(
                {
                    "seq": seq,
                    "point_id": point["id"],
                    "name": point["name"],
                    "address": point["address"],
                    "demand": point.get("demand", 0),
                    "arrive": iso(arrive),
                    "leave": iso(clock),
                    "distance_from_prev_m": round(distance, 1),
                    "coords": point["coords"],
                }
            )
            prev = point["coords"]

        back_m = haversine_m(prev, depot)
        leg_m += back_m
        clock += timedelta(minutes=back_m / 1000.0 / 25.0 * 60.0)
        total_distance += leg_m

        work_minutes = round((clock - start_at).total_seconds() / 60.0, 1)
        if work_minutes > float(constraints.get("max_workday_hours", 8)) * 60:
            violations.append(
                {
                    "type": "workday_exceeded",
                    "vehicle_id": vehicle["id"],
                    "value": work_minutes,
                    "limit": float(constraints.get("max_workday_hours", 8)) * 60,
                    "message": "Рейс длиннее рабочего дня",
                }
            )
        if vehicle["capacity"] and load > float(vehicle["capacity"]):
            violations.append(
                {
                    "type": "overload",
                    "vehicle_id": vehicle["id"],
                    "value": load,
                    "limit": vehicle["capacity"],
                    "message": "Перегруз",
                }
            )

        routes.append(
            {
                "vehicle_id": vehicle["id"],
                "vehicle_type": vehicle["type_id"],
                "vehicle_capacity": vehicle["capacity"],
                "load_kg": round(load, 1),
                "load_ratio": round(load / vehicle["capacity"], 4)
                if vehicle["capacity"]
                else 0,
                "stops": stops,
                "distance_m": round(leg_m, 1),
                "work_minutes": work_minutes,
                "end_time": iso(clock),
                "geometry": {
                    "type": "LineString",
                    "coordinates": [[depot["lon"], depot["lat"]]]
                    + [[s["coords"]["lon"], s["coords"]["lat"]] for s in stops]
                    + [[depot["lon"], depot["lat"]]],
                },
                "geometry_source": "straight",
            }
        )

    demand = sum(float(p.get("demand", 0)) for p in points)
    capacity = sum(v["capacity"] for v in vehicles)

    return {
        "run_id": uuid.uuid4().hex[:12],
        "created_at": iso(datetime.now()),
        "mock": True,
        "note": "Заглушка: укладка по вместимости + nearest neighbour, без VRP. Цифры не для защиты.",
        "options": {
            "start_time": body.get("options", {}).get("start_time", "08:00"),
            "unload_minutes": unload,
            "min_load_ratio": constraints.get("min_load_ratio", 0.9),
            "include_geometry": body.get("options", {}).get("include_geometry", True),
        },
        "routes": routes,
        "totals": {
            "distance_m": round(total_distance, 1),
            "vehicles_used": len(routes),
            "demand_kg": round(demand, 1),
            "capacity_kg": round(capacity, 1),
            "utilization": round(demand / capacity, 4) if capacity else 0,
            "avg_load_ratio": round(
                sum(r["load_ratio"] for r in routes) / len(routes), 4
            )
            if routes
            else 0,
        },
        "violations": violations,
        "warnings": [
            "Это mock-ответ. Реальные маршруты появятся после подключения backend/."
        ],
    }


class Handler(BaseHTTPRequestHandler):
    server_version = "MilkMockAPI/1.0"

    def _send(self, status, payload):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self._send(204, {})

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/api/health":
            data = load_data()
            return self._send(
                200,
                {
                    "status": "ok",
                    "mode": "mock",
                    "points": len(data.get("delivery_points", [])),
                    "vehicles": sum(
                        int(v.get("count", 1)) for v in data.get("vehicles", [])
                    ),
                    "demand_kg": sum(
                        float(p.get("demand", 0))
                        for p in data.get("delivery_points", [])
                    ),
                },
            )
        if path in ("/api/data", "/api/points"):
            data = load_data()
            return self._send(
                200,
                {
                    "depot": data["depot"],
                    "vehicles": expand_vehicles(data),
                    "vehicle_types": data.get("vehicles", []),
                    "delivery_points": data["delivery_points"],
                    "constraints": data.get("constraints", {}),
                    "mock": True,
                },
            )
        return self._send(404, {"detail": "Not found", "path": path})

    def do_POST(self):
        path = urlparse(self.path).path
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            body = json.loads(raw.decode("utf-8") or "{}")
        except json.JSONDecodeError:
            return self._send(400, {"detail": "Invalid JSON body"})

        if path == "/api/optimize":
            return self._send(200, mock_optimize(load_data(), body))
        if path == "/api/demo/seed":
            data = load_data()
            return self._send(
                200,
                {
                    "status": "ok",
                    "mock": True,
                    "points": len(data.get("delivery_points", [])),
                    "message": "Данные прочитаны из data/logistics_data.json.",
                },
            )
        return self._send(404, {"detail": "Not found", "path": path})

    def log_message(self, fmt, *args):
        print("[mock-api] " + fmt % args, flush=True)


if __name__ == "__main__":
    print(f"[mock-api] data={DATA_FILE} port={PORT}", flush=True)
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()

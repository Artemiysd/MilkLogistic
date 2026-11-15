from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from backend.app.db import DriverDB
from backend.app.dispatch import build_extra_plan, dispatch_run


def _run(run_id: str, vehicles: list[str]) -> dict:
    return {
        "run_id": run_id,
        "created_at": "2026-11-16T08:00:00",
        "depot": {"id": "depot", "address": "Склад", "coords": {"lat": 53.7, "lon": 23.8}},
        "routes": [
            {
                "route_number": index,
                "vehicle_id": vehicle,
                "vehicle_type": "truck",
                "vehicle_capacity": 4000,
                "load_kg": 3800,
                "distance_m": 1000,
                "work_minutes": 400,
                "end_time": "16:00",
                "stops": [
                    {
                        "seq": 1,
                        "address": f"Точка {index}",
                        "demand_kg": 3800,
                        "arrive": "09:00",
                        "leave": "09:30",
                    }
                ],
            }
            for index, vehicle in enumerate(vehicles, start=1)
        ],
    }


class DispatchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = DriverDB(Path(self.tmp.name) / "test.db")

    def tearDown(self):
        self.tmp.cleanup()

    def test_route_goes_only_to_driver_with_same_vehicle(self):
        driver_a = self.db.create_driver("Анна", vehicle_id="truck_a-1")
        driver_b = self.db.create_driver("Борис", vehicle_id="truck_b-1")

        # Для машины truck_c-1 водителя нет: рейс 2 не должен уйти Борису.
        assignments = dispatch_run(_run("run-1", ["truck_a-1", "truck_c-1"]), self.db.list_drivers(), self.db)
        self.assertEqual(len(assignments), 1)
        self.assertEqual(assignments[0]["driver_id"], driver_a["id"])

        # Появился водитель под машину C — теперь рейс 2 уходит именно ему.
        driver_c = self.db.create_driver("Виктор", vehicle_id="truck_c-1")
        assignments = dispatch_run(_run("run-2", ["truck_a-1", "truck_c-1"]), self.db.list_drivers(), self.db)
        by_route = {item["route_number"]: item["driver_id"] for item in assignments}
        self.assertEqual(by_route[1], driver_a["id"])
        self.assertEqual(by_route[2], driver_c["id"])
        self.assertNotEqual(by_route[2], driver_b["id"])

    def test_driver_without_vehicle_is_fallback(self):
        free = self.db.create_driver("Свободный")
        assignments = dispatch_run(_run("run-3", ["truck_x-1"]), self.db.list_drivers(), self.db)
        self.assertEqual(len(assignments), 1)
        self.assertEqual(assignments[0]["driver_id"], free["id"])

    def test_assignment_keeps_run_metadata(self):
        self.db.create_driver("Анна", vehicle_id="truck_a-1")
        assignments = dispatch_run(_run("run-4", ["truck_a-1"]), self.db.list_drivers(), self.db)
        payload = assignments[0]["payload"]
        self.assertEqual(payload["run_created_at"], "2026-11-16T08:00:00")
        self.assertEqual(payload["depot"]["address"], "Склад")

    def test_extra_plan_falls_back_without_coordinates(self):
        """Выгрузка без координат: доп. заезд не падает, берёт базовую матрицу."""
        data = {
            "depot": {"id": "depot", "address": "Склад"},
            "vehicles": [{"id": "van_1t", "capacity": 1000, "count": 1}],
            "delivery_points": [
                {"id": "b1", "name": "Магазин", "address": "Советская 10", "demand": 700},
                {"id": "b2", "name": "Магазин", "address": "Мостовая 5", "demand": 600},
            ],
            "constraints": {"min_load_ratio": 0.9, "max_workday_hours": 8, "unload_time_minutes": 30},
        }
        run = {
            "run_id": "run-extra",
            "created_at": "2026-11-16T08:00:00",
            "unassigned": [{"address": "Мостовая 5", "demand_kg": 600, "point_ids": ["b2"]}],
            "routes": [
                {
                    "route_number": 1,
                    "vehicle_id": "van_1t-1",
                    "vehicle_capacity": 1000,
                    "load_kg": 700,
                    "end_time": "10:15",
                    "stops": [{"seq": 1, "address": "Советская 10", "demand_kg": 700}],
                }
            ],
        }
        cache = {
            "points": [],
            "distance_m": [[0.0]],
            "duration_s": [[0.0]],
            "size": 1,
            "provider": "test",
            "built_at": "2026-11-16T08:00:00",
        }
        result = build_extra_plan(
            run, data, None, start_time="13:00", fallback_matrix=cache
        )
        self.assertEqual(result["wave"], 2)
        self.assertTrue(result["routes"])
        self.assertGreaterEqual(result["totals"]["assigned_kg"], 600)


if __name__ == "__main__":
    unittest.main(verbosity=2)

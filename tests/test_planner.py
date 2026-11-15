from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from logistics.data_loader import build_stops, load_problem, parse_clock, validate_problem
from logistics.export import export_plan
from logistics.planner import PlannerSettings, PlanningError, RoutePlanner
from logistics.routing import MatrixTravelTimeProvider

ROOT = Path(__file__).resolve().parents[1]
DATA_PATH = ROOT / "data" / "full_demo.json"
DATA = load_problem(DATA_PATH)


class PlannerTests(unittest.TestCase):
    def setUp(self):
        self.planner = RoutePlanner(PlannerSettings(default_drive_minutes=5))

    def test_dataset_is_grouped_by_physical_address(self):
        stops = build_stops(DATA)
        self.assertEqual(len(DATA["delivery_points"]), 214)
        self.assertEqual(len(stops), 192)

    # Вместимый датасет: 2 машины по 5 т, 10 заявок по 900 кг -> полная развозка
    # с загрузкой ровно 90% и рейсами, влезающими в смену.
    SMALL_FEASIBLE = {
        "depot": {"address": "Склад"},
        "vehicles": [{"id": "truck_5t", "capacity": 5000, "count": 2}],
        "delivery_points": [
            {"id": f"p{i}", "name": "Магазин", "address": f"Улица {i}", "demand": 900}
            for i in range(1, 11)
        ],
        "constraints": {"min_load_ratio": 0.9, "max_workday_hours": 8, "unload_time_minutes": 30},
    }

    def test_full_demo_assigns_every_delivery_at_most_once(self):
        result = self.planner.build(DATA)
        ids = [point_id for route in result.routes for visit in route.stops for point_id in visit.stop.point_ids]
        self.assertEqual(len(ids), len(set(ids)))
        # Каждая заявка либо развезена, либо честно в НЕ РАЗВЕЗЕНО (смены не резиновые).
        unassigned_points = sum(stop.customer_count for stop in result.unassigned_stops)
        self.assertEqual(result.assigned_points_count + unassigned_points, 214)
        self.assertEqual(result.assigned_demand_kg + result.unassigned_weight_kg, 63630)
        # Разгрузка фиксирована: при честных временах все рейсы влезают в смену.
        for route in result.routes:
            self.assertLessEqual(route.working_minutes, self.planner.settings.max_workday_minutes)
            self.assertLessEqual(route.finish_minutes, self.planner.settings.end_minutes)

    def test_small_feasible_dataset_is_delivered_fully(self):
        result = self.planner.build(self.SMALL_FEASIBLE)
        self.assertEqual(result.assigned_points_count, 10)
        self.assertEqual(result.assigned_demand_kg, 9000)
        self.assertEqual(len(result.unassigned_stops), 0)
        ids = [point_id for route in result.routes for visit in route.stops for point_id in visit.stop.point_ids]
        self.assertEqual(len(ids), len(set(ids)))

    def test_vehicle_capacity_and_minimum_load(self):
        result = self.planner.build(self.SMALL_FEASIBLE)
        for route in result.routes:
            self.assertLessEqual(route.load_kg, route.vehicle.capacity_kg)
            self.assertGreaterEqual(route.utilization, 0.90)

    def test_restricted_streets_go_to_small_vehicle(self):
        result = self.planner.build(DATA)
        restricted = {"советская", "мостовая", "замковая"}
        for route in result.routes:
            has_restricted = any(visit.stop.street.casefold() in restricted for visit in route.stops)
            if has_restricted:
                self.assertTrue(route.vehicle.restricted_to_small_streets)
                self.assertEqual(route.vehicle.capacity_kg, 1500)

    def test_busy_stop_is_scheduled_after_busy_until(self):
        data = {
            "depot": {"address": "Склад"},
            "vehicles": [{"id": "van_2t", "capacity": 2000, "count": 1}],
            "delivery_points": [
                {"id": "p1", "name": "Складчик", "address": "Улица 1", "demand": 500},
                {"id": "p2", "name": "Занятая точка", "address": "Улица 2", "demand": 500},
            ],
            "constraints": {"min_load_ratio": 0.9, "max_workday_hours": 8, "unload_time_minutes": 30},
        }
        busy_until = parse_clock("10:00")
        result = self.planner.build(data, busy_until_by_address={"Улица 2": busy_until})
        visit = next(v for route in result.routes for v in route.stops if v.stop.address == "Улица 2")
        self.assertGreaterEqual(visit.service_start_minutes, busy_until)
        self.assertGreater(visit.wait_minutes, 0)

    def test_matrix_provider_is_directional(self):
        provider = MatrixTravelTimeProvider({"A": {"B": 12}, "B": {"A": 19}}, fallback_minutes=5)
        self.assertEqual(provider.minutes("A", "B"), 12)
        self.assertEqual(provider.minutes("B", "A"), 19)

    def test_early_delivery_window_is_treated_as_open_before_shift(self):
        result = self.planner.build(DATA)
        route = next(r for r in result.routes if any(v.stop.address == "проспект Космонавтов 100" for v in r.stops))
        visit = next(v for v in route.stops if v.stop.address == "проспект Космонавтов 100")
        self.assertGreaterEqual(visit.service_start_minutes, 8 * 60)

    def test_duplicate_delivery_ids_are_rejected(self):
        data = json.loads(json.dumps(DATA))
        data["delivery_points"][1]["id"] = data["delivery_points"][0]["id"]
        with self.assertRaisesRegex(ValueError, "Повторяется ID доставки"):
            validate_problem(data)

    def test_invalid_demand_is_rejected(self):
        data = json.loads(json.dumps(DATA))
        data["delivery_points"][0]["demand"] = -10
        with self.assertRaisesRegex(ValueError, "demand должен быть положительным"):
            validate_problem(data)

    def test_missing_constraints_get_defaults(self):
        data = {
            "depot": {"address": "Склад"},
            "vehicles": [{"id": "van_1t", "capacity": 1000, "count": 1}],
            "delivery_points": [{"id": "p1", "name": "Магазин", "address": "Улица 1", "demand": 950}],
        }
        validated = validate_problem(data)
        self.assertEqual(validated["constraints"]["min_load_ratio"], 0.90)
        self.assertEqual(validated["constraints"]["unload_time_minutes"], 30)

    def test_bom_json_is_supported(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bom.json"
            path.write_text("\ufeff" + json.dumps(DATA, ensure_ascii=False), encoding="utf-8")
            self.assertEqual(len(load_problem(path)["delivery_points"]), 214)

    def test_export_creates_json_and_csv(self):
        result = self.planner.build(self.SMALL_FEASIBLE)
        with tempfile.TemporaryDirectory() as tmp:
            paths = export_plan(result, tmp)
            self.assertEqual(len(paths), 3)
            self.assertTrue(all(path.exists() and path.stat().st_size > 0 for path in paths))
            exported = json.loads((Path(tmp) / "plan.json").read_text(encoding="utf-8"))
            self.assertEqual(exported["summary"]["weight_assigned_kg"], 9000)

    def test_delivers_as_much_as_possible_when_fleet_is_too_small(self):
        data = {
            "depot": {"address": "Склад"},
            "vehicles": [{"id": "van_1t", "capacity": 1000, "count": 1}],
            "delivery_points": [{"id": "p1", "name": "Магазин", "address": "Улица 1", "demand": 1200},
                                {"id": "p2", "name": "Магазин 2", "address": "Улица 2", "demand": 400,
                                 "priority": True}],
            "constraints": {"min_load_ratio": 0.9, "max_workday_hours": 8, "one_trip_per_vehicle": True,
                            "unload_time_minutes": 30, "restricted_streets": [], "restricted_vehicle_capacity": 1500},
        }
        result = self.planner.build(data)
        # Частичная доставка вместо ошибки: приоритетная точка развезена, тяжёлая осталась.
        self.assertEqual(result.assigned_points_count, 1)
        self.assertEqual(result.assigned_demand_kg, 400)
        self.assertEqual(len(result.unassigned_stops), 1)
        self.assertEqual(result.unassigned_weight_kg, 1200)
        self.assertEqual(result.routes[0].stops[0].stop.point_ids, ["p2"])

    def test_required_vehicle_type_is_respected(self):
        data = {
            "depot": {"address": "Склад"},
            "vehicles": [{"id": "ref_1t", "type": "рефрижератор", "capacity": 1000, "count": 1},
                         {"id": "box_2t", "type": "тентованный", "capacity": 2000, "count": 1}],
            "delivery_points": [{"id": "p1", "address": "Улица 1", "demand": 500, "vehicle_type": "рефрижератор"},
                                {"id": "p2", "address": "Улица 2", "demand": 500},
                                {"id": "p3", "address": "Улица 3", "demand": 800}],
            "constraints": {"min_load_ratio": 0.9, "max_workday_hours": 8, "one_trip_per_vehicle": True,
                            "unload_time_minutes": 30, "restricted_streets": [], "restricted_vehicle_capacity": 1500},
        }
        result = self.planner.build(data)
        for route in result.routes:
            for visit in route.stops:
                required = visit.stop.required_type
                if required:
                    self.assertEqual(route.vehicle.vehicle_type.casefold(), required.casefold())

    def test_lunch_break_extends_finish_but_not_working_time(self):
        result = self.planner.build(DATA)
        for route in result.routes:
            # Инвариант «отработал = прошло времени минус обед» верен для рейса,
            # начатого со старта смены; второй рейс стартует позже (водитель ждал
            # свободную машину), поэтому для него проверяем только лимиты.
            if route.trip_no == 1:
                span = route.finish_minutes - self.planner.settings.start_minutes
                self.assertEqual(route.working_minutes, span - route.lunch_minutes)
            self.assertLessEqual(route.lunch_minutes, 60)
            self.assertLessEqual(route.working_minutes, self.planner.settings.max_workday_minutes)
            self.assertLessEqual(route.finish_minutes, self.planner.settings.end_minutes)


if __name__ == "__main__":
    unittest.main(verbosity=2)

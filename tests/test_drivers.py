from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from backend.app.db import DriverDB

STOPS = [
    {"seq": 1, "address": "Улица 1", "demand_kg": 100, "arrive": "09:00", "leave": "09:30"},
    {"seq": 2, "address": "Улица 2", "demand_kg": 200, "arrive": "10:00", "leave": "10:30"},
    {"seq": 3, "address": "Улица 3", "demand_kg": 300, "arrive": "11:00", "leave": "11:30"},
]


class DriverDBTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = DriverDB(Path(self.tmp.name) / "test.db")

    def tearDown(self):
        self.tmp.cleanup()

    def test_driver_lifecycle_and_token(self):
        driver = self.db.create_driver("Иван", "+375 29 000-00-00", "truck_4t_1-1")
        self.assertEqual(driver["name"], "Иван")
        self.assertTrue(driver["token"])
        self.assertEqual(self.db.driver_by_token(driver["token"])["id"], driver["id"])

        updated = self.db.update_driver(driver["id"], phone="+375 33 111-11-11")
        self.assertEqual(updated["phone"], "+375 33 111-11-11")

        self.assertEqual(len(self.db.list_drivers()), 1)
        self.db.delete_driver(driver["id"])
        self.assertEqual(self.db.list_drivers(), [])

    def test_assignment_progress_and_events(self):
        driver = self.db.create_driver("Пётр")
        assignment = self.db.create_assignment(
            run_id="run-1",
            dataset_id=None,
            route_number=1,
            driver_id=driver["id"],
            vehicle_id="truck_4t_1-1",
            payload={"vehicle_id": "truck_4t_1-1", "stops": STOPS},
        )
        self.assertEqual(assignment["status"], "assigned")
        self.assertEqual(assignment["progress"]["pending"], 3)

        self.db.set_stop_status(assignment["id"], 1, "delivered")
        current = self.db.assignment(assignment["id"])
        self.assertEqual(current["status"], "in_progress")
        self.assertEqual(current["progress"]["delivered"], 1)
        self.assertEqual(current["progress"]["percent"], 33)

        self.db.set_stop_status(assignment["id"], 2, "failed", reason="клиент закрыт")
        current = self.db.assignment(assignment["id"])
        self.assertEqual(current["progress"]["failed"], 1)
        self.assertEqual(current["progress"]["pending"], 1)

        self.db.set_stop_status(assignment["id"], 3, "delivered")
        current = self.db.assignment(assignment["id"])
        self.assertEqual(current["status"], "completed")
        self.assertEqual(current["progress"]["percent"], 100)

        events = self.db.events(assignment["id"])
        self.assertTrue(any(event["type"] == "stop_delivered" for event in events))
        self.assertTrue(any(event["type"] == "stop_failed" for event in events))

    def test_active_assignment_for_token(self):
        driver = self.db.create_driver("Сергей")
        with self.assertRaises(KeyError):
            self.db.active_assignment_for_token(driver["token"])

        assignment = self.db.create_assignment(
            run_id="run-2",
            dataset_id=None,
            route_number=1,
            driver_id=driver["id"],
            vehicle_id=None,
            payload={"stops": STOPS},
        )
        found = self.db.active_assignment_for_token(driver["token"])
        self.assertEqual(found["id"], assignment["id"])

        self.db.cancel_assignment(assignment["id"])
        with self.assertRaises(KeyError):
            self.db.active_assignment_for_token(driver["token"])


if __name__ == "__main__":
    unittest.main(verbosity=2)

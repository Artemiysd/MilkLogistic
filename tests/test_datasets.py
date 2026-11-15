from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path

from backend.app.datasets import (
    DatasetError,
    DatasetStore,
    collect_matrix_points,
    matrix_info,
)

ROOT = Path(__file__).resolve().parents[1]
DATA = json.loads((ROOT / "data" / "logistics_data.json").read_text(encoding="utf-8-sig"))


class DatasetStoreTests(unittest.TestCase):
    def test_save_list_activate_delete(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = DatasetStore(tmp)
            meta = store.save(copy.deepcopy(DATA), name="1С 15.11")

            self.assertEqual(meta["points"], 214)
            self.assertEqual(meta["addresses"], 192)
            self.assertEqual(meta["vehicles"], 16)
            self.assertEqual(meta["demand_kg"], 63630)

            self.assertEqual(len(store.list()), 1)
            self.assertIsNone(store.active())

            store.activate(meta["id"])
            self.assertEqual(store.active(), meta["id"])
            self.assertTrue(store.list()[0]["active"])

            loaded = store.load_data(meta["id"])
            self.assertEqual(len(loaded["delivery_points"]), 214)

            store.delete(meta["id"])
            self.assertIsNone(store.active())
            self.assertEqual(store.list(), [])

    def test_invalid_payload_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = DatasetStore(tmp)
            broken = copy.deepcopy(DATA)
            broken["delivery_points"][0]["demand"] = -1
            with self.assertRaises(DatasetError):
                store.save(broken)

    def test_unknown_dataset_is_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = DatasetStore(tmp)
            with self.assertRaises(DatasetError):
                store.meta("deadbeef00")
            with self.assertRaises(DatasetError):
                store.activate("../etc/passwd")

    def test_summary_reports_coordinates(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = DatasetStore(tmp)
            meta = store.save(copy.deepcopy(DATA), name="С координатами")
            self.assertEqual(meta["with_coords"], 214)
            self.assertTrue(meta["depot_coords"])

            broken = copy.deepcopy(DATA)
            broken["depot"] = {"id": "depot", "address": "Склад"}
            for point in broken["delivery_points"]:
                point["coords"] = None
            meta = store.save(broken, name="Без координат")
            self.assertEqual(meta["with_coords"], 0)
            self.assertFalse(meta["depot_coords"])

    def test_collect_matrix_points_uses_unique_coordinates(self):
        points, skipped = collect_matrix_points(copy.deepcopy(DATA))
        # склад + 192 физических адреса
        self.assertEqual(len(points), 193)
        self.assertEqual(points[0]["index"], 0)
        self.assertEqual(points[0]["kind"], "depot")
        self.assertEqual(skipped, [])
        addresses = [point["address"] for point in points]
        self.assertEqual(len(addresses), len(set(addresses)))
        self.assertTrue(all(point["lat"] and point["lon"] for point in points))

    def test_collect_matrix_points_reports_missing_coords(self):
        from collections import Counter

        from logistics.data_loader import normalize_address

        data = copy.deepcopy(DATA)
        counts = Counter(normalize_address(point["address"]) for point in data["delivery_points"])
        address = next(item for item, count in counts.items() if count == 1)
        for point in data["delivery_points"]:
            if normalize_address(point["address"]) == address:
                point["coords"] = None

        points, skipped = collect_matrix_points(data)
        self.assertLess(len(points), 193)
        self.assertEqual(skipped, [address])

    def test_matrix_info(self):
        self.assertFalse(matrix_info(None)["ready"])
        self.assertEqual(
            matrix_info({"size": 193, "provider": "osrm", "built_at": "x"})["size"], 193
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)

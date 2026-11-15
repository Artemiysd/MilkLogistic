"""Проверки нарезки маршрута на участки — самая ошибкоопасная часть сервиса."""

import unittest

from app.osrm import chunk_ranges, haversine_m
from app.models import PointIn


class ChunkRangesTest(unittest.TestCase):
    def test_single_segment_when_fits(self):
        self.assertEqual(list(chunk_ranges(3, 100)), [(0, 2)])

    def test_exact_limit(self):
        self.assertEqual(list(chunk_ranges(100, 100)), [(0, 99)])

    def test_overlap_and_coverage(self):
        ranges = list(chunk_ranges(215, 100))
        self.assertEqual(ranges, [(0, 99), (99, 198), (198, 214)])

    def test_segments_are_contiguous(self):
        ranges = list(chunk_ranges(250, 100))
        self.assertEqual(ranges[0][0], 0)
        self.assertEqual(ranges[-1][1], 249)
        for prev, nxt in zip(ranges, ranges[1:]):
            self.assertEqual(prev[1], nxt[0], "сегменты должны перекрываться одной точкой")

    def test_no_segment_larger_than_size(self):
        for start, end in chunk_ranges(1000, 50):
            self.assertLessEqual(end - start + 1, 50)

    def test_less_than_two_points(self):
        self.assertEqual(list(chunk_ranges(1, 100)), [])


class HaversineTest(unittest.TestCase):
    def test_depot_to_first_point(self):
        depot = PointIn(lat=53.708928, lon=23.828288)
        point = PointIn(lat=53.6753356, lon=23.9116412)
        distance = haversine_m(depot, point)
        # по прямой ~6.6 км, по дорогам OSRM дал 11.1 км (коэффициент извилистости ~1.7)
        self.assertGreater(distance, 6000)
        self.assertLess(distance, 7000)


if __name__ == "__main__":
    unittest.main()

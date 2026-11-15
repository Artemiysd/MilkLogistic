"""
Реальная дорожная матрица (OSRM) в терминах планировщика: минуты и километры по адресам.

Матрица хранится в data/matrix_cache.json рядом с JSON данных:
  * points       — узлы матрицы: index, id, address, lat, lon;
  * distance_m   — направленные расстояния, метры (size x size);
  * duration_s   — направленное время в пути, секунды (size x size).

Ключи матрицы — нормализованные адреса: у планировщика остановка это физический
адрес, а не отдельная заявка. Если адреса нет в матрице — fallback
(15 минут / 8 км), адрес запоминается в unknown_keys для отчёта.
"""

from __future__ import annotations

import json
from pathlib import Path

from .data_loader import normalize_address
from .logger import log

DEFAULT_FALLBACK_MINUTES = 15
DEFAULT_FALLBACK_KM = 8.0


def load_matrix_cache(data_path: Path | None = None) -> dict | None:
    """Ищет matrix_cache.json рядом с файлом данных и в корне data/; None если нет."""
    candidates: list[Path] = []
    if data_path is not None:
        candidates.append(data_path.parent / "matrix_cache.json")
    root = Path(__file__).resolve().parent.parent
    candidates.append(root / "data" / "matrix_cache.json")
    for candidate in candidates:
        if candidate.is_file():
            try:
                with candidate.open(encoding="utf-8-sig") as file:
                    return json.load(file)
            except (OSError, ValueError) as error:
                log("ЗАГРУЗКА", f"Матрица {candidate.name} не прочитана: {error}")
                return None
    return None


class MatrixTravelProvider:
    """Расстояния и время из дорожной матрицы OSRM."""

    def __init__(self, cache: dict, *, source: str = ""):
        self.points = cache["points"]
        self.distance_m = cache["distance_m"]
        self.duration_s = cache["duration_s"]
        self.provider_name = cache.get("provider", "unknown")
        self.built_at = cache.get("built_at")
        self.source = source

        self.index_by_address: dict[str, int] = {}
        for point in self.points:
            self.index_by_address.setdefault(
                normalize_address(point["address"]), int(point["index"])
            )

        self.unknown_keys: set[str] = set()
        self.fallback_minutes = DEFAULT_FALLBACK_MINUTES
        self.fallback_km = DEFAULT_FALLBACK_KM

    def _index(self, key: str) -> int | None:
        return self.index_by_address.get(normalize_address(key))

    def km(self, origin: str, destination: str, origin_street: str = "", destination_street: str = "") -> float:
        if normalize_address(origin) == normalize_address(destination):
            return 0.0
        i, j = self._index(origin), self._index(destination)
        if i is None or j is None:
            self.unknown_keys.add(origin if i is None else destination)
            return self.fallback_km
        return round(self.distance_m[i][j] / 1000.0, 3)

    def minutes(self, origin: str, destination: str, origin_street: str = "", destination_street: str = "") -> int:
        if normalize_address(origin) == normalize_address(destination):
            return 0
        i, j = self._index(origin), self._index(destination)
        if i is None or j is None:
            self.unknown_keys.add(origin if i is None else destination)
            return self.fallback_minutes
        return max(1, int(round(self.duration_s[i][j] / 60.0)))

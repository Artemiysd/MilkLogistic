"""Импорт выгрузок 1С и хранение наборов данных.

1С отдаёт JSON по контракту ``schema.json``. Файл принимается на
``POST /api/import`` (тело запроса) или ``POST /api/import/file`` (файл),
проверяется тем же валидатором, что и штатные данные, и сохраняется в
``IMPORT_DIR`` (по умолчанию ``/data/imports``):

    {id}.data.json    нормализованный набор (склад, машины, заявки, ограничения)
    {id}.meta.json    карточка набора: имя, источник, дата, сводка
    {id}.matrix.json  дорожная матрица этого набора (если удалось собрать)
    active.json       указатель на активный набор (его читает фронтенд)

Матрица для импортированной выгрузки строится через сервис маршрутизации
(``POST /api/calculate-table``) по координатам точек. Если координат нет или
сервис недоступен, набор всё равно сохраняется, а расчёт идёт по базовой
матрице с честными предупреждениями о неизвестных адресах.
"""

from __future__ import annotations

import json
import math
import re
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from logistics.data_loader import normalize_address, validate_problem

DATASET_ID_LEN = 10
DATASET_ID_RE = re.compile(r"^[0-9a-f]{8,32}$")
DETOUR = 1.30
EARTH_R_M = 6_371_000.0
FALLBACK_SPEED_M_S = 30_000.0 / 3600.0  # 30 км/ч для недостижимых пар
MAX_MATRIX_POINTS = 300


class DatasetError(RuntimeError):
    """Ошибка импорта или доступа к набору данных."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _haversine_m(a: dict, b: dict) -> float:
    dlat = math.radians(b["lat"] - a["lat"])
    dlon = math.radians(b["lon"] - a["lon"])
    s = (
        math.sin(dlat / 2) ** 2
        + math.cos(math.radians(a["lat"]))
        * math.cos(math.radians(b["lat"]))
        * math.sin(dlon / 2) ** 2
    )
    return 2 * EARTH_R_M * math.asin(math.sqrt(s))


def _safe_name(name: str | None, data: dict[str, Any]) -> str:
    if name and name.strip():
        return name.strip()[:120]
    meta = data.get("meta") if isinstance(data.get("meta"), dict) else {}
    for key in ("document_number", "date"):
        value = str(meta.get(key) or "").strip()
        if value:
            return f"Выгрузка 1С {value}"
    return f"Выгрузка 1С {datetime.now().strftime('%d.%m.%Y %H:%M')}"


def _addresses(data: dict[str, Any]) -> set[str]:
    return {
        normalize_address(point.get("address", ""))
        for point in data.get("delivery_points", [])
        if normalize_address(point.get("address", ""))
    }


def _summary(data: dict[str, Any]) -> dict[str, Any]:
    points = data.get("delivery_points", [])
    vehicles = data.get("vehicles", [])
    with_coords = sum(
        1
        for point in points
        if isinstance(point.get("coords"), dict)
        and point["coords"].get("lat") is not None
        and point["coords"].get("lon") is not None
    )
    depot = data.get("depot") if isinstance(data.get("depot"), dict) else {}
    depot_coords = depot.get("coords") if isinstance(depot.get("coords"), dict) else {}
    return {
        "points": len(points),
        "addresses": len(_addresses(data)),
        "vehicles": sum(int(item.get("count", 0) or 0) for item in vehicles),
        "vehicle_types": len(vehicles),
        "demand_kg": round(sum(float(point.get("demand", 0) or 0) for point in points), 1),
        "with_coords": with_coords,
        "depot_coords": bool(depot_coords.get("lat") is not None and depot_coords.get("lon") is not None),
    }


class DatasetStore:
    """Файловое хранилище импортированных наборов 1С."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.active_file = self.root / "active.json"

    # ------------------------------------------------------------ пути

    def _check_id(self, dataset_id: str) -> str:
        if not DATASET_ID_RE.fullmatch(str(dataset_id or "")):
            raise DatasetError(f"Некорректный идентификатор набора: {dataset_id!r}")
        return str(dataset_id)

    def _data_path(self, dataset_id: str) -> Path:
        return self.root / f"{self._check_id(dataset_id)}.data.json"

    def _meta_path(self, dataset_id: str) -> Path:
        return self.root / f"{self._check_id(dataset_id)}.meta.json"

    def _matrix_path(self, dataset_id: str) -> Path:
        return self.root / f"{self._check_id(dataset_id)}.matrix.json"

    # ------------------------------------------------------------ запись

    def save(
        self,
        data: dict[str, Any],
        *,
        name: str | None = None,
        source: str = "1c",
    ) -> dict[str, Any]:
        """Проверяет выгрузку и сохраняет её как новый набор."""
        try:
            validate_problem(data)
        except ValueError as exc:
            raise DatasetError(str(exc)) from exc

        for attempt in range(5):
            dataset_id = uuid.uuid4().hex[:DATASET_ID_LEN]
            if not self._meta_path(dataset_id).exists():
                break
        else:  # pragma: no cover - практически недостижимо
            raise DatasetError("Не удалось выделить идентификатор набора")

        meta = {
            "id": dataset_id,
            "name": _safe_name(name, data),
            "source": source,
            "created_at": _now(),
            "document": data.get("meta", {}) if isinstance(data.get("meta"), dict) else {},
            **_summary(data),
        }
        self._data_path(dataset_id).write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        self._meta_path(dataset_id).write_text(
            json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return meta

    def save_matrix(self, dataset_id: str, cache: dict[str, Any]) -> None:
        self._matrix_path(dataset_id).write_text(
            json.dumps(cache, ensure_ascii=False), encoding="utf-8"
        )

    # ------------------------------------------------------------ чтение

    def meta(self, dataset_id: str) -> dict[str, Any]:
        path = self._meta_path(dataset_id)
        if not path.exists():
            raise DatasetError(f"Набор {dataset_id} не найден")
        return json.loads(path.read_text(encoding="utf-8-sig"))

    def load_data(self, dataset_id: str) -> dict[str, Any]:
        path = self._data_path(dataset_id)
        if not path.exists():
            raise DatasetError(f"Набор {dataset_id} не найден")
        return json.loads(path.read_text(encoding="utf-8-sig"))

    def matrix(self, dataset_id: str) -> dict[str, Any] | None:
        path = self._matrix_path(dataset_id)
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            return None

    def list(self) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for path in self.root.glob("*.meta.json"):
            try:
                items.append(json.loads(path.read_text(encoding="utf-8-sig")))
            except (OSError, ValueError):
                continue
        items.sort(key=lambda item: str(item.get("created_at", "")), reverse=True)
        active = self.active()
        for item in items:
            item["active"] = item.get("id") == active
        return items

    # ------------------------------------------------------------ активный набор

    def active(self) -> str | None:
        if not self.active_file.exists():
            return None
        try:
            payload = json.loads(self.active_file.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            return None
        dataset_id = payload.get("dataset_id") if isinstance(payload, dict) else None
        if not dataset_id or not self._meta_path(str(dataset_id)).exists():
            return None
        return str(dataset_id)

    def activate(self, dataset_id: str | None) -> str | None:
        """Делает набор активным. ``None`` возвращает штатные данные из DATA_FILE."""
        if dataset_id is None:
            self.active_file.unlink(missing_ok=True)
            return None
        self.meta(dataset_id)  # проверка существования
        self.active_file.write_text(
            json.dumps({"dataset_id": dataset_id, "activated_at": _now()}, ensure_ascii=False),
            encoding="utf-8",
        )
        return dataset_id

    def delete(self, dataset_id: str) -> dict[str, Any]:
        meta = self.meta(dataset_id)
        for path in (self._data_path(dataset_id), self._meta_path(dataset_id), self._matrix_path(dataset_id)):
            path.unlink(missing_ok=True)
        if self.active() == dataset_id:
            self.activate(None)
        return meta


# ---------------------------------------------------------------- матрица


def collect_matrix_points(data: dict[str, Any]) -> tuple[list[dict[str, Any]], list[str]]:
    """Точки матрицы: склад + уникальные адреса, у которых есть координаты."""
    depot = data.get("depot", {})
    coords = depot.get("coords") if isinstance(depot.get("coords"), dict) else {}
    lat, lon = coords.get("lat"), coords.get("lon")
    if lat is None or lon is None:
        raise DatasetError("У склада нет координат — матрицу по этой выгрузке не построить")

    points: list[dict[str, Any]] = [
        {
            "index": 0,
            "id": str(depot.get("id", "depot")),
            "kind": "depot",
            "name": str(depot.get("name", "Склад")),
            "address": normalize_address(depot.get("address", "")),
            "lat": float(lat),
            "lon": float(lon),
        }
    ]
    seen = {points[0]["address"]}
    skipped: list[str] = []

    for point in data.get("delivery_points", []):
        address = normalize_address(point.get("address", ""))
        if not address or address in seen:
            continue
        point_coords = point.get("coords") if isinstance(point.get("coords"), dict) else {}
        plat, plon = point_coords.get("lat"), point_coords.get("lon")
        if plat is None or plon is None:
            skipped.append(address)
            continue
        seen.add(address)
        points.append(
            {
                "index": len(points),
                "id": str(point.get("id", len(points))),
                "kind": "delivery",
                "name": str(point.get("name", "")),
                "address": address,
                "lat": float(plat),
                "lon": float(plon),
            }
        )
    return points, skipped


def _table(routing_url: str, points: list[dict[str, Any]], timeout: float = 180.0) -> dict[str, Any]:
    payload = {
        "points": [{"id": str(point["index"]), "lat": point["lat"], "lon": point["lon"]} for point in points],
        "profile": "driving",
    }
    request = urllib.request.Request(
        routing_url.rstrip("/") + "/api/calculate-table",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")
        raise DatasetError(f"Сервис маршрутизации ответил {exc.code}: {detail[:300]}") from exc
    except (urllib.error.URLError, TimeoutError, ValueError) as exc:
        raise DatasetError(f"Сервис маршрутизации недоступен: {exc}") from exc


def build_matrix(data: dict[str, Any], routing_url: str | None, timeout: float = 180.0) -> dict[str, Any]:
    """Собирает матрицу расстояний/времени для выгрузки через сервис маршрутизации."""
    if not routing_url:
        raise DatasetError("ROUTING_URL не задан — матрицу собрать негде")
    points, skipped = collect_matrix_points(data)
    if len(points) < 2:
        raise DatasetError("Меньше двух точек с координатами — матрица не нужна")
    if len(points) > MAX_MATRIX_POINTS:
        raise DatasetError(
            f"Точек с координатами {len(points)}, лимит сервиса {MAX_MATRIX_POINTS}. "
            "Разбейте выгрузку на части или используйте базовую матрицу"
        )

    started = datetime.now(timezone.utc)
    response = _table(routing_url, points, timeout=timeout)
    size = int(response.get("size", len(points)))
    distances = response.get("distance_m") or []
    durations = response.get("duration_s") or []
    if size != len(points) or len(distances) != size or len(durations) != size:
        raise DatasetError("Сервис маршрутизации вернул матрицу неожиданного размера")

    cells = 0
    fallback_cells = 0
    for i in range(size):
        for j in range(size):
            distance = distances[i][j]
            duration = durations[i][j]
            if distance is None or duration is None:
                distance = _haversine_m(points[i], points[j]) * DETOUR
                duration = distance / FALLBACK_SPEED_M_S
                fallback_cells += 1
            distances[i][j] = round(float(distance), 1)
            durations[i][j] = round(float(duration), 1)
            if i != j:
                cells += 1

    return {
        "version": 1,
        "provider": "osrm",
        "base_url": response.get("provider") or routing_url,
        "built_at": started.isoformat(timespec="seconds"),
        "points": points,
        "size": size,
        "distance_m": distances,
        "duration_s": durations,
        "skipped_addresses": skipped,
        "stats": {
            "cells": cells,
            "fallback_cells": fallback_cells,
            "elapsed_ms": response.get("elapsed_ms"),
        },
    }


def matrix_info(cache: dict[str, Any] | None) -> dict[str, Any]:
    if not cache:
        return {"ready": False, "size": None, "provider": None, "built_at": None}
    return {
        "ready": bool(cache.get("size")),
        "size": cache.get("size"),
        "provider": cache.get("provider"),
        "built_at": cache.get("built_at"),
        "skipped_addresses": len(cache.get("skipped_addresses", [])),
    }

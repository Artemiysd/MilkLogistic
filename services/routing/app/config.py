"""Настройки сервиса маршрутизации. Всё через переменные окружения."""

from __future__ import annotations

import os
from dataclasses import dataclass


def _float(name: str, default: float) -> float:
    try:
        return float(os.environ[name])
    except (KeyError, ValueError):
        return default


def _int(name: str, default: int) -> int:
    try:
        return int(os.environ[name])
    except (KeyError, ValueError):
        return default


@dataclass(frozen=True)
class Settings:
    # OSRM: публичный demo или свой контейнер (http://osrm:5000)
    osrm_base_url: str = os.environ.get("OSRM_BASE_URL", "https://router.project-osrm.org").rstrip("/")
    osrm_timeout_s: float = _float("OSRM_TIMEOUT_SEC", 30.0)
    osrm_retries: int = _int("OSRM_RETRIES", 3)
    # пауза между запросами к OSRM: демо-сервер не любит очереди
    osrm_min_interval_s: float = _float("OSRM_MIN_INTERVAL_SEC", 0.4)
    # OSRM /table на demo-сервере: не больше 100 координат в матрице за запрос
    chunk_size: int = _int("OSRM_CHUNK_SIZE", 100)
    max_points: int = _int("MAX_POINTS", 300)
    cache_size: int = _int("CACHE_SIZE", 64)
    health_cache_s: float = _float("HEALTH_CACHE_SEC", 30.0)
    version: str = "1.0.0"


settings = Settings()

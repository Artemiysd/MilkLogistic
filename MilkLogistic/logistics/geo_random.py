from __future__ import annotations

import hashlib
import math

from .routing import TravelTimeProvider

DEFAULT_SPEED_KMH = 40.0
KM_MIN = 2.0
KM_MAX = 10.0


def _uniform(seed_text: str, lo: float, hi: float) -> float:
    """Детерминированное число в [lo, hi] по строке-затравке: одинаковый км при каждом запуске."""
    digest = hashlib.md5(seed_text.encode("utf-8")).digest()
    value = int.from_bytes(digest[:8], "big") / 2 ** 64
    return lo + value * (hi - lo)


class KmTravelTimeProvider:
    """Заглушка дорожной матрицы: генерирует километраж между адресами и переводит его в минуты.

    Каждому адресу присваивается детерминированный «радиус» от базы (2–14 км).
    Перегон между адресами = разница радиусов + случайная добавка 0.5–4 км, минимум 0.8 км.
    Симметрично: A->B = B->A. Время = км / скорость (по умолчанию 35 км/ч).
    """

    def __init__(self, speed_kmh: float = DEFAULT_SPEED_KMH, km_min: float = KM_MIN, km_max: float = KM_MAX):
        self.speed_kmh = max(5.0, float(speed_kmh))
        self.km_min = float(km_min)
        self.km_max = float(km_max)
        self.radius: dict[str, float] = {}
        self.pair_km: dict[tuple[str, str], float] = {}

    def radius_of(self, key: str) -> float:
        value = self.radius.get(key)
        if value is None:
            value = _uniform("radius|" + key.casefold(), self.km_min, self.km_max)
            self.radius[key] = value
        return value

    def km(self, origin: str, destination: str, origin_street: str = "", destination_street: str = "") -> float:
        if origin == destination:
            return 0.0
        pair = tuple(sorted((origin, destination), key=str.casefold))
        cached = self.pair_km.get(pair)
        if cached is None:
            radial = abs(self.radius_of(pair[0]) - self.radius_of(pair[1]))
            noise = _uniform("leg|" + pair[0].casefold() + ">" + pair[1].casefold(), 0.5, 2.0)
            cached = round(max(0.8, radial + noise), 1)
            self.pair_km[pair] = cached
        return cached

    def minutes(self, origin: str, destination: str, origin_street: str = "", destination_street: str = "") -> int:
        distance = self.km(origin, destination, origin_street, destination_street)
        return max(1, math.ceil(distance / self.speed_kmh * 60))

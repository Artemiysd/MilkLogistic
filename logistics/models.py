from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class DeliveryStop:
    """One physical delivery address; multiple customers at the same address are grouped."""

    key: str
    address: str
    street: str
    demand_kg: int
    points: list[dict[str, Any]] = field(default_factory=list)
    earliest_service_minutes: int | None = None
    restricted: bool = False
    busy_until: int | None = None
    priority: bool = False
    required_type: str | None = None

    @property
    def point_ids(self) -> list[str]:
        return [str(point.get("id", "")) for point in self.points]

    @property
    def customer_count(self) -> int:
        return len(self.points)


@dataclass(frozen=True)
class Vehicle:
    vehicle_id: str
    vehicle_type: str
    capacity_kg: int
    restricted_to_small_streets: bool = False

    @property
    def label(self) -> str:
        tonnes = self.capacity_kg / 1000
        return f"{tonnes:g} т ({self.vehicle_id})"


@dataclass
class StopVisit:
    stop: DeliveryStop
    arrival_minutes: int
    service_start_minutes: int
    service_end_minutes: int
    travel_minutes: int
    wait_minutes: int
    travel_km: float = 0.0


@dataclass
class VehicleRoute:
    vehicle: Vehicle
    stops: list[StopVisit]
    load_kg: int
    travel_minutes: int
    working_minutes: int
    finish_minutes: int
    warnings: list[str] = field(default_factory=list)
    total_km: float = 0.0
    lunch_minutes: int = 0
    driver_number: int = 0
    trip_no: int = 1

    @property
    def utilization(self) -> float:
        return self.load_kg / self.vehicle.capacity_kg if self.vehicle.capacity_kg else 0.0


@dataclass
class PlanningResult:
    routes: list[VehicleRoute]
    all_points_count: int
    all_stops_count: int
    total_demand_kg: int
    unassigned_stops: list[DeliveryStop] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    planner_mode: str = "heuristic"

    @property
    def assigned_points_count(self) -> int:
        return sum(visit.stop.customer_count for route in self.routes for visit in route.stops)

    @property
    def assigned_demand_kg(self) -> int:
        return sum(route.load_kg for route in self.routes)

    @property
    def vehicles_used(self) -> int:
        return len(self.routes)

    @property
    def unassigned_weight_kg(self) -> int:
        return sum(stop.demand_kg for stop in self.unassigned_stops)

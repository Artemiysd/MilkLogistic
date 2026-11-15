export interface DeliveryPoint {
    id : string;
    name: string;
    address: string;
    demand: number;
    latitude: number;
    longitude: number;
}
export interface Vehicle{
    id:string;
    name:string;
    capacity:number;
}

/* ------------------------------------------------------------------ API */

export interface ApiCoords {
    lat: number;
    lon: number;
}

export interface ApiDepot {
    id: string;
    name?: string;
    address: string;
    coords: ApiCoords;
}

export interface ApiPoint {
    id: string;
    name: string;
    address: string;
    street?: string;
    demand: number;
    coords: ApiCoords | null;
}

export interface ApiVehicleType {
    id: string;
    capacity: number;
    count: number;
    restricted_to_small_streets?: boolean;
}

export interface ApiData {
    depot: ApiDepot;
    vehicles: { id: string; type_id: string; capacity: number }[];
    vehicle_types: ApiVehicleType[];
    delivery_points: ApiPoint[];
    constraints: Record<string, unknown>;
}

export interface ApiRouteStop {
    seq: number;
    point_id: string | null;
    point_ids: string[];
    address: string;
    street: string;
    demand_kg: number;
    arrive: string;
    leave: string;
    wait_minutes: number;
    travel_m: number;
    coords: ApiCoords | null;
    orders: { id: string; name: string; demand_kg: number }[];
}

export interface ApiRoute {
    route_number: number;
    vehicle_id: string;
    vehicle_type: string;
    vehicle_capacity: number;
    driver: number;
    trip_no: number;
    load_kg: number;
    load_ratio: number;
    distance_m: number;
    work_minutes: number;
    end_time: string;
    warnings: string[];
    stops: ApiRouteStop[];
    geometry: { type: "LineString"; coordinates: [number, number][] } | null;
}

export interface ApiViolation {
    type: string;
    message: string;
    vehicle_id?: string;
    point_id?: string;
}

export interface OptimizeResult {
    run_id: string;
    created_at: string;
    elapsed_ms: number;
    routes: ApiRoute[];
    totals: {
        distance_m: number;
        vehicles_used: number;
        demand_kg: number;
        assigned_kg: number;
        unassigned_kg: number;
        capacity_kg: number;
        avg_load_ratio: number;
        orders_total: number;
        addresses_total: number;
        geometry_routes: number;
    };
    baseline: { distance_m: number; source: string };
    savings: { distance_pct: number | null; distance_m: number };
    unassigned: { address: string; demand_kg: number; point_ids: string[] }[];
    violations: ApiViolation[];
    warnings: string[];
}

export interface OptimizeRequest {
    drivers?: number | null;
    include_geometry?: boolean;
}

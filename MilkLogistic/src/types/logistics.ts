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
    coords?: ApiCoords | null;
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
    dataset_id?: string | null;
    dataset?: { id: string | null; name: string };
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
    status?: "pending" | "delivered" | "failed";
    reason?: string | null;
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
    min_load_kg?: number;
    underloaded?: boolean;
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
    wave?: number;
    parent_run_id?: string;
    rebalanced_from?: string;
    review?: {
        min_load_ratio: number;
        underloaded_routes: number[];
        underloaded_count: number;
        needs_decision: boolean;
    };
    extra?: {
        start_time: string;
        leftover_addresses: number;
        planned_addresses: number;
        added_from_dataset: number;
        vehicles: string[];
        source: string;
    };
}

export interface OptimizeRequest {
    drivers?: number | null;
    include_geometry?: boolean;
    dataset_id?: string | null;
}

/* ------------------------------------------------- Импорт 1С и водители */

export interface ImportResult {
    status: string;
    dataset: {
        id: string;
        name: string;
        points: number;
        addresses: number;
        vehicles: number;
        demand_kg: number;
        with_coords?: number;
        depot_coords?: boolean;
    };
    matrix: { ready: boolean; size: number | null; provider: string | null; error: string | null };
    active: string | null;
}

export interface Driver {
    id: string;
    name: string;
    phone: string | null;
    vehicle_id: string | null;
    token: string;
    active: boolean;
    created_at: string;
    link: string;
}

export interface AssignmentProgress {
    total: number;
    resolved: number;
    delivered: number;
    failed: number;
    pending: number;
    percent: number;
}

export interface AssignmentPayload {
    route_number: number;
    vehicle_id: string;
    vehicle_type?: string;
    vehicle_capacity: number;
    load_kg: number;
    distance_m: number;
    work_minutes: number;
    end_time: string;
    geometry: { type: "LineString"; coordinates: [number, number][] } | null;
    remaining_geometry?: { type: "LineString"; coordinates: [number, number][] } | null;
    remaining_distance_m?: number | null;
    stops: ApiRouteStop[];
    depot?: ApiDepot;
    run_created_at?: string;
}

export interface Assignment {
    id: string;
    run_id: string;
    dataset_id: string | null;
    route_number: number;
    driver_id: string;
    vehicle_id: string | null;
    status: string;
    created_at: string;
    updated_at: string;
    payload: AssignmentPayload;
    progress: AssignmentProgress;
    driver?: Driver | null;
    link?: string | null;
}

export interface DriverView {
    driver: Driver;
    assignment: Assignment;
    events: { id: number; type: string; text: string | null; created_at: string }[];
}

export interface DispatchOverview {
    drivers: Driver[];
    assignments: Assignment[];
    stats: { drivers: number; assignments: number; delivered: number; stops: number };
    current_run?: {
        run_id: string;
        created_at: string | null;
        dataset_id: string | null;
        dataset_name?: string;
        routes: number;
    } | null;
}


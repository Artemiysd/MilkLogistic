import { useEffect, useMemo, useState } from "react";
import { deliveryPoints, vehicles, depot } from "../data/mockData.ts";
import "./Dashboard.css";
import RouteInfo from "../components/RouteInfo";
import LogisticsMap, { type MapRoute } from "../components/LogisticsMap";
import type { ApiData, ApiRoute, OptimizeResult } from "../types/logistics.ts";
import { fetchData, optimize } from "../api/client.ts";
import { formatKm, formatMinutes, routeColor } from "../utils/format.ts";

function Dashboard() {
    const [apiData, setApiData] = useState<ApiData | null>(null);
    const [isOptimized, setIsOptimized] = useState(false);
    const [mapMode, setMapMode] = useState<"points" | "routes">("points");
    const [searchQuery, setSearchQuery] = useState("");
    const [result, setResult] = useState<OptimizeResult | null>(null);
    const [selectedRoute, setSelectedRoute] = useState<number | null>(null);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState<string | null>(null);

    useEffect(() => {
        let alive = true;
        fetchData()
            .then((data) => {
                if (alive) setApiData(data);
            })
            .catch(() => {
                /* остаёмся на локальном JSON */
            });
        return () => {
            alive = false;
        };
    }, []);

    const points = useMemo(
        () =>
            apiData
                ? apiData.delivery_points
                    .filter((point) => point.coords)
                    .map((point) => ({
                        id: point.id,
                        name: point.name,
                        address: point.address,
                        demand: Number(point.demand),
                        latitude: point.coords!.lat,
                        longitude: point.coords!.lon,
                    }))
                : deliveryPoints,
        [apiData],
    );

    const depotPoint = apiData
        ? { latitude: apiData.depot.coords.lat, longitude: apiData.depot.coords.lon }
        : depot;

    const totalDemand = points.reduce((sum, point) => sum + point.demand, 0);

    const totalVehicles = apiData
        ? apiData.vehicle_types.reduce((sum, type) => sum + type.count, 0)
        : vehicles.reduce((sum, vehicle) => sum + vehicle.count, 0);

    const filteredPoints = points.filter(
        (point) =>
            point.name.toLowerCase().includes(searchQuery.toLowerCase()) ||
            point.address.toLowerCase().includes(searchQuery.toLowerCase()),
    );

    const mapRoutes: MapRoute[] = useMemo(
        () =>
            (result?.routes ?? []).flatMap((route, index) =>
                route.geometry
                    ? [{
                        number: route.route_number,
                        color: routeColor(index),
                        positions: route.geometry.coordinates.map(
                            ([lon, lat]) => [lat, lon] as [number, number],
                        ),
                    }]
                    : [],
            ),
        [result],
    );

    const activeRoute: ApiRoute | null = useMemo(() => {
        if (!result || selectedRoute === null) return null;
        return result.routes.find((route) => route.route_number === selectedRoute) ?? null;
    }, [result, selectedRoute]);
    const selectedRoutePoints = useMemo(() => {
        if (!activeRoute) return [];

        return activeRoute.stops
            .filter((stop) => stop.coords !== null)
            .map((stop, index) => ({
                id: stop.point_id ?? `route-${activeRoute.route_number}-stop-${index}`,
                name: stop.address,
                address: stop.street || stop.address,
                demand: stop.demand_kg,
                latitude: stop.coords!.lat,
                longitude: stop.coords!.lon,
            }));
    }, [activeRoute]);

    async function runOptimize() {
        setLoading(true);
        setError(null);
        try {
            const response = await optimize({});
            setResult(response);
            setSelectedRoute(response.routes[0]?.route_number ?? null);
            setIsOptimized(true);
            setMapMode("routes");
        } catch (err) {
            setError(err instanceof Error ? err.message : "Не удалось рассчитать план");
        } finally {
            setLoading(false);
        }
    }

    return (
        <main className="dashboard">
            <header className="header">
                <div>
                    <h1> Dairy Logistics</h1>
                    <p>Оптимизация доставки молочной продукции</p>
                </div>
            </header>
            <div className="dashboard-content">
                <aside className="sidebar">
                    <h2>Общая информация</h2>
                    <div className="info-card">
                        <div>
                            <strong>{points.length}</strong>
                            <p>Точек доставки</p>
                        </div>
                    </div>
                    <div className="info-card">
                        <div>
                            <strong>{totalVehicles}</strong>
                            <p>Транспорта</p>
                        </div>
                    </div>
                    <div className="info-card">
                        <strong>{totalDemand} кг</strong>
                        <p>Общий обьем</p>
                    </div>
                    <button
                        className="optimize-button"
                        onClick={runOptimize}
                        disabled={loading}
                    >
                        {loading ? "Считаю маршруты…" : "Оптимизировать маршрут"}
                    </button>
                    {error && <div className="alert error">Ошибка: {error}</div>}
                    {result && result.unassigned.length > 0 && (
                        <div className="alert warn">
                            Не развезено {result.unassigned.length} адресов,{" "}
                            {result.totals.unassigned_kg} кг
                        </div>
                    )}
                </aside>
                <section className="map-container">
                    <div className="map-search">
                        <input
                            type="text"
                            placeholder={
                                mapMode === "points"
                                    ? "Поиск по названию или адресу..."
                                    : "Поиск по маршрутам..."
                            }
                            value={searchQuery}
                            onChange={(e) => setSearchQuery(e.target.value)}
                            disabled={mapMode === "routes"}
                        />
                    </div>
                    <div className="map-controls">
                        <button
                            className={mapMode === "points" ? "active" : ""}
                            onClick={() => setMapMode("points")}
                        >
                            Все точки
                        </button>
                        <button
                            className={mapMode === "routes" ? "active" : ""}
                            onClick={() => setMapMode("routes")}
                        >
                            Маршруты
                        </button>
                    </div>
                    {mapMode === "points" ? (
                        <LogisticsMap
                            points={filteredPoints}
                            depot={depotPoint}
                        />
                    ) : mapRoutes.length > 0 ? (
                        <>
                            <LogisticsMap
                                points={selectedRoutePoints}
                                depot={depotPoint}
                                routes={mapRoutes}
                                selectedRoute={selectedRoute}
                            />
                            <div className="route-picker">
                                {result?.routes.map((route, index) => (
                                    <button
                                        key={route.route_number}
                                        className={selectedRoute === route.route_number ? "active" : ""}
                                        style={{ borderColor: routeColor(index) }}
                                        onClick={() => setSelectedRoute(route.route_number)}
                                    >
                                        {route.vehicle_id}
                                    </button>
                                ))}
                            </div>
                        </>
                    ) : (
                        <div className="routes-placeholder">
                            <h3>Маршруты ещё не построены</h3>
                            <p>Ожидаем результат алгоритма оптимизации</p>
                        </div>
                    )}
                </section>
            </div>
            <section className="results">
                <h2>Результат оптимизации</h2>
                {activeRoute ? (
                    <RouteInfo
                        vehicle={activeRoute.vehicle_id}
                        points={activeRoute.stops.length}
                        load={activeRoute.load_kg}
                        capacity={activeRoute.vehicle_capacity}
                        distance={Number((activeRoute.distance_m / 1000).toFixed(1))}
                        time={formatMinutes(activeRoute.work_minutes)}
                    />
                ) : (
                    <div className="route-info">
                        <h3>Информация о маршруте</h3>
                        <p>Появится после расчёта плана</p>
                    </div>
                )}
                <div className="results-grid">
                    <div className="result-card">
                        <p>До оптимизации</p>
                        <strong>{result ? formatKm(result.baseline.distance_m) : "—"}</strong>
                    </div>
                    <div className="result-card">
                        <p>После оптимизации</p>
                        <strong>{result ? formatKm(result.totals.distance_m) : isOptimized ? "—" : "-"}</strong>
                    </div>
                    <div className="result-card">
                        <p>Экономия</p>
                        <strong>
                            {result && result.savings.distance_pct != null
                                ? `${result.savings.distance_pct.toFixed(1)}%`
                                : isOptimized ? "—" : "-"}
                        </strong>
                    </div>
                </div>
            </section>
        </main>
    );
}export default Dashboard;

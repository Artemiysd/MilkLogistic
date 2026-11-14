import { useEffect, useMemo, useState } from "react";
import { deliveryPoints as fallbackPoints, depot as fallbackDepot, vehicles as fallbackVehicles } from "../data/mockData.ts";
import type { ApiData, ApiRoute, OptimizeResult } from "../types/logistics.ts";
import { fetchData, optimize } from "../api/client.ts";
import { formatKm, formatMinutes, formatPercent, routeColor } from "../utils/format.ts";
import LogisticsMap, { type MapPoint, type MapRoute } from "../components/LogisticsMap.tsx";
import RouteInfo from "../components/RouteInfo.tsx";
import "./Dashboard.css";

type Source = "api" | "local";

function toMapPoint(point: {
    id: string;
    name: string;
    address: string;
    demand: number;
    latitude: number;
    longitude: number;
    color?: string;
}): MapPoint {
    return point;
}

function Dashboard() {
    const [mapPoints, setMapPoints] = useState<MapPoint[]>(
        fallbackPoints.map((point) => toMapPoint(point)),
    );
    const [depot, setDepot] = useState<{ latitude: number; longitude: number }>({
        latitude: fallbackDepot.latitude,
        longitude: fallbackDepot.longitude,
    });
    const [vehiclesTotal, setVehiclesTotal] = useState(
        fallbackVehicles.reduce((sum, vehicle) => sum + vehicle.count, 0),
    );
    const [source, setSource] = useState<Source>("local");
    const [query, setQuery] = useState("");
    const [drivers, setDrivers] = useState(16);
    const [result, setResult] = useState<OptimizeResult | null>(null);
    const [selectedRoute, setSelectedRoute] = useState<number | null>(null);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState<string | null>(null);

    useEffect(() => {
        let alive = true;
        fetchData()
            .then((data: ApiData) => {
                if (!alive) return;
                const points = data.delivery_points
                    .filter((point) => point.coords)
                    .map((point) => ({
                        id: point.id,
                        name: point.name,
                        address: point.address,
                        demand: Number(point.demand),
                        latitude: point.coords!.lat,
                        longitude: point.coords!.lon,
                    }));
                setMapPoints(points);
                setDepot({
                    latitude: data.depot.coords.lat,
                    longitude: data.depot.coords.lon,
                });
                setVehiclesTotal(
                    data.vehicle_types.reduce((sum, type) => sum + type.count, 0),
                );
                setSource("api");
            })
            .catch(() => {
                if (alive) setSource("local");
            });
        return () => {
            alive = false;
        };
    }, []);

    const totalDemand = useMemo(
        () => mapPoints.reduce((sum, point) => sum + point.demand, 0),
        [mapPoints],
    );

    const filteredPoints = useMemo(() => {
        const needle = query.trim().toLowerCase();
        if (!needle) return mapPoints;
        return mapPoints.filter(
            (point) =>
                point.name.toLowerCase().includes(needle) ||
                point.address.toLowerCase().includes(needle),
        );
    }, [mapPoints, query]);

    const routeColors = useMemo(() => {
        const colors = new Map<string, string>();
        result?.routes.forEach((route, index) => {
            const color = routeColor(index);
            route.stops.forEach((stop) => {
                stop.point_ids.forEach((id) => colors.set(id, color));
            });
        });
        return colors;
    }, [result]);

    const displayPoints = useMemo(
        () =>
            filteredPoints.map((point) => ({
                ...point,
                color: routeColors.get(point.id),
            })),
        [filteredPoints, routeColors],
    );

    const mapRoutes: MapRoute[] = useMemo(
        () =>
            (result?.routes ?? []).flatMap((route, index) => {
                if (!route.geometry) return [];
                return [
                    {
                        number: route.route_number,
                        color: routeColor(index),
                        positions: route.geometry.coordinates.map(
                            ([lon, lat]) => [lat, lon] as [number, number],
                        ),
                    },
                ];
            }),
        [result],
    );

    const activeRoute: ApiRoute | null = useMemo(() => {
        if (!result || selectedRoute === null) return null;
        return result.routes.find((route) => route.route_number === selectedRoute) ?? null;
    }, [result, selectedRoute]);

    async function runOptimize() {
        setLoading(true);
        setError(null);
        try {
            const response = await optimize({ drivers, include_geometry: true });
            setResult(response);
            setSelectedRoute(response.routes[0]?.route_number ?? null);
        } catch (err) {
            setError(err instanceof Error ? err.message : "Не удалось рассчитать план");
        } finally {
            setLoading(false);
        }
    }

    const savings = result?.savings?.distance_pct;

    return (
        <main className="dashboard">
            <header className="header">
                <div>
                    <h1>Dairy Logistics</h1>
                    <p>Оптимизация доставки молочной продукции</p>
                </div>
                <span className={`status-badge ${source}`}>
                    {source === "api" ? "данные из API" : "локальный JSON (API недоступен)"}
                </span>
            </header>

            <div className="dashboard-content">
                <aside className="sidebar">
                    <h2>Общая информация</h2>
                    <div className="info-card">
                        <div>
                            <strong>{mapPoints.length}</strong>
                            <p>Точек доставки</p>
                        </div>
                    </div>
                    <div className="info-card">
                        <div>
                            <strong>{vehiclesTotal}</strong>
                            <p>Транспорта</p>
                        </div>
                    </div>
                    <div className="info-card">
                        <strong>{totalDemand} кг</strong>
                        <p>Общий объём</p>
                    </div>

                    <label className="drivers-field">
                        Водителей на смену
                        <input
                            type="number"
                            min={1}
                            max={vehiclesTotal}
                            value={drivers}
                            onChange={(event) => setDrivers(Number(event.target.value) || 1)}
                        />
                    </label>

                    <button className="optimize-button" onClick={runOptimize} disabled={loading}>
                        {loading ? "Считаю маршруты…" : "Оптимизировать маршруты"}
                    </button>

                    {error && <div className="alert error">Ошибка: {error}</div>}
                    {result && result.unassigned.length > 0 && (
                        <div className="alert warn">
                            Не развезено {result.unassigned.length} адресов,{" "}
                            {result.totals.unassigned_kg} кг. Увеличьте число водителей.
                        </div>
                    )}
                </aside>

                <section className="map-container">
                    <div className="map-search">
                        <input
                            type="search"
                            value={query}
                            placeholder="Поиск точки по названию или адресу"
                            onChange={(event) => setQuery(event.target.value)}
                        />
                    </div>
                    <div className="map-box">
                        <LogisticsMap
                            points={displayPoints}
                            depot={depot}
                            routes={mapRoutes}
                            selectedRoute={selectedRoute}
                        />
                    </div>
                    {mapRoutes.length > 0 && (
                        <div className="map-legend">
                            <span className="legend-item">
                                <span className="legend-dot" style={{ background: "#111827" }} />
                                склад
                            </span>
                            <span className="legend-text">
                                маршрутов: {mapRoutes.length} · точек: {mapPoints.length}
                            </span>
                        </div>
                    )}
                </section>
            </div>

            <section className="results">
                <h2>Результат оптимизации</h2>
                <div className="results-grid">
                    <div className="result-card">
                        <p>До оптимизации</p>
                        <strong>{result ? formatKm(result.baseline.distance_m) : "—"}</strong>
                        <span>порядок накладной</span>
                    </div>
                    <div className="result-card">
                        <p>После оптимизации</p>
                        <strong>{result ? formatKm(result.totals.distance_m) : "—"}</strong>
                        <span>{result ? `${result.totals.vehicles_used} машин` : "нажмите кнопку"}</span>
                    </div>
                    <div className="result-card savings">
                        <p>Экономия</p>
                        <strong>{savings != null ? `${savings.toFixed(1)} %` : "—"}</strong>
                        <span>{result ? formatKm(result.savings.distance_m) : "—"}</span>
                    </div>
                    <div className="result-card">
                        <p>Средняя загрузка</p>
                        <strong>{result ? formatPercent(result.totals.avg_load_ratio) : "—"}</strong>
                        <span>{result ? `развезено ${result.totals.assigned_kg} кг` : "—"}</span>
                    </div>
                </div>

                {result && (
                    <>
                        <div className="routes-head">
                            <h3>Маршруты ({result.routes.length})</h3>
                            <span className="route-total">
                                расчёт {result.elapsed_ms} мс · заявок {result.totals.orders_total} ·
                                адресов {result.totals.addresses_total}
                            </span>
                        </div>

                        <div className="routes-grid">
                            {result.routes.map((route, index) => {
                                const color = routeColor(index);
                                const active = selectedRoute === route.route_number;
                                return (
                                    <button
                                        key={route.route_number}
                                        className={`route-card${active ? " active" : ""}`}
                                        style={{ borderLeftColor: color }}
                                        onClick={() =>
                                            setSelectedRoute(active ? null : route.route_number)
                                        }
                                    >
                                        <div className="route-card-top">
                                            <span className="route-dot" style={{ background: color }} />
                                            <strong>{route.vehicle_id}</strong>
                                            <span className="route-load">
                                                {Math.round(route.load_ratio * 100)} %
                                            </span>
                                        </div>
                                        <div className="load-bar">
                                            <span
                                                style={{
                                                    width: `${Math.min(route.load_ratio * 100, 100)}%`,
                                                    background: color,
                                                }}
                                            />
                                        </div>
                                        <div className="route-card-bottom">
                                            <span>{formatKm(route.distance_m)}</span>
                                            <span>{formatMinutes(route.work_minutes)}</span>
                                            <span>{route.stops.length} точек</span>
                                        </div>
                                    </button>
                                );
                            })}
                        </div>

                        {activeRoute && (
                            <div className="route-detail">
                                <RouteInfo
                                    vehicle={activeRoute.vehicle_id}
                                    points={activeRoute.stops.length}
                                    load={activeRoute.load_kg}
                                    capacity={activeRoute.vehicle_capacity}
                                    distance={Number((activeRoute.distance_m / 1000).toFixed(1))}
                                    time={formatMinutes(activeRoute.work_minutes)}
                                />
                                <div className="stops-table-wrap">
                                    <table className="stops-table">
                                        <thead>
                                            <tr>
                                                <th>#</th>
                                                <th>Адрес</th>
                                                <th>кг</th>
                                                <th>Прибытие</th>
                                                <th>Выезд</th>
                                                <th>Заявки</th>
                                            </tr>
                                        </thead>
                                        <tbody>
                                            {activeRoute.stops.map((stop) => (
                                                <tr key={`${activeRoute.route_number}-${stop.seq}`}>
                                                    <td>{stop.seq}</td>
                                                    <td>{stop.address}</td>
                                                    <td>{stop.demand_kg}</td>
                                                    <td>{stop.arrive}</td>
                                                    <td>{stop.leave}</td>
                                                    <td>{stop.point_ids.join(", ")}</td>
                                                </tr>
                                            ))}
                                        </tbody>
                                    </table>
                                </div>
                            </div>
                        )}

                        {result.violations.length > 0 && (
                            <div className="alert warn">
                                <strong>Нарушения ({result.violations.length})</strong>
                                <ul>
                                    {result.violations.slice(0, 10).map((violation, index) => (
                                        <li key={index}>{violation.message}</li>
                                    ))}
                                </ul>
                            </div>
                        )}

                        <p className="report-link">
                            Полный отчёт по прогону:{" "}
                            <a href={`/api/runs/${result.run_id}/report`}>
                                /api/runs/{result.run_id}/report
                            </a>
                        </p>
                    </>
                )}
            </section>
        </main>
    );
}

export default Dashboard;

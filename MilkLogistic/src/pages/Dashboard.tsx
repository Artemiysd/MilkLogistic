import { useEffect, useMemo, useRef, useState } from "react";
import { deliveryPoints, vehicles, depot } from "../data/mockData.ts";
import "./Dashboard.css";
import RouteInfo from "../components/RouteInfo";
import LogisticsMap, { type MapRoute } from "../components/LogisticsMap";
import type { ApiData, ApiRoute, OptimizeResult } from "../types/logistics.ts";
import {
    dispatchRun,
    fetchData,
    importDatasetFile,
    optimize,
    rebalanceRun,
    runExtraWave,
} from "../api/client.ts";
import { formatKm, formatMinutes, plural, routeColor } from "../utils/format.ts";
import { appPath } from "../utils/base.ts";

function Dashboard() {
    const [apiData, setApiData] = useState<ApiData | null>(null);
    const [mapMode, setMapMode] = useState<"points" | "routes">("points");
    const [searchQuery, setSearchQuery] = useState("");
    const [result, setResult] = useState<OptimizeResult | null>(null);
    const [selectedRoute, setSelectedRoute] = useState<number | null>(null);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [operation, setOperation] = useState<string | null>(null);
    const [uploading, setUploading] = useState(false);
    const [sending, setSending] = useState(false);
    const [rebalancing, setRebalancing] = useState(false);
    const [extraLoading, setExtraLoading] = useState(false);
    const [extraDataset, setExtraDataset] = useState<{ id: string; name: string; points: number } | null>(null);
    const fileRef = useRef<HTMLInputElement>(null);

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

    const allPoints = useMemo(
        () =>
            apiData
                ? apiData.delivery_points.map((point) => ({
                    id: point.id,
                    name: point.name,
                    address: point.address,
                    demand: Number(point.demand),
                    latitude: point.coords?.lat ?? Number.NaN,
                    longitude: point.coords?.lon ?? Number.NaN,
                }))
                : deliveryPoints,
        [apiData],
    );

    const points = useMemo(
        () => allPoints.filter(
            (point) => Number.isFinite(point.latitude) && Number.isFinite(point.longitude),
        ),
        [allPoints],
    );

    const depotCoords = apiData
        ? apiData.depot?.coords ?? null
        : { lat: depot.latitude, lon: depot.longitude };

    const depotPoint = depotCoords
        ? { latitude: depotCoords.lat, longitude: depotCoords.lon }
        : null;

    const pointsWithoutCoords = apiData
        ? allPoints.length - points.length
        : 0;
    const noCoords = Boolean(apiData) && (!depotCoords || points.length === 0);

    const totalDemand = allPoints.reduce((sum, point) => sum + point.demand, 0);

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

    const underloaded = result?.routes.filter((route) => route.underloaded) ?? [];
    const ready = result ? result.routes.length - underloaded.length : 0;
    const deliveredPct = result && result.totals.demand_kg
        ? Math.round((result.totals.assigned_kg / result.totals.demand_kg) * 100)
        : 0;

    async function runOptimize() {
        setLoading(true);
        setError(null);
        setOperation(null);
        setExtraDataset(null);
        try {
            const response = await optimize({});
            setResult(response);
            setSelectedRoute(response.routes[0]?.route_number ?? null);
            setMapMode("routes");
        } catch (err) {
            setError(err instanceof Error ? err.message : "Не удалось рассчитать план");
        } finally {
            setLoading(false);
        }
    }

    async function uploadFile(event: React.ChangeEvent<HTMLInputElement>) {
        const file = event.target.files?.[0];
        event.target.value = "";
        if (!file) return;
        const hasPlan = Boolean(result);
        setUploading(true);
        setError(null);
        setOperation(null);
        try {
            const imported = await importDatasetFile(file, !hasPlan);
            const coordsOk = imported.dataset.depot_coords !== false
                && (imported.dataset.with_coords ?? imported.dataset.points) > 0;
            const matrix = imported.matrix.ready
                ? `матрица ${imported.matrix.size}×${imported.matrix.size}`
                : `матрица не собрана${imported.matrix.error ? ` (${imported.matrix.error})` : ""}`;
            const coordsNote = coordsOk ? "" : " · без координат, карта не построится";
            if (hasPlan) {
                setExtraDataset({
                    id: imported.dataset.id,
                    name: imported.dataset.name,
                    points: imported.dataset.points,
                });
                setOperation(
                    `Выгрузка добавлена для доп. заезда: ${imported.dataset.name} · ` +
                    `${imported.dataset.points} точек · ${matrix}${coordsNote}`,
                );
            } else {
                setResult(null);
                setSelectedRoute(null);
                setMapMode("points");
                setApiData(await fetchData());
                setOperation(
                    `Загружено: ${imported.dataset.name} · ${imported.dataset.points} точек, ` +
                    `${imported.dataset.vehicles} машин · ${matrix}${coordsNote}`,
                );
            }
        } catch (err) {
            setError(err instanceof Error ? err.message : "Не удалось импортировать выгрузку");
        } finally {
            setUploading(false);
        }
    }

    async function sendToDrivers(includeUnderloaded: boolean) {
        if (!result) return;
        setSending(true);
        setError(null);
        try {
            const response = await dispatchRun(result.run_id, {
                allow_underloaded: includeUnderloaded,
            });
            const skipped = response.skipped?.length ?? 0;
            setOperation(
                `Отправлено рейсов: ${response.count}` +
                (skipped ? `, не отправлено с недогрузом: ${skipped}` : "") +
                ". Открываю кабинет логиста…",
            );
            window.location.href = "/dispatch";
        } catch (err) {
            setError(err instanceof Error ? err.message : "Не удалось отправить маршруты");
            setSending(false);
        }
    }

    async function rebalance() {
        if (!result) return;
        setRebalancing(true);
        setError(null);
        try {
            const fresh = await rebalanceRun(result.run_id);
            setResult(fresh);
            setSelectedRoute(fresh.routes[0]?.route_number ?? null);
            setMapMode("routes");
            setOperation(
                `Пересчитано без недогруза: ${fresh.routes.length} рейсов, ` +
                `не развезено ${fresh.unassigned.length} адресов`,
            );
        } catch (err) {
            setError(err instanceof Error ? err.message : "Не удалось пересчитать план");
        } finally {
            setRebalancing(false);
        }
    }

    async function planExtraWave() {
        if (!result) return;
        setExtraLoading(true);
        setError(null);
        setOperation(null);
        try {
            const extra = await runExtraWave(result.run_id, {
                start_time: "13:00",
                dataset_id: extraDataset?.id ?? null,
            });
            setResult(extra);
            setSelectedRoute(extra.routes[0]?.route_number ?? null);
            setMapMode("routes");
            const added = extra.extra?.added_from_dataset ?? 0;
            setOperation(
                `Доп. заезд: ${extra.routes.length} рейсов, ` +
                `${extra.routes.reduce((sum, route) => sum + route.stops.length, 0)} точек` +
                (added ? `, из новой выгрузки добавлено ${added} заявок` : ""),
            );
            setExtraDataset(null);
        } catch (err) {
            setError(err instanceof Error ? err.message : "Не удалось спланировать доп. заезд");
        } finally {
            setExtraLoading(false);
        }
    }

    return (
        <main className="dashboard">
            <header className="header">
                <div>
                    <h1>Молочный мир · доставка</h1>
                    <p>Планирование маршрутов и выдача их водителям</p>
                </div>
                <div className="header-actions">
                    <a className="header-link" href={appPath("/examples/1c-example-day.json")} download>Скачать пример 1С</a>
                    <a className="header-link" href={appPath("/dispatch")}>Кабинет логиста →</a>
                </div>
            </header>

            <div className="steps">
                <div className={`step ${!result ? "active" : "done"}`}>
                    <span>1</span> Данные
                </div>
                <div className={`step ${result ? "active" : ""}`}>
                    <span>2</span> План
                </div>
                <div className="step">
                    <span>3</span> Водители
                </div>
            </div>

            <div className="dashboard-content">
                <aside className="sidebar">
                    <h2>Данные рейса</h2>
                    <p className="dataset-name">{apiData?.dataset?.name ?? "Загрузка данных…"}</p>
                    <div className="info-card">
                        <strong>{allPoints.length}</strong>
                        <p>{plural(allPoints.length, "адрес доставки", "адреса доставки", "адресов доставки")}</p>
                    </div>
                    <div className="info-card">
                        <strong>{totalVehicles}</strong>
                        <p>машин в парке</p>
                    </div>
                    <div className="info-card">
                        <strong>{Math.round(totalDemand)} кг</strong>
                        <p>общий вес заявок</p>
                    </div>

                    <button
                        className="optimize-button"
                        onClick={runOptimize}
                        disabled={loading}
                    >
                        {loading ? "Считаю маршруты…" : "Построить маршруты"}
                    </button>

                    <button
                        className="ghost-button"
                        onClick={() => fileRef.current?.click()}
                        disabled={uploading}
                    >
                        {uploading ? "Загружаю выгрузку…" : "Загрузить 1С / JSON"}
                    </button>
                    <input
                        ref={fileRef}
                        type="file"
                        accept=".json,application/json"
                        hidden
                        onChange={uploadFile}
                    />
                    <p className="hint">
                        Формат — как в примере. После расчёта новая выгрузка пойдёт
                        в доп. заезд, текущий план не сбросится.
                    </p>
                    <a className="example-link" href={appPath("/examples/1c-example-day.json")} download>
                        Скачать пример 1С (JSON)
                    </a>

                    {result && result.unassigned.length > 0 && (
                        <button
                            className="ghost-button"
                            onClick={planExtraWave}
                            disabled={extraLoading}
                        >
                            {extraLoading
                                ? "Планирую доп. заезд…"
                                : extraDataset
                                    ? `Доп. заезд (+${extraDataset.points} из 1С)`
                                    : "Доп. заезд по остатку"}
                        </button>
                    )}

                    {noCoords && (
                        <div className="alert warn">
                            Нет координат
                            {pointsWithoutCoords > 0 ? `: ${pointsWithoutCoords} точек без coords` : ""}.
                            Карта не построится, расстояния — по запасной оценке.
                        </div>
                    )}
                    {operation && <div className="alert info">{operation}</div>}
                    {error && <div className="alert error">Ошибка: {error}</div>}
                </aside>

                <section className="map-container">
                    <div className="map-topline">
                        <div className="map-search">
                            <input
                                type="text"
                                placeholder={
                                    mapMode === "points"
                                        ? "Поиск по названию или адресу..."
                                        : "Маршруты на карте"
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
                    </div>
                    {mapMode === "points" ? (
                        depotPoint ? (
                            <LogisticsMap
                                points={filteredPoints}
                                depot={depotPoint}
                            />
                        ) : (
                            <div className="routes-placeholder">
                                <h3>Нет координат для карты</h3>
                                <p>
                                    В этой выгрузке у точек и склада нет координат.
                                    Заявки можно посчитать, но карта не построится —
                                    добавьте `coords` в JSON (широта/долгота).
                                </p>
                            </div>
                        )
                    ) : mapRoutes.length > 0 && depotPoint ? (
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
                                        className={[
                                            selectedRoute === route.route_number ? "active" : "",
                                            route.underloaded ? "underloaded" : "",
                                        ].join(" ").trim()}
                                        style={{ borderColor: routeColor(index) }}
                                        onClick={() => setSelectedRoute(route.route_number)}
                                        title={`Загрузка ${Math.round(route.load_ratio * 100)}%`}
                                    >
                                        {route.vehicle_id} · {Math.round(route.load_ratio * 100)}%
                                    </button>
                                ))}
                            </div>
                        </>
                    ) : !depotPoint ? (
                        <div className="routes-placeholder">
                            <h3>Нет координат для карты</h3>
                            <p>Маршруты рассчитаны, но линию на карте не нарисовать без координат склада.</p>
                        </div>
                    ) : (
                        <div className="routes-placeholder">
                            <h3>Маршруты ещё не построены</h3>
                            <p>Нажмите «Построить маршруты» — на карте появятся рейсы</p>
                        </div>
                    )}
                </section>
            </div>

            <section className="results">
                <h2>Результат</h2>

                <div className="results-grid">
                    <div className="result-card">
                        <p>Доставлено</p>
                        <strong>{result ? `${deliveredPct}%` : "—"}</strong>
                        <span>
                            {result
                                ? `${result.totals.assigned_kg} из ${result.totals.demand_kg} кг`
                                : "после расчёта"}
                        </span>
                    </div>
                    <div className="result-card">
                        <p>Не развезено</p>
                        <strong>{result ? result.unassigned.length : "—"}</strong>
                        <span>{result ? `${result.totals.unassigned_kg} кг` : "адресов"}</span>
                    </div>
                    <div className="result-card">
                        <p>Пробег</p>
                        <strong>{result ? formatKm(result.totals.distance_m) : "—"}</strong>
                        <span>
                            {result
                                ? `было ${formatKm(result.baseline.distance_m)} · ` +
                                  `${result.savings.distance_pct ?? 0}%`
                                : "оптимизированный"}
                        </span>
                    </div>
                    <div className="result-card">
                        <p>Машин в рейсах</p>
                        <strong>{result ? result.totals.vehicles_used : "—"}</strong>
                        <span>{result ? `из ${totalVehicles}` : "всего в парке"}</span>
                    </div>
                </div>

                {result && (
                    <div className={`review ${underloaded.length ? "has-warning" : "is-clean"}`}>
                        <div className="review-head">
                            <div>
                                <h3>Проверка плана логистом</h3>
                                <p>
                                    {underloaded.length === 0
                                        ? "Все рейсы загружены на 90% и выше — можно отправлять."
                                        : `Машин с загрузкой ниже 90%: ${underloaded.length}. ` +
                                          "По правилу они не выезжают — решите, что делать."}
                                </p>
                            </div>
                            <div className="review-counts">
                                <div className="ok"><strong>{ready}</strong><span>≥ 90%</span></div>
                                <div className={underloaded.length ? "bad" : ""}>
                                    <strong>{underloaded.length}</strong><span>&lt; 90%</span>
                                </div>
                            </div>
                        </div>

                        {underloaded.length > 0 && (
                            <ul className="review-list">
                                {underloaded.map((route) => (
                                    <li key={route.route_number}>
                                        <span>Рейс {route.route_number} · {route.vehicle_id}</span>
                                        <em>
                                            {route.load_kg} из {route.vehicle_capacity} кг ·{" "}
                                            {Math.round(route.load_ratio * 100)}%
                                        </em>
                                    </li>
                                ))}
                            </ul>
                        )}

                        <div className="review-actions">
                            <button
                                className="primary"
                                disabled={sending || ready === 0}
                                onClick={() => void sendToDrivers(false)}
                            >
                                {sending
                                    ? "Отправляю…"
                                    : ready === 0
                                        ? "Нет рейсов ≥ 90%"
                                        : `Отправить ${ready} ${plural(ready, "рейс", "рейса", "рейсов")} ≥ 90%`}
                            </button>
                            {underloaded.length > 0 && (
                                <>
                                    <button
                                        className="ghost"
                                        disabled={sending}
                                        onClick={() => void sendToDrivers(true)}
                                    >
                                        Отправить все {result.routes.length}
                                    </button>
                                    <button
                                        className="ghost"
                                        disabled={rebalancing}
                                        onClick={() => void rebalance()}
                                    >
                                        {rebalancing ? "Пересчитываю…" : "Пересчитать без недогруза"}
                                    </button>
                                </>
                            )}
                        </div>
                        <p className="review-hint">
                            «Пересчитать без недогруза» уберёт полупустые рейсы: их адреса
                            уйдут в «не развезено» и их можно закрыть доп. заездом.
                        </p>
                    </div>
                )}

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
            </section>
        </main>
    );
}

export default Dashboard;



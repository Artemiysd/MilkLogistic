import { useCallback, useEffect, useMemo, useState } from "react";
import LogisticsMap, { type MapRoute } from "../components/LogisticsMap";
import { fetchDriverView, markStop, reportProblem } from "../api/client";
import type { ApiRouteStop, DeliveryPoint, DriverView } from "../types/logistics";
import "./DriverPage.css";

const REASONS = [
    "Клиент закрыт",
    "Отказ клиента",
    "Не успел по времени",
    "Нет доступа на территорию",
    "Другое",
];

interface Props {
    token: string;
}

function DriverPage({ token }: Props) {
    const [view, setView] = useState<DriverView | null>(null);
    const [error, setError] = useState<string | null>(null);
    const [busy, setBusy] = useState<number | null>(null);
    const [reasonFor, setReasonFor] = useState<number | null>(null);
    const [reason, setReason] = useState(REASONS[0]);
    const [notice, setNotice] = useState<string | null>(null);

    const load = useCallback(async () => {
        try {
            setView(await fetchDriverView(token));
            setError(null);
        } catch (err) {
            setError(err instanceof Error ? err.message : "Не удалось загрузить маршрут");
        }
    }, [token]);

    useEffect(() => {
        void load();
        const timer = setInterval(() => void load(), 15_000);
        return () => clearInterval(timer);
    }, [load]);

    const assignment = view?.assignment;
    const stops = assignment?.payload.stops ?? [];
    const pending = useMemo(
        () => stops.filter((stop) => (stop.status ?? "pending") === "pending")
            .sort((a, b) => a.seq - b.seq),
        [stops],
    );
    const closed = useMemo(
        () => stops.filter((stop) => (stop.status ?? "pending") !== "pending"),
        [stops],
    );

    const points: DeliveryPoint[] = pending
        .filter((stop) => stop.coords)
        .map((stop) => ({
            id: `${stop.seq}`,
            name: stop.address,
            address: stop.address,
            demand: stop.demand_kg,
            latitude: stop.coords!.lat,
            longitude: stop.coords!.lon,
        }));

    const depotCoords = assignment?.payload.depot?.coords;

    const geometry = assignment?.payload.remaining_geometry ?? assignment?.payload.geometry;
    const routePositions = geometry
        ? geometry.coordinates.map(([lon, lat]) => [lat, lon] as [number, number])
        : null;

    const routes: MapRoute[] = routePositions
        ? [{
            number: assignment?.payload.route_number ?? 1,
            color: "#2563eb",
            positions: routePositions,
        }]
        : [];

    // Без геометрии от OSRM рисуем прямые между оставшимися точками и складом.
    const fallbackPositions: [number, number][] = depotCoords
        ? [
            [depotCoords.lat, depotCoords.lon],
            ...pending.map((stop) => [stop.coords!.lat, stop.coords!.lon] as [number, number]),
            [depotCoords.lat, depotCoords.lon],
        ]
        : [];

    const visibleRoutes: MapRoute[] = routes.length > 0
        ? routes
        : fallbackPositions.length > 2
            ? [{ number: assignment?.payload.route_number ?? 1, color: "#2563eb", positions: fallbackPositions }]
            : [];

    async function deliver(stop: ApiRouteStop) {
        setBusy(stop.seq);
        setNotice(null);
        try {
            await markStop(token, stop.seq, "delivered");
            await load();
            setNotice(`Точка «${stop.address}» отмечена доставленной`);
        } catch (err) {
            setNotice(err instanceof Error ? err.message : "Не удалось сохранить отметку");
        } finally {
            setBusy(null);
        }
    }

    async function fail(stop: ApiRouteStop) {
        setBusy(stop.seq);
        setNotice(null);
        try {
            await markStop(token, stop.seq, "failed", reason);
            await load();
            setReasonFor(null);
            setNotice(`Точка «${stop.address}»: ${reason.toLowerCase()}`);
        } catch (err) {
            setNotice(err instanceof Error ? err.message : "Не удалось сохранить причину");
        } finally {
            setBusy(null);
        }
    }

    async function problem() {
        const text = window.prompt("Что случилось? Логист увидит это сообщение");
        if (!text) return;
        try {
            await reportProblem(token, "other", text);
            setNotice("Сообщение отправлено логисту");
        } catch (err) {
            setNotice(err instanceof Error ? err.message : "Не удалось отправить сообщение");
        }
    }

    if (error && !view) {
        return (
            <main className="driver-page">
                <div className="driver-error">
                    <h1>Маршрут недоступен</h1>
                    <p>{error}</p>
                    <p>Ссылку выдаёт логист. Если она устарела — попросите новую.</p>
                </div>
            </main>
        );
    }

    if (!view || !assignment) {
        return <main className="driver-page"><p className="driver-loading">Загружаем маршрут…</p></main>;
    }

    const progress = assignment.progress;
    const driver = view.driver;
    const done = progress.percent >= 100;

    return (
        <main className="driver-page">
            <header className="driver-header">
                <div>
                    <h1>Маршрут водителя</h1>
                    <p className="driver-subtitle">
                        {driver.name}
                        {driver.phone ? <> · <a href={`tel:${driver.phone}`}>{driver.phone}</a></> : null}
                    </p>
                </div>
                <span className={`status-badge status-${assignment.status}`}>
                    {assignment.status === "completed" ? "завершён" : assignment.status === "problem" ? "проблема" : "в работе"}
                </span>
            </header>

            <section className="driver-summary">
                <div><strong>{assignment.payload.vehicle_id}</strong><span>машина</span></div>
                <div><strong>{(() => {
                    const meters = assignment.payload.remaining_distance_m ?? assignment.payload.distance_m;
                    return meters ? (meters / 1000).toFixed(1) : "—";
                })()} км</strong><span>пробег</span></div>
                <div><strong>{assignment.payload.end_time || "—"}</strong><span>возврат</span></div>
                <div><strong>{progress.pending}</strong><span>осталось</span></div>
            </section>

            <div className="driver-progress">
                <div className="driver-progress-bar">
                    <div style={{ width: `${progress.percent}%` }} />
                </div>
                <p>
                    Доставлено {progress.delivered} из {progress.total}
                    {progress.failed > 0 ? ` · не доставлено ${progress.failed}` : ""}
                </p>
            </div>

            {notice && <div className="driver-notice">{notice}</div>}

            {done ? (
                <section className="driver-done">
                    <h2>Смена закрыта</h2>
                    <p>Все точки маршрута отмечены. Передайте итог логисту.</p>
                </section>
            ) : (
                <>
                    {depotCoords && (
                        <div className="driver-map">
                            <LogisticsMap
                                points={points}
                                depot={{ latitude: depotCoords.lat, longitude: depotCoords.lon }}
                                routes={visibleRoutes}
                            />
                        </div>
                    )}

                    <section className="driver-stops">
                        <h2>Осталось развезти: {pending.length}</h2>
                        {pending.map((stop) => (
                            <article key={stop.seq} className="driver-stop">
                                <div className="driver-stop-head">
                                    <span className="driver-stop-num">{stop.seq}</span>
                                    <div>
                                        <strong>{stop.address}</strong>
                                        <p>
                                            прибыть {stop.arrive} · выгрузить {stop.leave} · {stop.demand_kg} кг
                                            {stop.orders.length > 1 ? ` · ${stop.orders.length} заявки` : ""}
                                        </p>
                                    </div>
                                </div>
                                <div className="driver-stop-actions">
                                    <button
                                        className="driver-btn ok"
                                        disabled={busy === stop.seq}
                                        onClick={() => void deliver(stop)}
                                    >
                                        Доставлено
                                    </button>
                                    <button
                                        className="driver-btn bad"
                                        disabled={busy === stop.seq}
                                        onClick={() => setReasonFor(reasonFor === stop.seq ? null : stop.seq)}
                                    >
                                        Не доставлено
                                    </button>
                                </div>
                                {reasonFor === stop.seq && (
                                    <div className="driver-reason">
                                        <select value={reason} onChange={(event) => setReason(event.target.value)}>
                                            {REASONS.map((item) => <option key={item} value={item}>{item}</option>)}
                                        </select>
                                        <button
                                            className="driver-btn bad"
                                            disabled={busy === stop.seq}
                                            onClick={() => void fail(stop)}
                                        >
                                            Подтвердить
                                        </button>
                                    </div>
                                )}
                            </article>
                        ))}
                    </section>
                </>
            )}

            <section className="driver-help">
                <button className="driver-btn" onClick={() => void problem()}>
                    Сообщить логисту о проблеме
                </button>
                <p>Не успеваешь? Сообщи — логист пересчитает остаток маршрута.</p>
            </section>

            {closed.length > 0 && (
                <section className="driver-closed">
                    <h2>Закрытые точки</h2>
                    <ul>
                        {closed.map((stop) => (
                            <li key={stop.seq} className={stop.status === "failed" ? "failed" : "delivered"}>
                                <span>{stop.address}</span>
                                <em>
                                    {stop.status === "failed"
                                        ? `не доставлено${stop.reason ? `: ${stop.reason}` : ""}`
                                        : "доставлено"}
                                </em>
                            </li>
                        ))}
                    </ul>
                </section>
            )}

            {view.events.length > 0 && (
                <section className="driver-log">
                    <h2>Журнал</h2>
                    <ul>
                        {view.events.slice(0, 8).map((event) => (
                            <li key={event.id}>
                                <span>{event.created_at.replace("T", " ").slice(0, 16)}</span>
                                <em>{event.type}</em>
                                {event.text ? <p>{event.text}</p> : null}
                            </li>
                        ))}
                    </ul>
                </section>
            )}
        </main>
    );
}

export default DriverPage;

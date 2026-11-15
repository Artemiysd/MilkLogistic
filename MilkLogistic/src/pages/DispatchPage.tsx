import { useCallback, useEffect, useState } from "react";
import {
    cancelAssignment,
    cleanupDrivers,
    createDriver,
    deleteDriver,
    fetchDispatch,
    replanAssignment,
    resetShift,
    seedDrivers,
} from "../api/client";
import type { Assignment, DispatchOverview, Driver } from "../types/logistics";
import "./DispatchPage.css";

function linkOf(driver: Driver): string {
    return `${window.location.origin}${driver.link}`;
}

function DispatchPage() {
    const [overview, setOverview] = useState<DispatchOverview | null>(null);
    const [error, setError] = useState<string | null>(null);
    const [notice, setNotice] = useState<string | null>(null);
    const [busy, setBusy] = useState<string | null>(null);
    const [form, setForm] = useState({ name: "", phone: "", vehicle_id: "" });

    const load = useCallback(async () => {
        try {
            setOverview(await fetchDispatch());
            setError(null);
        } catch (err) {
            setError(err instanceof Error ? err.message : "Не удалось загрузить данные");
        }
    }, []);

    useEffect(() => {
        void load();
        const timer = setInterval(() => void load(), 10_000);
        return () => clearInterval(timer);
    }, [load]);

    async function addDriver(event: React.FormEvent) {
        event.preventDefault();
        if (!form.name.trim()) return;
        setBusy("create");
        try {
            await createDriver({
                name: form.name.trim(),
                phone: form.phone.trim() || null,
                vehicle_id: form.vehicle_id.trim() || null,
            });
            setForm({ name: "", phone: "", vehicle_id: "" });
            setNotice("Водитель добавлен");
            await load();
        } catch (err) {
            setNotice(err instanceof Error ? err.message : "Не удалось добавить водителя");
        } finally {
            setBusy(null);
        }
    }

    async function seed() {
        setBusy("seed");
        try {
            const result = await seedDrivers();
            setNotice(`Создано водителей: ${result.count}`);
            await load();
        } catch (err) {
            setNotice(err instanceof Error ? err.message : "Не удалось создать водителей");
        } finally {
            setBusy(null);
        }
    }

    async function cleanup() {
        if (!window.confirm("Удалить водителей, у которых нет маршрутов?")) return;
        setBusy("cleanup");
        try {
            const result = await cleanupDrivers();
            setNotice(`Удалено водителей: ${result.deleted}, осталось: ${result.left}`);
            await load();
        } catch (err) {
            setNotice(err instanceof Error ? err.message : "Не удалось очистить базу");
        } finally {
            setBusy(null);
        }
    }

    async function reset() {
        if (!window.confirm("Сбросить смену? Маршруты будут отменены, водители удалены.")) return;
        setBusy("reset");
        try {
            const result = await resetShift();
            setNotice(
                `Смена сброшена: отменено маршрутов ${result.cancelled}, ` +
                `удалено водителей ${result.deleted_drivers}`,
            );
            await load();
        } catch (err) {
            setNotice(err instanceof Error ? err.message : "Не удалось сбросить смену");
        } finally {
            setBusy(null);
        }
    }

    async function removeDriver(driver: Driver) {
        if (!window.confirm(`Удалить водителя ${driver.name}?`)) return;
        setBusy(driver.id);
        try {
            await deleteDriver(driver.id);
            await load();
        } catch (err) {
            setNotice(err instanceof Error ? err.message : "Не удалось удалить водителя");
        } finally {
            setBusy(null);
        }
    }

    async function copyLink(driver: Driver) {
        try {
            await navigator.clipboard.writeText(linkOf(driver));
            setNotice(`Ссылка для ${driver.name} скопирована`);
        } catch {
            setNotice(linkOf(driver));
        }
    }

    async function replan(assignment: Assignment) {
        setBusy(assignment.id);
        try {
            const result = await replanAssignment(assignment.id);
            setNotice(
                `Маршрут ${assignment.route_number} пересчитан: ` +
                `${result.replanned_stops} точек, не влезло ${result.left_pending}`,
            );
            await load();
        } catch (err) {
            setNotice(err instanceof Error ? err.message : "Не удалось пересчитать маршрут");
        } finally {
            setBusy(null);
        }
    }

    async function cancel(assignment: Assignment) {
        if (!window.confirm(`Отменить маршрут ${assignment.route_number}?`)) return;
        setBusy(assignment.id);
        try {
            await cancelAssignment(assignment.id);
            await load();
        } catch (err) {
            setNotice(err instanceof Error ? err.message : "Не удалось отменить маршрут");
        } finally {
            setBusy(null);
        }
    }

    const stats = overview?.stats;
    const current = overview?.current_run ?? null;
    const drivers = overview?.drivers ?? [];
    const assignments = overview?.assignments ?? [];
    const demo = assignments[0];

    return (
        <main className="dispatch-page">
            <header className="dispatch-header">
                <div>
                    <h1>Кабинет логиста</h1>
                    <p>
                        {current
                            ? `Смена: ${current.dataset_name || "штатные данные"} · ` +
                              `${current.created_at?.replace("T", " ").slice(0, 16) || ""} · ` +
                              `${current.routes} маршрутов`
                            : "Маршруты ещё не выданы"}
                    </p>
                </div>
                <div className="dispatch-header-actions">
                    <button
                        className="dispatch-danger"
                        disabled={busy === "reset"}
                        onClick={() => void reset()}
                    >
                        Начать смену заново
                    </button>
                    <a className="dispatch-link" href="/">← К планированию</a>
                </div>
            </header>

            {error && <div className="dispatch-alert error">{error}</div>}
            {notice && <div className="dispatch-alert">{notice}</div>}
            {!overview && !error && <div className="dispatch-alert">Загружаем смену…</div>}

            {overview && (
                <>
                    <section className="dispatch-stats">
                        <div><strong>{stats?.drivers ?? 0}</strong><span>водителей в смене</span></div>
                        <div><strong>{stats?.assignments ?? 0}</strong><span>маршрутов выдано</span></div>
                        <div><strong>{stats?.delivered ?? 0}</strong><span>точек доставлено</span></div>
                        <div><strong>{stats?.stops ?? 0}</strong><span>точек всего</span></div>
                    </section>

                    <section className="dispatch-guide">
                        <h2>Как водитель смотрит маршрут</h2>
                        <ol>
                            <li>Нажмите «Открыть маршрут» у нужного водителя.</li>
                            <li>Скопируйте ссылку и отправьте её водителю — она работает на телефоне без пароля.</li>
                            <li>Водитель отмечает «Доставлено» — точка исчезает с карты, здесь растёт прогресс.</li>
                        </ol>
                        <div className="dispatch-guide-actions">
                            <button
                                className="dispatch-primary"
                                disabled={!demo?.driver}
                                onClick={() => demo?.driver && window.open(demo.driver.link, "_blank")}
                            >
                                Посмотреть как водитель
                            </button>
                            {demo?.driver && (
                                <button className="dispatch-ghost" onClick={() => void copyLink(demo.driver!)}>
                                    Скопировать первую ссылку
                                </button>
                            )}
                            <a className="dispatch-ghost" href="/" style={{ textDecoration: "none" }}>
                                Нужен другой маршрут? Постройте план
                            </a>
                        </div>
                    </section>

                    <section className="dispatch-panel">
                        <h2>Водители смены ({drivers.length})</h2>
                        <form className="dispatch-form" onSubmit={(event) => void addDriver(event)}>
                            <input
                                placeholder="Имя"
                                value={form.name}
                                onChange={(event) => setForm({ ...form, name: event.target.value })}
                            />
                            <input
                                placeholder="Телефон"
                                value={form.phone}
                                onChange={(event) => setForm({ ...form, phone: event.target.value })}
                            />
                            <input
                                placeholder="Машина (необязательно)"
                                value={form.vehicle_id}
                                onChange={(event) => setForm({ ...form, vehicle_id: event.target.value })}
                            />
                            <button type="submit" disabled={busy === "create" || !form.name.trim()}>
                                Добавить
                            </button>
                            <button type="button" className="ghost" disabled={busy === "seed"} onClick={() => void seed()}>
                                Создать по парку
                            </button>
                            <button type="button" className="ghost" disabled={busy === "cleanup"} onClick={() => void cleanup()}>
                                Убрать свободных
                            </button>
                        </form>

                        {drivers.length === 0 ? (
                            <p className="dispatch-empty">
                                В смене пока нет водителей. Нажмите «Создать по парку» или отправьте
                                маршруты с главной — они создадутся сами.
                            </p>
                        ) : (
                            <table className="dispatch-table">
                                <thead>
                                    <tr><th>Водитель</th><th>Телефон</th><th>Машина</th><th>Ссылка для водителя</th><th /></tr>
                                </thead>
                                <tbody>
                                    {drivers.map((driver) => (
                                        <tr key={driver.id}>
                                            <td>{driver.name}</td>
                                            <td>{driver.phone || "—"}</td>
                                            <td>{driver.vehicle_id || "—"}</td>
                                            <td className="dispatch-link-cell">
                                                <a href={driver.link} target="_blank" rel="noreferrer">открыть</a>
                                                <button onClick={() => void copyLink(driver)}>копировать</button>
                                            </td>
                                            <td>
                                                <button
                                                    className="danger"
                                                    disabled={busy === driver.id}
                                                    onClick={() => void removeDriver(driver)}
                                                >
                                                    удалить
                                                </button>
                                            </td>
                                        </tr>
                                    ))}
                                </tbody>
                            </table>
                        )}
                    </section>

                    <section className="dispatch-panel">
                        <h2>Маршруты смены</h2>
                        {assignments.length === 0 ? (
                            <p className="dispatch-empty">
                                Маршруты не выданы. На главной постройте план и нажмите
                                «Отправить N рейсов ≥ 90%».
                            </p>
                        ) : (
                            <div className="dispatch-assignments">
                                {assignments.map((assignment) => {
                                    const driver = assignment.driver;
                                    return (
                                        <article key={assignment.id} className="dispatch-card">
                                            <div className="dispatch-card-head">
                                                <div>
                                                    <strong>
                                                        {driver?.name || "Водитель"} · маршрут {assignment.route_number}
                                                    </strong>
                                                    <p>
                                                        {assignment.vehicle_id || "машина не указана"} ·{" "}
                                                        {assignment.payload.stops.length} точек ·{" "}
                                                        {assignment.payload.distance_m
                                                            ? `${(assignment.payload.distance_m / 1000).toFixed(1)} км`
                                                            : "—"}
                                                        {assignment.payload.run_created_at
                                                            ? ` · выдан ${assignment.payload.run_created_at.replace("T", " ")}`
                                                            : ""}
                                                    </p>
                                                </div>
                                                <span className={`dispatch-status dispatch-status-${assignment.status}`}>
                                                    {assignment.status}
                                                </span>
                                            </div>
                                            <div className="dispatch-progress">
                                                <div style={{ width: `${assignment.progress.percent}%` }} />
                                            </div>
                                            <p className="dispatch-progress-text">
                                                Доставлено {assignment.progress.delivered} из {assignment.progress.total}
                                                {assignment.progress.failed > 0
                                                    ? ` · проблемных ${assignment.progress.failed}`
                                                    : ""}
                                            </p>
                                            <div className="dispatch-card-actions">
                                                {driver && (
                                                    <a className="dispatch-open" href={driver.link} target="_blank" rel="noreferrer">
                                                        Открыть маршрут
                                                    </a>
                                                )}
                                                <button
                                                    disabled={busy === assignment.id || assignment.status === "completed"}
                                                    onClick={() => void replan(assignment)}
                                                >
                                                    Пересчитать остаток
                                                </button>
                                                <button
                                                    className="danger"
                                                    disabled={busy === assignment.id}
                                                    onClick={() => void cancel(assignment)}
                                                >
                                                    Отменить
                                                </button>
                                            </div>
                                        </article>
                                    );
                                })}
                            </div>
                        )}
                    </section>
                </>
            )}
        </main>
    );
}

export default DispatchPage;

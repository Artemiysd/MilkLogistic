import type {
    ApiData,
    Assignment,
    DispatchOverview,
    Driver,
    DriverView,
    ImportResult,
    OptimizeRequest,
    OptimizeResult,
} from "../types/logistics.ts";

/**
 * Базовый адрес API.
 * В Docker фронт и бэкенд на одном origin: nginx проксирует /api -> api:8000.
 * Локально `npm run dev` работает через proxy из vite.config.ts.
 */
const BASE = (import.meta.env.VITE_API_BASE_URL ?? "").replace(/\/$/, "");

export class ApiError extends Error {
    status: number;

    constructor(message: string, status: number) {
        super(message);
        this.name = "ApiError";
        this.status = status;
    }
}

async function request<T>(path: string, init: RequestInit = {}, timeoutMs = 30_000): Promise<T> {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeoutMs);
    const isForm = typeof FormData !== "undefined" && init.body instanceof FormData;

    try {
        const response = await fetch(`${BASE}${path}`, {
            ...init,
            signal: controller.signal,
            headers: {
                Accept: "application/json",
                ...(init.body && !isForm ? { "Content-Type": "application/json" } : {}),
                ...init.headers,
            },
        });
        const text = await response.text();
        const payload = text ? JSON.parse(text) : null;

        if (!response.ok) {
            const detail = payload && (payload.detail ?? payload.message);
            throw new ApiError(
                typeof detail === "string" ? detail : `HTTP ${response.status}`,
                response.status,
            );
        }
        return payload as T;
    } catch (error) {
        if (error instanceof ApiError) throw error;
        if (error instanceof DOMException && error.name === "AbortError") {
            throw new ApiError(`Сервер не ответил за ${Math.round(timeoutMs / 1000)} с`, 408);
        }
        throw new ApiError(error instanceof Error ? error.message : "Сеть недоступна", 0);
    } finally {
        clearTimeout(timer);
    }
}

/** Склад, машины, точки и ограничения */
export function fetchData(): Promise<ApiData> {
    return request<ApiData>("/api/data", {}, 15_000);
}

/** Расчёт плана: планировщик + матрица + геометрия маршрутов */
export function optimize(body: OptimizeRequest): Promise<OptimizeResult> {
    return request<OptimizeResult>(
        "/api/optimize",
        { method: "POST", body: JSON.stringify(body) },
        180_000,
    );
}

/** Импорт выгрузки 1С файлом: набор сохраняется и становится активным */
export async function importDatasetFile(file: File, activate = true): Promise<ImportResult> {
    const form = new FormData();
    form.append("files", file);
    const response = await request<ImportResult & {
        results?: ImportResult[];
        errors?: { file: string; error: string }[];
    }>(
        `/api/import/file?activate=${activate ? "true" : "false"}&build_matrix=true`,
        { method: "POST", body: form },
        180_000,
    );
    if (response.dataset) return response;
    const first = response.results?.[0];
    if (!first) {
        throw new ApiError(response.errors?.[0]?.error ?? "Файл не импортирован", 422);
    }
    return first;
}

/** Вторая волна: остаток прогона + (опционально) свежая выгрузка 1С */
export function runExtraWave(
    runId: string,
    body: { dataset_id?: string | null; start_time?: string; max_vehicles?: number | null } = {},
): Promise<OptimizeResult> {
    return request<OptimizeResult>(
        `/api/runs/${runId}/extra`,
        { method: "POST", body: JSON.stringify(body) },
        180_000,
    );
}

/* ------------------------------------------------------------------ водители */

export function fetchDrivers(): Promise<{ count: number; drivers: Driver[] }> {
    return request<{ count: number; drivers: Driver[] }>("/api/drivers");
}

export function createDriver(body: {
    name: string;
    phone?: string | null;
    vehicle_id?: string | null;
}): Promise<Driver> {
    return request<Driver>("/api/drivers", { method: "POST", body: JSON.stringify(body) });
}

export function deleteDriver(driverId: string): Promise<{ status: string }> {
    return request<{ status: string }>(`/api/drivers/${driverId}`, { method: "DELETE" });
}

export function seedDrivers(): Promise<{ status: string; count: number }> {
    return request<{ status: string; count: number }>("/api/drivers/seed", { method: "POST" });
}

/** Удаляет водителей без выданных маршрутов (чистка базы после смены парка) */
export function cleanupDrivers(): Promise<{ status: string; deleted: number; left: number }> {
    return request<{ status: string; deleted: number; left: number }>(
        "/api/drivers/unassigned",
        { method: "DELETE" },
    );
}

/** Раздать рейсы прогона водителям и получить их персональные ссылки */
export function dispatchRun(
    runId: string,
    options: { allow_underloaded?: boolean; exclude_routes?: number[] } = {},
): Promise<{
    status: string;
    count: number;
    assignments: Assignment[];
    skipped?: { route_number: number; vehicle_id: string; load_ratio: number; reason: string }[];
}> {
    return request(
        `/api/runs/${runId}/dispatch`,
        {
            method: "POST",
            body: JSON.stringify({
                allow_underloaded: Boolean(options.allow_underloaded),
                exclude_routes: options.exclude_routes ?? null,
            }),
        },
        60_000,
    );
}

/** Пересчитать прогон с жёстким правилом «не выпускать машину ниже 90%» */
export function rebalanceRun(runId: string): Promise<OptimizeResult> {
    return request<OptimizeResult>(
        `/api/runs/${runId}/rebalance`,
        { method: "POST", body: "{}" },
        180_000,
    );
}

export function fetchDispatch(): Promise<DispatchOverview> {
    return request<DispatchOverview>("/api/dispatch", {}, 15_000);
}

/** Начать смену с чистого листа: отменить маршруты и удалить водителей */
export function resetShift(): Promise<{ status: string; cancelled: number; deleted_drivers: number }> {
    return request("/api/dispatch/reset", { method: "POST", body: "{}" }, 30_000);
}

export function fetchDriverView(token: string): Promise<DriverView> {
    return request<DriverView>(`/api/driver/${token}`, {}, 15_000);
}

export function markStop(
    token: string,
    seq: number,
    status: "delivered" | "failed",
    reason?: string,
): Promise<{ status: string; assignment: Assignment }> {
    return request(`/api/driver/${token}/stops/${seq}`, {
        method: "POST",
        body: JSON.stringify({ status, reason }),
    });
}

export function reportProblem(
    token: string,
    type: string,
    text: string,
): Promise<{ status: string }> {
    return request(`/api/driver/${token}/problem`, {
        method: "POST",
        body: JSON.stringify({ type, text }),
    });
}

export function replanAssignment(assignmentId: string): Promise<{
    status: string;
    assignment: Assignment;
    replanned_stops: number;
    left_pending: number;
    warnings: string[];
}> {
    return request(`/api/assignments/${assignmentId}/replan`, { method: "POST", body: "{}" }, 120_000);
}

export function cancelAssignment(assignmentId: string): Promise<{ status: string }> {
    return request(`/api/assignments/${assignmentId}/cancel`, { method: "POST", body: "{}" });
}


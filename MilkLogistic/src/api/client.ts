import type { ApiData, OptimizeRequest, OptimizeResult } from "../types/logistics.ts";

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

    try {
        const response = await fetch(`${BASE}${path}`, {
            ...init,
            signal: controller.signal,
            headers: {
                Accept: "application/json",
                ...(init.body ? { "Content-Type": "application/json" } : {}),
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

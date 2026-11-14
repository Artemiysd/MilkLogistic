export const ROUTE_COLORS = [
    "#2563eb", "#dc2626", "#059669", "#d97706", "#7c3aed", "#0891b2",
    "#be123c", "#4d7c0f", "#9333ea", "#b45309", "#0369a1", "#15803d",
    "#a21caf", "#c2410c", "#0f766e", "#4338ca",
];

export function routeColor(index: number): string {
    return ROUTE_COLORS[index % ROUTE_COLORS.length];
}

export function formatKm(meters?: number | null): string {
    if (meters == null || Number.isNaN(meters)) return "—";
    return `${(meters / 1000).toFixed(1)} км`;
}

export function formatMinutes(minutes?: number | null): string {
    if (minutes == null || Number.isNaN(minutes)) return "—";
    const total = Math.round(minutes);
    return `${Math.floor(total / 60)} ч ${String(total % 60).padStart(2, "0")} мин`;
}

export function formatPercent(ratio?: number | null): string {
    if (ratio == null || Number.isNaN(ratio)) return "—";
    return `${(ratio * 100).toFixed(1)} %`;
}

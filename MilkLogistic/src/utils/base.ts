/**
 * Базовый путь приложения.
 *
 * В Docker и Electron приложение живёт в корне ("/"), на GitHub Pages —
 * в подпапке "/MilkLogistic/". Все внутренние ссылки строим через appPath(),
 * а входящий pathname разбираем через stripBase().
 */
export const APP_BASE = (import.meta.env.BASE_URL || "/").replace(/\/+$/, "") + "/";

export function appPath(path: string): string {
    const clean = path.replace(/^\/+/, "");
    const joined = `${APP_BASE}${clean}`;
    return joined.replace(/\/{2,}/g, "/");
}

export function stripBase(pathname: string): string {
    const base = APP_BASE.replace(/\/+$/, "");
    if (base && pathname.startsWith(base)) {
        const rest = pathname.slice(base.length);
        return rest.startsWith("/") ? rest : `/${rest}`;
    }
    return pathname;
}

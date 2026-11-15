/**
 * Electron-обёртка MilkLogistic.
 *
 * Приложение раздаёт собранный фронтенд из dist/ через локальный HTTP-сервер
 * (нужно для нормальной работы путей /dispatch и /driver/<token>) и открывает
 * его в окне. Расчёты выполняет бэкенд: по умолчанию http://localhost:8080
 * (поднятый docker-стек). Адрес можно переопределить:
 *   - переменной окружения MILK_API_BASE;
 *   - файлом milk-config.json рядом с приложением или в папке профиля:
 *     { "apiBase": "http://localhost:8080" }
 */

const { app, BrowserWindow, dialog, shell } = require("electron");
const fs = require("node:fs");
const http = require("node:http");
const path = require("node:path");

const DEFAULT_API = "http://localhost:8080";

const MIME = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".svg": "image/svg+xml",
    ".ico": "image/x-icon",
    ".woff": "font/woff",
    ".woff2": "font/woff2",
    ".map": "application/json",
    ".txt": "text/plain; charset=utf-8",
};

function readConfigFile() {
    const candidates = [
        path.join(process.resourcesPath || "", "milk-config.json"),
        path.join(app.getPath("userData"), "milk-config.json"),
        path.join(process.cwd(), "milk-config.json"),
    ];
    for (const candidate of candidates) {
        try {
            if (!candidate || !fs.existsSync(candidate)) continue;
            const parsed = JSON.parse(fs.readFileSync(candidate, "utf8"));
            if (parsed && typeof parsed.apiBase === "string" && parsed.apiBase.trim()) {
                return parsed.apiBase.trim().replace(/\/+$/, "");
            }
        } catch {
            /* битый конфиг игнорируем */
        }
    }
    return "";
}

function apiBase() {
    return (process.env.MILK_API_BASE || readConfigFile() || DEFAULT_API).replace(/\/+$/, "");
}

function serveStatic(root) {
    const server = http.createServer((request, response) => {
        try {
            const url = new URL(request.url || "/", "http://127.0.0.1");
            const relative = decodeURIComponent(url.pathname);
            let filePath = path.join(root, relative);
            if (!filePath.startsWith(root)) {
                response.writeHead(403).end("Forbidden");
                return;
            }
            if (!fs.existsSync(filePath) || fs.statSync(filePath).isDirectory()) {
                filePath = path.join(root, "index.html");
            }
            response.writeHead(200, {
                "Content-Type": MIME[path.extname(filePath).toLowerCase()] || "application/octet-stream",
                "Cache-Control": "no-cache",
            });
            fs.createReadStream(filePath).pipe(response);
        } catch (error) {
            response.writeHead(500).end(String(error));
        }
    });
    return new Promise((resolve) => server.listen(0, "127.0.0.1", () => resolve(server)));
}

async function createWindow() {
    const root = path.join(__dirname, "..", "dist");
    if (!fs.existsSync(path.join(root, "index.html"))) {
        dialog.showErrorBox(
            "Не найден фронтенд",
            "Сначала соберите приложение: npm run build",
        );
        app.quit();
        return;
    }

    const server = await serveStatic(root);
    const port = server.address().port;

    const window = new BrowserWindow({
        width: 1440,
        height: 900,
        minWidth: 1024,
        minHeight: 700,
        backgroundColor: "#f5f7f6",
        title: "MilkLogistic",
        autoHideMenuBar: true,
        webPreferences: {
            preload: path.join(__dirname, "preload.cjs"),
            contextIsolation: true,
            nodeIntegration: false,
            additionalArguments: [`--milk-api-base=${apiBase()}`],
        },
    });

    window.webContents.setWindowOpenHandler(({ url }) => {
        shell.openExternal(url);
        return { action: "deny" };
    });

    await window.loadURL(`http://127.0.0.1:${port}/`);
}

app.whenReady().then(() => {
    createWindow();
    app.on("activate", () => {
        if (BrowserWindow.getAllWindows().length === 0) createWindow();
    });
});

app.on("window-all-closed", () => {
    if (process.platform !== "darwin") app.quit();
});

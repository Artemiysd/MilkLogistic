# MilkLogistic — планирование доставки «Молочного Мира»

Оптимизация развоза 214 заявок по Гродно: реальные дорожные расстояния, распределение
по 16 машинам, маршруты на карте и сравнение «как в накладной / после оптимизации».

![статус](https://img.shields.io/badge/докер-docker%20compose-blue)
![данные](https://img.shields.io/badge/точек-214-green)
![матрица](https://img.shields.io/badge/матрица-215%C3%97215-green)

## Что умеет

- загружает заявки, парк и ограничения из JSON (`data/logistics_data.json`);
- считает дорожные расстояния и время по OSRM (матрица 215×215, `data/matrix_cache.json`);
- распределяет адреса по машинам с учётом грузоподъёмности, минимальной загрузки 90%,
  ограничения улиц (Советская / Мостовая / Замковая — только 1,5 т), окон доставки и смены;
- строит порядок объезда, расписание с прибытием/выездом и километражем;
- рисует маршруты на карте, считает экономию и показывает отчёт;
- умеет работать частично: если машин не хватает, честно показывает «не развезено» и вес.

Текущий результат на реальных данных: **658.3 км → 619.8 км (−5.8%)**, 16 машин,
63 630 кг, 0 нарушений, расчёт ~0.6 с.

## Архитектура

```
data/logistics_data.json ──┐
                           ├─> backend (FastAPI) ──> logistics/ (планировщик) ──> JSON плана
data/matrix_cache.json  ───┘            │
                                        ├─> services/routing (FastAPI) ──> OSRM ──> геометрия
                                        └─> PostgreSQL (под историю/1С)

frontend (React + Leaflet) ──> nginx ──> /api ──> backend
```

| Компонент | Что делает | Порт |
|---|---|---|
| `frontend` (`src/`) | карта, маршруты, статистика | 8080 |
| `backend/` | API, отчёты, связка планировщика и матрицы | 8000 |
| `logistics/` | планировщик: распределение, порядок, расписание | — |
| `services/routing/` | координаты → OSRM → км/минуты/геометрия | 8100 |
| `osrm/` | движок маршрутизации по карте Гродно | 5000 |
| `db` | PostgreSQL (задел под 1С и историю) | 5432 |

## Быстрый старт

Нужен **Docker Desktop** (Windows) или Docker + Compose (Linux/macOS).

```powershell
git clone https://github.com/Artemiysd/MilkLogistic.git
cd MilkLogistic
Copy-Item .env.example .env
docker compose -f docker-compose.yml -f docker-compose.backend.yml up -d --build
```

Открыть **http://localhost:8080** и нажать «Оптимизировать маршруты».

На Windows проще: двойной щелчок по `start.bat` (сам поднимет Docker, бэкенд и OSRM).

### Без Docker (для разработки)

```bash
# бэкенд
cd backend
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000

# фронтенд (в другом терминале)
npm install
npm run dev          # http://localhost:5173, /api проксируется на :8000
```

Свой OSRM не обязателен для разработки: в `.env.example` по умолчанию публичный
`https://router.project-osrm.org`. Для офлайн-демо — `infra/osrm/prepare.ps1`.

## Структура

```
backend/app/          FastAPI: main.py (эндпоинты), planner_adapter.py (матрица + маппинг),
                      report.py (HTML-отчёт с картой)
logistics/            планировщик Даника: planner, routing, data_loader, models, export
services/routing/     сервис маршрутизации: OSRM-клиент, кэш, нарезка на блоки
src/                  React + TypeScript + Leaflet
data/                 данные задачи, ручные правки координат, матрица расстояний
infra/                nginx, mock-api, сборка матрицы, подготовка OSRM, preflight
schema.json           контракт данных (1С ↔ бэкенд ↔ фронтенд)
```

## API

| Метод | Путь | Назначение |
|---|---|---|
| GET | `/api/health` | состояние, точки, машины, матрица |
| GET | `/api/data` | склад, машины, точки, ограничения |
| POST | `/api/optimize` | расчёт плана (`{"drivers": 16}`) |
| GET | `/api/runs` | последние прогоны |
| GET | `/api/runs/{id}` | полный JSON прогона |
| GET | `/api/runs/{id}/report` | HTML-отчёт: карта, маршруты, расшифровка |
| GET | `/api/runs/{id}/geojson` | линии маршрутов для ГИС |

Сервис маршрутизации: `POST /api/calculate-route`, `POST /api/calculate-table` —
см. [`services/routing/README.md`](services/routing/README.md).

Swagger: http://localhost:8000/docs

## Скрипты (Windows)

| Файл | Что делает |
|---|---|
| `start.bat` / `stop.bat` | поднять / остановить стек |
| `check.bat` | 12 проверок окружения |
| `verify-osrm.bat` | сверка маршрута с матрицей на всех точках |
| `plan.bat` | открыть страницу проверки расчётов |

Linux/macOS: те же команды, но через `docker compose` напрямую.

## Ограничения модели

- разгрузка считается **30 минут на физический адрес** (192 адреса из 214 заявок);
- смена 08:00–17:00, обед 13:00–14:00, работа ≤ 8 ч;
- машина делает один рейс, водитель может взять несколько машин за смену, если успевает;
- точка на Советской / Мостовой / Замковой обслуживается только машиной 1,5 т;
- расстояние между одинаковыми координатами = 0, но разгрузка на каждую организацию своя.

## Документация

- [`README-INFRA.md`](README-INFRA.md) — Docker, сервисы, матрица, проверки, демо без интернета
- [`README-ALGORITHM.md`](README-ALGORITHM.md) — правила планировщика и его CLI
- [`schema.json`](schema.json) — контракт входных данных

## Ветки

`main` — готовая сборка (фронтенд + бэкенд + инфраструктура).
`frontend`, `algorithm` — история разработки отдельных частей.

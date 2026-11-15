# Как подключиться к разработке

Инструкция для нового человека в проекте: от чистого компьютера до первого Pull Request.

## 1. Что поставить

| Что | Зачем | Проверка |
|---|---|---|
| **Docker Desktop** | весь стек (OSRM, Postgres, API, фронт) | `docker --version` |
| **Git** | ветки, PR | `git --version` |
| Node.js 22+ *(не обязательно)* | живая разработка фронта через `npm run dev` | `node -v` |
| Python 3.12+ *(не обязательно)* | тесты планировщика и routing локально | `python --version` |

Windows: после установки Docker Desktop включи WSL2, затем **перезапусти терминал** —
иначе будет ошибка `docker-credential-desktop not found` (docker не попал в PATH).

## 2. Первый запуск

**macOS / Linux:**

```bash
git clone https://github.com/Artemiysd/MilkLogistic.git
cd MilkLogistic
git checkout develop
cp .env.example .env
./start.sh
```

`start.sh` сам поднимет бэкенд, routing и базу (и свой OSRM, если граф уже собран),
дождётся ответа API и откроет браузер. Остановить — `./stop.sh`.

Вручную, без скрипта:

```bash
docker compose -f docker-compose.yml -f docker-compose.backend.yml up -d --build
```

**Windows (PowerShell):**

```powershell
git clone https://github.com/Artemiysd/MilkLogistic.git
cd MilkLogistic
git checkout develop
Copy-Item .env.example .env
docker compose -f docker-compose.yml -f docker-compose.backend.yml up -d --build
```

Или двойной щелчок `start.bat`.

> **Apple Silicon (M1–M4):** образ OSRM собран только под amd64, в compose уже стоит
> `platform: linux/amd64`. Нужно, чтобы в Docker Desktop было включено
> Settings → General → «Use Rosetta for x86_64/amd64 emulation».
> Если видишь `no matching manifest for linux/arm64` — включи Rosetta и перезапусти Docker.

Важно: **всегда** запускать с `-f docker-compose.backend.yml`. Без него `api`
поднимется как заглушка с read-only `/data`, и прогоны не будут сохраняться.

## 3. Проверить, что всё живо

```powershell
.\check.bat                      # 12 проверок: docker, порты, данные, API, OSRM
curl.exe http://localhost:8000/api/health
```

| Адрес | Что |
|---|---|
| http://localhost:8080 | интерфейс (по умолчанию ветка `develop`) |
| http://localhost:8000/api/health | API: точки, машины, матрица |
| http://localhost:8000/docs | Swagger |
| http://localhost:8100/api/health | сервис маршрутизации + OSRM |

Ожидаемо: `points=214`, `vehicles=16`, `matrix.size=215`.

## 4. OSRM: публичный или свой

На чистом клоне графа OSRM нет (он в `.gitignore`, ~370 МБ). По умолчанию `.env.example`
смотрит на публичный `https://router.project-osrm.org` — нужен интернет, но ничего
скачивать не надо.

Свой OSRM (офлайн-демо, быстрее в 10 раз):

```powershell
.\infra\osrm\prepare.ps1          # скачает Беларусь (~335 МБ) и нарежет Гродно, 2-3 мин
# в .env: OSRM_BASE_URL=http://osrm:5000
docker compose -f docker-compose.yml -f docker-compose.backend.yml --profile geo up -d
```

## 5. Где что менять

| Что | Где | Как проверить |
|---|---|---|
| Интерфейс | `src/` (их вёрстка + API) | `npm install`, `npm run dev` → http://localhost:5173, правки видны сразу |
| API | `backend/app/` | `docker compose -f docker-compose.yml -f docker-compose.backend.yml up -d --build api` |
| Планировщик | `logistics/` | `python -m unittest discover -s tests -v` |
| Сервис маршрутизации | `services/routing/` | `cd services/routing && python -m unittest discover -s tests -t . -v` |
| Данные | `data/logistics_data.json` | после правок — пересобрать матрицу (ниже) |

Сборка фронта без Docker:

```powershell
npm install
npm run dev        # /api проксируется на localhost:8000, см. vite.config.ts
npm run build      # проверка типов и сборки перед пушем
```

### После изменения точек или координат

Матрица расстояний устареет, её нужно пересобрать и закоммитить:

```powershell
docker compose --profile tools run --rm matrix --base-url http://osrm:5000
# или с публичным OSRM: без --base-url
py infra/matrix/report.py      # проверить цифры в готовой матрице
```

## 6. Правила веток

```
main      — итоговая версия, пушим только через PR от develop
develop   — рабочая ветка, сюда идут все фичи
frontend  — история фронта
algorithm — история планировщика
infra-routing — инфра + API + подсчёт расстояний, фронт 1:1
```

Рабочий цикл:

```powershell
git checkout develop
git pull
git checkout -b feature/название-фичи
# ... правки ...
git add -A
git commit -m "что сделал"
git push -u origin feature/название-фичи
```

Дальше — Pull Request в `develop` на GitHub.

## 7. Что не коммитить

Уже в `.gitignore`, но на всякий случай:

- `.env` — личные настройки;
- `infra/osrm/data/` — граф OSRM (~370 МБ);
- `data/runs/`, `data/exports/` — результаты прогонов;
- `node_modules/`, `__pycache__/`, `*.tar` (архивы образов).

Проверить перед коммитом: `git status --short` не должен показывать эти пути.

## 8. Полезные команды

| Windows | macOS / Linux | Что делает |
|---|---|---|
| `start.bat` | `./start.sh` | поднять стек |
| `stop.bat` | `./stop.sh` | остановить |
| `check.bat` | `./check.sh` | проверки окружения |
| `verify-osrm.bat` | `./verify-osrm.sh` | сверка маршрута с матрицей |
| `plan.bat` | открыть http://localhost:8000/ | страница проверки расчётов |
| — | `./infra/osrm/prepare.sh` | подготовка своего OSRM |

Логи: `docker compose logs -f api`. Статус: `docker compose ps`.

## 9. Частые проблемы

| Симптом | Решение |
|---|---|
| `docker: команда не найдена` | Docker не в PATH — перезапусти терминал |
| `docker-credential-desktop not found` | то же самое (Windows) |
| `no matching manifest for linux/arm64` (Mac) | включить Rosetta в Docker Desktop → Settings → General |
| `permission denied: ./start.sh` (Mac) | выполнить один раз `chmod +x *.sh infra/osrm/*.sh` |
| `выполнение сценариев отключено` (Windows) | `powershell -ExecutionPolicy Bypass -File script.ps1` или `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` |
| `port is already allocated` | поменяй `*_PORT` в `.env` |
| отчёт по прогону отдаёт 404 | API поднят без `docker-compose.backend.yml` (нет `RUNS_DIR`, `/data` read-only) |
| `routing` в статусе `degraded` | OSRM недоступен: проверь `OSRM_BASE_URL` в `.env` |
| фронт показывает старую версию | `docker compose build web && docker compose up -d web` |

## 10. Что почитать

- [`README.md`](README.md) — обзор, архитектура, API
- [`README-INFRA.md`](README-INFRA.md) — Docker, сервисы, матрица, демо без интернета
- [`README-ALGORITHM.md`](README-ALGORITHM.md) — правила планировщика
- [`services/routing/README.md`](services/routing/README.md) — сервис маршрутизации
- [`schema.json`](schema.json) — контракт входных данных

# Dockerfile бэкенда: обёртка над планировщиком logistics/ + FastAPI.
# Контекст сборки — корень репозитория (см. docker-compose.backend.yml).
#
# Требования к приложению:
#   * backend/app/main.py: FastAPI-приложение app
#   * пакет logistics/ в корне репозитория (планировщик)
#   * слушает 0.0.0.0:8000, GET /api/health -> 200

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PLANNER_ROOT=/app \
    PYTHONPATH=/app

WORKDIR /app

COPY backend/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY logistics ./logistics
COPY backend/app ./app

RUN useradd --create-home --uid 10001 appuser \
    && mkdir -p /app/logs \
    && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

HEALTHCHECK --interval=15s --timeout=5s --retries=5 --start-period=10s \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=4).status==200 else 1)"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]

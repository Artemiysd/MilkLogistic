# Dockerfile бэкенда. Сознательно не содержит кода — его наполняет команда бэкенда.
# Требования к приложению:
#   * пакет app/ внутри backend/, точка входа app.main:app (FastAPI)
#   * слушает 0.0.0.0:8000
#   * GET /api/health -> 200 без обращения к внешним сервисам
#   * зависимости в backend/requirements.txt

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# отдельный слой зависимостей — пересборка кода не тянет pip заново
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN useradd --create-home --uid 10001 appuser \
    && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

HEALTHCHECK --interval=15s --timeout=3s --retries=5 --start-period=10s \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=2).status==200 else 1)"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]

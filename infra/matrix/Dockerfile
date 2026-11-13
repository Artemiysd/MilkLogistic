# Сборка матрицы OSRM в контейнере (без Python на хосте).
#   docker compose run --rm matrix
#   docker compose run --rm matrix --verify
FROM python:3.12-alpine

ENV PYTHONUNBUFFERED=1 PYTHONIOENCODING=utf-8
WORKDIR /app

COPY infra/matrix/build_matrix.py infra/matrix/report.py ./infra/matrix/
COPY data/logistics_data.json data/geocode_overrides.json ./data/

ENTRYPOINT ["python", "infra/matrix/build_matrix.py"]

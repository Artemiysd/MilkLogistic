# Заглушка API: без зависимостей, чтобы образ собирался за секунды
# даже без интернета на площадке.
FROM python:3.12-alpine

ENV PYTHONUNBUFFERED=1 \
    DATA_FILE=/data/logistics_data.json \
    PORT=8000

WORKDIR /app
COPY app.py .

EXPOSE 8000

HEALTHCHECK --interval=10s --timeout=3s --retries=5 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=2).status==200 else 1)"

CMD ["python", "app.py"]

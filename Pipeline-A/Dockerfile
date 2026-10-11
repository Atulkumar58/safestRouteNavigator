FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY main.py ./
COPY ingestion ./ingestion
COPY processing ./processing
COPY storage ./storage
COPY data ./data

RUN useradd --create-home --uid 10001 appuser && chown -R appuser:appuser /app
USER 10001
EXPOSE 8081
CMD ["python", "main.py"]

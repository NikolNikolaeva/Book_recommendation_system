# syntax=docker/dockerfile:1
FROM python:3.12-slim-bookworm
WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY book_recsys ./book_recsys
COPY templates ./templates
COPY static ./static
COPY data ./data

RUN mkdir -p /app/data

EXPOSE 8000
# Задай SESSION_SECRET, ENV=production, SEED_DEMO_DATA=0 в orchestrator
CMD ["uvicorn", "book_recsys.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]

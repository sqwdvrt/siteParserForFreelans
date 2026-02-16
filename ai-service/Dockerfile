# AI Service: consumer (ai-process), user_embed_consumer
FROM python:3.11-slim

WORKDIR /app

# Install system deps for psycopg2, sentence-transformers
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src ./src
COPY cmd ./cmd

ENV PYTHONPATH=/app/src
ENV PYTHONUNBUFFERED=1

# Default: ai-process consumer
CMD ["python", "cmd/consumer/main.py"]

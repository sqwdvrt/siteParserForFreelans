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

RUN groupadd --system app && useradd --system --gid app --create-home --home-dir /home/app --shell /usr/sbin/nologin app && \
    mkdir -p /tmp/hf /tmp/transformers /tmp/torch /tmp/.cache && \
    chown -R app:app /app /home/app /tmp

ENV PYTHONPATH=/app/src
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
ENV HF_HOME=/tmp/hf
ENV TRANSFORMERS_CACHE=/tmp/transformers
ENV TORCH_HOME=/tmp/torch
ENV XDG_CACHE_HOME=/tmp/.cache

USER app:app

# Default: ai-process consumer
CMD ["python", "cmd/consumer/main.py"]

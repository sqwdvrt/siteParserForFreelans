# AI Service: consumer (ai-process), user_embed_consumer
# Build stage: install runtime deps into venv (excluding dev/test deps).
FROM python:3.11-slim@sha256:0b23cfb7425d065008b778022a17b1551c82f8b4866ee5a7a200084b7e2eafbf AS builder

ARG TORCH_VERSION=2.8.0
ENV VENV_PATH=/opt/venv
ENV PIP_NO_CACHE_DIR=1
ENV PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /build

RUN python -m venv "${VENV_PATH}" && \
    "${VENV_PATH}/bin/pip" install --upgrade pip

COPY requirements.txt .
RUN awk 'BEGIN{dev=0} /^# Dev/{dev=1} dev==0 {print}' requirements.txt > requirements.runtime.txt

# Pin torch from CPU index explicitly to avoid pulling CUDA wheels.
RUN "${VENV_PATH}/bin/pip" install --index-url https://download.pytorch.org/whl/cpu "torch==${TORCH_VERSION}" && \
    "${VENV_PATH}/bin/pip" install -r requirements.runtime.txt

# Runtime stage: copy prebuilt venv with runtime deps only.
FROM python:3.11-slim@sha256:0b23cfb7425d065008b778022a17b1551c82f8b4866ee5a7a200084b7e2eafbf

ENV VENV_PATH=/opt/venv
ENV PATH="${VENV_PATH}/bin:${PATH}"

WORKDIR /app

COPY --from=builder "${VENV_PATH}" "${VENV_PATH}"

COPY src ./src
COPY cmd ./cmd

RUN groupadd --system app && useradd --system --gid app --create-home --home-dir /home/app --shell /usr/sbin/nologin app && \
    mkdir -p /tmp/hf /tmp/transformers /tmp/torch /tmp/.cache && \
    chown -R app:app /app /home/app /tmp

ENV PYTHONPATH=/app/src
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
ENV PIP_NO_CACHE_DIR=1
ENV PIP_DISABLE_PIP_VERSION_CHECK=1
ENV HF_HOME=/tmp/hf
ENV TRANSFORMERS_CACHE=/tmp/transformers
ENV TORCH_HOME=/tmp/torch
ENV XDG_CACHE_HOME=/tmp/.cache

USER app:app

# Default: ai-process consumer
CMD ["python", "cmd/consumer/main.py"]

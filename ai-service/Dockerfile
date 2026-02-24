# AI Service: consumer (ai-process), user_embed_consumer
# Build stage: install runtime deps into venv (excluding dev/test deps).
FROM python:3.11-slim@sha256:0b23cfb7425d065008b778022a17b1551c82f8b4866ee5a7a200084b7e2eafbf AS builder

ARG TORCH_VERSION=2.8.0
ARG EMBEDDING_MODEL=paraphrase-multilingual-MiniLM-L12-v2
ENV VENV_PATH=/opt/venv
ENV PIP_NO_CACHE_DIR=1
ENV PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /build

RUN python -m venv "${VENV_PATH}" && \
    "${VENV_PATH}/bin/pip" install --upgrade pip "setuptools>=80.9.0" "wheel>=0.46.2"

COPY requirements.txt .
RUN awk 'BEGIN{dev=0} /^# Dev/{dev=1} dev==0 {print}' requirements.txt > requirements.runtime.txt

# Pin torch from CPU index explicitly to avoid pulling CUDA wheels.
RUN "${VENV_PATH}/bin/pip" install --index-url https://download.pytorch.org/whl/cpu "torch==${TORCH_VERSION}" && \
    "${VENV_PATH}/bin/pip" install -r requirements.runtime.txt

# Preload default embedding model into image to avoid runtime cold-start downloads.
# Use isolated HF cache in build step to avoid stale lock waits across interrupted builds.
RUN rm -rf /tmp/hf-build && mkdir -p /tmp/hf-build && \
    HF_HOME=/tmp/hf-build \
    HF_HUB_DISABLE_XET=1 \
    TRANSFORMERS_CACHE=/tmp/hf-build/transformers \
    SENTENCE_TRANSFORMERS_HOME=/tmp/hf-build/sentence-transformers \
    EMBEDDING_MODEL="${EMBEDDING_MODEL}" "${VENV_PATH}/bin/python" -u - <<'PY'
import os
from pathlib import Path
from sentence_transformers import SentenceTransformer

model_name = os.environ["EMBEDDING_MODEL"]
dst = Path("/opt/models") / model_name
dst.parent.mkdir(parents=True, exist_ok=True)
model = SentenceTransformer(model_name, cache_folder="/tmp/hf-build/sentence-transformers")
model.save(str(dst))
print(f"Preloaded embedding model '{model_name}' to '{dst}'")
PY

# Runtime stage: copy prebuilt venv with runtime deps only.
FROM python:3.11-slim@sha256:0b23cfb7425d065008b778022a17b1551c82f8b4866ee5a7a200084b7e2eafbf

ENV VENV_PATH=/opt/venv
ENV PATH="${VENV_PATH}/bin:${PATH}"

WORKDIR /app

COPY --from=builder "${VENV_PATH}" "${VENV_PATH}"
COPY --from=builder /opt/models /opt/models

COPY src ./src
COPY cmd ./cmd

RUN /usr/local/bin/python -m pip install --upgrade "setuptools>=80.9.0" "wheel>=0.46.2" && \
    "${VENV_PATH}/bin/pip" install --upgrade "setuptools>=80.9.0" "wheel>=0.46.2" && \
    groupadd --system app && useradd --system --gid app --create-home --home-dir /home/app --shell /usr/sbin/nologin app && \
    mkdir -p /tmp/hf /tmp/transformers /tmp/torch /tmp/.cache && \
    chown -R app:app /app /home/app /tmp /opt/models

ENV PYTHONPATH=/app/src
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
ENV PIP_NO_CACHE_DIR=1
ENV PIP_DISABLE_PIP_VERSION_CHECK=1
ENV EMBEDDING_MODELS_DIR=/opt/models
ENV EMBEDDING_REQUIRE_LOCAL=1
ENV HF_HOME=/tmp/hf
ENV HF_HUB_DISABLE_XET=1
ENV TRANSFORMERS_CACHE=/tmp/transformers
ENV TORCH_HOME=/tmp/torch
ENV XDG_CACHE_HOME=/tmp/.cache

USER app:app

# Default: ai-process consumer
CMD ["python", "cmd/consumer/main.py"]

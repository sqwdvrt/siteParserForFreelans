# AI Service: consumer (ai-process), user_embed_consumer
# Build stage: install runtime deps into venv (excluding dev/test deps).
FROM python:3.11-slim@sha256:6d98ca198cea726f2c86da2699594339a7b7ff08e49728797b4ed6e3b5c3b62a AS builder

ARG TORCH_VERSION=2.8.0
ARG EMBEDDING_MODEL=all-MiniLM-L6-v2
ARG RERANK_MODEL=BAAI/bge-reranker-base
ENV VENV_PATH=/opt/venv
ENV PIP_NO_CACHE_DIR=1
ENV PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /build

RUN python -m venv "${VENV_PATH}" && \
    "${VENV_PATH}/bin/pip" install --upgrade "pip==26.0" "setuptools==80.9.0" "wheel==0.46.2"

COPY requirements.txt .
COPY requirements.runtime.lock .

# Pin torch from CPU index explicitly to avoid pulling CUDA wheels.
RUN "${VENV_PATH}/bin/pip" install --index-url https://download.pytorch.org/whl/cpu "torch==${TORCH_VERSION}" && \
    "${VENV_PATH}/bin/pip" install -r requirements.runtime.lock

# Some upstream wheels bundle third-party audit SBOMs under *.dist-info/sboms/.
# Trivy treats those embedded SBOMs as additional package inventories, which
# creates false HIGH findings for tooling that is not actually installed at
# runtime (for example wheel/jaraco.context from auditwheel metadata). They are
# not used by the service at runtime, so drop them from the shipped venv.
RUN find "${VENV_PATH}/lib" -type d -path '*/site-packages/*.dist-info/sboms' -prune -exec rm -rf {} +

# Preload default embedding and rerank models into image to avoid runtime cold-start downloads.
# Use isolated HF cache in build step to avoid stale lock waits across interrupted builds.
RUN rm -rf /tmp/hf-build && mkdir -p /tmp/hf-build && \
    HF_HOME=/tmp/hf-build \
    HF_HUB_DISABLE_XET=1 \
    TRANSFORMERS_CACHE=/tmp/hf-build/transformers \
    SENTENCE_TRANSFORMERS_HOME=/tmp/hf-build/sentence-transformers \
    EMBEDDING_MODEL="${EMBEDDING_MODEL}" \
    RERANK_MODEL="${RERANK_MODEL}" \
    "${VENV_PATH}/bin/python" -u - <<'PY'
import os
from pathlib import Path
from sentence_transformers import CrossEncoder, SentenceTransformer

embedding_name = os.environ["EMBEDDING_MODEL"]
embedding_dst = Path("/opt/models") / embedding_name
embedding_dst.parent.mkdir(parents=True, exist_ok=True)
embedding_model = SentenceTransformer(embedding_name, cache_folder="/tmp/hf-build/sentence-transformers")
embedding_model.save(str(embedding_dst))
print(f"Preloaded embedding model '{embedding_name}' to '{embedding_dst}'")

rerank_name = os.environ["RERANK_MODEL"]
rerank_dst = Path("/opt/models") / rerank_name
rerank_dst.parent.mkdir(parents=True, exist_ok=True)
rerank_model = CrossEncoder(rerank_name, cache_folder="/tmp/hf-build/sentence-transformers")
rerank_model.save(str(rerank_dst))
print(f"Preloaded rerank model '{rerank_name}' to '{rerank_dst}'")
PY

# Runtime stage: copy prebuilt venv with runtime deps only.
FROM python:3.11-slim@sha256:6d98ca198cea726f2c86da2699594339a7b7ff08e49728797b4ed6e3b5c3b62a

ENV VENV_PATH=/opt/venv
ENV PATH="${VENV_PATH}/bin:${PATH}"

WORKDIR /app

COPY --from=builder "${VENV_PATH}" "${VENV_PATH}"
COPY --from=builder /opt/models /opt/models

COPY src ./src
COPY cmd ./cmd

RUN groupadd --system app && useradd --system --gid app --create-home --home-dir /home/app --shell /usr/sbin/nologin app && \
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

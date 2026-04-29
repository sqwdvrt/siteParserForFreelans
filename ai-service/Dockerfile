# AI Service: consumer (ai-process), user_embed_consumer
# Build stage: install runtime deps into venv (excluding dev/test deps).
FROM python:3.11-slim-bookworm@sha256:9c6f90801e6b68e772b7c0ca74260cbf7af9f320acec894e26fccdaccfbe3b47 AS builder

ARG TORCH_VERSION=2.8.0
ARG EMBEDDING_MODEL=all-MiniLM-L6-v2
ARG RERANK_MODEL=BAAI/bge-reranker-base
ARG PRELOAD_LOCAL_MODELS=0
ENV VENV_PATH=/opt/venv
ENV PIP_NO_CACHE_DIR=1
ENV PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /build

RUN python -m venv "${VENV_PATH}" && \
    "${VENV_PATH}/bin/pip" install --upgrade "pip==26.0.1" "setuptools==80.9.0" "wheel==0.46.2"

COPY requirements.txt .
COPY requirements.runtime.lock .

# Pin torch from CPU index explicitly to avoid pulling CUDA wheels.
RUN "${VENV_PATH}/bin/pip" install --index-url https://download.pytorch.org/whl/cpu "torch==${TORCH_VERSION}" && \
    "${VENV_PATH}/bin/pip" install -r requirements.runtime.lock

# `wheel` is only needed while assembling the venv and should not ship in the
# runtime image; Trivy rightfully flags old wheel metadata if we keep it around.
RUN "${VENV_PATH}/bin/pip" uninstall -y wheel

# Some upstream wheels bundle third-party audit SBOMs under *.dist-info/sboms/.
# Trivy treats those embedded SBOMs as additional package inventories, which
# creates false HIGH findings for tooling that is not actually installed at
# runtime (for example wheel/jaraco.context from auditwheel metadata). They are
# not used by the service at runtime, so drop them from the shipped venv.
RUN find "${VENV_PATH}/lib" -type d -path '*/site-packages/*.dist-info/sboms' -prune -exec rm -rf {} + && \
    find "${VENV_PATH}/lib" -type d \( \
      -path '*/site-packages/pip-*.dist-info' -o \
      -path '*/site-packages/setuptools-*.dist-info' -o \
      -path '*/site-packages/setuptools/_vendor/jaraco.context-*.dist-info' -o \
      -path '*/site-packages/setuptools/_vendor/*.dist-info' -o \
      -path '*/site-packages/setuptools/_vendor/wheel-*.dist-info' \
    \) -prune -exec rm -rf {} +

# Production deploys keep the runtime image small and allow model downloads at
# startup. Set PRELOAD_LOCAL_MODELS=1 when an offline image is explicitly needed.
RUN <<'SH'
set -eu
mkdir -p /opt/models
if [ "${PRELOAD_LOCAL_MODELS}" != "1" ]; then
  echo "Skipping local model preload; set PRELOAD_LOCAL_MODELS=1 to bundle models"
  exit 0
fi

rm -rf /tmp/hf-build
mkdir -p /tmp/hf-build
HF_HOME=/tmp/hf-build \
HF_HUB_DISABLE_XET=1 \
TRANSFORMERS_CACHE=/tmp/hf-build/transformers \
SENTENCE_TRANSFORMERS_HOME=/tmp/hf-build/sentence-transformers \
EMBEDDING_MODEL="${EMBEDDING_MODEL}" \
RERANK_MODEL="${RERANK_MODEL}" \
"${VENV_PATH}/bin/python" -u - <<'PY'
import os
from pathlib import Path
from huggingface_hub import snapshot_download
from sentence_transformers import CrossEncoder, SentenceTransformer

def preload_repo(model_name: str, *, validate):
    dst = Path("/opt/models") / model_name
    dst.parent.mkdir(parents=True, exist_ok=True)
    candidates = [model_name]
    if "/" not in model_name:
        candidates.append(f"sentence-transformers/{model_name}")
    last_exc = None
    for repo_id in candidates:
        try:
            snapshot_download(
                repo_id=repo_id,
                cache_dir="/tmp/hf-build",
                local_dir=str(dst),
            )
            validate(str(dst))
            print(f"Preloaded model '{model_name}' from repo '{repo_id}' to '{dst}'")
            return
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
    raise last_exc


preload_repo(
    os.environ["EMBEDDING_MODEL"],
    validate=lambda model_path: SentenceTransformer(
        model_path,
        cache_folder="/tmp/hf-build/sentence-transformers",
        local_files_only=True,
    ),
)
preload_repo(
    os.environ["RERANK_MODEL"],
    validate=lambda model_path: CrossEncoder(
        model_path,
        cache_folder="/tmp/hf-build/sentence-transformers",
        local_files_only=True,
    ),
)
PY
SH

# Runtime stage: copy prebuilt venv with runtime deps only.
FROM python:3.11-slim-bookworm@sha256:9c6f90801e6b68e772b7c0ca74260cbf7af9f320acec894e26fccdaccfbe3b47

ENV VENV_PATH=/opt/venv
ENV PATH="${VENV_PATH}/bin:${PATH}"

RUN apt-get update && \
    apt-get upgrade -y --no-install-recommends && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY --from=builder "${VENV_PATH}" "${VENV_PATH}"
COPY --from=builder /opt/models /opt/models

COPY src ./src
COPY cmd ./cmd

RUN groupadd --system app && useradd --system --gid app --create-home --home-dir /home/app --shell /usr/sbin/nologin app && \
    rm -rf /usr/local/lib/python3.11/ensurepip \
      /usr/local/lib/python3.11/site-packages/pip-*.dist-info \
      /usr/local/lib/python3.11/site-packages/setuptools-*.dist-info \
      /usr/local/lib/python3.11/site-packages/wheel-*.dist-info \
      /usr/local/lib/python3.11/site-packages/setuptools/_vendor/jaraco.context-*.dist-info \
      /usr/local/lib/python3.11/site-packages/setuptools/_vendor/*.dist-info && \
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

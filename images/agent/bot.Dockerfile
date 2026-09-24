# images/agent/bot.Dockerfile
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    STREAMLIT_BROWSER_GATHER_USAGE_STATS=false \
    STREAMLIT_SERVER_HEADLESS=true

RUN apt-get update && apt-get install -y --no-install-recommends \
      curl build-essential \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Upgrade pip before any install so the resolver used below is current.
RUN pip install --no-cache-dir -U pip

# Install torch CPU-only before requirements.txt (separate index).
# Mirrors images/core/Dockerfile — keep the two in step.
#
# Must be a separate step: pip's index resolution with +cpu local version
# labels inside requirements.txt alongside a standard index is
# unpredictable. Two-step install is deterministic.
# Do NOT add torch to requirements.txt. Left on the default PyPI index it
# resolves to the CUDA build (torch 2.5.1+cu124), which drags in ~2.7 GB of
# nvidia-*-cu12 packages that this image can never use — the compose GPU
# profile is commented out and the Agent only ever embeds on CPU.
#
# Architecture-aware: the `+cpu` local-version wheels are published only for
# x86_64 / Windows. On arm64 (e.g. Apple Silicon, where Docker builds a
# linux/arm64 image) that wheel does not exist, so we install the plain
# manylinux aarch64 wheel — which is already CPU-only. TARGETARCH is a
# BuildKit built-in build arg ("arm64" / "amd64").
ARG TARGETARCH
RUN if [ "$TARGETARCH" = "arm64" ]; then \
      pip install --no-cache-dir torch==2.5.1 \
        --extra-index-url https://download.pytorch.org/whl/cpu; \
    else \
      pip install --no-cache-dir torch==2.5.1+cpu \
        --extra-index-url https://download.pytorch.org/whl/cpu; \
    fi

# deps first for caching
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

# Pre-download embedding model at build time (mirrors Core Dockerfile).
# Both images must use the same model name.
ENV SENTENCE_TRANSFORMERS_HOME=/opt/embedding_model
RUN python -c "from sentence_transformers import SentenceTransformer; \
               SentenceTransformer('all-MiniLM-L6-v2')"

# app code
COPY app/ /app/

EXPOSE 8501
HEALTHCHECK CMD curl --fail --silent http://localhost:8501/_stcore/health || exit 1

ENTRYPOINT ["streamlit", "run", "bot.py", "--server.port=8501", "--server.address=0.0.0.0"]

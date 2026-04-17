# Dockerfile for Coyote Core
# Build context should be images/core/core_analysis/

FROM python:3.11-slim-bullseye

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Set the working directory
WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Install torch CPU-only before requirements.txt (separate index).
# Must be a separate step: pip's index resolution with +cpu local version
# labels inside requirements.txt alongside a standard index is
# unpredictable. Two-step install is deterministic.
# Do NOT add torch to requirements.txt.
RUN pip install --no-cache-dir torch==2.5.1+cpu \
      --extra-index-url https://download.pytorch.org/whl/cpu

# Copy requirements first for better layer caching
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt \
    && pip install --no-cache-dir \
       https://github.com/explosion/spacy-models/releases/download/en_core_web_sm-3.7.1/en_core_web_sm-3.7.1-py3-none-any.whl \
    && python -m spacy validate

# Pre-download the embedding model at build time.
# Eliminates HuggingFace Hub download on first container start.
# CRITICAL: If EMBEDDING_MODEL_NAME changes in shared/embedding_config.py,
# update this line to match and rebuild both images.
ENV SENTENCE_TRANSFORMERS_HOME=/opt/embedding_model
RUN python -c "from sentence_transformers import SentenceTransformer; \
               SentenceTransformer('all-MiniLM-L6-v2')"

# Copy the coyote package
COPY coyote/ /app/coyote/

# Copy shared modules (embedding_config.py)
COPY shared/ /app/shared/

# Create data directories
RUN mkdir -p /app/data/logs

# Expose Flask port
EXPOSE 5000

# Run the server
CMD ["python", "-m", "coyote.coyote_server"]

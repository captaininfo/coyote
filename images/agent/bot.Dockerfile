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

# deps first for caching
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -U pip && \
    pip install --no-cache-dir -r /app/requirements.txt

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

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

# Copy requirements first for better layer caching
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt \
    && pip install --no-cache-dir \
       https://github.com/explosion/spacy-models/releases/download/en_core_web_sm-3.7.1/en_core_web_sm-3.7.1-py3-none-any.whl \
    && python -m spacy validate

# Copy the coyote package
COPY coyote/ /app/coyote/

# Create data directories
RUN mkdir -p /app/data/logs

# Expose Flask port
EXPOSE 5000

# Run the server
CMD ["python", "-m", "coyote.coyote_server"]

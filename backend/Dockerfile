# DaTaK Gateway Backend Dockerfile

FROM python:3.12-slim

WORKDIR /app

# Install system dependencies for CAN and serial
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    can-utils \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY pyproject.toml .
RUN touch README.md
RUN pip install --no-cache-dir -e .

# Copy application code
COPY app/ app/
COPY alembic/ alembic/
COPY alembic.ini .

# Create data directories
RUN mkdir -p data/exports data/buffer configs

# Expose API port
EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')" || exit 1

# Run with uvicorn
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

# ============================================================================
# Multi-stage Dockerfile for Algo Trading Platform
# ============================================================================

# ---------------------------------------------------------------------------
# Stage 1: Builder — install Python dependencies into a virtualenv
# ---------------------------------------------------------------------------
FROM python:3.12-slim AS builder

RUN apt-get update && \
    apt-get install -y --no-install-recommends gcc libpq-dev && \
    rm -rf /var/lib/apt/lists/*

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r /tmp/requirements.txt

# ---------------------------------------------------------------------------
# Stage 2: Runtime — lean production image
# ---------------------------------------------------------------------------
FROM python:3.12-slim AS runtime

RUN apt-get update && \
    apt-get install -y --no-install-recommends libpq5 curl && \
    rm -rf /var/lib/apt/lists/*

# Copy virtualenv from builder
COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Create non-root user
RUN groupadd --gid 1000 appuser && \
    useradd --uid 1000 --gid appuser --shell /bin/bash --create-home appuser

WORKDIR /app

# Copy application code
COPY core/ ./core/
COPY strategies/ ./strategies/
COPY data/ ./data/
COPY analytics/ ./analytics/
COPY backtest/ ./backtest/
COPY config/ ./config/
COPY dashboard/ ./dashboard/
COPY pyproject.toml ./

# Create directories for runtime data
RUN mkdir -p /app/logs /app/data/cache && \
    chown -R appuser:appuser /app

USER appuser

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD curl -f http://localhost:8080/api/health || exit 1

CMD ["uvicorn", "core.api:app", "--host", "0.0.0.0", "--port", "8080", "--workers", "1"]

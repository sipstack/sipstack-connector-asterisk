# SIPSTACK Asterisk Connector
# Multi-stage build for smaller final image

# Build stage
FROM python:3.8-slim as builder

# Install build dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Create virtual environment
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Copy requirements and install dependencies
COPY requirements.txt /tmp/
RUN pip install --no-cache-dir -r /tmp/requirements.txt

# Runtime stage
FROM python:3.8-slim

# Install runtime dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    curl \
    procps \
    && rm -rf /var/lib/apt/lists/* \
    && update-ca-certificates

# Create non-root user
# Note: When building, users should use --build-arg PUID=xxx PGID=xxx
# Or just run as root with user: "PUID:PGID" in docker-compose.yml
ARG PUID=1000
ARG PGID=1000
RUN groupadd -g ${PGID} sipstack && \
    useradd -r -u ${PUID} -g sipstack -s /bin/false -m -d /app sipstack

# Copy virtual environment from builder
COPY --from=builder /opt/venv /opt/venv

# Copy application code
WORKDIR /app
COPY --chown=sipstack:sipstack src/ /app/
COPY --chown=sipstack:sipstack requirements.txt /app/
COPY --chown=sipstack:sipstack VERSION /app/
COPY --chown=sipstack:sipstack scripts/ /app/scripts/

# Make scripts executable
RUN chmod +x /app/healthcheck.py && \
    chmod +x /app/scripts/*.sh

# Set environment variables
ENV PATH="/opt/venv/bin:$PATH"
ENV PYTHONPATH="/app"
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

# Default environment variables (can be overridden)
ENV LOG_LEVEL=INFO
ENV API_TIMEOUT=30
ENV BATCH_SIZE=200
ENV BATCH_TIMEOUT=30

# Switch to non-root user
USER sipstack

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD python /app/healthcheck.py || exit 1

# Expose metrics port
EXPOSE 8000

# Run the application directly
CMD ["python", "-u", "-m", "main"]
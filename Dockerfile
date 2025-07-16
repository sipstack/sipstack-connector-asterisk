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
    && rm -rf /var/lib/apt/lists/* \
    && update-ca-certificates

# Create non-root user
RUN useradd -r -s /bin/false -m -d /app sipstack

# Copy virtual environment from builder
COPY --from=builder /opt/venv /opt/venv

# Copy application code
WORKDIR /app
COPY --chown=sipstack:sipstack src/ /app/
COPY --chown=sipstack:sipstack requirements.txt /app/
COPY --chown=sipstack:sipstack VERSION /app/

# Set environment variables
ENV PATH="/opt/venv/bin:$PATH"
ENV PYTHONPATH="/app"
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

# Default environment variables (can be overridden)
ENV LOG_LEVEL=INFO
ENV API_TIMEOUT=30
ENV BATCH_SIZE=100
ENV BATCH_TIMEOUT=30

# Switch to non-root user
USER sipstack

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD python -c "import socket; socket.create_connection(('localhost', 8000), timeout=5)" || exit 1

# Expose metrics port
EXPOSE 8000

# Run the application with unbuffered output and proper signal handling
CMD ["python", "-u", "-m", "main"]
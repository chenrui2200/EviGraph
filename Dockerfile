# =============================================
# Stage 1: Build frontend
# =============================================
FROM node:22-alpine AS frontend-builder

WORKDIR /app

# Copy frontend source
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm ci

COPY frontend/ ./
RUN npm run build

# =============================================
# Stage 2: Python backend
# =============================================
FROM python:3.13-slim AS backend

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONIOENCODING=utf-8

# Install build dependencies for PyMuPDF
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libmupdf-dev \
    poppler-utils \
    tesseract-ocr \
    tesseract-ocr-chn \
    tesseract-ocr-eng \
    && rm -rf /var/lib/apt/lists/*

# Copy uv from official image
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app

# Copy backend source and install dependencies
COPY backend/pyproject.toml backend/uv.lock* ./
RUN uv sync --no-dev

COPY backend/ ./

# =============================================
# Stage 3: Final runtime
# =============================================
FROM python:3.13-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONIOENCODING=utf-8

RUN apt-get update && apt-get install -y --no-install-recommends \
    libmupdf1 \
    poppler-utils \
    tesseract-ocr \
    tesseract-ocr-chn \
    tesseract-ocr-eng \
    nginx \
    && rm -rf /var/lib/apt/lists/* \
    && apt-get clean

# Copy uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app

# Install backend (no dev dependencies in production)
COPY --from=backend /app/backend/pyproject.toml /app/backend/uv.lock* ./
RUN uv sync --no-dev --no-install-project
ENV PYTHONPATH=/app/backend

# Copy backend source
COPY --from=backend /app/backend/ ./backend/

# Copy built frontend
COPY --from=frontend-builder /app/dist ./frontend/dist

# Copy nginx config for serving frontend
COPY nginx.conf /etc/nginx/nginx.conf

EXPOSE 5001 80

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:5001/api/health')" || exit 1

# Start nginx (frontend) + Flask (backend) via supervisord
COPY supervisord.conf /etc/supervisord.conf

CMD ["/usr/bin/supervisord", "-c", "/etc/supervisord.conf"]

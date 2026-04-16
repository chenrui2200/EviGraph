# =============================================
# Stage 1: Build frontend
# =============================================
FROM node:22-alpine AS frontend-builder

WORKDIR /app

COPY frontend/package.json frontend/package-lock.json* ./
RUN npm ci

COPY frontend/ ./
RUN npm run build

# =============================================
# Stage 2: Python backend
# =============================================
FROM python:3.12-slim AS backend-builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONIOENCODING=utf-8


WORKDIR /app/backend

# Copy requirements and install dependencies via pip
COPY backend/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/ ./

# 创建稳定软链接，避免不同 Python 小版本路径差异
RUN ln -s $(python -c "import site; print(site.getsitepackages()[0])") /python-site-packages

# =============================================
# Stage 3: Final runtime
# =============================================
FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONIOENCODING=utf-8

WORKDIR /app

# Install runtime system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    nginx supervisor \
    poppler-utils \
    && rm -rf /var/lib/apt/lists/*

# Copy backend (site-packages + source)
COPY --from=backend-builder /python-site-packages /usr/local/lib/python3.12/site-packages
COPY --from=backend-builder /usr/local/bin /usr/local/bin
COPY --from=backend-builder /app/backend /app/backend

# Copy built frontend
COPY --from=frontend-builder /app/dist /app/frontend/dist

# Copy nginx & supervisord configs
COPY nginx.conf /etc/nginx/nginx.conf
COPY supervisord.conf /etc/supervisor/conf.d/supervisord.conf

ENV PYTHONPATH=/app/backend

EXPOSE 5001 80

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:5001/api/health')" || exit 1

# Start nginx (frontend) + Flask (backend) via supervisord
CMD ["/usr/bin/supervisord", "-c", "/etc/supervisor/conf.d/supervisord.conf"]

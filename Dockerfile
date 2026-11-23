# Stage 1: Build the React + TypeScript + Vite dashboard
FROM node:26-slim AS web-builder

WORKDIR /app/web

COPY web/package.json web/package-lock.json* ./
RUN if [ -f package-lock.json ]; then npm ci; else npm install; fi

COPY web/ ./
RUN npm run build

# Stage 2: Python 3.14 + uv runtime serving API + built web/dist on port 8000
FROM python:3.14-slim AS runtime

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/

WORKDIR /app

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PYTHONUNBUFFERED=1 \
    AIWATCHDOGS_DB_PATH=/app/data/aiwatchdogs.db \
    AIWATCHDOGS_WEB_DIST=/app/web/dist \
    AIWATCHDOGS_HOST=0.0.0.0 \
    AIWATCHDOGS_PORT=8000 \
    HOST=0.0.0.0 \
    PORT=8000

COPY pyproject.toml uv.lock* README.md LICENSE ./
COPY src/ ./src/
COPY datasets/ ./datasets/

RUN if [ -f uv.lock ]; then uv sync --no-dev --frozen; else uv sync --no-dev; fi

COPY --from=web-builder /app/web/dist ./web/dist

RUN useradd --create-home --shell /usr/sbin/nologin aiwatchdogs \
    && mkdir -p /app/data \
    && chown -R aiwatchdogs:aiwatchdogs /app

USER aiwatchdogs

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python3 -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3)" || exit 1

CMD ["uv", "run", "--no-sync", "aiwatchdogs-hub"]

# Stage 1: Build the React + TypeScript + Vite dashboard (pinned Node version)
FROM node:26.13.1-slim AS web-builder

WORKDIR /app/web

COPY web/package.json web/package-lock.json ./
RUN npm ci

COPY web/ ./
RUN npm run build

# Stage 2: Pinned Python 3.14 + pinned uv runtime serving API + built web/dist on port 8000
FROM python:3.14.7-slim AS runtime

COPY --from=ghcr.io/astral-sh/uv:0.13.9 /uv /uvx /usr/local/bin/

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

COPY pyproject.toml uv.lock README.md LICENSE ./
COPY src/ ./src/
COPY projects/ ./projects/

RUN uv sync --no-dev --frozen

COPY --from=web-builder /app/web/dist ./web/dist

RUN useradd --create-home --shell /usr/sbin/nologin aiwatchdogs \
    && mkdir -p /app/data \
    && chown -R aiwatchdogs:aiwatchdogs /app

USER aiwatchdogs

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python3 -c "import json, sys, urllib.request; r = urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3); d = json.loads(r.read().decode()); sys.exit(0 if r.status == 200 and d.get('db_ok') is True and d.get('status') == 'ok' else 1)"

CMD ["uv", "run", "--no-sync", "aiwatchdogs-hub"]

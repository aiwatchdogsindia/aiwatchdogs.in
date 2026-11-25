# Stage 1: Build the React + TypeScript + Vite dashboard (pinned Node version)
FROM node:26.13.1-slim AS web-builder

WORKDIR /app/web

COPY web/package.json web/package-lock.json ./
RUN npm ci

COPY web/index.html web/vite.config.ts web/tsconfig.json web/tsconfig.app.json web/tsconfig.node.json ./
COPY web/src/ ./src/
COPY web/public/ ./public/
RUN npm run build

# Stage 2: Build the Python virtual environment with pinned uv (not shipped to runtime)
FROM python:3.14.7-slim AS python-builder

COPY --from=ghcr.io/astral-sh/uv:0.13.9 /uv /usr/local/bin/uv

WORKDIR /app

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

COPY pyproject.toml uv.lock .python-version README.md LICENSE ./
COPY src/ ./src/

RUN uv sync --no-dev --frozen \
    && python3 -m compileall -q /app/src

# Stage 3: Minimal non-root runtime serving API + built web/dist on port 8000 (no uv/uvx)
FROM python:3.14.7-slim AS runtime

WORKDIR /app

ENV PATH="/app/.venv/bin:${PATH}" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    AIWATCHDOGS_DB_PATH=/app/data/aiwatchdogs.db \
    AIWATCHDOGS_WEB_DIST=/app/web/dist \
    AIWATCHDOGS_HOST=0.0.0.0 \
    AIWATCHDOGS_PORT=8000 \
    HOST=0.0.0.0 \
    PORT=8000

COPY --from=python-builder --chown=root:root /app/.venv /app/.venv
COPY --from=python-builder --chown=root:root /app/src /app/src
COPY --chown=root:root projects/ /app/projects/
COPY --from=web-builder --chown=root:root /app/web/dist /app/web/dist

RUN useradd --create-home --shell /usr/sbin/nologin aiwatchdogs \
    && mkdir -p /app/data \
    && chown aiwatchdogs:aiwatchdogs /app/data \
    && chmod 0700 /app/data

USER aiwatchdogs

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python3 -c "import json, sys, urllib.request; r = urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3); d = json.loads(r.read().decode()); sys.exit(0 if r.status == 200 and d.get('db_ok') is True and d.get('status') == 'ok' else 1)"

CMD ["/app/.venv/bin/aiwatchdogs-hub"]

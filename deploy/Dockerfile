# Control-plane image: FastAPI app + built web UI. Build from repo root:
#   docker build -f deploy/Dockerfile -t hive-control .
FROM node:22-slim AS webbuild
WORKDIR /web
COPY web/package*.json ./
RUN npm ci
COPY web/ ./
RUN npm run build

FROM python:3.13-slim
RUN apt-get update && apt-get install -y --no-install-recommends git && rm -rf /var/lib/apt/lists/*
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project --no-dev
COPY hive/ hive/
RUN uv sync --frozen --no-dev
COPY --from=webbuild /web/dist /app/web-dist
ENV HIVE_WEB_DIST=/app/web-dist HIVE_DATA_DIR=/data
EXPOSE 8000
CMD ["uv", "run", "--no-sync", "uvicorn", "--factory", "hive.api:production_app", "--host", "0.0.0.0", "--port", "8000"]

# syntax=docker/dockerfile:1

FROM --platform=$BUILDPLATFORM node:22-slim AS web
WORKDIR /src/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.13-slim AS build
COPY --from=ghcr.io/astral-sh/uv:0.12 /uv /bin/uv
ENV UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/app/.venv
WORKDIR /src/backend
COPY backend/pyproject.toml backend/uv.lock ./
RUN uv sync --frozen --no-dev --no-editable --compile-bytecode --no-install-project
COPY backend/src ./src
RUN uv sync --frozen --no-dev --no-editable --compile-bytecode

FROM python:3.13-slim AS runtime
ARG VERSION=dev
LABEL org.opencontainers.image.source="https://github.com/BohdainK/LinerSync" \
      org.opencontainers.image.licenses="PolyForm-Noncommercial-1.0.0" \
      org.opencontainers.image.version="${VERSION}"
ENV PATH=/app/.venv/bin:$PATH \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    LINERSYNC_HOST=0.0.0.0 \
    LINERSYNC_PORT=8000 \
    LINERSYNC_DATA_DIR=/data \
    LINERSYNC_WEB_DIR=/app/web
RUN groupadd --gid 1000 linersync \
    && useradd --uid 1000 --gid 1000 --no-create-home --shell /usr/sbin/nologin linersync \
    && mkdir /data \
    && chown 1000:1000 /data
COPY --from=build /app/.venv /app/.venv
COPY --from=web /src/frontend/dist /app/web
WORKDIR /data
VOLUME /data
USER 1000:1000
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=3)"]
CMD ["linersync", "serve"]

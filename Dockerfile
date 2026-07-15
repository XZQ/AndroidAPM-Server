# syntax=docker/dockerfile:1.7
FROM ghcr.io/astral-sh/uv:0.11.28 AS uv

FROM python:3.11.15-slim-bookworm AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH="/app/.venv/bin:${PATH}"

COPY --from=uv /uv /uvx /bin/
WORKDIR /app

RUN groupadd --system --gid 10001 androidapm \
    && useradd --system --uid 10001 --gid androidapm --home-dir /nonexistent androidapm

COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project

COPY alembic.ini ./
COPY alembic ./alembic
COPY proto ./proto
COPY scripts ./scripts
COPY src ./src
RUN uv sync --frozen --no-dev

USER 10001:10001
EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/health/live', timeout=2)"]

CMD ["uvicorn", "androidapm_server.main:app", "--host", "0.0.0.0", "--port", "8080", "--proxy-headers"]

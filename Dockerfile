# syntax=docker/dockerfile:1.7
# Multi-stage build per research R9. Single image, two entry points
# (uvicorn HTTP service vs `python -m forecast_sidecar.train_cli`); the
# Cloud Run Job overrides CMD.

# ---- builder ------------------------------------------------------------
FROM python:3.11-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates \
 && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:0.5.0 /uv /uvx /usr/local/bin/

WORKDIR /app

# Copy lockfile + project metadata first so layer caches when only src/ changes.
COPY pyproject.toml uv.lock README.md ./
COPY src/ ./src/

RUN uv sync --frozen --no-dev

COPY configs/ ./configs/

# ---- runtime ------------------------------------------------------------
FROM python:3.11-slim AS runtime

# `libgomp1` is the OpenMP runtime LightGBM dynamically links against.
RUN apt-get update \
 && apt-get install -y --no-install-recommends libgomp1 ca-certificates \
 && rm -rf /var/lib/apt/lists/* \
 && useradd --system --uid 10001 --create-home --home-dir /home/forecast --shell /sbin/nologin forecast

ARG GIT_SHA=unknown
ENV GIT_SHA=${GIT_SHA} \
    PATH="/app/.venv/bin:${PATH}" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8080

WORKDIR /app

COPY --from=builder --chown=forecast:forecast /app /app
COPY --chown=forecast:forecast docker/ ./docker/

USER forecast

EXPOSE 8080

# ENTRYPOINT empty so `docker run forecast-sidecar python -m forecast_sidecar.train_cli ...`
# works without indirection. Cloud Run Job sets its own command.
ENTRYPOINT []
CMD ["uvicorn", "forecast_sidecar.main:app", "--host", "0.0.0.0", "--port", "8080"]

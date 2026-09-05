# SPDX-License-Identifier: Apache-2.0
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH="/opt/venv/bin:$PATH" PYTHONUNBUFFERED=1
WORKDIR /app

# dependencies first (cached layer)
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project

COPY pipeline ./pipeline
COPY migrations ./migrations
COPY docker-entrypoint.sh ./
RUN uv sync --frozen --no-dev && chmod +x docker-entrypoint.sh \
    && useradd --create-home --uid 10001 pipeline && mkdir -p /data /inbox && chown -R pipeline /data /inbox

USER pipeline
EXPOSE 8000
ENTRYPOINT ["/app/docker-entrypoint.sh"]
CMD ["serve"]

FROM ghcr.io/astral-sh/uv:0.12.23 AS uv
FROM python:3.12.13-slim
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --no-install-project
COPY src ./src
RUN uv sync --locked --no-dev && useradd --create-home --uid 10001 cmdl \
    && mkdir -p artifacts && chown cmdl:cmdl artifacts
USER cmdl
CMD ["uvicorn", "cmdl.api.app:app", "--host", "0.0.0.0", "--port", "8000"]

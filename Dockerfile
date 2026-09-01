# Build and runtime are separated so the image ships the virtualenv without uv,
# the lockfile, or a compiler toolchain.
FROM python:3.14-slim AS build

COPY --from=ghcr.io/astral-sh/uv:0.11 /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app

# Dependencies resolve from the lockfile alone, so this layer is cached until
# the lockfile itself changes rather than on every source edit.
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project --extra server

COPY src ./src
RUN uv sync --frozen --no-dev --extra server


FROM python:3.14-slim AS runtime

# Runs unprivileged. The service reads a database and parses files people
# upload, so it has no business owning anything in its own filesystem.
RUN useradd --create-home --uid 1000 schemagate

WORKDIR /app

COPY --from=build --chown=schemagate:schemagate /app/.venv /app/.venv
COPY --from=build --chown=schemagate:schemagate /app/src /app/src

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

USER schemagate

EXPOSE 8000

# Reads configuration from the environment at startup and refuses to run
# without it, so a misconfigured container fails here rather than on the first
# request an hour later.
CMD ["uvicorn", "schemagate.api.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]

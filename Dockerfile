# Build and runtime are separated so the image ships the virtualenv without uv,
# the lockfile, or a compiler toolchain.
#
# Defaults to `all`, which is what an image is for: someone running the
# container has not chosen a provider yet and should not have to rebuild to
# change their mind. Build with --build-arg EXTRAS="server,postgres,anthropic"
# for a smaller image with only what you use.
FROM python:3.14-slim AS build

COPY --from=ghcr.io/astral-sh/uv:0.11 /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app

# Dependencies resolve from the lockfile alone, so this layer is cached until
# the lockfile itself changes rather than on every source edit.
COPY pyproject.toml uv.lock README.md ./
ARG EXTRAS=all
RUN uv sync --frozen --no-dev --no-install-project --extra $(echo "$EXTRAS" | sed "s/,/ --extra /g")

COPY src ./src
RUN uv sync --frozen --no-dev --extra $(echo "$EXTRAS" | sed "s/,/ --extra /g")


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
#
# Binds every interface, unlike the CLI's own default of loopback. A container
# that only answered itself would be a container that does nothing.
CMD ["schemagate", "serve", "--host", "0.0.0.0", "--port", "8000"]

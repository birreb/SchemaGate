from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI

from schemagate import __version__
from schemagate.api.logging import record_requests
from schemagate.api.routes import SchemaSource, router
from schemagate.config import Settings
from schemagate.errors import ConfigurationError
from schemagate.extract.base import Extractor


def create_app(
    settings: Settings | None = None,
    schemas: SchemaSource | None = None,
    extractor: Extractor | None = None,
) -> FastAPI:
    """Build the application.

    Dependencies are constructor arguments rather than module globals, so a test
    can supply a table definition without a database and a model without a
    server. Left out, the real ones are built at startup.
    """
    resolved = settings or Settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        owned = None
        if app.state.schemas is None:
            from schemagate.db.pool import PoolSchemas

            owned = PoolSchemas(resolved)
            app.state.schemas = owned

        if app.state.extractor is None and resolved.provider is not None:
            app.state.extractor = build_extractor(resolved)

        try:
            yield
        finally:
            if owned is not None:
                await owned.close()

    app = FastAPI(title="SchemaGate", version=__version__, lifespan=lifespan)
    app.middleware("http")(record_requests)
    app.state.settings = resolved
    app.state.schemas = schemas
    app.state.extractor = extractor
    app.include_router(router)
    return app


def build_extractor(settings: Settings) -> Extractor:
    """Construct the model client named in configuration.

    API keys are never read here. Each SDK picks up its own standard variable
    (ANTHROPIC_API_KEY, OPENAI_API_KEY), which keeps the credential out of this
    codebase entirely.
    """
    return make_extractor(
        provider=settings.provider or "ollama",
        model=_configured_model(settings),
        base_url=settings.openai_base_url,
        ollama_host=settings.ollama_host,
        timeout=settings.model_timeout_seconds,
    )


def _configured_model(settings: Settings) -> str | None:
    if settings.provider == "anthropic":
        return settings.anthropic_model
    if settings.provider in {"openai", "openai_compatible"}:
        return settings.openai_model
    return settings.ollama_model


def make_extractor(
    provider: str,
    model: str | None = None,
    api_key: str | None = None,
    base_url: str | None = None,
    ollama_host: str = "http://localhost:11434",
    timeout: float = 120.0,
) -> Extractor:
    """Build one extractor from plain values.

    Shared by configuration and by a request that carries its own credentials,
    so both paths construct the client the same way. `api_key` is passed
    straight to the SDK and is never stored, logged, or returned.
    """
    if provider == "anthropic":
        from anthropic import AsyncAnthropic

        from schemagate.config import DEFAULT_ANTHROPIC_MODEL
        from schemagate.extract.anthropic import AnthropicExtractor

        return AnthropicExtractor(
            client=(
                AsyncAnthropic(api_key=api_key, timeout=timeout)
                if api_key
                else AsyncAnthropic(timeout=timeout)
            ),
            model=model or DEFAULT_ANTHROPIC_MODEL,
        )

    if provider in {"openai", "openai_compatible"}:
        from openai import AsyncOpenAI

        from schemagate.extract.openai import OpenAIExtractor

        if not model:
            raise ConfigurationError(
                "This provider needs a model named. OpenAI model names change "
                "often, so one is never guessed."
            )
        if provider == "openai_compatible" and not base_url:
            raise ConfigurationError(
                "An OpenAI-compatible provider needs a base_url, since that is "
                "the only thing distinguishing it from OpenAI itself."
            )
        return OpenAIExtractor(
            client=AsyncOpenAI(api_key=api_key or "unused", base_url=base_url, timeout=timeout),
            model=model,
        )

    if provider != "ollama":
        raise ConfigurationError(
            f"Unknown provider {provider!r}. Choose anthropic, openai, openai_compatible or ollama."
        )

    from ollama import AsyncClient

    from schemagate.config import DEFAULT_OLLAMA_MODEL
    from schemagate.extract.ollama import OllamaExtractor

    return OllamaExtractor(
        client=AsyncClient(host=base_url or ollama_host, timeout=timeout),
        model=model or DEFAULT_OLLAMA_MODEL,
    )


def make_model_client(
    provider: str,
    api_key: str | None = None,
    base_url: str | None = None,
    ollama_host: str = "http://localhost:11434",
) -> Any:
    """Build the raw SDK client used only to enumerate models."""
    if provider == "anthropic":
        from anthropic import AsyncAnthropic

        return AsyncAnthropic(api_key=api_key) if api_key else AsyncAnthropic()

    if provider in {"openai", "openai_compatible"}:
        from openai import AsyncOpenAI

        return AsyncOpenAI(api_key=api_key or "unused", base_url=base_url)

    if provider == "ollama":
        from ollama import AsyncClient

        return AsyncClient(host=base_url or ollama_host)

    raise ConfigurationError(
        f"Unknown provider {provider!r}. Choose anthropic, openai, openai_compatible or ollama."
    )

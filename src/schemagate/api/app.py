from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from schemagate import __version__
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
    app.state.settings = resolved
    app.state.schemas = schemas
    app.state.extractor = extractor
    app.include_router(router)
    return app


def build_extractor(settings: Settings) -> Extractor:
    """Construct the configured model client.

    API keys are never read here. Each SDK picks up its own standard variable
    (ANTHROPIC_API_KEY, OPENAI_API_KEY), which keeps the credential out of this
    codebase entirely.
    """
    if settings.provider == "anthropic":
        from anthropic import AsyncAnthropic

        from schemagate.extract.anthropic import AnthropicExtractor

        return AnthropicExtractor(client=AsyncAnthropic(), model=settings.anthropic_model)

    if settings.provider == "openai":
        from openai import AsyncOpenAI

        from schemagate.extract.openai import OpenAIExtractor

        if not settings.openai_model:
            raise ConfigurationError(
                "SCHEMAGATE_PROVIDER is 'openai' but SCHEMAGATE_OPENAI_MODEL is unset. "
                "OpenAI model names change often, so this one has to be named rather "
                "than guessed."
            )
        return OpenAIExtractor(client=AsyncOpenAI(), model=settings.openai_model)

    from ollama import AsyncClient

    from schemagate.extract.ollama import OllamaExtractor

    return OllamaExtractor(
        client=AsyncClient(host=settings.ollama_host), model=settings.ollama_model
    )

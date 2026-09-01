from typing import Any

from schemagate.config import DEFAULT_EFFORT, Settings
from schemagate.errors import ConfigurationError
from schemagate.extract.base import Extractor
from schemagate.optional import require


def build_extractor(settings: Settings, model: str | None = None) -> Extractor:
    """Construct the model client named in configuration.

    API keys are never read here. Each SDK picks up its own standard variable
    (ANTHROPIC_API_KEY, OPENAI_API_KEY), which keeps the credential out of this
    codebase entirely.
    """
    return make_extractor(
        provider=settings.provider or "ollama",
        model=model or _configured_model(settings),
        base_url=settings.openai_base_url,
        ollama_host=settings.ollama_host,
        timeout=settings.model_timeout_seconds,
        effort=settings.effort,
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
    effort: str | None = DEFAULT_EFFORT,
) -> Extractor:
    """Build one extractor from plain values.

    Shared by configuration and by a request that carries its own credentials,
    so both paths construct the client the same way. `api_key` is passed
    straight to the SDK and is never stored, logged, or returned.

    `effort` defaults to the value the service uses, so a library caller gets
    the same cost as the endpoint. Pass None to send nothing, which is what an
    older model needs.
    """
    if provider == "anthropic":
        from schemagate.config import DEFAULT_ANTHROPIC_MODEL
        from schemagate.extract.anthropic import AnthropicExtractor

        sdk = require("anthropic")
        return AnthropicExtractor(
            client=(
                sdk.AsyncAnthropic(api_key=api_key, timeout=timeout)
                if api_key
                else sdk.AsyncAnthropic(timeout=timeout)
            ),
            model=model or DEFAULT_ANTHROPIC_MODEL,
            effort=effort,
        )

    if provider in {"openai", "openai_compatible"}:
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
        sdk = require("openai")
        return OpenAIExtractor(
            client=sdk.AsyncOpenAI(api_key=api_key or "unused", base_url=base_url, timeout=timeout),
            model=model,
        )

    if provider != "ollama":
        raise ConfigurationError(
            f"Unknown provider {provider!r}. Choose anthropic, openai, openai_compatible or ollama."
        )

    from schemagate.config import DEFAULT_OLLAMA_MODEL
    from schemagate.extract.ollama import OllamaExtractor

    sdk = require("ollama")
    return OllamaExtractor(
        client=sdk.AsyncClient(host=base_url or ollama_host, timeout=timeout),
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
        sdk = require("anthropic")
        return sdk.AsyncAnthropic(api_key=api_key) if api_key else sdk.AsyncAnthropic()

    if provider in {"openai", "openai_compatible"}:
        sdk = require("openai")
        return sdk.AsyncOpenAI(api_key=api_key or "unused", base_url=base_url)

    if provider == "ollama":
        sdk = require("ollama")
        return sdk.AsyncClient(host=base_url or ollama_host)

    raise ConfigurationError(
        f"Unknown provider {provider!r}. Choose anthropic, openai, openai_compatible or ollama."
    )

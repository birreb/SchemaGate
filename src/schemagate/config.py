from typing import Any, Literal

from pydantic import Field, SecretStr, ValidationError, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from schemagate.errors import ConfigurationError, UnknownConnectionError
from schemagate.validate.rules import SumRule

ENV_PREFIX = "SCHEMAGATE_"

DEFAULT_MAX_UPLOAD_BYTES = 10 * 1024 * 1024

DEFAULT_OLLAMA_MODEL = "qwen3"

# Named by the Anthropic SDK docs as current. OpenAI names change often
# enough that defaulting to one would fail later with a model nobody chose,
# so that one has to be given.
DEFAULT_ANTHROPIC_MODEL = "claude-opus-5"

# `openai_compatible` covers everything that speaks the OpenAI API at another
# address: Groq, OpenRouter, Together, DeepSeek, vLLM, LM Studio, and
# Gemini's compatibility endpoint. One adapter rather than one per vendor.
Provider = Literal["ollama", "anthropic", "openai", "openai_compatible"]


class Settings(BaseSettings):
    """Runtime configuration, read from the environment.

    Connection strings are held as secrets so that they cannot reach a log line
    through an accidental repr of this object.
    """

    # The nested delimiter lets a connection be given as
    # SCHEMAGATE_CONNECTIONS__primary=postgresql://... which contains no braces
    # or quotes and so cannot be mangled by an env file parser. The JSON form
    # still works, but it has to be quoted, and uv refuses to read it otherwise.
    model_config = SettingsConfigDict(
        env_prefix=ENV_PREFIX, env_nested_delimiter="__", extra="ignore"
    )

    connections: dict[str, SecretStr]
    max_upload_bytes: int = DEFAULT_MAX_UPLOAD_BYTES
    # Arithmetic checks per table, keyed by qualified name, for example
    # {"public.invoices": [{"terms": ["subtotal", "tax"], "equals": "total"}]}
    rules: dict[str, list[SumRule]] = Field(default_factory=dict)

    # Which model service to extract with. Left unset, documents that need a
    # model are refused rather than silently sent somewhere nobody asked for.
    provider: Provider | None = None
    ollama_host: str = "http://localhost:11434"
    ollama_model: str = DEFAULT_OLLAMA_MODEL
    anthropic_model: str = DEFAULT_ANTHROPIC_MODEL
    openai_model: str | None = None
    openai_base_url: str | None = None

    # Whether a caller may name a provider and supply its key in the request.
    # Off unless the operator turns it on: it is the playground's way to try a
    # provider without editing configuration, and it means a credential
    # crosses HTTP, which is not something a caller gets to decide.
    allow_request_credentials: bool = False

    def __init__(self, **overrides: Any) -> None:
        try:
            super().__init__(**overrides)
        except ValidationError as error:
            raise ConfigurationError(_describe(error)) from error

    @field_validator("connections")
    @classmethod
    def _reject_empty(cls, value: dict[str, SecretStr]) -> dict[str, SecretStr]:
        if not value:
            raise ValueError("at least one connection must be configured")
        return value

    def dsn(self, name: str) -> str:
        """Return the connection string registered under `name`."""
        secret = self.connections.get(name)
        if secret is None:
            known = ", ".join(sorted(self.connections)) or "none"
            raise UnknownConnectionError(
                f"Unknown connection {name!r}. Configured connections: {known}."
            )
        return secret.get_secret_value()

    def rules_for(self, table: str) -> tuple[SumRule, ...]:
        """Arithmetic checks configured for a table, by qualified name."""
        return tuple(self.rules.get(table, ()))


def _describe(error: ValidationError) -> str:
    problems = [
        f"{ENV_PREFIX}{str(detail['loc'][0]).upper() if detail['loc'] else 'UNKNOWN'}"
        f" ({detail['msg']})"
        for detail in error.errors()
    ]
    return "Invalid configuration: " + "; ".join(problems) + "."

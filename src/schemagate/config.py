import hmac
from typing import Any, Literal

from pydantic import Field, SecretStr, ValidationError, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from schemagate.errors import ConfigurationError, UnknownConnectionError
from schemagate.extract.cost import Price
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

Effort = Literal["low", "medium", "high", "xhigh", "max"]

# The current models think by default at high effort. Extraction against a
# compiled schema has a fixed answer shape and nothing to reason about.
DEFAULT_EFFORT: Effort = "low"


class Settings(BaseSettings):
    """Runtime configuration, read from the environment.

    Connection strings and API keys are held as secrets so that they cannot
    reach a log line through an accidental repr of this object.
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

    # Free text passed to the model alongside a document, per table. For the
    # things a schema cannot say: which of two dates is the issue date, what
    # a supplier's own wording means, which page to ignore.
    instructions: dict[str, str] = Field(default_factory=dict)

    # Which model service to extract with. Left unset, documents that need a
    # model are refused rather than silently sent somewhere nobody asked for.
    provider: Provider | None = None
    ollama_host: str = "http://localhost:11434"
    ollama_model: str = DEFAULT_OLLAMA_MODEL
    anthropic_model: str = DEFAULT_ANTHROPIC_MODEL
    openai_model: str | None = None
    openai_base_url: str | None = None

    # How hard the model is asked to think, where the provider exposes that.
    # Set to null to send nothing, which is what an older model needs.
    effort: Effort | None = DEFAULT_EFFORT

    # The model used to match a column heading to a column by meaning, which
    # compares two short lists of names. Unset, the extraction model does both.
    header_model: str | None = None

    # What each model costs, per million tokens, for example
    # {"claude-opus-5": {"input": 5, "output": 25, "cached_input": 0.5}}
    #
    # Configuration rather than a table in this repository: a hardcoded price
    # goes stale and nothing here would detect it. A model with no price still
    # reports its tokens.
    prices: dict[str, Price] = Field(default_factory=dict)

    # Keys a caller must present, as `Authorization: Bearer ...` or `X-API-Key`.
    # Empty means the endpoints are open. Every extraction spends money, so an
    # instance reachable by more than your own application should set these.
    api_keys: list[SecretStr] = Field(default_factory=list)

    # Requests per key per minute, 0 for no limit. Applied per key when keys
    # are configured and per client address when they are not.
    rate_limit_per_minute: int = 0

    # How many documents may be in flight at once. Each holds its upload, its
    # rendered pages and its answer in memory, and OCR is CPU bound.
    max_concurrent_extractions: int = 8

    # Whether a caller may name a provider and supply its key in the request.
    # Off unless the operator turns it on: it is the playground's way to try a
    # provider without editing configuration, and it means a credential
    # crosses HTTP, which is not something a caller gets to decide.
    allow_request_credentials: bool = False

    # Both SDKs default to ten minutes, which holds a worker and a connection
    # on a provider that has stopped answering. A document that has not been
    # read in two minutes is not going to be.
    model_timeout_seconds: float = 120.0
    database_timeout_seconds: float = 10.0

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

    @field_validator("api_keys", mode="before")
    @classmethod
    def _split_keys(cls, value: Any) -> Any:
        """Accept a comma-separated list as well as JSON.

        Pydantic reads a list-valued environment variable as JSON. A
        comma-separated string is the more common spelling, so both work.
        """
        if isinstance(value, str) and not value.strip().startswith("["):
            return [part.strip() for part in value.split(",") if part.strip()]
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

    def instructions_for(self, table: str) -> str | None:
        """Guidance configured for a table, by qualified name."""
        return self.instructions.get(table) or None

    @property
    def authenticated(self) -> bool:
        return bool(self.api_keys)

    def accepts(self, presented: str | None) -> bool:
        """Whether a presented key is one of the configured ones.

        Compared with `hmac.compare_digest`, and every configured key is
        compared even after one matches, so the time taken does not depend on
        which key matched or on how far a wrong one got.
        """
        if not self.api_keys:
            return True
        if not presented:
            return False
        found = False
        for key in self.api_keys:
            if hmac.compare_digest(presented, key.get_secret_value()):
                found = True
        return found


def _describe(error: ValidationError) -> str:
    problems = [
        f"{ENV_PREFIX}{str(detail['loc'][0]).upper() if detail['loc'] else 'UNKNOWN'}"
        f" ({detail['msg']})"
        for detail in error.errors()
    ]
    return "Invalid configuration: " + "; ".join(problems) + "."

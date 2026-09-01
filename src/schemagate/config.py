from typing import Any

from pydantic import Field, SecretStr, ValidationError, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from schemagate.errors import ConfigurationError, UnknownConnectionError
from schemagate.validate.rules import SumRule

ENV_PREFIX = "SCHEMAGATE_"

DEFAULT_MAX_UPLOAD_BYTES = 10 * 1024 * 1024

DEFAULT_OLLAMA_MODEL = "qwen3"


class Settings(BaseSettings):
    """Runtime configuration, read from the environment.

    Connection strings are held as secrets so that they cannot reach a log line
    through an accidental repr of this object.
    """

    model_config = SettingsConfigDict(env_prefix=ENV_PREFIX, extra="ignore")

    connections: dict[str, SecretStr]
    max_upload_bytes: int = DEFAULT_MAX_UPLOAD_BYTES
    # Arithmetic checks per table, keyed by qualified name, for example
    # {"public.invoices": [{"terms": ["subtotal", "tax"], "equals": "total"}]}
    rules: dict[str, list[SumRule]] = Field(default_factory=dict)

    # Where a local Ollama server is listening. Left unset, documents that need
    # a model are refused rather than silently sent anywhere.
    ollama_host: str | None = None
    ollama_model: str = DEFAULT_OLLAMA_MODEL

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

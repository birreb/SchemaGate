from typing import Any

from pydantic import SecretStr, ValidationError, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from schemagate.errors import ConfigurationError, UnknownConnectionError

ENV_PREFIX = "SCHEMAGATE_"

DEFAULT_MAX_UPLOAD_BYTES = 10 * 1024 * 1024


class Settings(BaseSettings):
    """Runtime configuration, read from the environment.

    Connection strings are held as secrets so that they cannot reach a log line
    through an accidental repr of this object.
    """

    model_config = SettingsConfigDict(env_prefix=ENV_PREFIX, extra="ignore")

    connections: dict[str, SecretStr]
    max_upload_bytes: int = DEFAULT_MAX_UPLOAD_BYTES

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


def _describe(error: ValidationError) -> str:
    problems = [
        f"{ENV_PREFIX}{str(detail['loc'][0]).upper() if detail['loc'] else 'UNKNOWN'}"
        f" ({detail['msg']})"
        for detail in error.errors()
    ]
    return "Invalid configuration: " + "; ".join(problems) + "."

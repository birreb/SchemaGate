import json

import pytest

from schemagate.config import Settings
from schemagate.errors import ConfigurationError, UnknownConnectionError

DSN = "postgresql://user:hunter2@localhost:5432/billing"


def test_resolves_a_named_connection_to_its_dsn(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SCHEMAGATE_CONNECTIONS", json.dumps({"primary": DSN}))

    settings = Settings()

    assert settings.dsn("primary") == DSN


def test_rejects_an_unknown_connection_name(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SCHEMAGATE_CONNECTIONS", json.dumps({"primary": DSN}))

    settings = Settings()

    with pytest.raises(UnknownConnectionError) as caught:
        settings.dsn("reporting")

    message = str(caught.value)
    assert "reporting" in message
    assert "primary" in message, "the error should name the connections that do exist"


def test_missing_connections_names_the_variable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SCHEMAGATE_CONNECTIONS", raising=False)

    with pytest.raises(ConfigurationError) as caught:
        Settings()

    assert "SCHEMAGATE_CONNECTIONS" in str(caught.value)


def test_at_least_one_connection_is_required(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SCHEMAGATE_CONNECTIONS", json.dumps({}))

    with pytest.raises(ConfigurationError):
        Settings()


def test_dsn_does_not_leak_through_repr(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SCHEMAGATE_CONNECTIONS", json.dumps({"primary": DSN}))

    settings = Settings()

    for rendered in (repr(settings), str(settings), repr(settings.connections)):
        assert "hunter2" not in rendered
        assert DSN not in rendered


def test_upload_limit_defaults_and_is_overridable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SCHEMAGATE_CONNECTIONS", json.dumps({"primary": DSN}))
    monkeypatch.delenv("SCHEMAGATE_MAX_UPLOAD_BYTES", raising=False)

    assert Settings().max_upload_bytes == 10 * 1024 * 1024

    monkeypatch.setenv("SCHEMAGATE_MAX_UPLOAD_BYTES", "2048")

    assert Settings().max_upload_bytes == 2048

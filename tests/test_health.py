import pytest
from fastapi.testclient import TestClient

from schemagate.api.app import create_app
from schemagate.config import Settings
from schemagate.errors import ConfigurationError


def test_health_reports_ok() -> None:
    app = create_app(settings=Settings(connections={"primary": "postgresql://localhost/db"}))

    response = TestClient(app).get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_an_unconfigured_app_refuses_to_be_built(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SCHEMAGATE_CONNECTIONS", raising=False)

    with pytest.raises(ConfigurationError) as caught:
        create_app()

    assert "SCHEMAGATE_CONNECTIONS" in str(caught.value), (
        "a misconfigured deployment should refuse to start naming the missing "
        "variable, rather than returning 500s an hour later"
    )

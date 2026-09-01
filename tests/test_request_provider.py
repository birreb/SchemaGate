from typing import Any

from fastapi.testclient import TestClient

from schemagate.api.app import create_app
from schemagate.config import Settings
from schemagate.schema.spec import ColumnSpec, TableRef, TableSchema

DSN = "postgresql://user:password@localhost:5432/billing"
KEY = "sk-test-not-a-real-key-000"

INVOICES = TableSchema(
    schema="public",
    name="invoices",
    columns=(ColumnSpec(name="invoice_number", data_type="text", nullable=True, ordinal=1),),
)

CSV = b"invoice_number\nINV-1\n"


class FakeSchemas:
    async def fetch(self, connection: str, schema: str, table: str) -> TableSchema:
        return INVOICES

    async def tables(self, connection: str) -> tuple[TableRef, ...]:
        return ()


def client(**overrides: Any) -> TestClient:
    settings = Settings(connections={"primary": DSN}, **overrides)
    return TestClient(
        create_app(settings=settings, schemas=FakeSchemas()), raise_server_exceptions=False
    )


def upload(**form: str) -> dict[str, Any]:
    return {
        "files": {"file": ("invoices.csv", CSV, "text/csv")},
        "data": {"connection": "primary", "table": "invoices", **form},
    }


def test_credentials_in_a_request_are_refused_by_default() -> None:
    response = client().post(
        "/v1/extract", **upload(provider="anthropic", api_key=KEY, model="claude-opus-5")
    )

    assert response.status_code == 403, (
        "accepting a credential over HTTP has to be something the operator "
        "turned on, not something any caller can do"
    )


def test_the_refusal_names_the_setting_that_allows_it() -> None:
    response = client().post("/v1/extract", **upload(provider="anthropic", api_key=KEY))

    assert "SCHEMAGATE_ALLOW_REQUEST_CREDENTIALS" in response.text


def test_a_key_in_a_request_is_never_echoed_back() -> None:
    response = client().post("/v1/extract", **upload(provider="anthropic", api_key=KEY))

    assert KEY not in response.text, "an error body is the easiest place for a key to escape"


def test_a_provider_may_be_chosen_when_the_operator_allows_it() -> None:
    subject = client(allow_request_credentials=True)

    # A tabular file needs no model, so this proves the request was accepted
    # rather than that the provider worked.
    response = subject.post(
        "/v1/extract", **upload(provider="anthropic", api_key=KEY, model="claude-opus-5")
    )

    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_choosing_openai_still_requires_naming_a_model() -> None:
    subject = client(allow_request_credentials=True)

    response = subject.post("/v1/extract", **upload(provider="openai", api_key=KEY))

    assert response.status_code == 422
    assert "model" in response.text.lower()


def test_an_openai_compatible_endpoint_needs_a_base_url() -> None:
    subject = client(allow_request_credentials=True)

    response = subject.post(
        "/v1/extract", **upload(provider="openai_compatible", api_key=KEY, model="llama-3.3")
    )

    assert response.status_code == 422
    assert "base_url" in response.text.lower()


def test_an_unknown_provider_is_rejected() -> None:
    subject = client(allow_request_credentials=True)

    response = subject.post("/v1/extract", **upload(provider="wishful", api_key=KEY))

    assert response.status_code == 422


def test_the_server_still_works_with_no_provider_in_the_request() -> None:
    response = client().post("/v1/extract", **upload())

    assert response.status_code == 200

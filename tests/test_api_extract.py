import json
import re
from typing import Any

import pytest
from fastapi.testclient import TestClient
from fpdf import FPDF

from schemagate.api.app import create_app
from schemagate.config import Settings
from schemagate.errors import ConfigurationError, TableNotFoundError
from schemagate.extract.base import ModelT
from schemagate.schema.spec import ColumnSpec, TableRef, TableSchema

DSN = "postgresql://user:password@localhost:5432/billing"

INVOICES = TableSchema(
    schema="public",
    name="invoices",
    columns=(
        ColumnSpec(name="id", data_type="int8", nullable=False, ordinal=1, is_identity=True),
        ColumnSpec(name="invoice_number", data_type="text", nullable=False, ordinal=2),
        ColumnSpec(
            name="subtotal", data_type="numeric", nullable=False, ordinal=3, numeric_scale=2
        ),
        ColumnSpec(name="issued_on", data_type="date", nullable=True, ordinal=4),
    ),
)

CSV = b"invoice_number,subtotal,issued_on\nINV-1,1234.56,2026-01-05\n"


class FakeSchemas:
    async def fetch(self, connection: str, schema: str, table: str) -> TableSchema:
        if table != "invoices":
            raise TableNotFoundError(f"Table {schema}.{table} does not exist.")
        return INVOICES

    async def tables(self, connection: str) -> tuple[TableRef, ...]:
        return (TableRef(schema="public", name="invoices", kind="table"),)


class StubExtractor:
    async def extract(self, document: str, model: type[ModelT]) -> ModelT:
        return model.model_validate({"rows": []})


def client(**overrides: Any) -> TestClient:
    settings = Settings(connections={"primary": DSN}, **overrides)
    app = create_app(settings=settings, schemas=FakeSchemas(), extractor=StubExtractor())
    return TestClient(app, raise_server_exceptions=False)


def upload(data: bytes = CSV, name: str = "invoices.csv", **form: str) -> dict[str, Any]:
    return {
        "files": {"file": (name, data, "text/csv")},
        "data": {"connection": "primary", "table": "invoices", **form},
    }


def test_a_csv_comes_back_as_rows() -> None:
    response = client().post("/v1/extract", **upload())

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["table"] == "public.invoices"
    assert body["route"] == "tabular"
    assert body["rows"][0]["invoice_number"] == "INV-1"


def test_money_leaves_as_a_string() -> None:
    response = client().post("/v1/extract", **upload())

    raw = json.loads(response.text)["rows"][0]["subtotal"]

    assert raw == "1234.56"
    assert isinstance(raw, str), (
        "a JSON number becomes a float in every client parser, which throws away "
        "the exactness the whole pipeline exists to preserve"
    )


def test_dates_leave_as_iso_strings() -> None:
    body = client().post("/v1/extract", **upload()).json()

    assert body["rows"][0]["issued_on"] == "2026-01-05"


def test_columns_the_database_owns_are_absent() -> None:
    body = client().post("/v1/extract", **upload()).json()

    assert "id" not in body["rows"][0]


def test_a_failed_check_is_reported_without_being_an_error() -> None:
    data = b"invoice_number,subtotal,issued_on\nINV-1,not a number,2026-01-05\n"

    response = client().post("/v1/extract", **upload(data))

    assert response.status_code == 200, "extraction worked; a check did not hold"
    body = response.json()
    assert body["status"] == "flagged"
    assert body["validation"]["failures"][0]["column"] == "subtotal"
    assert body["validation"]["failures"][0]["row"] == 0


def test_an_unknown_connection_is_the_callers_mistake() -> None:
    response = client().post("/v1/extract", **upload(connection="nope"))

    assert response.status_code == 400


def test_an_unknown_table_is_not_found() -> None:
    response = client().post("/v1/extract", **upload(table="ghosts"))

    assert response.status_code == 404


def test_an_unsupported_file_type_says_so() -> None:
    response = client().post("/v1/extract", **upload(b"\x00\x01\x02 nonsense", "thing.bin"))

    assert response.status_code == 415


def test_an_oversized_upload_is_refused() -> None:
    subject = client(max_upload_bytes=16)

    response = subject.post("/v1/extract", **upload())

    assert response.status_code == 413


def test_a_request_without_a_file_is_rejected() -> None:
    response = client().post("/v1/extract", data={"connection": "primary", "table": "invoices"})

    assert response.status_code == 422


def test_the_error_body_never_contains_the_connection_string() -> None:
    response = client().post("/v1/extract", **upload(table="ghosts"))

    assert "password" not in response.text
    assert DSN not in response.text


def test_timings_are_reported() -> None:
    body = client().post("/v1/extract", **upload()).json()

    assert "parse" in body["timings_ms"]


def test_health_still_answers() -> None:
    assert client().get("/health").json() == {"status": "ok"}


@pytest.mark.parametrize("schema_name", ["public", "billing"])
def test_the_schema_defaults_to_public_and_can_be_given(schema_name: str) -> None:
    response = client().post("/v1/extract", **upload(schema=schema_name))

    assert response.status_code == 200


def test_no_extractor_is_configured_by_default() -> None:
    app = create_app(settings=Settings(connections={"primary": DSN}), schemas=FakeSchemas())

    with TestClient(app):
        assert app.state.extractor is None, (
            "with no model server configured, documents needing one are refused "
            "rather than sent somewhere nobody asked for"
        )


def test_an_extractor_is_built_when_a_provider_is_configured() -> None:
    settings = Settings(connections={"primary": DSN}, provider="ollama", ollama_model="qwen3")
    app = create_app(settings=settings, schemas=FakeSchemas())

    with TestClient(app):
        assert app.state.extractor is not None


def test_the_playground_is_served_at_the_root() -> None:
    response = client().get("/")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "SchemaGate" in response.text


def test_the_playground_loads_nothing_from_the_internet() -> None:
    page = client().get("/").text

    for attribute in ('src="http', "src='http", 'href="http', "href='http", "@import"):
        assert attribute not in page, (
            "the service is meant to run inside a private network, so a page that "
            "fetched a font or a script from the internet would not load there"
        )


def test_the_playground_only_calls_its_own_endpoints() -> None:
    page = client().get("/").text

    calls = re.findall(r'fetch\(\s*["\'`]([^"\'`]+)', page)

    assert calls, "the page does call the API"
    assert all(target.startswith("/") for target in calls), (
        f"every request must be relative to this service, got {calls}"
    )


def test_an_unreachable_database_is_not_an_internal_error() -> None:
    from schemagate.errors import DatabaseUnavailableError

    class DownSchemas:
        async def fetch(self, connection: str, schema: str, table: str) -> TableSchema:
            raise DatabaseUnavailableError(f"Cannot reach the database for {connection!r}.")

        async def tables(self, connection: str) -> tuple[TableRef, ...]:
            raise DatabaseUnavailableError(f"Cannot reach the database for {connection!r}.")

    app = create_app(
        settings=Settings(connections={"primary": DSN}),
        schemas=DownSchemas(),
        extractor=StubExtractor(),
    )

    response = TestClient(app, raise_server_exceptions=False).post("/v1/extract", **upload())

    assert response.status_code == 503, (
        "the database being down is not the caller's fault and not a bug in us"
    )
    assert "password" not in response.text


def test_a_document_needing_a_model_is_not_the_callers_fault() -> None:
    """415 blames the upload. A PDF is supported; the server is just not set up."""
    app = create_app(settings=Settings(connections={"primary": DSN}), schemas=FakeSchemas())
    subject = TestClient(app, raise_server_exceptions=False)

    document = FPDF()
    document.add_page()
    document.set_font("helvetica", size=12)
    document.cell(0, 8, "Invoice INV-1 total 10.00")

    response = subject.post(
        "/v1/extract",
        files={"file": ("invoice.pdf", bytes(document.output()), "application/pdf")},
        data={"connection": "primary", "table": "invoices"},
    )

    assert response.status_code == 503, (
        "the caller uploaded a supported file type and could not have known "
        "the server has no model configured"
    )


def test_anthropic_is_selected_by_configuration(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-not-real")
    settings = Settings(connections={"primary": DSN}, provider="anthropic")

    app = create_app(settings=settings, schemas=FakeSchemas())

    with TestClient(app):
        from schemagate.extract.anthropic import AnthropicExtractor

        assert isinstance(app.state.extractor, AnthropicExtractor)


def test_openai_needs_a_model_named(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key-not-real")
    settings = Settings(connections={"primary": DSN}, provider="openai")

    with (
        pytest.raises(ConfigurationError),
        TestClient(create_app(settings=settings, schemas=FakeSchemas())),
    ):
        pass


def test_the_playground_offers_every_provider() -> None:
    page = client().get("/").text

    for provider in ("anthropic", "openai", "openai_compatible", "ollama"):
        assert f'value="{provider}"' in page


def test_the_playground_hides_the_key_field_contents() -> None:
    page = client().get("/").text

    assert 'type="password"' in page, "an API key should not be readable over a shoulder"


def test_the_playground_asks_the_provider_for_its_models() -> None:
    page = client().get("/").text

    assert "/v1/models" in page, (
        "a hardcoded list would go stale and would not reflect what a given "
        "key is entitled to"
    )

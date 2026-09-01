import logging
from typing import Any

import pytest
from fastapi.testclient import TestClient

from schemagate.api.app import create_app
from schemagate.config import Settings
from schemagate.schema.spec import ColumnSpec, TableRef, TableSchema

DSN = "postgresql://user:hunter2@localhost:5432/billing"

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
    app = create_app(settings=settings, schemas=FakeSchemas())
    return TestClient(app, raise_server_exceptions=False)


def extract_line(caplog: pytest.LogCaptureFixture) -> dict[str, Any]:
    """The line logged for the extract call, as a plain dict.

    Read through __dict__ because the fields are attached with `extra`, which
    a LogRecord carries but does not declare.
    """
    record = next(r for r in caplog.records if r.__dict__.get("path") == "/v1/extract")
    return dict(record.__dict__)


def upload() -> dict[str, Any]:
    return {
        "files": {"file": ("invoices.csv", CSV, "text/csv")},
        "data": {"connection": "primary", "table": "invoices"},
    }


def test_a_request_is_logged(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.INFO, logger="schemagate"):
        client().post("/v1/extract", **upload())

    assert caplog.records, "a service people run should say what it did"


def test_the_log_says_what_happened(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.INFO, logger="schemagate"):
        client().post("/v1/extract", **upload())

    line = extract_line(caplog)
    assert line["status"] == 200
    assert line["table"] == "public.invoices"
    assert line["route"] == "tabular"
    assert line["rows"] == 1
    assert line["duration_ms"] >= 0


def test_every_request_carries_an_id(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.INFO, logger="schemagate"):
        response = client().post("/v1/extract", **upload())

    line = extract_line(caplog)
    assert line["request_id"]
    assert response.headers["x-request-id"] == line["request_id"], (
        "the caller needs the same id to quote when reporting a problem"
    )


def test_a_supplied_request_id_is_kept(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.INFO, logger="schemagate"):
        response = client().post(
            "/v1/extract", **upload(), headers={"x-request-id": "from-the-caller"}
        )

    assert response.headers["x-request-id"] == "from-the-caller", (
        "a gateway upstream has usually assigned one already"
    )


def test_nothing_secret_is_ever_logged(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.DEBUG, logger="schemagate"):
        client().post("/v1/extract", **upload())

    written = "\n".join(f"{r.getMessage()} {r.__dict__}" for r in caplog.records)
    assert "hunter2" not in written
    assert DSN not in written
    assert "INV-1" not in written, "document content is the customer's, not ours to log"


def test_a_failed_request_is_logged_with_its_status(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.INFO, logger="schemagate"):
        client().post(
            "/v1/extract",
            files={"file": ("x.bin", b"\x00\x01\x02 nonsense", "application/octet-stream")},
            data={"connection": "primary", "table": "invoices"},
        )

    assert extract_line(caplog)["status"] == 415


def test_timeouts_are_configurable() -> None:
    settings = Settings(
        connections={"primary": DSN}, model_timeout_seconds=12.5, database_timeout_seconds=3.0
    )

    assert settings.model_timeout_seconds == 12.5
    assert settings.database_timeout_seconds == 3.0


def test_timeouts_have_sane_defaults() -> None:
    settings = Settings(connections={"primary": DSN})

    assert 0 < settings.model_timeout_seconds <= 300, (
        "the SDKs default to ten minutes, which holds a worker on a hung provider"
    )
    assert 0 < settings.database_timeout_seconds <= 60

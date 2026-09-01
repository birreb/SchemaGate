from typing import Any

from fastapi.testclient import TestClient

from schemagate.api.app import create_app
from schemagate.config import Settings
from schemagate.db.introspect import to_table_ref
from schemagate.schema.spec import TableRef, TableSchema

DSN = "postgresql://user:password@localhost:5432/billing"

TABLES = (
    TableRef(schema="public", name="invoices", kind="table"),
    TableRef(schema="public", name="recent_invoices", kind="view"),
    TableRef(schema="billing", name="payments", kind="table"),
)


class FakeSchemas:
    async def fetch(self, connection: str, schema: str, table: str) -> TableSchema:
        raise AssertionError("not used here")

    async def tables(self, connection: str) -> tuple[TableRef, ...]:
        return TABLES


def client(**overrides: Any) -> TestClient:
    settings = Settings(connections={"primary": DSN, "reporting": DSN}, **overrides)
    app = create_app(settings=settings, schemas=FakeSchemas())
    return TestClient(app, raise_server_exceptions=False)


def test_the_configured_connections_are_listed_by_name() -> None:
    body = client().get("/v1/connections").json()

    assert body["connections"] == ["primary", "reporting"]


def test_listing_connections_never_reveals_a_connection_string() -> None:
    response = client().get("/v1/connections")

    assert "password" not in response.text
    assert DSN not in response.text


def test_tables_are_listed_for_a_connection() -> None:
    body = client().get("/v1/tables", params={"connection": "primary"}).json()

    assert {"schema": "public", "name": "invoices", "kind": "table"} in body["tables"]


def test_views_are_listed_too_and_marked_as_such() -> None:
    body = client().get("/v1/tables", params={"connection": "primary"}).json()

    kinds = {entry["name"]: entry["kind"] for entry in body["tables"]}
    assert kinds["recent_invoices"] == "view", (
        "a view is extractable, and knowing which is which is worth showing"
    )


def test_an_unknown_connection_is_rejected() -> None:
    response = client().get("/v1/tables", params={"connection": "nope"})

    assert response.status_code == 400


def test_a_catalog_row_becomes_a_table_reference() -> None:
    ref = to_table_ref({"schema": "public", "name": "invoices", "relkind": "r"})

    assert ref == TableRef(schema="public", name="invoices", kind="table")


def test_every_relation_kind_is_named_in_words() -> None:
    kinds = {
        "r": "table",
        "p": "partitioned table",
        "v": "view",
        "m": "materialized view",
        "f": "foreign table",
    }
    for letter, word in kinds.items():
        ref = to_table_ref({"schema": "s", "name": "n", "relkind": letter})
        assert ref.kind == word

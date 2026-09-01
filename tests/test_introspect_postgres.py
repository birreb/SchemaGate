from __future__ import annotations

import asyncpg
import pytest

from schemagate.db.introspect import introspect
from schemagate.errors import TableNotFoundError
from schemagate.schema.factory import build_row_model
from schemagate.schema.spec import ColumnSpec, TableSchema

pytestmark = pytest.mark.postgres

SCHEMA = "schemagate_introspection_test"

FIXTURE_SQL = f"""
DROP SCHEMA IF EXISTS {SCHEMA} CASCADE;
CREATE SCHEMA {SCHEMA};

CREATE TYPE {SCHEMA}.invoice_status AS ENUM ('draft', 'sent', 'paid');

CREATE TABLE {SCHEMA}.invoices (
    id             bigint GENERATED ALWAYS AS IDENTITY,
    invoice_number varchar(50) NOT NULL,
    vat_id         text,
    status         {SCHEMA}.invoice_status NOT NULL,
    subtotal       numeric(12, 2) NOT NULL,
    tax            numeric(12, 2) NOT NULL,
    total          numeric(12, 2) GENERATED ALWAYS AS (subtotal + tax) STORED,
    tags           text[],
    issued_on      date NOT NULL,
    created_at     timestamptz NOT NULL DEFAULT now()
);

COMMENT ON COLUMN {SCHEMA}.invoices.vat_id IS 'Seller VAT number, not the buyer';
"""


@pytest.fixture
async def invoices(connection: asyncpg.Connection[asyncpg.Record]) -> TableSchema:
    await connection.execute(FIXTURE_SQL)
    return await introspect(connection, SCHEMA, "invoices")


def column(schema: TableSchema, name: str) -> ColumnSpec:
    return next(candidate for candidate in schema.columns if candidate.name == name)


async def test_reads_every_column_in_declaration_order(invoices: TableSchema) -> None:
    assert [c.name for c in invoices.ordered] == [
        "id",
        "invoice_number",
        "vat_id",
        "status",
        "subtotal",
        "tax",
        "total",
        "tags",
        "issued_on",
        "created_at",
    ]


async def test_reads_column_comments(invoices: TableSchema) -> None:
    assert column(invoices, "vat_id").description == "Seller VAT number, not the buyer"


async def test_reads_enum_labels_in_declaration_order(invoices: TableSchema) -> None:
    status = column(invoices, "status")

    assert status.enum_labels == ("draft", "sent", "paid")
    assert status.data_type == "invoice_status"


async def test_reads_array_types_with_the_postgres_underscore_convention(
    invoices: TableSchema,
) -> None:
    assert column(invoices, "tags").data_type == "_text"


async def test_reads_varchar_length(invoices: TableSchema) -> None:
    assert column(invoices, "invoice_number").max_length == 50


async def test_reads_nullability(invoices: TableSchema) -> None:
    assert column(invoices, "vat_id").nullable is True
    assert column(invoices, "invoice_number").nullable is False


async def test_flags_identity_generated_and_defaulted_columns(invoices: TableSchema) -> None:
    assert column(invoices, "id").is_identity is True
    assert column(invoices, "total").is_generated is True
    assert column(invoices, "created_at").has_default is True


async def test_the_database_keeps_the_columns_it_owns(invoices: TableSchema) -> None:
    assert [c.name for c in invoices.extractable] == [
        "invoice_number",
        "vat_id",
        "status",
        "subtotal",
        "tax",
        "tags",
        "issued_on",
    ]


async def test_a_real_table_compiles_into_a_usable_model(invoices: TableSchema) -> None:
    schema = build_row_model(invoices).model_json_schema()

    assert schema["additionalProperties"] is False
    assert sorted(schema["required"]) == sorted(schema["properties"])
    assert schema["properties"]["subtotal"]["type"] == "string", "numeric must not become a float"
    assert schema["properties"]["status"]["enum"] == ["draft", "sent", "paid"]
    assert schema["properties"]["tags"]["anyOf"][0]["type"] == "array"
    assert schema["properties"]["vat_id"]["description"] == "Seller VAT number, not the buyer", (
        "a column comment has to survive all the way into the schema the model sees"
    )


async def test_an_unknown_table_is_reported_by_name(
    connection: asyncpg.Connection[asyncpg.Record],
) -> None:
    with pytest.raises(TableNotFoundError) as caught:
        await introspect(connection, SCHEMA, "does_not_exist")

    assert "does_not_exist" in str(caught.value)


async def test_a_view_is_introspectable_like_a_table(
    connection: asyncpg.Connection[asyncpg.Record], invoices: TableSchema
) -> None:
    await connection.execute(
        f"CREATE VIEW {SCHEMA}.recent AS SELECT invoice_number, total FROM {SCHEMA}.invoices"
    )

    schema = await introspect(connection, SCHEMA, "recent")

    assert [c.name for c in schema.ordered] == ["invoice_number", "total"]

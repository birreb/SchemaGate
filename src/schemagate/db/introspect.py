from collections.abc import Sequence
from typing import Any, Protocol

from schemagate.errors import TableNotFoundError
from schemagate.schema.spec import ColumnSpec, TableSchema

# Read from pg_catalog rather than information_schema. The standard views report
# every enum column as USER-DEFINED, carry no column comments at all, and apply
# permission-filtering joins that the catalog tables do not.
#
# `base` resolves to the element type for arrays and to the type itself
# otherwise, so an enum column and an array of that enum both find their labels.
INTROSPECTION_SQL = """
SELECT
    a.attname                                    AS name,
    t.typname                                    AS data_type,
    NOT a.attnotnull                             AS nullable,
    a.attnum::int                                AS ordinal,
    col_description(a.attrelid, a.attnum)        AS description,
    CASE
        WHEN base.typtype = 'e' THEN ARRAY(
            SELECT e.enumlabel
            FROM pg_enum e
            WHERE e.enumtypid = base.oid
            ORDER BY e.enumsortorder
        )
    END                                          AS enum_labels,
    CASE
        WHEN t.typname IN ('varchar', 'bpchar') AND a.atttypmod > 0
        THEN a.atttypmod - 4
    END                                          AS max_length,
    -- Decimal places the column accepts. Integers hold none, and numeric(p,s)
    -- encodes s in the low half of atttypmod, the same way information_schema
    -- computes it. A bare `numeric` declares no scale and reports null.
    CASE
        WHEN t.typname IN ('int2', 'int4', 'int8') THEN 0
        WHEN t.typname IN ('numeric', 'decimal') AND a.atttypmod <> -1
        THEN (a.atttypmod - 4) & 65535
    END                                          AS numeric_scale,
    a.atthasdef                                  AS has_default,
    a.attgenerated <> ''                         AS is_generated,
    a.attidentity <> ''                          AS is_identity
FROM pg_attribute a
JOIN pg_class c ON c.oid = a.attrelid
JOIN pg_namespace n ON n.oid = c.relnamespace
JOIN pg_type t ON t.oid = a.atttypid
JOIN pg_type base ON base.oid = CASE
    WHEN t.typelem <> 0 AND t.typlen = -1 THEN t.typelem
    ELSE t.oid
END
WHERE n.nspname = $1
  AND c.relname = $2
  AND c.relkind IN ('r', 'p', 'v', 'm', 'f')
  AND a.attnum > 0
  AND NOT a.attisdropped
ORDER BY a.attnum
"""


class Row(Protocol):
    """Column access by name.

    Satisfied by both `asyncpg.Record` and a plain dict, which keeps the mapping
    below unit-testable without a database.
    """

    def __getitem__(self, key: str, /) -> Any: ...


class Queryable(Protocol):
    """The slice of an asyncpg connection this module needs."""

    async def fetch(self, query: str, *args: object) -> Sequence[Row]: ...


async def introspect(connection: Queryable, schema: str, table: str) -> TableSchema:
    """Read the definition of `schema.table` from the live database.

    Names are passed as query parameters, never interpolated. Because discovery
    runs before anything else touches the table, the catalog doubles as an
    allowlist: a name that is not there never reaches another statement.
    """
    records = await connection.fetch(INTROSPECTION_SQL, schema, table)
    if not records:
        raise TableNotFoundError(
            f"Table {schema}.{table} does not exist, or the configured role cannot see it."
        )
    return TableSchema(
        schema=schema,
        name=table,
        columns=tuple(to_column_spec(record) for record in records),
    )


def to_column_spec(record: Row) -> ColumnSpec:
    """Turn one catalog row into a ColumnSpec."""
    return ColumnSpec(
        name=record["name"],
        data_type=record["data_type"],
        nullable=record["nullable"],
        ordinal=record["ordinal"],
        description=record["description"],
        enum_labels=tuple(record["enum_labels"] or ()),
        max_length=record["max_length"],
        numeric_scale=record["numeric_scale"],
        has_default=record["has_default"],
        is_generated=record["is_generated"],
        is_identity=record["is_identity"],
    )

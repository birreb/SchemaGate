import re
from dataclasses import replace
from functools import lru_cache
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, create_model

from schemagate.errors import UnsupportedColumnTypeError
from schemagate.schema.spec import ColumnSpec, TableSchema

# Exact numerics are carried as strings and parsed to Decimal by the validation
# gate. Binding them to float here would round money before anyone could check it.
SCALAR_TYPES: dict[str, type] = {
    "bool": bool,
    "bpchar": str,
    "char": str,
    "date": str,
    "decimal": str,
    "float4": float,
    "float8": float,
    "int2": int,
    "int4": int,
    "int8": int,
    "name": str,
    "numeric": str,
    "text": str,
    "time": str,
    "timestamp": str,
    "timestamptz": str,
    "timetz": str,
    "uuid": str,
    "varchar": str,
}

_MODEL_CONFIG = ConfigDict(extra="forbid")

# The instructions tell a model to copy values exactly, which is what keeps it
# from reformatting a number. That leaves dates in whatever the document used,
# and "01 September 2026" is a perfectly reasonable thing to copy. The column
# knows what it wants, so it says so.
FORMAT_HINTS = {
    "date": "Format as YYYY-MM-DD.",
    "timestamp": "Format as YYYY-MM-DDTHH:MM:SS.",
    "timestamptz": "Format as YYYY-MM-DDTHH:MM:SS with an offset.",
    "time": "Format as HH:MM:SS.",
    "timetz": "Format as HH:MM:SS with an offset.",
}


@lru_cache(maxsize=256)
def build_row_model(table: TableSchema) -> type[BaseModel]:
    """Compile one row of `table` into a Pydantic model.

    Results are cached on the table definition, so an unchanged table returns
    the identical class and the provider can reuse its compiled schema.
    """
    columns = table.extractable
    if not columns:
        raise UnsupportedColumnTypeError(
            f"Table {table.qualified_name} has no columns to extract. Every column is "
            f"generated, an identity column, or NOT NULL with a default."
        )

    fields: dict[str, Any] = {
        column.name: (_annotation_for(column), Field(description=_describe(column)))
        for column in columns
    }
    return create_model(_model_name(table, "Row"), __config__=_MODEL_CONFIG, **fields)


@lru_cache(maxsize=256)
def build_container_model(table: TableSchema) -> type[BaseModel]:
    """Wrap the row model in the object sent to the provider.

    Structured output has to be a JSON object at the top level, so a bare array
    of rows is not a valid response format.
    """
    row_model = build_row_model(table)
    rows = (
        list[row_model],  # type: ignore[valid-type]
        Field(description=f"Rows found in the document, for {table.qualified_name}."),
    )
    return create_model(_model_name(table, "Rows"), __config__=_MODEL_CONFIG, rows=rows)


def _describe(column: ColumnSpec) -> str | None:
    """What the model is told about this column.

    The developer's own comment first, since it carries the meaning, followed by
    a format note where the type has one worth stating.
    """
    hint = FORMAT_HINTS.get(column.data_type)
    parts = [part for part in (column.description, hint) if part]
    return " ".join(parts) or None


def _annotation_for(column: ColumnSpec) -> Any:
    annotation = _base_annotation(column)
    return annotation | None if column.nullable else annotation


def _base_annotation(column: ColumnSpec) -> Any:
    if column.data_type.startswith("_"):
        element = replace(column, data_type=column.data_type[1:], nullable=False)
        return list[_base_annotation(element)]  # type: ignore[misc]

    if column.enum_labels:
        return Literal[column.enum_labels]

    try:
        return SCALAR_TYPES[column.data_type]
    except KeyError:
        raise UnsupportedColumnTypeError(
            f"Column {column.name!r} has type {column.data_type!r}, which SchemaGate "
            f"cannot compile. Supported types: {', '.join(sorted(SCALAR_TYPES))}."
        ) from None


def _model_name(table: TableSchema, suffix: str) -> str:
    parts = [part for part in re.split(r"[^0-9a-zA-Z]+", table.name) if part]
    stem = "".join(part[:1].upper() + part[1:] for part in parts)
    return f"{stem or 'Table'}{suffix}"

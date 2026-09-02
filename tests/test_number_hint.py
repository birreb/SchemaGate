"""A numeric column tells the model it wants the number alone.

The system prompt says to copy values exactly, and an invoice prints a quantity
with its unit: `2 st`, `8 hrs`, `42,17 l`. Copied exactly, that is not a
number, the gate rejects it, and the row is lost. Found by the ingestion
benchmark on every line-item case with a unit column. The column says what it
wants, the way date columns already do.
"""

from schemagate.schema.factory import NUMBER_HINT, build_row_model
from schemagate.schema.spec import ColumnSpec, TableSchema


def description_of(column: ColumnSpec) -> str:
    table = TableSchema(schema="public", name="t", columns=(column,))
    schema = build_row_model(table).model_json_schema()
    return str(schema["properties"][column.name]["description"])


def test_a_numeric_column_asks_for_the_number_alone() -> None:
    column = ColumnSpec(name="quantity", data_type="numeric", nullable=False, ordinal=1)

    assert description_of(column) == NUMBER_HINT


def test_an_integer_column_asks_for_the_number_alone() -> None:
    column = ColumnSpec(name="count", data_type="int4", nullable=False, ordinal=1)

    assert NUMBER_HINT in description_of(column)


def test_the_column_comment_comes_first() -> None:
    column = ColumnSpec(
        name="quantity",
        data_type="numeric",
        nullable=False,
        ordinal=1,
        description="Quantity as printed.",
    )

    assert description_of(column) == f"Quantity as printed. {NUMBER_HINT}"


def test_a_text_column_gets_no_number_hint() -> None:
    column = ColumnSpec(name="supplier", data_type="text", nullable=False, ordinal=1)
    table = TableSchema(schema="public", name="t", columns=(column,))

    properties = build_row_model(table).model_json_schema()["properties"]

    assert "description" not in properties["supplier"]

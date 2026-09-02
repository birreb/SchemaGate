"""Values a model returns as JSON scalars, not strings.

The compiled model asks for exact numbers as strings, but an integer, float or
boolean column is typed as itself, so its value arrives as a Python int, float
or bool. The gate reads text, and must spell such a value out rather than fail
on it. Found by the ingestion benchmark: every line-item extraction crashed on
`line_no`, an int2.
"""

from decimal import Decimal

from schemagate.schema.spec import ColumnSpec, TableSchema
from schemagate.validate.gate import validate


def table(*columns: ColumnSpec) -> TableSchema:
    return TableSchema(schema="public", name="t", columns=columns)


def test_an_integer_from_the_model_is_accepted_for_an_integer_column() -> None:
    schema = table(ColumnSpec(name="line_no", data_type="int2", nullable=False, ordinal=1))

    report = validate([{"line_no": 3}], schema)

    assert report.rows[0]["line_no"] == 3
    assert report.failures == ()


def test_a_float_from_the_model_is_accepted_for_a_float_column() -> None:
    schema = table(ColumnSpec(name="weight", data_type="float8", nullable=True, ordinal=1))

    report = validate([{"weight": 12.5}], schema)

    assert report.rows[0]["weight"] == 12.5
    assert report.failures == ()


def test_a_boolean_from_the_model_is_accepted_for_a_boolean_column() -> None:
    schema = table(ColumnSpec(name="active", data_type="bool", nullable=False, ordinal=1))

    report = validate([{"active": True}, {"active": False}], schema)

    assert [row["active"] for row in report.rows] == [True, False]
    assert report.failures == ()


def test_an_integer_for_an_exact_column_keeps_its_value() -> None:
    schema = table(
        ColumnSpec(name="qty", data_type="numeric", nullable=False, ordinal=1, numeric_scale=3)
    )

    report = validate([{"qty": 7}], schema)

    assert report.rows[0]["qty"] == Decimal(7)
    assert report.failures == ()


def test_a_boolean_for_a_numeric_column_is_a_type_failure_not_a_crash() -> None:
    schema = table(ColumnSpec(name="total", data_type="numeric", nullable=False, ordinal=1))

    report = validate([{"total": True}], schema)

    assert report.rows[0]["total"] is None
    assert [failure.rule for failure in report.failures] == ["type"]

"""Bounds a numeric column's type cannot carry."""

from decimal import Decimal

from schemagate.schema.spec import ColumnSpec, TableSchema
from schemagate.validate.gate import validate
from schemagate.validate.rules import RangeRule, parse_rule

TABLE = TableSchema(
    schema="public",
    name="invoices",
    columns=(
        ColumnSpec(name="tax", data_type="numeric", nullable=False, ordinal=1, numeric_scale=2),
        ColumnSpec(name="shipping", data_type="numeric", nullable=True, ordinal=2, numeric_scale=2),
    ),
)


def test_a_value_below_the_minimum_is_reported() -> None:
    report = validate(
        [{"tax": "0.00", "shipping": "0"}], TABLE, [RangeRule("tax", minimum=Decimal("0.01"))]
    )

    assert [(f.column, f.rule) for f in report.failures] == [("tax", "range")]


def test_a_value_above_the_maximum_is_reported() -> None:
    rule = RangeRule("shipping", maximum=Decimal(500))

    report = validate([{"tax": "1", "shipping": "20501.44"}], TABLE, [rule])

    assert [(f.column, f.rule) for f in report.failures] == [("shipping", "range")]


def test_a_value_within_bounds_passes() -> None:
    rules = [RangeRule("tax", minimum=Decimal("0.01")), RangeRule("shipping", maximum=Decimal(500))]

    assert validate([{"tax": "12.50", "shipping": "149"}], TABLE, rules).failures == ()


def test_a_null_is_not_out_of_range() -> None:
    assert (
        validate(
            [{"tax": "1", "shipping": None}], TABLE, [RangeRule("shipping", maximum=Decimal(1))]
        ).failures
        == ()
    )


def test_bounds_are_configured_with_min_and_max() -> None:
    assert parse_rule({"column": "shipping", "max": 500}) == RangeRule(
        "shipping", maximum=Decimal(500)
    )
    assert parse_rule({"column": "tax", "min": "0.01", "max": "1e6"}) == RangeRule(
        "tax", minimum=Decimal("0.01"), maximum=Decimal("1e6")
    )

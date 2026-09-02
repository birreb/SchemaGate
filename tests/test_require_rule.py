"""A nullable column the operator still expects to see filled."""

from schemagate.schema.spec import ColumnSpec, TableSchema
from schemagate.validate.gate import validate
from schemagate.validate.rules import RequireRule, parse_rule

TABLE = TableSchema(
    schema="public",
    name="invoices",
    columns=(
        ColumnSpec(name="supplier", data_type="text", nullable=False, ordinal=1),
        ColumnSpec(name="vat_id", data_type="text", nullable=True, ordinal=2),
    ),
)


def test_a_missing_value_is_reported_where_one_is_expected() -> None:
    report = validate([{"supplier": "Anyone AB", "vat_id": None}], TABLE, [RequireRule("vat_id")])

    assert [(f.column, f.rule) for f in report.failures] == [("vat_id", "required")]


def test_a_present_value_passes() -> None:
    rows = [{"supplier": "Anyone AB", "vat_id": "SE556000000001"}]

    assert validate(rows, TABLE, [RequireRule("vat_id")]).failures == ()


def test_the_rule_is_configured_with_require_true() -> None:
    assert parse_rule({"column": "vat_id", "require": True}) == RequireRule(column="vat_id")


def test_the_rows_still_come_back() -> None:
    report = validate([{"supplier": "Anyone AB", "vat_id": None}], TABLE, [RequireRule("vat_id")])

    assert report.rows[0]["supplier"] == "Anyone AB"

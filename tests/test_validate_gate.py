from decimal import Decimal
from typing import Any

from schemagate.schema.spec import ColumnSpec, TableSchema
from schemagate.validate.gate import validate
from schemagate.validate.rules import SumRule

INVOICE = TableSchema(
    schema="public",
    name="invoices",
    columns=(
        ColumnSpec(
            name="invoice_number", data_type="varchar", nullable=False, ordinal=1, max_length=8
        ),
        ColumnSpec(name="subtotal", data_type="numeric", nullable=False, ordinal=2),
        ColumnSpec(name="tax", data_type="numeric", nullable=False, ordinal=3),
        ColumnSpec(name="total", data_type="numeric", nullable=False, ordinal=4),
    ),
)

TOTALS = SumRule(terms=("subtotal", "tax"), equals="total")


def row(**overrides: Any) -> dict[str, str | None]:
    base: dict[str, str | None] = {
        "invoice_number": "INV-1",
        "subtotal": "100.00",
        "tax": "25.00",
        "total": "125.00",
    }
    return {**base, **overrides}


def test_a_consistent_document_passes() -> None:
    report = validate((row(),), INVOICE, rules=(TOTALS,))

    assert report.ok
    assert report.failures == ()


def test_coerced_values_are_returned() -> None:
    report = validate((row(),), INVOICE, rules=(TOTALS,))

    assert report.rows[0]["total"] == Decimal("125.00")


def test_a_total_that_does_not_add_up_is_caught() -> None:
    report = validate((row(total="130.00"),), INVOICE, rules=(TOTALS,))

    assert not report.ok
    failure = report.failures[0]
    assert failure.rule == "arithmetic"
    assert failure.column == "total"
    assert "125.00" in failure.detail, "the report should say what the sum came to"
    assert "130.00" in failure.detail, "and what the document claimed"


def test_arithmetic_uses_decimals_not_floats() -> None:
    schema = TableSchema(
        schema="public",
        name="t",
        columns=(
            ColumnSpec(name="a", data_type="numeric", nullable=False, ordinal=1),
            ColumnSpec(name="b", data_type="numeric", nullable=False, ordinal=2),
            ColumnSpec(name="c", data_type="numeric", nullable=False, ordinal=3),
        ),
    )
    rule = SumRule(terms=("a", "b"), equals="c", tolerance=Decimal("0"))

    report = validate(({"a": "0.1", "b": "0.2", "c": "0.3"},), schema, rules=(rule,))

    assert report.ok, "0.1 + 0.2 == 0.3 exactly in decimal, and only floats disagree"


def test_a_rounding_difference_within_tolerance_passes() -> None:
    report = validate((row(total="125.01"),), INVOICE, rules=(TOTALS,))

    assert report.ok


def test_a_difference_beyond_tolerance_fails() -> None:
    report = validate((row(total="125.02"),), INVOICE, rules=(TOTALS,))

    assert not report.ok


def test_a_rule_is_skipped_when_a_term_is_missing() -> None:
    schema = TableSchema(
        schema="public",
        name="t",
        columns=(
            ColumnSpec(name="a", data_type="numeric", nullable=True, ordinal=1),
            ColumnSpec(name="c", data_type="numeric", nullable=True, ordinal=2),
        ),
    )
    rule = SumRule(terms=("a", "b"), equals="c")

    report = validate(({"a": "1.00", "c": "1.00"},), schema, rules=(rule,))

    assert report.ok, "a rule naming a column the table does not have cannot be judged"


def test_a_rule_is_skipped_when_a_value_is_null() -> None:
    report = validate((row(tax=None),), INVOICE, rules=(TOTALS,))

    assert [f.rule for f in report.failures] == ["not_null"], (
        "the missing value is the finding; the arithmetic cannot also be judged"
    )


def test_an_over_long_value_is_caught() -> None:
    report = validate((row(invoice_number="INV-0000001"),), INVOICE, rules=())

    failure = report.failures[0]
    assert failure.rule == "length"
    assert failure.column == "invoice_number"
    assert "8" in failure.detail


def test_a_value_at_the_limit_is_allowed() -> None:
    report = validate((row(invoice_number="INV-0001"),), INVOICE, rules=())

    assert report.ok


def test_arithmetic_is_not_attempted_on_a_row_that_failed_coercion() -> None:
    report = validate((row(total="not a number"),), INVOICE, rules=(TOTALS,))

    assert [f.rule for f in report.failures] == ["type"], (
        "a second complaint about the same cell adds noise, not information"
    )


def test_failures_carry_the_row_index() -> None:
    rows = (row(), row(total="999.00"))

    report = validate(rows, INVOICE, rules=(TOTALS,))

    assert report.failures[0].row == 1


def test_every_row_is_checked() -> None:
    rows = (row(total="1.00"), row(), row(total="2.00"))

    report = validate(rows, INVOICE, rules=(TOTALS,))

    assert [f.row for f in report.failures] == [0, 2]

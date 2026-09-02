"""Rules for what a schema cannot say about a value.

A sum rule was the first. The ingestion benchmark showed three more errors a
gate could catch and did not: a line total with two printed columns glued
together, the buyer's VAT number stored as the seller's, and an identifier of
the wrong shape. Each is a rule an operator can state in one line.
"""

from decimal import Decimal

import pytest

from schemagate.schema.spec import ColumnSpec, TableSchema
from schemagate.validate.gate import validate
from schemagate.validate.rules import (
    PatternRule,
    ProductRule,
    RejectRule,
    SumRule,
    parse_rule,
)

LINES = TableSchema(
    schema="public",
    name="invoice_lines",
    columns=(
        ColumnSpec(
            name="quantity", data_type="numeric", nullable=False, ordinal=1, numeric_scale=3
        ),
        ColumnSpec(
            name="unit_price", data_type="numeric", nullable=False, ordinal=2, numeric_scale=4
        ),
        ColumnSpec(
            name="line_total", data_type="numeric", nullable=False, ordinal=3, numeric_scale=2
        ),
    ),
)

INVOICES = TableSchema(
    schema="public",
    name="invoices",
    columns=(
        ColumnSpec(name="supplier", data_type="text", nullable=False, ordinal=1),
        ColumnSpec(name="vat_id", data_type="text", nullable=True, ordinal=2),
    ),
)


def test_a_product_that_holds_passes() -> None:
    rule = ProductRule(factors=("quantity", "unit_price"), equals="line_total")

    report = validate(
        [{"quantity": "12", "unit_price": "245.00", "line_total": "2940.00"}], LINES, [rule]
    )

    assert report.failures == ()


def test_a_line_total_with_a_glued_column_is_caught() -> None:
    rule = ProductRule(factors=("quantity", "unit_price"), equals="line_total")

    report = validate(
        [{"quantity": "12", "unit_price": "245.00", "line_total": "122940.00"}], LINES, [rule]
    )

    assert [(f.column, f.rule) for f in report.failures] == [("line_total", "arithmetic")]
    assert "2940.00" in report.failures[0].detail


def test_a_product_is_skipped_when_a_factor_is_null() -> None:
    rule = ProductRule(factors=("quantity", "unit_price"), equals="line_total")

    report = validate([{"quantity": None, "unit_price": "1", "line_total": "1"}], LINES, [rule])

    assert [f.rule for f in report.failures] == ["not_null"]


def test_the_buyers_vat_number_is_rejected_as_the_sellers() -> None:
    rule = RejectRule(column="vat_id", reject=("SE559012345601",))

    report = validate([{"supplier": "Anyone AB", "vat_id": "SE559012345601"}], INVOICES, [rule])

    assert [(f.column, f.rule) for f in report.failures] == [("vat_id", "rejected_value")]


def test_rejection_ignores_case_and_spacing() -> None:
    rule = RejectRule(column="supplier", reject=("Halvard Industri AB",))

    report = validate([{"supplier": "HALVARD  INDUSTRI ab", "vat_id": None}], INVOICES, [rule])

    assert [f.rule for f in report.failures] == ["rejected_value"]


def test_a_value_not_on_the_list_passes() -> None:
    rule = RejectRule(column="vat_id", reject=("SE559012345601",))

    report = validate([{"supplier": "Anyone AB", "vat_id": "SE556000000001"}], INVOICES, [rule])

    assert report.failures == ()


def test_an_identifier_of_the_wrong_shape_is_caught() -> None:
    rule = PatternRule(column="vat_id", pattern=r"[A-Z]{2}[A-Z0-9]{2,12}")

    report = validate([{"supplier": "Great Lakes Inc.", "vat_id": "38-2947103"}], INVOICES, [rule])

    assert [(f.column, f.rule) for f in report.failures] == [("vat_id", "pattern")]


def test_a_null_is_not_checked_against_a_pattern() -> None:
    rule = PatternRule(column="vat_id", pattern=r"[A-Z]{2}[A-Z0-9]{2,12}")

    report = validate([{"supplier": "Great Lakes Inc.", "vat_id": None}], INVOICES, [rule])

    assert report.failures == ()


def test_rules_are_parsed_by_their_keys() -> None:
    assert parse_rule({"terms": ["subtotal", "tax"], "equals": "total"}) == SumRule(
        terms=("subtotal", "tax"), equals="total"
    )
    assert parse_rule(
        {"factors": ["quantity", "unit_price"], "equals": "line_total"}
    ) == ProductRule(factors=("quantity", "unit_price"), equals="line_total")
    assert parse_rule({"column": "vat_id", "reject": ["SE1"]}) == RejectRule(
        column="vat_id", reject=("SE1",)
    )
    assert parse_rule({"column": "vat_id", "pattern": "[A-Z]{2}.*"}) == PatternRule(
        column="vat_id", pattern="[A-Z]{2}.*"
    )


def test_a_tolerance_can_be_given() -> None:
    rule = parse_rule({"factors": ["a"], "equals": "b", "tolerance": "0.5"})

    assert isinstance(rule, ProductRule)
    assert rule.tolerance == Decimal("0.5")


def test_an_unrecognised_rule_is_an_error_not_a_silence() -> None:
    with pytest.raises(ValueError) as caught:
        parse_rule({"column": "vat_id"})

    assert "terms" in str(caught.value) and "pattern" in str(caught.value)


def test_a_bad_pattern_is_refused_when_parsed() -> None:
    with pytest.raises(Exception):  # noqa: B017
        parse_rule({"column": "vat_id", "pattern": "("})

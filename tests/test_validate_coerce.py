import datetime as dt
import uuid
from decimal import Decimal
from typing import Any

import pytest

from schemagate.schema.spec import ColumnSpec, TableSchema
from schemagate.validate.coerce import coerce_rows


def schema(*columns: ColumnSpec) -> TableSchema:
    return TableSchema(schema="public", name="invoices", columns=columns)


def column(name: str = "value", data_type: str = "numeric", **overrides: Any) -> ColumnSpec:
    defaults: dict[str, Any] = {"nullable": True, "ordinal": 1}
    return ColumnSpec(name=name, data_type=data_type, **{**defaults, **overrides})


def coerce_one(value: str | None, spec: ColumnSpec) -> Any:
    rows, failures = coerce_rows(({spec.name: value},), schema(spec))
    assert not failures, f"unexpected failure: {failures}"
    return rows[0][spec.name]


def failure_for(value: str | None, spec: ColumnSpec) -> Any:
    _, failures = coerce_rows(({spec.name: value},), schema(spec))
    assert len(failures) == 1, f"expected exactly one failure, got {failures}"
    return failures[0]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("10.00", "10.00"),
        ("1234.56", "1234.56"),
        ("-10.50", "-10.50"),
        ("0", "0"),
    ],
)
def test_plain_decimals(text: str, expected: str) -> None:
    assert coerce_one(text, column()) == Decimal(expected)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("10,00", "10.00"),
        ("1234,5", "1234.5"),
        ("0,99", "0.99"),
    ],
)
def test_a_comma_decimal_separator_is_understood(text: str, expected: str) -> None:
    assert coerce_one(text, column()) == Decimal(expected), (
        "a semicolon-delimited European export writes the decimal with a comma"
    )


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("1.234,56", "1234.56"),
        ("1,234.56", "1234.56"),
        ("1.234.567,89", "1234567.89"),
        ("1,234,567.89", "1234567.89"),
    ],
)
def test_when_both_separators_appear_the_last_one_is_the_decimal(text: str, expected: str) -> None:
    assert coerce_one(text, column()) == Decimal(expected)


@pytest.mark.parametrize("text", ["1,234", "1.234"])
def test_a_lone_separator_before_three_digits_is_refused_as_ambiguous(text: str) -> None:
    failure = failure_for(text, column())

    assert failure.rule == "ambiguous_number"
    assert text in failure.detail, "the report has to quote the value a human must resolve"


def test_grouping_repeated_without_a_decimal_is_not_ambiguous() -> None:
    assert coerce_one("1.234.567", column()) == Decimal("1234567")


def test_currency_symbols_and_spacing_are_stripped() -> None:
    assert coerce_one("€ 1.234,56", column()) == Decimal("1234.56")
    # A no-break space, which is what French and Nordic locales put between
    # thousands groups. Deliberate, so ruff's homoglyph check is waived here.
    assert coerce_one("1 234,56", column()) == Decimal("1234.56")  # noqa: RUF001


def test_accounting_parentheses_mean_negative() -> None:
    assert coerce_one("(1.234,56)", column()) == Decimal("-1234.56")


def test_integers() -> None:
    assert coerce_one("3", column(data_type="int4")) == 3


def test_an_integer_column_refuses_a_fraction() -> None:
    assert failure_for("3.5", column(data_type="int4")).rule == "type"


def test_an_integer_column_refuses_the_float_a_spreadsheet_could_not_represent() -> None:
    failure = failure_for("1.2345678901234567e+19", column(data_type="int8"))

    assert failure.rule == "type", (
        "the spreadsheet reader deliberately leaves these in float form so that "
        "the gate rejects them rather than writing fabricated digits"
    )


@pytest.mark.parametrize(
    ("text", "expected"),
    [("true", True), ("false", False), ("yes", True), ("no", False), ("1", True), ("0", False)],
)
def test_booleans(text: str, expected: bool) -> None:
    assert coerce_one(text, column(data_type="bool")) is expected


def test_dates() -> None:
    assert coerce_one("2026-01-05", column(data_type="date")) == dt.date(2026, 1, 5)


def test_a_date_column_refuses_an_ambiguous_regional_format() -> None:
    failure = failure_for("05/01/2026", column(data_type="date"))

    assert failure.rule == "type", "05/01/2026 is January or May depending on the reader"


def test_timestamps() -> None:
    value = coerce_one("2026-02-06T14:30:00", column(data_type="timestamp"))

    assert value == dt.datetime(2026, 2, 6, 14, 30)


def test_uuids() -> None:
    text = "b0e7a1c2-0000-4000-8000-000000000001"

    assert coerce_one(text, column(data_type="uuid")) == uuid.UUID(text)


def test_text_passes_through() -> None:
    assert coerce_one("Acme Ltd", column(data_type="text")) == "Acme Ltd"


def test_enum_labels_are_checked() -> None:
    spec = column(data_type="invoice_status", enum_labels=("draft", "sent"))

    assert coerce_one("draft", spec) == "draft"
    assert failure_for("posted", spec).rule == "enum"


def test_a_null_is_kept_for_a_nullable_column() -> None:
    assert coerce_one(None, column(nullable=True)) is None


def test_a_null_in_a_required_column_is_reported() -> None:
    assert failure_for(None, column(nullable=False)).rule == "not_null"


def test_a_failure_names_the_row_and_column() -> None:
    spec = column("total", "numeric", nullable=False)
    rows = ({"total": "10.00"}, {"total": "not a number"})

    _, failures = coerce_rows(rows, schema(spec))

    assert len(failures) == 1
    assert failures[0].row == 1
    assert failures[0].column == "total"


def test_good_rows_survive_alongside_bad_ones() -> None:
    spec = column("total", "numeric")
    rows = ({"total": "10.00"}, {"total": "oops"}, {"total": "20.00"})

    coerced, failures = coerce_rows(rows, schema(spec))

    assert [row["total"] for row in coerced] == [Decimal("10.00"), None, Decimal("20.00")]
    assert len(failures) == 1, "one bad row must not discard the rest"


def test_arrays() -> None:
    spec = column("tags", "_text")

    assert coerce_one("a,b,c", spec) == ["a", "b", "c"]


# The three-step cascade for a separator followed by exactly three digits:
# the column's declared scale, then the convention the rest of the file reveals,
# then refusal.


def test_a_money_column_settles_the_ambiguity_from_its_scale() -> None:
    spec = column(data_type="numeric", numeric_scale=2)

    assert coerce_one("1,234", spec) == Decimal("1234"), (
        "numeric(p,2) holds two decimals, so three digits after the separator "
        "cannot be a fraction and the separator must be grouping"
    )


def test_a_scale_of_zero_settles_it_too() -> None:
    assert coerce_one("1.234", column(data_type="numeric", numeric_scale=0)) == Decimal("1234")


def test_an_integer_column_settles_it() -> None:
    assert coerce_one("1,234", column(data_type="int8", numeric_scale=0)) == 1234


def test_a_scale_that_allows_three_decimals_does_not_settle_it() -> None:
    spec = column(data_type="numeric", numeric_scale=3)

    assert failure_for("1,234", spec).rule == "ambiguous_number", (
        "numeric(p,3) permits three decimals, so the value really could be either"
    )


def test_the_rest_of_the_file_settles_what_the_scale_cannot() -> None:
    spec = column("total", "numeric", numeric_scale=3)
    rows = ({"total": "10,50"}, {"total": "1,234"})

    coerced, failures = coerce_rows(rows, schema(spec))

    assert not failures
    assert coerced[1]["total"] == Decimal("1.234"), (
        "10,50 proves the comma is this file's decimal separator"
    )


def test_the_file_can_prove_the_comma_is_grouping() -> None:
    spec = column("total", "numeric", numeric_scale=3)
    rows = ({"total": "9,876.50"}, {"total": "1,234"})

    coerced, failures = coerce_rows(rows, schema(spec))

    assert not failures
    assert coerced[1]["total"] == Decimal("1234")


def test_the_scale_outranks_the_rest_of_the_file() -> None:
    spec = column("total", "numeric", numeric_scale=2)
    rows = ({"total": "10,50"}, {"total": "1,234"})

    coerced, failures = coerce_rows(rows, schema(spec))

    assert not failures
    assert coerced[1]["total"] == Decimal("1234"), (
        "the column is the stronger authority: two decimals cannot hold three digits"
    )


def test_a_file_that_contradicts_itself_is_refused() -> None:
    spec = column("total", "numeric", numeric_scale=3)
    rows = ({"total": "10,50"}, {"total": "9,876.50"}, {"total": "1,234"})

    _, failures = coerce_rows(rows, schema(spec))

    assert [f.rule for f in failures] == ["ambiguous_number"], (
        "one row says comma decimal and another says comma grouping, "
        "so the file is not evidence of anything"
    )


def test_with_no_scale_and_no_other_clue_it_still_refuses() -> None:
    assert failure_for("1,234", column(data_type="numeric")).rule == "ambiguous_number"


def test_the_refusal_explains_what_would_resolve_it() -> None:
    detail = failure_for("1,234", column(data_type="numeric")).detail

    assert "scale" in detail.lower(), "the message should point at the fix"


def test_a_missing_value_is_not_a_failure_when_the_column_has_a_fallback() -> None:
    spec = column("status", "text", nullable=False, has_default=True, default_expr="'draft'::text")

    _, failures = coerce_rows(({"status": None},), schema(spec))

    assert failures == (), (
        "the column is NOT NULL but the database has a default for it, so a "
        "document that says nothing is not an error"
    )


def test_a_missing_value_is_still_a_failure_without_a_fallback() -> None:
    spec = column("supplier", "text", nullable=False)

    _, failures = coerce_rows(({"supplier": None},), schema(spec))

    assert [f.rule for f in failures] == ["not_null"]

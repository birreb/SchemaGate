from typing import Any

import pytest

from schemagate.errors import MalformedDocumentError
from schemagate.ingest.tabular import Table, align
from schemagate.schema.spec import ColumnSpec, TableSchema


def schema(*names: str, **flags: Any) -> TableSchema:
    columns = tuple(
        ColumnSpec(name=name, data_type="text", nullable=True, ordinal=index, **flags)
        for index, name in enumerate(names, start=1)
    )
    return TableSchema(schema="public", name="invoices", columns=columns)


def test_matches_headers_that_already_agree() -> None:
    table = Table(headers=("invoice_number",), rows=(("INV-1",),))

    assert align(table, schema("invoice_number")).rows == ({"invoice_number": "INV-1"},)


def test_matching_ignores_case() -> None:
    table = Table(headers=("Invoice_Number",), rows=(("INV-1",),))

    assert align(table, schema("invoice_number")).rows == ({"invoice_number": "INV-1"},)


@pytest.mark.parametrize(
    "header", ["Invoice Number", "invoice-number", "Invoice   Number", " invoice number "]
)
def test_matching_ignores_separators_and_spacing(header: str) -> None:
    table = Table(headers=(header,), rows=(("INV-1",),))

    assert align(table, schema("invoice_number")).rows == ({"invoice_number": "INV-1"},)


def test_matching_ignores_decoration_around_a_name() -> None:
    table = Table(headers=("Total (EUR)",), rows=(("10.00",),))

    assert align(table, schema("total_eur")).rows == ({"total_eur": "10.00"},)


def test_empty_cells_become_null_rather_than_an_empty_string() -> None:
    table = Table(headers=("vat_id",), rows=((("",)),))

    assert align(table, schema("vat_id")).rows == ({"vat_id": None},)


def test_whitespace_only_cells_become_null() -> None:
    table = Table(headers=("vat_id",), rows=(("   ",),))

    assert align(table, schema("vat_id")).rows == ({"vat_id": None},)


def test_values_are_not_coerced_here() -> None:
    table = Table(headers=("total",), rows=(("  10.00  ",),))

    assert align(table, schema("total")).rows == ({"total": "10.00"},)


def test_reports_headers_the_table_does_not_have() -> None:
    table = Table(headers=("invoice_number", "colour"), rows=(("INV-1", "red"),))

    result = align(table, schema("invoice_number"))

    assert result.unmatched_headers == ("colour",)
    assert result.rows == ({"invoice_number": "INV-1"},)


def test_reports_columns_the_file_does_not_have() -> None:
    table = Table(headers=("invoice_number",), rows=(("INV-1",),))

    result = align(table, schema("invoice_number", "total"))

    assert result.missing_columns == ("total",)


def test_nothing_is_missing_when_every_column_is_present() -> None:
    table = Table(headers=("a", "b"), rows=(("1", "2"),))

    result = align(table, schema("a", "b"))

    assert result.missing_columns == ()
    assert result.unmatched_headers == ()


def test_columns_the_database_owns_are_never_matched() -> None:
    table = Table(headers=("id", "total"), rows=(("7", "10.00"),))
    target = TableSchema(
        schema="public",
        name="invoices",
        columns=(
            ColumnSpec(name="id", data_type="int8", nullable=False, ordinal=1, is_identity=True),
            ColumnSpec(name="total", data_type="numeric", nullable=False, ordinal=2),
        ),
    )

    result = align(table, target)

    assert result.rows == ({"total": "10.00"},)
    assert result.unmatched_headers == ("id",)


def test_two_headers_matching_one_column_is_an_error() -> None:
    table = Table(headers=("Invoice Number", "invoice_number"), rows=(("a", "b"),))

    with pytest.raises(MalformedDocumentError) as caught:
        align(table, schema("invoice_number"))

    assert "invoice_number" in str(caught.value)


def test_every_row_is_aligned() -> None:
    table = Table(headers=("a",), rows=(("1",), ("2",), ("3",)))

    assert align(table, schema("a")).rows == ({"a": "1"}, {"a": "2"}, {"a": "3"})

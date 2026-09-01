import datetime as dt
import io
from typing import Any

import pytest
from openpyxl import Workbook

from schemagate.errors import MalformedDocumentError
from schemagate.ingest.tabular import read_spreadsheet


def workbook(sheets: dict[str, list[list[Any]]]) -> bytes:
    book = Workbook()
    book.remove(book.worksheets[0])
    for title, rows in sheets.items():
        sheet = book.create_sheet(title=title)
        for row in rows:
            sheet.append(row)
    buffer = io.BytesIO()
    book.save(buffer)
    return buffer.getvalue()


def single(rows: list[list[Any]]) -> bytes:
    return workbook({"Sheet1": rows})


def test_reads_headers_and_rows() -> None:
    table = read_spreadsheet(single([["invoice_number", "total"], ["INV-1", "10.00"]]))

    assert table.headers == ("invoice_number", "total")
    assert table.rows == (("INV-1", "10.00"),)


def test_whole_numbers_do_not_gain_a_decimal_point() -> None:
    table = read_spreadsheet(single([["quantity"], [3]]))

    assert table.rows == (("3",),), "Excel stores 3 as 3.0, which no integer column would accept"


def test_fractional_numbers_keep_their_value() -> None:
    table = read_spreadsheet(single([["total"], [1234.56]]))

    assert table.rows == (("1234.56",),)


def test_dates_render_as_iso() -> None:
    table = read_spreadsheet(single([["issued_on"], [dt.date(2026, 1, 5)]]))

    assert table.rows == (("2026-01-05",),)


def test_timestamps_render_as_iso() -> None:
    table = read_spreadsheet(single([["seen_at"], [dt.datetime(2026, 2, 6, 14, 30)]]))

    assert table.rows == (("2026-02-06T14:30:00",),)


def test_times_render_as_iso() -> None:
    table = read_spreadsheet(single([["starts_at"], [dt.time(9, 15)]]))

    assert table.rows == (("09:15:00",),)


def test_booleans_render_in_the_form_a_boolean_column_accepts() -> None:
    table = read_spreadsheet(single([["paid"], [True], [False]]))

    assert table.rows == (("true",), ("false",))


def test_empty_cells_render_as_empty_strings() -> None:
    table = read_spreadsheet(single([["a", "b"], ["x", None]]))

    assert table.rows == (("x", ""),)


def test_headers_are_stripped() -> None:
    assert read_spreadsheet(single([[" total "], [1]])).headers == ("total",)


def test_blank_rows_are_skipped() -> None:
    table = read_spreadsheet(single([["a"], ["1"], [None], ["2"]]))

    assert table.rows == (("1",), ("2",))


def test_reads_the_first_sheet_by_default() -> None:
    data = workbook({"Invoices": [["a"], ["1"]], "Notes": [["b"], ["2"]]})

    assert read_spreadsheet(data).headers == ("a",)


def test_reads_a_named_sheet() -> None:
    data = workbook({"Invoices": [["a"], ["1"]], "Notes": [["b"], ["2"]]})

    assert read_spreadsheet(data, sheet="Notes").headers == ("b",)


def test_an_unknown_sheet_name_lists_the_ones_that_exist() -> None:
    data = workbook({"Invoices": [["a"], ["1"]], "Notes": [["b"], ["2"]]})

    with pytest.raises(MalformedDocumentError) as caught:
        read_spreadsheet(data, sheet="Missing")

    message = str(caught.value)
    assert "Missing" in message
    assert "Invoices" in message and "Notes" in message


def test_an_empty_sheet_is_rejected() -> None:
    with pytest.raises(MalformedDocumentError):
        read_spreadsheet(single([]))


def test_a_file_that_is_not_a_spreadsheet_is_rejected() -> None:
    with pytest.raises(MalformedDocumentError):
        read_spreadsheet(b"invoice_number,total\nINV-1,10.00\n")


def test_rejects_a_duplicate_header() -> None:
    with pytest.raises(MalformedDocumentError):
        read_spreadsheet(single([["total", "total"], [1, 2]]))

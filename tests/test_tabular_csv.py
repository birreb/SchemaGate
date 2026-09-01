import pytest

from schemagate.errors import MalformedDocumentError
from schemagate.ingest.tabular import read_csv


def test_reads_headers_and_rows() -> None:
    table = read_csv(b"invoice_number,total\nINV-1,10.00\nINV-2,20.00\n")

    assert table.headers == ("invoice_number", "total")
    assert table.rows == (("INV-1", "10.00"), ("INV-2", "20.00"))


def test_strips_whitespace_around_headers() -> None:
    assert read_csv(b" invoice_number , total \nINV-1,10.00\n").headers == (
        "invoice_number",
        "total",
    )


def test_reads_semicolon_delimited_files() -> None:
    table = read_csv(b"invoice_number;total\nINV-1;10,00\n")

    assert table.headers == ("invoice_number", "total")
    assert table.rows == (("INV-1", "10,00"),)


def test_reads_tab_delimited_files() -> None:
    assert read_csv(b"a\tb\n1\t2\n").headers == ("a", "b")


def test_a_single_column_file_is_not_mistaken_for_another_delimiter() -> None:
    table = read_csv(b"invoice_number\nINV-1\nINV-2\n")

    assert table.headers == ("invoice_number",)
    assert table.rows == (("INV-1",), ("INV-2",))


def test_strips_the_byte_order_mark_a_spreadsheet_export_leaves_behind() -> None:
    table = read_csv("invoice_number,total\nINV-1,10.00\n".encode("utf-8-sig"))

    assert table.headers == ("invoice_number", "total"), "a BOM must not corrupt the first header"


def test_reads_non_utf8_encodings() -> None:
    table = read_csv("supplier\nBjörn Ähläng AB\n".encode("cp1252"))

    assert table.rows == (("Björn Ähläng AB",),)


def test_keeps_quoted_delimiters_inside_a_field() -> None:
    table = read_csv(b'name,total\n"Ltd, Acme",10.00\n')

    assert table.rows == (("Ltd, Acme", "10.00"),)


def test_keeps_quoted_newlines_inside_a_field() -> None:
    table = read_csv(b'name,note\n"Acme","line one\nline two"\n')

    assert table.rows == (("Acme", "line one\nline two"),)


def test_pads_short_rows_so_column_positions_stay_aligned() -> None:
    table = read_csv(b"a,b,c\n1,2\n")

    assert table.rows == (("1", "2", ""),)


def test_ignores_a_trailing_delimiter() -> None:
    table = read_csv(b"a,b\n1,2,\n")

    assert table.rows == (("1", "2"),)


def test_rejects_a_row_with_more_cells_than_headers() -> None:
    with pytest.raises(MalformedDocumentError) as caught:
        read_csv(b"a,b\n1,2,3\n")

    assert "2" in str(caught.value), "the error should say which row disagrees with the header"


def test_skips_blank_lines() -> None:
    table = read_csv(b"a,b\n1,2\n\n3,4\n\n")

    assert table.rows == (("1", "2"), ("3", "4"))


def test_an_empty_file_is_rejected() -> None:
    with pytest.raises(MalformedDocumentError):
        read_csv(b"")


def test_a_header_only_file_yields_no_rows() -> None:
    table = read_csv(b"a,b\n")

    assert table.headers == ("a", "b")
    assert table.rows == ()


def test_rejects_a_duplicate_header() -> None:
    with pytest.raises(MalformedDocumentError) as caught:
        read_csv(b"total,total\n1,2\n")

    assert "total" in str(caught.value)

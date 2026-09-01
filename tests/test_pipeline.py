import io
from decimal import Decimal
from typing import Any

import pytest
from fpdf import FPDF
from openpyxl import Workbook

from schemagate.errors import UnsupportedFileTypeError
from schemagate.extract.base import ModelT
from schemagate.pipeline import Route, process
from schemagate.schema.spec import ColumnSpec, TableSchema
from schemagate.validate.rules import SumRule

INVOICES = TableSchema(
    schema="public",
    name="invoices",
    columns=(
        ColumnSpec(name="id", data_type="int8", nullable=False, ordinal=1, is_identity=True),
        ColumnSpec(name="invoice_number", data_type="text", nullable=False, ordinal=2),
        ColumnSpec(
            name="subtotal", data_type="numeric", nullable=False, ordinal=3, numeric_scale=2
        ),
        ColumnSpec(name="tax", data_type="numeric", nullable=False, ordinal=4, numeric_scale=2),
        ColumnSpec(name="total", data_type="numeric", nullable=False, ordinal=5, numeric_scale=2),
    ),
)

TOTALS = SumRule(terms=("subtotal", "tax"), equals="total")

CSV = b"invoice_number,subtotal,tax,total\nINV-1,100.00,25.00,125.00\n"


class StubExtractor:
    """Answers with fixed rows, so the pipeline is testable without a model."""

    def __init__(self, rows: list[dict[str, Any]] | None = None) -> None:
        self.rows = (
            rows
            if rows is not None
            else [{"invoice_number": "INV-9", "subtotal": "10.00", "tax": "2.50", "total": "12.50"}]
        )
        self.documents: list[str] = []

    async def extract(self, document: str, model: type[ModelT]) -> ModelT:
        self.documents.append(document)
        return model.model_validate({"rows": self.rows})


def spreadsheet() -> bytes:
    book = Workbook()
    sheet = book.worksheets[0]
    sheet.append(["invoice_number", "subtotal", "tax", "total"])
    sheet.append(["INV-2", 100, 25, 125])
    buffer = io.BytesIO()
    book.save(buffer)
    return buffer.getvalue()


def pdf() -> bytes:
    document = FPDF()
    document.add_page()
    document.set_font("helvetica", size=12)
    document.cell(0, 8, "Invoice INV-9 subtotal 10.00 tax 2.50 total 12.50")
    return bytes(document.output())


async def test_a_csv_takes_the_free_route() -> None:
    result = await process(CSV, "invoices.csv", INVOICES, extractor=None)

    assert result.route is Route.TABULAR
    assert result.rows[0]["invoice_number"] == "INV-1"


async def test_a_csv_never_calls_the_model() -> None:
    extractor = StubExtractor()

    await process(CSV, "invoices.csv", INVOICES, extractor=extractor)

    assert extractor.documents == [], "a native data grid must not cost a model call"


async def test_values_come_back_coerced() -> None:
    result = await process(CSV, "invoices.csv", INVOICES, extractor=None)

    assert result.rows[0]["total"] == Decimal("125.00")


async def test_columns_the_database_owns_are_not_returned() -> None:
    result = await process(CSV, "invoices.csv", INVOICES, extractor=None)

    assert "id" not in result.rows[0]


async def test_a_spreadsheet_takes_the_free_route() -> None:
    result = await process(spreadsheet(), "invoices.xlsx", INVOICES, extractor=None)

    assert result.route is Route.TABULAR
    assert result.rows[0]["subtotal"] == Decimal("100")


async def test_a_digital_pdf_goes_to_the_model_as_markdown() -> None:
    extractor = StubExtractor()

    result = await process(pdf(), "invoice.pdf", INVOICES, extractor=extractor)

    assert result.route is Route.NATIVE_PDF
    assert "INV-9" in extractor.documents[0], "the parsed markdown is what the model sees"
    assert result.rows[0]["invoice_number"] == "INV-9"


async def test_arithmetic_is_checked_on_the_model_path_too() -> None:
    extractor = StubExtractor(
        [{"invoice_number": "INV-9", "subtotal": "10.00", "tax": "2.50", "total": "99.00"}]
    )

    result = await process(pdf(), "invoice.pdf", INVOICES, extractor=extractor, rules=(TOTALS,))

    assert result.status == "flagged"
    assert result.failures[0].rule == "arithmetic"


async def test_a_consistent_document_reports_ok() -> None:
    result = await process(CSV, "invoices.csv", INVOICES, extractor=None, rules=(TOTALS,))

    assert result.status == "ok"
    assert result.failures == ()


async def test_a_bad_value_flags_rather_than_fails() -> None:
    data = b"invoice_number,subtotal,tax,total\nINV-1,oops,25.00,125.00\n"

    result = await process(data, "invoices.csv", INVOICES, extractor=None)

    assert result.status == "flagged"
    assert result.rows, "the rows that did parse still come back"


async def test_headers_that_match_nothing_are_reported() -> None:
    data = b"invoice_number,subtotal,tax,total,colour\nINV-1,100.00,25.00,125.00,red\n"

    result = await process(data, "invoices.csv", INVOICES, extractor=None)

    assert result.unmatched_headers == ("colour",)


async def test_a_pdf_with_no_extractor_is_refused() -> None:
    with pytest.raises(UnsupportedFileTypeError):
        await process(pdf(), "invoice.pdf", INVOICES, extractor=None)


async def test_an_unsupported_upload_is_refused() -> None:
    with pytest.raises(UnsupportedFileTypeError):
        await process(b"\x00\x01\x02 nonsense", "mystery.bin", INVOICES, extractor=None)


async def test_timings_are_reported_per_stage() -> None:
    result = await process(CSV, "invoices.csv", INVOICES, extractor=None)

    assert set(result.timings_ms) >= {"parse", "validate"}
    assert all(value >= 0 for value in result.timings_ms.values())


async def test_the_table_is_named_in_the_result() -> None:
    result = await process(CSV, "invoices.csv", INVOICES, extractor=None)

    assert result.table == "public.invoices"

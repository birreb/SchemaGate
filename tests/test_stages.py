import io
from typing import Any

from fpdf import FPDF
from openpyxl import Workbook

from schemagate.pipeline import process
from schemagate.schema.spec import ColumnSpec, TableSchema

INVOICES = TableSchema(
    schema="public",
    name="invoices",
    columns=(
        ColumnSpec(name="id", data_type="int8", nullable=False, ordinal=1, is_identity=True),
        ColumnSpec(name="invoice_number", data_type="text", nullable=False, ordinal=2),
        ColumnSpec(name="total", data_type="numeric", nullable=False, ordinal=3, numeric_scale=2),
    ),
)

CSV = b"invoice_number,total\nINV-1,10.00\nINV-2,20.00\n"


def spreadsheet() -> bytes:
    book = Workbook()
    sheet = book.worksheets[0]
    sheet.append(["invoice_number", "total"])
    sheet.append(["INV-1", 10])
    buffer = io.BytesIO()
    book.save(buffer)
    return buffer.getvalue()


def pdf() -> bytes:
    document = FPDF()
    document.add_page()
    document.set_font("helvetica", size=12)
    document.cell(0, 8, "Invoice INV-9 total 12.50")
    return bytes(document.output())


class Stub:
    async def extract(self, document: str, model: Any, images: Any = ()) -> Any:
        return model.model_validate({"rows": [{"invoice_number": "INV-9", "total": "12.50"}]})


def names(result: Any) -> list[str]:
    return [stage.name for stage in result.stages]


async def test_a_tabular_file_reports_the_stages_it_went_through() -> None:
    result = await process(CSV, "invoices.csv", INVOICES, extractor=None)

    assert names(result) == ["read", "match", "check"], (
        "a data grid is read and matched by column name; no model is involved"
    )


async def test_a_pdf_reports_the_model_stage() -> None:
    result = await process(pdf(), "invoice.pdf", INVOICES, extractor=Stub())

    assert names(result) == ["read", "extract", "check"]


async def test_each_stage_says_what_happened() -> None:
    result = await process(CSV, "invoices.csv", INVOICES, extractor=None)

    detail = {stage.name: stage.detail for stage in result.stages}
    assert "csv" in detail["read"].lower()
    assert "2" in detail["match"], "two columns matched"
    assert "2" in detail["check"], "two rows checked"


async def test_the_read_stage_names_what_it_found() -> None:
    result = await process(spreadsheet(), "book.xlsx", INVOICES, extractor=None)

    assert "spreadsheet" in result.stages[0].detail.lower()


async def test_a_failed_check_is_visible_in_the_stage() -> None:
    data = b"invoice_number,total\nINV-1,not a number\n"

    result = await process(data, "invoices.csv", INVOICES, extractor=None)

    assert "1 failure" in result.stages[-1].detail


async def test_a_clean_run_says_so() -> None:
    result = await process(CSV, "invoices.csv", INVOICES, extractor=None)

    assert "no failures" in result.stages[-1].detail


async def test_every_stage_is_timed() -> None:
    result = await process(pdf(), "invoice.pdf", INVOICES, extractor=Stub())

    assert all(stage.ms >= 0 for stage in result.stages)
    assert len(result.stages) == 3


async def test_the_columns_the_database_owns_are_named_as_skipped() -> None:
    result = await process(CSV, "invoices.csv", INVOICES, extractor=None)

    assert "id" in result.stages[1].detail, (
        "seeing which columns were never asked for is half the explanation"
    )

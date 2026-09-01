import io

import pytest
from fpdf import FPDF
from PIL import Image, ImageDraw, ImageFont

from schemagate.ingest.pdf import ocr_available, read_pdf

pytestmark = pytest.mark.ocr


def scanned_pdf() -> bytes:
    image = Image.new("RGB", (1700, 900), "white")
    draw = ImageDraw.Draw(image)
    try:
        font = ImageFont.truetype("arial.ttf", 46)
    except OSError:
        font = None
    for index, line in enumerate(
        ["INVOICE INV-2026-0147", "Supplier: Northgate Supply Co.", "Total 11425.24"]
    ):
        draw.text((50, 60 + index * 90), line, fill="black", font=font)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    buffer.seek(0)
    document = FPDF()
    document.add_page()
    document.image(buffer, x=6, y=6, w=198)
    return bytes(document.output())


def test_ocr_reads_a_scanned_page() -> None:
    result = read_pdf(scanned_pdf(), allow_ocr=True)

    assert "INV-2026-0147" in result.markdown
    assert result.needs_ocr is False, "OCR supplied the text, so nothing further is needed"
    assert result.route == "ocr"


def test_a_digital_pdf_does_not_pay_for_ocr() -> None:
    document = FPDF()
    document.add_page()
    document.set_font("helvetica", size=12)
    document.cell(0, 8, "Invoice INV-1 total 10.00")

    result = read_pdf(bytes(document.output()), allow_ocr=True)

    assert result.route == "native", (
        "OCR is slower and only for pages with no text layer; a readable PDF "
        "must not be routed through it"
    )


def test_ocr_is_reported_as_available_once_its_libraries_are_present() -> None:
    assert ocr_available() is True


async def test_the_pipeline_reads_a_scanned_invoice_end_to_end() -> None:
    from typing import Any

    from schemagate.extract.base import ModelT
    from schemagate.pipeline import Route, process
    from schemagate.schema.spec import ColumnSpec, TableSchema

    seen: list[str] = []

    class Recorder:
        async def extract(self, document: str, model: type[ModelT]) -> ModelT:
            seen.append(document)
            return model.model_validate(
                {"rows": [{"invoice_number": "INV-2026-0147", "total": "11425.24"}]}
            )

    schema = TableSchema(
        schema="public",
        name="invoices",
        columns=(
            ColumnSpec(name="invoice_number", data_type="text", nullable=False, ordinal=1),
            ColumnSpec(
                name="total", data_type="numeric", nullable=False, ordinal=2, numeric_scale=2
            ),
        ),
    )

    result: Any = await process(scanned_pdf(), "scan.pdf", schema, extractor=Recorder())

    assert result.route is Route.OCR_PDF
    assert "INV-2026-0147" in seen[0], "the text OCR recovered is what the model reads"
    assert result.status == "ok"

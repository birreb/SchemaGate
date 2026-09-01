import io
import threading

import pytest
from fpdf import FPDF
from PIL import Image, ImageDraw

from schemagate.errors import MalformedDocumentError
from schemagate.ingest import pdf as pdf_module
from schemagate.ingest.pdf import read_pdf, read_pdf_async


def text_pdf(lines: list[str]) -> bytes:
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("helvetica", size=12)
    for line in lines:
        pdf.cell(0, 8, line, new_x="LMARGIN", new_y="NEXT")
    return bytes(pdf.output())


def multipage_pdf(pages: int) -> bytes:
    pdf = FPDF()
    pdf.set_font("helvetica", size=12)
    for number in range(1, pages + 1):
        pdf.add_page()
        pdf.cell(0, 8, f"Page {number} total 10.00")
    return bytes(pdf.output())


def scanned_pdf() -> bytes:
    image = Image.new("RGB", (600, 300), "white")
    ImageDraw.Draw(image).text((20, 20), "Invoice INV-002 Total 125.00", fill="black")
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    buffer.seek(0)
    pdf = FPDF()
    pdf.add_page()
    pdf.image(buffer, x=10, y=10, w=180)
    return bytes(pdf.output())


def blank_pdf() -> bytes:
    pdf = FPDF()
    pdf.add_page()
    return bytes(pdf.output())


def test_a_digital_pdf_yields_markdown() -> None:
    result = read_pdf(text_pdf(["Invoice INV-001", "Total: 125.00"]))

    assert "INV-001" in result.markdown
    assert "125.00" in result.markdown


def test_a_digital_pdf_does_not_need_ocr() -> None:
    result = read_pdf(text_pdf(["Invoice INV-001", "Total: 125.00"]))

    assert result.needs_ocr is False
    assert result.pdf_type == "text_based"


def test_sparse_pages_do_not_send_a_readable_pdf_to_ocr() -> None:
    data = multipage_pdf(3)

    result = read_pdf(data)

    assert result.pages_flagged_sparse == (1, 2, 3), "the parser flags every page here"
    assert result.needs_ocr is False, (
        "pages_needing_ocr means the page is sparse, not that extraction failed; "
        "routing on it would send a readable PDF to a paid vision model"
    )
    assert "Page 1" in result.markdown


def test_page_count_is_reported() -> None:
    assert read_pdf(multipage_pdf(3)).page_count == 3


def test_a_scanned_pdf_needs_ocr() -> None:
    result = read_pdf(scanned_pdf())

    assert result.needs_ocr is True
    assert result.pdf_type in {"scanned", "image_based"}


def test_a_scanned_pdf_yields_no_markdown_rather_than_none() -> None:
    assert read_pdf(scanned_pdf()).markdown == ""


def test_a_blank_page_needs_ocr() -> None:
    assert read_pdf(blank_pdf()).needs_ocr is True


@pytest.mark.parametrize(
    ("label", "data"),
    [
        ("plain text", b"invoice_number,total\nINV-1,10\n"),
        ("truncated", b"%PDF-1.4\ntrash"),
        ("empty", b""),
    ],
)
def test_input_that_is_not_a_pdf_is_reported(label: str, data: bytes) -> None:
    with pytest.raises(MalformedDocumentError):
        read_pdf(data)


async def test_the_async_reader_returns_the_same_result() -> None:
    data = text_pdf(["Invoice INV-001"])

    assert (await read_pdf_async(data)).markdown == read_pdf(data).markdown


async def test_parsing_runs_off_the_event_loop_thread(monkeypatch: pytest.MonkeyPatch) -> None:
    caller = threading.get_ident()
    seen: list[int] = []
    original = pdf_module.read_pdf

    def spy(data: bytes) -> pdf_module.PdfText:
        seen.append(threading.get_ident())
        return original(data)

    monkeypatch.setattr(pdf_module, "read_pdf", spy)

    await read_pdf_async(text_pdf(["Invoice INV-001"]))

    assert seen and seen[0] != caller, (
        "pdf-inspector is native code holding the GIL; parsing on the event loop "
        "stalls every other request for the duration"
    )

import io

import pytest
from fpdf import FPDF
from openpyxl import Workbook
from PIL import Image

from schemagate.errors import UnsupportedFileTypeError
from schemagate.ingest.router import FileKind, detect_kind

OLE2_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"


def pdf_bytes() -> bytes:
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("helvetica", size=12)
    pdf.cell(0, 8, "Invoice")
    return bytes(pdf.output())


def xlsx_bytes() -> bytes:
    book = Workbook()
    book.worksheets[0].append(["a"])
    buffer = io.BytesIO()
    book.save(buffer)
    return buffer.getvalue()


def image_bytes(fmt: str) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (8, 8), "white").save(buffer, format=fmt)
    return buffer.getvalue()


def test_a_pdf_is_recognised() -> None:
    assert detect_kind(pdf_bytes()) is FileKind.PDF


def test_a_workbook_is_recognised() -> None:
    assert detect_kind(xlsx_bytes()) is FileKind.SPREADSHEET


def test_a_legacy_workbook_is_recognised() -> None:
    assert detect_kind(OLE2_MAGIC + b"\x00" * 32) is FileKind.SPREADSHEET


@pytest.mark.parametrize("fmt", ["PNG", "JPEG", "GIF", "TIFF", "WEBP"])
def test_images_are_recognised(fmt: str) -> None:
    assert detect_kind(image_bytes(fmt)) is FileKind.IMAGE


def test_heic_is_recognised_by_its_ftyp_box() -> None:
    data = b"\x00\x00\x00\x18ftypheic" + b"\x00" * 16

    assert detect_kind(data) is FileKind.IMAGE


def test_delimited_text_is_recognised() -> None:
    assert detect_kind(b"invoice_number,total\nINV-1,10.00\n") is FileKind.CSV


def test_text_in_another_encoding_is_still_text() -> None:
    assert detect_kind("supplier\nBjörn AB\n".encode("cp1252")) is FileKind.CSV


def test_the_file_name_does_not_override_the_bytes() -> None:
    assert detect_kind(pdf_bytes(), filename="statement.csv") is FileKind.PDF, (
        "a caller can name an upload anything; only the content decides"
    )


def test_a_zip_that_is_not_a_workbook_is_rejected() -> None:
    buffer = io.BytesIO()
    import zipfile

    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("word/document.xml", "<xml/>")

    with pytest.raises(UnsupportedFileTypeError):
        detect_kind(buffer.getvalue())


def test_an_unrecognised_binary_is_rejected() -> None:
    with pytest.raises(UnsupportedFileTypeError):
        detect_kind(b"\x00\x01\x02\x03 not anything we handle \x7f\xfe")


def test_an_empty_upload_is_rejected() -> None:
    with pytest.raises(UnsupportedFileTypeError):
        detect_kind(b"")


def test_utf16_text_is_not_mistaken_for_binary() -> None:
    data = "invoice_number,total\nINV-1,10.00\n".encode("utf-16")

    assert detect_kind(data) is FileKind.CSV, (
        "UTF-16 is half NUL bytes, and the CSV reader supports it through its "
        "byte order mark, so the router must not reject it as binary"
    )


def test_utf32_text_is_not_mistaken_for_binary() -> None:
    assert detect_kind("a,b\n1,2\n".encode("utf-32")) is FileKind.CSV

import io
import zipfile
from enum import StrEnum

from schemagate.errors import UnsupportedFileTypeError

# Shared with the decoder rather than duplicated, so the two cannot disagree
# about which byte order marks mean text.
from schemagate.ingest.tabular import BOMS as TEXT_BOMS

ZIP_MAGIC = b"PK\x03\x04"

# Compound File Binary, the container behind .xls and other pre-2007 Office files.
OLE2_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"

# HEIC and friends put a `ftyp` box four bytes in rather than at the start.
FTYP_BRANDS = (b"heic", b"heix", b"hevc", b"mif1", b"msf1", b"avif")

# Entries that identify a zip archive as a spreadsheet rather than a document.
XLSX_MARKER = "xl/"
ODS_MIMETYPE = "application/vnd.oasis.opendocument.spreadsheet"


class FileKind(StrEnum):
    CSV = "csv"
    SPREADSHEET = "spreadsheet"
    PDF = "pdf"
    IMAGE = "image"


def detect_kind(data: bytes, filename: str | None = None) -> FileKind:
    """Identify an upload from its content.

    `filename` is accepted so it can be quoted in an error, never to decide the
    outcome. A caller can name a file anything, and routing a PDF to the CSV
    reader because it was posted as `statement.csv` is a bug waiting to happen.
    """
    if not data:
        raise UnsupportedFileTypeError(_describe("The upload is empty.", filename))

    if data.startswith(b"%PDF-"):
        return FileKind.PDF
    if data.startswith(OLE2_MAGIC):
        return FileKind.SPREADSHEET
    if data.startswith(ZIP_MAGIC):
        return _zip_kind(data, filename)
    if _is_image(data):
        return FileKind.IMAGE
    if _is_text(data):
        return FileKind.CSV

    raise UnsupportedFileTypeError(
        _describe("The upload is not a PDF, spreadsheet, image or delimited text file.", filename)
    )


def _zip_kind(data: bytes, filename: str | None) -> FileKind:
    """Tell a workbook from any other zip archive.

    Both .xlsx and .docx are zip files. Handing a Word document to the
    spreadsheet reader would fail with a confusing error, so it is separated
    here where the message can say what was actually uploaded.
    """
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            names = archive.namelist()
            if any(name.startswith(XLSX_MARKER) for name in names):
                return FileKind.SPREADSHEET
            if "mimetype" in names and archive.read("mimetype").decode() == ODS_MIMETYPE:
                return FileKind.SPREADSHEET
    except (zipfile.BadZipFile, UnicodeDecodeError, KeyError) as error:
        raise UnsupportedFileTypeError(
            _describe(
                f"The upload looks like a zip archive but could not be read: {error}", filename
            )
        ) from error

    raise UnsupportedFileTypeError(
        _describe("The upload is a zip archive but not a spreadsheet.", filename)
    )


def _is_image(data: bytes) -> bool:
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return True
    if data[:3] == b"\xff\xd8\xff":
        return True
    if data[:4] == b"GIF8":
        return True
    if data[:4] in (b"II*\x00", b"MM\x00*"):
        return True
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return True
    return data[4:8] == b"ftyp" and data[8:12] in FTYP_BRANDS


def _is_text(data: bytes) -> bool:
    """Treat the upload as text when nothing in it says otherwise.

    A NUL byte is the usual tell for binary, but only for the single-byte and
    UTF-8 encodings. UTF-16 text is roughly half NUL bytes by construction, so
    a byte order mark has to be honoured before that rule is applied.
    """
    if any(data.startswith(bom) for bom, _ in TEXT_BOMS):
        return True

    sample = data[:8192]
    if b"\x00" in sample:
        return False
    return bool(sample.strip())


def _describe(message: str, filename: str | None) -> str:
    return f"{message} (filename: {filename!r})" if filename else message

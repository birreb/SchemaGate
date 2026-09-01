import importlib.util
import os
import pathlib
from dataclasses import dataclass
from functools import cache

import anyio.to_thread
import pdf_inspector

from schemagate.errors import MalformedDocumentError

# Classifications where the page carries no recoverable text layer.
IMAGE_TYPES = frozenset({"scanned", "image_based"})


@dataclass(frozen=True, slots=True)
class PdfText:
    """What was recovered, how, and whether it was enough."""

    markdown: str
    page_count: int
    pdf_type: str
    needs_ocr: bool
    has_encoding_issues: bool
    pages_flagged_sparse: tuple[int, ...]
    route: str = "native"


@cache
def ocr_available() -> bool:
    """Whether local OCR can run, and point the parser at its libraries if so.

    pdf-inspector ships neither PDFium nor ONNX Runtime, by design: most
    documents never need them and they are tens of megabytes. Both are installed
    by the `ocr` extra, and are found here rather than left to the system library
    path, which on Windows would not find them at all.

    The OCR models themselves are downloaded and checksum-verified by the parser
    on first use, so nothing has to be vendored.
    """
    pdfium = _installed_at("pypdfium2_raw")
    runtime = _installed_at("onnxruntime")
    if pdfium is None or runtime is None:
        return False

    _point_at("PDFIUM_LIB_PATH", pdfium, "pdfium")
    _point_at("ORT_DYLIB_PATH", runtime / "capi", "onnxruntime")
    return "PDFIUM_LIB_PATH" in os.environ and "ORT_DYLIB_PATH" in os.environ


def _installed_at(module: str) -> pathlib.Path | None:
    """Where a package lives, without importing it.

    Only its location is wanted. Importing onnxruntime to read one path would
    pull numpy in behind it, which is slow at startup and, for a package this
    project never calls, work done for nothing.
    """
    try:
        spec = importlib.util.find_spec(module)
    except (ImportError, ValueError):
        return None
    return pathlib.Path(spec.origin).parent if spec and spec.origin else None


def _point_at(variable: str, directory: pathlib.Path, stem: str) -> None:
    """Set `variable` to the shared library in `directory`, unless already set."""
    if os.environ.get(variable):
        return
    for suffix in (".dll", ".so", ".dylib"):
        for candidate in sorted(directory.glob(f"*{stem}*{suffix}*")):
            os.environ[variable] = str(candidate)
            return


def read_pdf(data: bytes, allow_ocr: bool = False) -> PdfText:
    """Extract markdown from a PDF, using OCR only where there is no text layer.

    Blocking and CPU bound. Call `read_pdf_async` from request handlers.
    """
    try:
        result = pdf_inspector.process_pdf_bytes(data)
    except ValueError as error:
        raise MalformedDocumentError(f"The file is not a readable PDF: {error}") from error

    markdown = result.markdown or ""
    needs_ocr = _needs_ocr(result.pdf_type, markdown, result.has_encoding_issues)

    if needs_ocr and allow_ocr and ocr_available():
        recovered = _run_ocr(data)
        if recovered is not None and recovered.strip():
            return PdfText(
                markdown=recovered,
                page_count=result.page_count,
                pdf_type=result.pdf_type,
                needs_ocr=False,
                has_encoding_issues=result.has_encoding_issues,
                pages_flagged_sparse=tuple(result.pages_needing_ocr),
                route="ocr",
            )

    return PdfText(
        markdown=markdown,
        page_count=result.page_count,
        pdf_type=result.pdf_type,
        needs_ocr=needs_ocr,
        has_encoding_issues=result.has_encoding_issues,
        pages_flagged_sparse=tuple(result.pages_needing_ocr),
        route="native",
    )


def _run_ocr(data: bytes) -> str | None:
    """Read the pages the native layer could not.

    `auto` routes only the pages that failed, so a mixed document pays for OCR
    on the scanned pages alone. A failure here is not fatal: the caller still has
    the native result and the knowledge that it was not enough.
    """
    try:
        return str(pdf_inspector.process_pdf_with_ocr_bytes(data, mode="auto").markdown or "")
    except (ValueError, OSError, RuntimeError):
        return None


async def read_pdf_async(data: bytes, allow_ocr: bool = False) -> PdfText:
    """Run `read_pdf` in a worker thread.

    pdf-inspector is compiled native code and holds the GIL for the length of a
    call, so parsing on the event loop stalls every other request in flight. OCR
    takes the better part of a second, which makes this more important, not less.
    """
    return await anyio.to_thread.run_sync(read_pdf, data, allow_ocr)


def _needs_ocr(pdf_type: str, markdown: str, has_encoding_issues: bool) -> bool:
    """Decide whether the native text layer was good enough to use.

    Deliberately not based on `pages_needing_ocr`. That field marks pages the
    parser found sparse, and it lists every page of a short but perfectly
    readable invoice. Routing on it would send documents that already parsed
    cleanly through OCR, or to a paid vision model.

    Encoding issues do force OCR. A broken CID map yields text that looks
    plausible and is wrong, which is worse than no text at all.
    """
    if pdf_type in IMAGE_TYPES:
        return True
    if has_encoding_issues:
        return True
    return not markdown.strip()

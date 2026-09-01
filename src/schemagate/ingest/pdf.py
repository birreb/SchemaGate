import importlib.util
import io
import os
import pathlib
from collections.abc import Sequence
from dataclasses import dataclass
from functools import cache
from typing import Any

import anyio.to_thread

from schemagate.errors import MalformedDocumentError
from schemagate.ingest.images import NormalisedImage, normalise
from schemagate.optional import require

# Classifications where the page carries no recoverable text layer.
IMAGE_TYPES = frozenset({"scanned", "image_based"})

# A page of a real document carries hundreds of characters. OCR returning a
# handful means it failed, whether or not it says so, and CI proved it does
# not always say so: the same blurred page that the parser flags on one
# platform it passes silently on another, returning a single wrong character
# both times. Self-assessment is a useful signal and a poor guarantee.
MIN_OCR_CHARS_PER_PAGE = 40


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
    # The parser's own verdict on its OCR. It reports which pages it could
    # not read well enough, which is the difference between escalating and
    # handing a model the nonsense OCR produced.
    hosted_recommended: bool = False
    pages_for_vision: tuple[int, ...] = ()


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
    parser = require("pdf_inspector")
    try:
        result = parser.process_pdf_bytes(data)
    except ValueError as error:
        raise MalformedDocumentError(f"The file is not a readable PDF: {error}") from error

    markdown = result.markdown or ""
    needs_ocr = _needs_ocr(result.pdf_type, markdown, result.has_encoding_issues)

    if needs_ocr and allow_ocr and ocr_available():
        recovered = _run_ocr(data)
        if recovered is not None:
            text, flagged = recovered
            escalate = _pages_to_reread(text, flagged, result)
            if text.strip() or escalate:
                return PdfText(
                    markdown=text,
                    page_count=result.page_count,
                    pdf_type=result.pdf_type,
                    needs_ocr=False,
                    has_encoding_issues=result.has_encoding_issues,
                    pages_flagged_sparse=tuple(result.pages_needing_ocr),
                    route="ocr",
                    hosted_recommended=bool(escalate),
                    pages_for_vision=escalate,
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


def _pages_to_reread(text: str, flagged: Sequence[int], result: Any) -> tuple[int, ...]:
    """Which pages a vision model should look at instead.

    The parser's own `pages_recommending_hosted` when it sets it, and otherwise
    a plain length check: a page that produced almost no text did not survive
    OCR regardless of what the parser thinks of its own work.
    """
    if flagged:
        return tuple(flagged)

    pages = max(1, int(getattr(result, "page_count", 1) or 1))
    if len(text.strip()) < MIN_OCR_CHARS_PER_PAGE * pages:
        return tuple(range(1, pages + 1))
    return ()


def _run_ocr(data: bytes) -> tuple[str, tuple[int, ...]] | None:
    """Read the pages the native layer could not, and note any it could not either.

    `auto` routes only the pages that failed, so a mixed document pays for OCR
    on the scanned pages alone. A failure here is not fatal: the caller still has
    the native result and the knowledge that it was not enough.

    `pages_recommending_hosted` is the parser saying its own output is not worth
    trusting for those pages. Measured on a small blurred scan it returns a
    single wrong character, so ignoring the signal would hand that to a model as
    the document and get a confident, invented answer back.
    """
    parser = require("pdf_inspector")
    try:
        result = parser.process_pdf_with_ocr_bytes(data, mode="auto")
    except (ValueError, OSError, RuntimeError):
        return None
    escalate = tuple(getattr(result, "pages_recommending_hosted", ()) or ())
    return str(result.markdown or ""), escalate


def render_pages(data: bytes, pages: Sequence[int]) -> tuple[NormalisedImage, ...]:
    """Rasterise one-indexed pages so a vision model can look at them.

    Uses the PDFium that already ships with the `ocr` extra. PyMuPDF is the
    better known choice for this and is AGPL, which would make this project
    AGPL too.
    """
    pdfium = require("pypdfium2")

    rendered: list[NormalisedImage] = []
    document = pdfium.PdfDocument(data)
    try:
        for number in pages:
            index = number - 1
            if not 0 <= index < len(document):
                continue
            # 200 DPI against the 72 a PDF point assumes. Enough for small print
            # without producing an image the model will only downscale again.
            bitmap = document[index].render(scale=200 / 72)
            buffer = io.BytesIO()
            bitmap.to_pil().save(buffer, format="PNG")
            rendered.append(normalise(buffer.getvalue()))
    finally:
        document.close()
    return tuple(rendered)


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

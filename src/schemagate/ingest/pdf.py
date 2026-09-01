from dataclasses import dataclass

import anyio.to_thread
import pdf_inspector

from schemagate.errors import MalformedDocumentError

# Classifications where the page carries no recoverable text layer.
IMAGE_TYPES = frozenset({"scanned", "image_based"})


@dataclass(frozen=True, slots=True)
class PdfText:
    """What the native layer recovered, and whether it was enough."""

    markdown: str
    page_count: int
    pdf_type: str
    needs_ocr: bool
    has_encoding_issues: bool
    pages_flagged_sparse: tuple[int, ...]


def read_pdf(data: bytes) -> PdfText:
    """Extract markdown from a PDF and decide whether OCR is still required.

    Blocking and CPU bound. Call `read_pdf_async` from request handlers.
    """
    try:
        result = pdf_inspector.process_pdf_bytes(data)
    except ValueError as error:
        raise MalformedDocumentError(f"The file is not a readable PDF: {error}") from error

    markdown = result.markdown or ""
    return PdfText(
        markdown=markdown,
        page_count=result.page_count,
        pdf_type=result.pdf_type,
        needs_ocr=_needs_ocr(result.pdf_type, markdown, result.has_encoding_issues),
        has_encoding_issues=result.has_encoding_issues,
        pages_flagged_sparse=tuple(result.pages_needing_ocr),
    )


async def read_pdf_async(data: bytes) -> PdfText:
    """Run `read_pdf` in a worker thread.

    pdf-inspector is compiled native code and holds the GIL for the length of a
    call, so parsing on the event loop stalls every other request in flight.
    """
    return await anyio.to_thread.run_sync(read_pdf, data)


def _needs_ocr(pdf_type: str, markdown: str, has_encoding_issues: bool) -> bool:
    """Decide whether the native text layer was good enough to use.

    Deliberately not based on `pages_needing_ocr`. That field marks pages the
    parser found sparse, and it lists every page of a short but perfectly
    readable invoice. Routing on it would send documents that already parsed
    cleanly to a paid vision model.

    Encoding issues do force OCR. A broken CID map yields text that looks
    plausible and is wrong, which is worse than no text at all.
    """
    if pdf_type in IMAGE_TYPES:
        return True
    if has_encoding_issues:
        return True
    return not markdown.strip()

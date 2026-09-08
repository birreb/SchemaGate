import importlib.util
import io
import os
import pathlib
from collections.abc import Sequence
from dataclasses import dataclass
from functools import cache
from itertools import pairwise
from typing import Any, Protocol

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

    if not needs_ocr:
        # Rebuilt from where each piece of text sits, so that two columns
        # stay two columns. The parser's markdown can put a tax rate and an
        # amount into one cell, and `25 26 618,54` is one number to a model.
        markdown = _layout_text(parser, data) or markdown

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


class Positioned(Protocol):
    """A piece of text and where it sits, as the parser reports it."""

    text: str
    x: float
    y: float
    width: float
    font_size: float
    page: int


# A gap wider than this many font sizes between two pieces on one line is a
# column boundary. A word space is about a quarter of the font size, and a
# thousands separator inside one piece is never a gap at all.
COLUMN_GAP = 1.2

# Pieces whose baselines differ by less than this share of the font size sit
# on one line. Superscripts and slightly misaligned cells still join it.
SAME_LINE = 0.4

# Lines that have to stand together before they are read as a table, and the
# columns such a table has to have. Two lines side by side are a label and a
# value as often as they are a table, and two columns cannot lose one and
# still be read.
SHORTEST_TABLE = 3
NARROWEST_TABLE = 3

# A step down the page wider than this many times the tightest step so far
# ends the table. Rows of one table are evenly spaced, and what follows the
# last of them, a totals column or the next block of the letter, sits after a
# wider space than the rows left between themselves.
BLOCK_GAP = 1.35


@dataclass(frozen=True)
class _Cell:
    """One column's text on one line, and the span of page it covers."""

    text: str
    left: float
    right: float


def layout_lines(items: Sequence[Positioned]) -> str:
    """Lines of text in reading order, with column gaps marked by ` | `.

    Grouped by page, then by baseline from the top of the page down, then
    ordered left to right. Two pieces separated by more than a word's worth of
    space are different columns and are joined with a bar; anything closer is
    joined with a space, and the spaces inside a piece are left as they are.

    A cell a row leaves empty is kept as an empty column, so what follows it
    stays under its own heading. A line billed as a fixed fee prints no
    quantity and no unit price, and dropping those two cells puts the tax
    rate beside it where the quantity belongs.
    """
    pages: dict[int, list[Positioned]] = {}
    for item in items:
        if item.text.strip():
            pages.setdefault(item.page, []).append(item)

    rendered: list[str] = []
    for page in sorted(pages):
        lines: list[tuple[float, list[Positioned]]] = []
        for item in sorted(pages[page], key=lambda piece: (-piece.y, piece.x)):
            if lines and abs(lines[-1][0] - item.y) <= max(2.0, item.font_size * SAME_LINE):
                lines[-1][1].append(item)
            else:
                lines.append((item.y, [item]))
        rendered.extend(
            _rendered([_cells_of(pieces) for _, pieces in lines], [y for y, _ in lines])
        )
        rendered.append("")
    return "\n".join(rendered).strip()


def _cells_of(pieces: list[Positioned]) -> list[_Cell]:
    """One line's pieces as cells, joined across gaps narrower than a column."""
    pieces.sort(key=lambda piece: piece.x)
    cells = [_Cell(pieces[0].text, pieces[0].x, pieces[0].x + pieces[0].width)]
    for previous, current in pairwise(pieces):
        gap = current.x - (previous.x + previous.width)
        right = current.x + current.width
        if gap > COLUMN_GAP * max(previous.font_size, 6.0):
            cells.append(_Cell(current.text, current.x, right))
        else:
            last = cells[-1]
            cells[-1] = _Cell(f"{last.text} {current.text}", last.left, right)
    return cells


def _rendered(rows: Sequence[Sequence[_Cell]], baselines: Sequence[float]) -> list[str]:
    """A page's lines as text, with the empty cells of a table kept in place.

    A block's columns are only used once one of its rows has a cell in every
    one of them. Label and value pairs down a page also stand in a block, and
    their values start wherever the label ends, so the columns read off them
    are an artefact of the labels' lengths and no row is short of anything.
    """
    lines = [_plain(row) for row in rows]
    for start, stop in _blocks(rows, baselines):
        columns = _columns(rows[start:stop])
        if len(columns) < NARROWEST_TABLE:
            continue
        placed = [_placed(row, columns) for row in rows[start:stop]]
        if not any(row is not None and len(row) == len(columns) for row in placed):
            continue
        for offset, row in enumerate(placed):
            if row is not None and len(row) < len(columns):
                lines[start + offset] = _joined(row)
    return lines


def _plain(row: Sequence[_Cell]) -> str:
    return " | ".join(cell.text for cell in row).strip()


def _placed(row: Sequence[_Cell], columns: Sequence[tuple[float, float]]) -> dict[int, str] | None:
    """Each cell of a row under its column, or nothing when two cells share one.

    A shared column means the columns were read off some other row that ran
    across this one, and joining the two cells would drop a break the line
    does have. The line is left as it stands instead.
    """
    slots: dict[int, str] = {}
    for cell in row:
        at = next(
            (
                index
                for index, (left, right) in enumerate(columns)
                if cell.left < right and left < cell.right
            ),
            None,
        )
        if at is None or at in slots:
            return None
        slots[at] = cell.text
    return slots


def _joined(row: dict[int, str]) -> str:
    """A row's cells with its empty columns kept, apart from the outer ones.

    Empty columns before the first cell and after the last are dropped. A
    total sitting under the right of a table is not a row of it, and treating
    it as one would put bars in front of every such line.
    """
    text = row[min(row)]
    for index in range(min(row) + 1, max(row) + 1):
        text += " |" + (f" {row[index]}" if index in row else "")
    return text.strip()


def _blocks(rows: Sequence[Sequence[_Cell]], baselines: Sequence[float]) -> list[tuple[int, int]]:
    """Runs of evenly spaced neighbouring lines that carry more than one column.

    A line of running text is one cell wide and ends the run, and so does a
    step down the page wider than the rows have been leaving between
    themselves. A table's columns are then worked out from its own rows,
    rather than from an address block above it or a totals column below.
    """
    blocks: list[tuple[int, int]] = []
    start: int | None = None
    pitch = 0.0

    def close(stop: int) -> None:
        if start is not None and stop - start >= SHORTEST_TABLE:
            blocks.append((start, stop))

    for index, row in enumerate(rows):
        if len(row) < 2:
            close(index)
            start, pitch = None, 0.0
            continue
        if start is None:
            start, pitch = index, 0.0
            continue
        gap = baselines[index - 1] - baselines[index]
        if pitch and gap > BLOCK_GAP * pitch:
            close(index)
            start, pitch = index, 0.0
        else:
            pitch = min(pitch, gap) if pitch else gap
    close(len(rows))
    return blocks


def _columns(rows: Sequence[Sequence[_Cell]]) -> list[tuple[float, float]]:
    """The spans of page a block's columns cover, merged where cells overlap."""
    columns: list[list[float]] = []
    for left, right in sorted((cell.left, cell.right) for row in rows for cell in row):
        if columns and left <= columns[-1][1]:
            columns[-1][1] = max(columns[-1][1], right)
        else:
            columns.append([left, right])
    return [(left, right) for left, right in columns]


def _layout_text(parser: Any, data: bytes) -> str:
    """The text layer rebuilt from positions, or empty when the parser cannot say."""
    try:
        items = parser.extract_text_with_positions_bytes(data)
    except Exception:
        # Anything the parser cannot position falls back to its own markdown.
        return ""
    return layout_lines(items)


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

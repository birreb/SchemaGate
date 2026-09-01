import time
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

import anyio.to_thread

from schemagate.errors import ExtractorNotConfiguredError, UnsupportedFileTypeError
from schemagate.extract.base import Extractor, compose
from schemagate.ingest.headers import map_headers
from schemagate.ingest.images import NormalisedImage, normalise
from schemagate.ingest.pdf import read_pdf_async, render_pages
from schemagate.ingest.router import FileKind, detect_kind
from schemagate.ingest.tabular import Table, align, read_csv, read_spreadsheet
from schemagate.schema.factory import build_container_model
from schemagate.schema.spec import TableSchema
from schemagate.validate.gate import validate
from schemagate.validate.report import Failure
from schemagate.validate.rules import SumRule


class Route(StrEnum):
    TABULAR = "tabular"
    NATIVE_PDF = "native_pdf"
    OCR_PDF = "ocr_pdf"
    VISION = "vision"


@dataclass(frozen=True, slots=True)
class Stage:
    """One step of the pipeline, and what it did.

    The deterministic pipeline is the argument this project makes, and an
    argument nobody can see is not much of one. Each step says what it found so
    the path from a table definition to a row is legible rather than asserted.
    """

    name: str
    detail: str
    ms: int


@dataclass(frozen=True, slots=True)
class Extraction:
    """What came out of a document, and everything that did not hold."""

    table: str
    route: Route
    rows: tuple[dict[str, Any], ...]
    failures: tuple[Failure, ...] = ()
    unmatched_headers: tuple[str, ...] = ()
    missing_columns: tuple[str, ...] = ()
    timings_ms: dict[str, int] = field(default_factory=dict)
    stages: tuple[Stage, ...] = ()

    @property
    def status(self) -> str:
        """`flagged` is not an error.

        Extraction succeeded and a check did not hold, which is neither the
        caller's mistake nor ours. The caller decides what to do about it.
        """
        return "ok" if not self.failures else "flagged"


async def process(
    data: bytes,
    filename: str | None,
    schema: TableSchema,
    extractor: Extractor | None,
    rules: Sequence[SumRule] = (),
    instructions: str | None = None,
) -> Extraction:
    """Take a file to validated rows for one table.

    Deterministic all the way through. The only step that involves a model is
    the one for documents that have no data grid to read.
    """
    timings: dict[str, int] = {}
    steps = _Steps()
    kind = detect_kind(data, filename)

    with _timed(timings, "parse"):
        route, rows, alignment = await _read(data, kind, schema, extractor, instructions, steps)

    with _timed(timings, "validate"), steps.step("check") as note:
        report = validate(rows, schema, rules)
        note.detail = _describe_check(report.rows, report.failures)

    return Extraction(
        table=schema.qualified_name,
        route=route,
        rows=report.rows,
        failures=report.failures,
        unmatched_headers=alignment[0],
        missing_columns=alignment[1],
        timings_ms=timings,
        stages=tuple(steps.recorded),
    )


def _describe_check(rows: Sequence[Any], failures: Sequence[Failure]) -> str:
    counted = f"{len(rows)} row{'' if len(rows) == 1 else 's'}"
    if not failures:
        return f"{counted}, no failures"
    kinds = sorted({failure.rule for failure in failures})
    plural = "" if len(failures) == 1 else "s"
    return f"{counted}, {len(failures)} failure{plural}: {', '.join(kinds)}"


@dataclass
class _Note:
    detail: str = ""


class _Steps:
    """Collects what each stage did, with how long it took."""

    def __init__(self) -> None:
        self.recorded: list[Stage] = []

    @contextmanager
    def step(self, name: str) -> Iterator[_Note]:
        note = _Note()
        started = time.perf_counter()
        try:
            yield note
        finally:
            elapsed = int((time.perf_counter() - started) * 1000)
            self.recorded.append(Stage(name=name, detail=note.detail, ms=elapsed))


async def _read(
    data: bytes,
    kind: FileKind,
    schema: TableSchema,
    extractor: Extractor | None,
    instructions: str | None = None,
    steps: "_Steps | None" = None,
) -> tuple[Route, tuple[dict[str, str | None], ...], tuple[tuple[str, ...], tuple[str, ...]]]:
    steps = steps or _Steps()

    if kind in {FileKind.CSV, FileKind.SPREADSHEET}:
        with steps.step("read") as note:
            table = await _read_table(data, kind)
            source = "CSV" if kind is FileKind.CSV else "Spreadsheet"
            note.detail = f"{source}, {len(table.headers)} columns, {len(table.rows)} data rows"

        with steps.step("match") as note:
            aligned = align(table, schema)

            # Headings that do not match by spelling may still mean a column:
            # `Fakturanr` is an invoice number and no amount of string handling
            # will say so. Only the names are sent, never the rows, so the data
            # still never reaches a provider.
            aliases: dict[str, str] = {}
            if aligned.unmatched_headers and aligned.missing_columns:
                aliases = await map_headers(aligned.unmatched_headers, schema, extractor)
                if aliases:
                    aligned = align(table, schema, aliases)

            note.detail = _describe_match(aligned, schema, aliases)

        return Route.TABULAR, aligned.rows, (aligned.unmatched_headers, aligned.missing_columns)

    if kind is FileKind.PDF:
        with steps.step("read") as note:
            parsed = await read_pdf_async(data, allow_ocr=True)
            note.detail = (
                f"PDF, {parsed.page_count} page{'' if parsed.page_count == 1 else 's'}, "
                f"{parsed.pdf_type}, {len(parsed.markdown)} characters "
                f"via {'OCR' if parsed.route == 'ocr' else 'the text layer'}"
            )

        if parsed.needs_ocr:
            raise UnsupportedFileTypeError(
                "This PDF has no readable text layer, and local OCR is not installed. "
                "Install the `ocr` extra to read scanned documents without sending "
                "them anywhere."
            )

        if parsed.hosted_recommended:
            # The parser is telling us its own OCR is not worth trusting for
            # these pages. Sending that text on would produce a confident,
            # invented answer, so the pages themselves go to the model instead.
            images = await anyio.to_thread.run_sync(render_pages, data, parsed.pages_for_vision)
            if images:
                with steps.step("extract") as note:
                    rows = await _ask(extractor, "", schema, instructions, images)
                    note.detail = (
                        f"OCR could not read {len(images)} page"
                        f"{'' if len(images) == 1 else 's'}, so the page itself went to a "
                        f"vision model. {len(rows)} rows returned"
                    )
                return Route.VISION, rows, ((), ())

        route = Route.OCR_PDF if parsed.route == "ocr" else Route.NATIVE_PDF
        with steps.step("extract") as note:
            rows = await _ask(extractor, parsed.markdown, schema, instructions)
            note.detail = _describe_extract(len(rows), schema, "text")
        return route, rows, ((), ())

    if kind is FileKind.IMAGE:
        with steps.step("read") as note:
            # Normalised off the event loop: decoding and resampling a photograph
            # is the same kind of CPU-bound work as parsing a PDF.
            image = await anyio.to_thread.run_sync(normalise, data)
            note.detail = f"Image, normalised to {image.width} by {image.height}"

        with steps.step("extract") as note:
            rows = await _ask(extractor, "", schema, instructions, (image,))
            note.detail = _describe_extract(len(rows), schema, "the image")
        return Route.VISION, rows, ((), ())

    raise UnsupportedFileTypeError(f"{kind.value} uploads are not supported.")


def _describe_match(
    aligned: Any, schema: TableSchema, aliases: dict[str, str] | None = None
) -> str:
    """What lined up, and what the database keeps for itself."""
    wanted = schema.extractable
    matched = len(wanted) - len(aligned.missing_columns)
    parts = [f"{matched} of {len(wanted)} columns matched"]
    owned = [c.name for c in schema.columns if not c.is_extractable]
    if owned:
        parts.append(f"the database fills {', '.join(owned)}")
    if aliases:
        named = ", ".join(f"{header} to {column}" for header, column in aliases.items())
        parts.append(f"a model matched {named} by meaning")
    if aligned.unmatched_headers:
        parts.append(f"ignored {', '.join(aligned.unmatched_headers)}")
    return "; ".join(parts)


def _describe_extract(rows: int, schema: TableSchema, source: str) -> str:
    fields = len(schema.extractable)
    return (
        f"A model read {source} against a schema of {fields} "
        f"field{'' if fields == 1 else 's'}, returning {rows} row{'' if rows == 1 else 's'}"
    )


async def _read_table(data: bytes, kind: FileKind) -> Table:
    """Parse off the event loop.

    Both readers are compiled native code that holds the GIL, so a large file
    parsed inline would stall every other request in flight.
    """
    reader = read_csv if kind is FileKind.CSV else read_spreadsheet
    return await anyio.to_thread.run_sync(reader, data)


async def _ask(
    extractor: Extractor | None,
    document: str,
    schema: TableSchema,
    instructions: str | None = None,
    images: Sequence[NormalisedImage] = (),
) -> tuple[dict[str, str | None], ...]:
    if extractor is None:
        # Not the caller's mistake. The file type is supported and they could
        # not have known the server has no model behind it.
        raise ExtractorNotConfiguredError(
            "This document needs a model to read it, and no model is configured. "
            "Set SCHEMAGATE_PROVIDER."
        )

    container = build_container_model(schema)
    answer = await extractor.extract(compose(document, instructions), container, images)
    rows: list[dict[str, str | None]] = answer.model_dump()["rows"]
    return tuple(rows)


@contextmanager
def _timed(into: dict[str, int], name: str) -> Iterator[None]:
    """Record how long a stage took, in whole milliseconds."""
    started = time.perf_counter()
    try:
        yield
    finally:
        into[name] = int((time.perf_counter() - started) * 1000)

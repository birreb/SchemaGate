import time
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

import anyio.to_thread

from schemagate.errors import UnsupportedFileTypeError
from schemagate.extract.base import Extractor
from schemagate.ingest.pdf import read_pdf_async
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
    VISION = "vision"


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
) -> Extraction:
    """Take a file to validated rows for one table.

    Deterministic all the way through. The only step that involves a model is
    the one for documents that have no data grid to read.
    """
    timings: dict[str, int] = {}
    kind = detect_kind(data, filename)

    with _timed(timings, "parse"):
        route, rows, alignment = await _read(data, kind, schema, extractor)

    with _timed(timings, "validate"):
        report = validate(rows, schema, rules)

    return Extraction(
        table=schema.qualified_name,
        route=route,
        rows=report.rows,
        failures=report.failures,
        unmatched_headers=alignment[0],
        missing_columns=alignment[1],
        timings_ms=timings,
    )


async def _read(
    data: bytes, kind: FileKind, schema: TableSchema, extractor: Extractor | None
) -> tuple[Route, tuple[dict[str, str | None], ...], tuple[tuple[str, ...], tuple[str, ...]]]:
    if kind in {FileKind.CSV, FileKind.SPREADSHEET}:
        table = await _read_table(data, kind)
        aligned = align(table, schema)
        return Route.TABULAR, aligned.rows, (aligned.unmatched_headers, aligned.missing_columns)

    if kind is FileKind.PDF:
        parsed = await read_pdf_async(data)
        if parsed.needs_ocr:
            raise UnsupportedFileTypeError(
                "This PDF has no readable text layer. Scanned documents need the OCR or "
                "vision route, which is not built yet."
            )
        return Route.NATIVE_PDF, await _ask(extractor, parsed.markdown, schema), ((), ())

    raise UnsupportedFileTypeError(
        f"{kind.value} uploads need the vision route, which is not built yet."
    )


async def _read_table(data: bytes, kind: FileKind) -> Table:
    """Parse off the event loop.

    Both readers are compiled native code that holds the GIL, so a large file
    parsed inline would stall every other request in flight.
    """
    reader = read_csv if kind is FileKind.CSV else read_spreadsheet
    return await anyio.to_thread.run_sync(reader, data)


async def _ask(
    extractor: Extractor | None, document: str, schema: TableSchema
) -> tuple[dict[str, str | None], ...]:
    if extractor is None:
        raise UnsupportedFileTypeError(
            "This document needs a model to read it, and no extractor is configured."
        )

    container = build_container_model(schema)
    answer = await extractor.extract(document, container)
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

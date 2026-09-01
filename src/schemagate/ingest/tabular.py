import csv
import datetime as dt
import io
from dataclasses import dataclass
from typing import Any

from charset_normalizer import from_bytes
from python_calamine import CalamineError, CalamineWorkbook

from schemagate.errors import MalformedDocumentError

# Restricted on purpose. Left to guess freely, the sniffer will happily decide
# that a column of dates is colon-delimited.
DELIMITERS = ",;\t|"

SNIFF_BYTES = 8192


@dataclass(frozen=True, slots=True)
class Table:
    """A grid of strings. Type coercion is the validation gate's job."""

    headers: tuple[str, ...]
    rows: tuple[tuple[str, ...], ...]


def read_csv(data: bytes) -> Table:
    """Parse delimited text into a Table.

    Handles the two things that actually break real uploads: spreadsheet
    exports that leave a byte order mark on the first header, and European
    files that use semicolons because the comma is the decimal separator.
    """
    text = _decode(data)
    if not text.strip():
        raise MalformedDocumentError("The file is empty.")

    reader = csv.reader(io.StringIO(text, newline=""), delimiter=_sniff(text))
    grid = [row for row in reader if any(cell.strip() for cell in row)]
    if not grid:
        raise MalformedDocumentError("The file has no header row.")

    headers = tuple(header.strip() for header in grid[0])
    _reject_duplicates(headers)
    return Table(headers=headers, rows=tuple(_align(row, len(headers)) for row in grid[1:]))


def read_spreadsheet(data: bytes, sheet: str | None = None) -> Table:
    """Parse a workbook into a Table, reading the first sheet unless named.

    Uses calamine, which is a Rust engine and also reads the legacy `.xls` and
    `.ods` formats that openpyxl cannot open at all.
    """
    try:
        book = CalamineWorkbook.from_filelike(io.BytesIO(data))
    except CalamineError as error:
        raise MalformedDocumentError(f"The file is not a readable workbook: {error}") from error

    try:
        worksheet = _select_sheet(book, sheet)
        grid = [[_render(cell) for cell in row] for row in worksheet.to_python()]
    finally:
        book.close()

    grid = [row for row in grid if any(cell.strip() for cell in row)]
    if not grid:
        raise MalformedDocumentError(f"Sheet {worksheet.name!r} has no rows.")

    headers = tuple(header.strip() for header in grid[0])
    _reject_duplicates(headers)
    return Table(headers=headers, rows=tuple(_align(row, len(headers)) for row in grid[1:]))


def _select_sheet(book: CalamineWorkbook, sheet: str | None) -> Any:
    if sheet is None:
        return book.get_sheet_by_index(0)
    if sheet not in book.sheet_names:
        available = ", ".join(repr(name) for name in book.sheet_names)
        raise MalformedDocumentError(f"No sheet named {sheet!r}. This workbook has: {available}.")
    return book.get_sheet_by_name(sheet)


def _render(value: Any) -> str:
    """Render a typed cell as the string a column would have been given as text.

    Calamine returns every number as a float, so an integer 3 arrives as 3.0.
    Passed on unchanged it would be rejected by any integer column, so whole
    numbers lose the decimal point here.
    """
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        return str(int(value)) if value.is_integer() else str(value)
    if isinstance(value, dt.datetime | dt.date | dt.time):
        return value.isoformat()
    return str(value)


def _decode(data: bytes) -> str:
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        pass

    detected = from_bytes(data).best()
    if detected is None:
        raise MalformedDocumentError("The file is not text in any encoding we could identify.")
    return str(detected)


def _sniff(text: str) -> str:
    try:
        return csv.Sniffer().sniff(text[:SNIFF_BYTES], delimiters=DELIMITERS).delimiter
    except csv.Error:
        # A single-column file has no delimiter to find, which is not an error.
        return ","


def _reject_duplicates(headers: tuple[str, ...]) -> None:
    seen: set[str] = set()
    for header in headers:
        key = header.casefold()
        if key in seen:
            raise MalformedDocumentError(f"Column {header!r} appears more than once in the header.")
        seen.add(key)


def _align(row: list[str], width: int) -> tuple[str, ...]:
    # A trailing delimiter produces one empty cell that carries no information.
    while len(row) > width and not row[-1].strip():
        row = row[:-1]

    if len(row) > width:
        raise MalformedDocumentError(f"A row has {len(row)} cells but the header declares {width}.")
    return tuple(row) + ("",) * (width - len(row))

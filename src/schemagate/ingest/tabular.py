import codecs
import csv
import datetime as dt
import io
import re
from dataclasses import dataclass
from typing import Any

from charset_normalizer import from_bytes
from python_calamine import CalamineError, CalamineWorkbook

from schemagate.errors import MalformedDocumentError
from schemagate.schema.spec import TableSchema

# Restricted on purpose. Left to guess freely, the sniffer will happily decide
# that a column of dates is colon-delimited.
DELIMITERS = ",;\t|"

SNIFF_BYTES = 8192

# A file with no consistent delimiter has one column. Parsing it with a
# character that cannot appear in text keeps quote handling while splitting
# nothing at all.
SINGLE_COLUMN = chr(0)

# Above 2**53 a float can no longer represent every integer, so expanding one
# to integer notation appends digits that were never in the file.
EXACT_INTEGER_LIMIT = 2**53

# Byte order marks are the only deterministic evidence of an encoding. UTF-32
# is checked first because its mark begins with the UTF-16 one.
BOMS = (
    (codecs.BOM_UTF32_LE, "utf-32"),
    (codecs.BOM_UTF32_BE, "utf-32"),
    (codecs.BOM_UTF8, "utf-8-sig"),
    (codecs.BOM_UTF16_LE, "utf-16"),
    (codecs.BOM_UTF16_BE, "utf-16"),
)

# Measured rather than guessed: a real detection scores coherence around 0.2 to
# 0.35, while a guess on a two-line file scores 0.00 and still returns an
# answer. Multi-byte encodings are self-validating like UTF-8, so heavy
# multi-byte use is evidence in its own right.
MIN_COHERENCE = 0.15
MIN_MULTI_BYTE_USAGE = 0.3

# Below this there are too few non-ASCII bytes for any statistical method to
# say anything, whatever score it reports.
MIN_DETECTION_BYTES = 32


@dataclass(frozen=True, slots=True)
class Table:
    """A grid of strings. Type coercion is the validation gate's job."""

    headers: tuple[str, ...]
    rows: tuple[tuple[str, ...], ...]


@dataclass(frozen=True, slots=True)
class Alignment:
    """A table's rows keyed by column name, and what did not line up.

    Both mismatch lists are reported rather than raised. A file with a spare
    column is still usable, and a caller can decide whether a missing column
    matters more than the rows that did parse.
    """

    rows: tuple[dict[str, str | None], ...]
    unmatched_headers: tuple[str, ...] = ()
    missing_columns: tuple[str, ...] = ()


def align(table: Table, schema: TableSchema) -> Alignment:
    """Key each row by column name, matching headers loosely.

    A header is matched by comparing it to the column name with case, spacing
    and punctuation removed, so `Invoice Number` and `invoice-number` both find
    `invoice_number`. Columns the database owns are never matched, because a
    file cannot supply an identity value.
    """
    columns = {_normalize(column.name): column.name for column in schema.extractable}

    positions: dict[str, int] = {}
    unmatched: list[str] = []
    for index, header in enumerate(table.headers):
        column = columns.get(_normalize(header))
        if column is None:
            unmatched.append(header)
        elif column in positions:
            raise MalformedDocumentError(
                f"Headers {table.headers[positions[column]]!r} and {header!r} both match "
                f"column {column!r}. Rename one of them."
            )
        else:
            positions[column] = index

    rows = tuple(
        {column: _cell(row[index]) for column, index in positions.items()} for row in table.rows
    )
    missing = tuple(name for name in columns.values() if name not in positions)
    return Alignment(rows=rows, unmatched_headers=tuple(unmatched), missing_columns=missing)


def _cell(value: str) -> str | None:
    """An empty cell means no value, which is null rather than an empty string.

    Left as `""` it would satisfy a NOT NULL text column and quietly write a
    blank where the file said nothing at all.
    """
    stripped = value.strip()
    return stripped or None


def _normalize(name: str) -> str:
    return re.sub(r"[^0-9a-z]+", "_", name.strip().casefold()).strip("_")


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

    Only up to 2**53. A workbook holding a 20-digit account number has already
    lost it, because the writing application rounded to a float before saving.
    Expanding that float to integer notation would invent the missing digits and
    hand the database a plausible, wrong identifier. Left in float form it fails
    coercion instead and gets reported, which is the honest outcome.
    """
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        if value.is_integer() and abs(value) <= EXACT_INTEGER_LIMIT:
            return str(int(value))
        return str(value)
    if isinstance(value, dt.datetime | dt.date | dt.time):
        return value.isoformat()
    return str(value)


def _decode(data: bytes) -> str:
    """Decode by evidence, and fall back rather than guess.

    Statistical detection needs enough text to work with. On a two-line file it
    reports no coherence at all and still returns an answer, which is how a
    German supplier name comes back in Arabic presentation forms.
    """
    for bom, encoding in BOMS:
        if data.startswith(bom):
            return data.decode(encoding)

    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        pass

    if len(data) >= MIN_DETECTION_BYTES:
        detected = from_bytes(data).best()
        if detected is not None and (
            detected.coherence >= MIN_COHERENCE or detected.multi_byte_usage >= MIN_MULTI_BYTE_USAGE
        ):
            return str(detected)

    # cp1252, not latin-1. Byte 0x80 is the euro sign in cp1252 and a control
    # character in latin-1, which matters for every invoice priced in euros.
    # latin-1 is the last resort only because it decodes any byte at all.
    try:
        return data.decode("cp1252")
    except UnicodeDecodeError:
        return data.decode("latin-1")


def _sniff(text: str) -> str:
    try:
        return csv.Sniffer().sniff(text[:SNIFF_BYTES], delimiters=DELIMITERS).delimiter
    except csv.Error:
        return _from_header(text)


def _from_header(text: str) -> str:
    """Choose a delimiter from the header row when the sniffer gives up.

    The sniffer abandons two different files: one genuine column whose values
    contain punctuation, and several columns whose rows are ragged. The header
    row tells them apart, because it is the one line that names columns rather
    than carrying data. No delimiter there means one column.
    """
    lines = text.splitlines()
    header = lines[0] if lines else ""
    counts = {delimiter: header.count(delimiter) for delimiter in DELIMITERS}
    best = max(counts, key=lambda delimiter: counts[delimiter])
    return best if counts[best] else SINGLE_COLUMN


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

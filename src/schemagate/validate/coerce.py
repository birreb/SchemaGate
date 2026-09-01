import datetime as dt
import json
import re
import uuid
from collections.abc import Mapping, Sequence
from decimal import Decimal, InvalidOperation
from typing import Any

from schemagate.schema.spec import ColumnSpec, TableSchema
from schemagate.validate.report import Failure

TRUE_WORDS = frozenset({"true", "t", "yes", "y", "1"})
FALSE_WORDS = frozenset({"false", "f", "no", "n", "0"})

# Symbols only. A separator must never appear here: stripping a dot would turn
# 1.234 into 1234 silently.
CURRENCY_SYMBOLS = frozenset("€$£¥₹₽₩")

INTEGER_TYPES = frozenset({"int2", "int4", "int8"})
FLOAT_TYPES = frozenset({"float4", "float8"})
EXACT_TYPES = frozenset({"numeric", "decimal"})
JSON_TYPES = frozenset({"json", "jsonb"})

# A separator followed by exactly three digits is either a thousands separator
# or a decimal point, and nothing in the string says which.
GROUP_SIZE = 3

# Spelled out rather than using %B or %b, which follow the machine's locale.
# On a Swedish host those expect Swedish month names and a document in English
# would be refused for a reason nobody could see.
MONTHS = {
    name: number
    for number, names in enumerate(
        [
            ("january", "jan"),
            ("february", "feb"),
            ("march", "mar"),
            ("april", "apr"),
            ("may",),
            ("june", "jun"),
            ("july", "jul"),
            ("august", "aug"),
            ("september", "sep", "sept"),
            ("october", "oct"),
            ("november", "nov"),
            ("december", "dec"),
        ],
        start=1,
    )
    for name in names
}

# A month name settles which number is the month, so these are safe to read. A
# date written only in numbers is not, and stays refused: 05/01/2026 is January
# or May depending on who typed it.
DAY_FIRST = re.compile(r"^(\d{1,2})\s+([A-Za-z]+)\.?,?\s+(\d{4})$")
MONTH_FIRST = re.compile(r"^([A-Za-z]+)\.?\s+(\d{1,2}),?\s+(\d{4})$")


class _AmbiguousNumberError(ValueError):
    """A number whose separator could be grouping or decimal, with no way to tell."""


def coerce_rows(
    rows: Sequence[Mapping[str, str | None]], schema: TableSchema
) -> tuple[tuple[dict[str, Any], ...], tuple[Failure, ...]]:
    """Turn the strings that came out of a file or a model into database types.

    A row that fails is reported, not dropped, and the rows around it still
    come back. The caller decides whether a single bad line is worth rejecting
    the whole document.
    """
    conventions = _conventions(rows, schema)

    coerced: list[dict[str, Any]] = []
    failures: list[Failure] = []

    for index, row in enumerate(rows):
        values: dict[str, Any] = {}
        for column in schema.extractable:
            text = row.get(column.name)
            value, failure = _coerce_cell(text, column, index, conventions.get(column.name))
            values[column.name] = value
            if failure is not None:
                failures.append(failure)
        coerced.append(values)

    return tuple(coerced), tuple(failures)


def _conventions(
    rows: Sequence[Mapping[str, str | None]], schema: TableSchema
) -> dict[str, str | None]:
    """Read each numeric column once to see which separator the file uses.

    Done before any conversion, because a value that is ambiguous on its own may
    be settled by a value further down the same column.
    """
    numeric = [
        column
        for column in schema.extractable
        if column.data_type in EXACT_TYPES | FLOAT_TYPES | INTEGER_TYPES
    ]
    return {
        column.name: _convention(
            [_strip(value)[0] for row in rows if isinstance(value := row.get(column.name), str)]
        )
        for column in numeric
    }


def _coerce_cell(
    text: str | None, column: ColumnSpec, row: int, convention: str | None = None
) -> tuple[Any, Failure | None]:
    if text is None:
        # A default satisfies NOT NULL on the database's side, so a document
        # that says nothing about such a column has not done anything wrong.
        if column.nullable or column.has_default:
            return None, None
        return None, Failure(
            row=row,
            column=column.name,
            rule="not_null",
            detail=f"Column {column.name!r} is NOT NULL but the document gave no value.",
        )

    try:
        return _convert(text, column, convention), None
    except _AmbiguousNumberError:
        return None, Failure(
            row=row,
            column=column.name,
            rule="ambiguous_number",
            detail=(
                f"{text!r} could be a thousands separator or a decimal point, and a wrong "
                f"reading is out by a factor of a thousand. Column {column.name!r} declares "
                f"no scale that settles it and no other value in this column does either. "
                f"Declaring the column as numeric(p,s) would resolve it."
            ),
            value=text,
        )
    except _NotALabelError as error:
        return None, Failure(
            row=row, column=column.name, rule="enum", detail=str(error), value=text
        )
    except (ValueError, ArithmeticError) as error:
        return None, Failure(
            row=row,
            column=column.name,
            rule="type",
            detail=f"{text!r} is not a valid {column.data_type}: {error}",
            value=text,
        )


class _NotALabelError(ValueError):
    """A value that is not one of the enum's labels."""


def _convert(text: str, column: ColumnSpec, convention: str | None = None) -> Any:
    if column.enum_labels:
        if text not in column.enum_labels:
            allowed = ", ".join(repr(label) for label in column.enum_labels)
            raise _NotALabelError(f"{text!r} is not one of: {allowed}.")
        return text

    if column.data_type.startswith("_"):
        element = ColumnSpec(
            name=column.name,
            data_type=column.data_type[1:],
            nullable=False,
            ordinal=column.ordinal,
            enum_labels=column.enum_labels,
            numeric_scale=column.numeric_scale,
        )
        return [_convert(item.strip(), element, convention) for item in _split_array(text)]

    return _convert_scalar(text, column.data_type, column.numeric_scale, convention)


def _convert_scalar(
    text: str, data_type: str, scale: int | None = None, convention: str | None = None
) -> Any:
    if data_type in EXACT_TYPES:
        return _to_decimal(text, scale, convention)
    if data_type in INTEGER_TYPES:
        return _to_int(text, scale, convention)
    if data_type in FLOAT_TYPES:
        return float(_normalise(_strip(text)[0], scale, convention))
    if data_type == "bool":
        return _to_bool(text)
    if data_type == "date":
        return _to_date(text)
    if data_type in {"timestamp", "timestamptz"}:
        return _to_datetime(text)
    if data_type in {"time", "timetz"}:
        return dt.time.fromisoformat(text.strip())
    if data_type in JSON_TYPES:
        return _to_json(text)
    if data_type == "uuid":
        return uuid.UUID(text.strip())
    return text


def _split_array(text: str) -> list[str]:
    inner = text.strip()
    if inner.startswith("{") and inner.endswith("}"):
        inner = inner[1:-1]
    return [item for item in inner.split(",") if item.strip()]


def _to_decimal(text: str, scale: int | None = None, convention: str | None = None) -> Decimal:
    body, negative = _strip(text)
    try:
        value = Decimal(_normalise(body, scale, convention))
    except InvalidOperation as error:
        raise ValueError("not a number") from error
    return -value if negative else value


def _to_int(text: str, scale: int | None = None, convention: str | None = None) -> int:
    body, negative = _strip(text)
    digits = _normalise(body, scale, convention)
    if not digits.isdigit():
        raise ValueError(
            "an integer column takes digits only, so a fraction or an exponent "
            "means the source already lost the exact value"
        )
    return -int(digits) if negative else int(digits)


def _to_date(text: str) -> dt.date:
    body = text.strip()
    try:
        return dt.date.fromisoformat(body)
    except ValueError:
        pass

    named = _from_month_name(body)
    if named is None:
        raise ValueError(
            "expected an ISO date like 2026-09-01, or one naming its month like "
            "'01 September 2026'. A date written only in numbers cannot be read "
            "safely, since 05/01/2026 is January or May depending on the writer"
        )
    return named


def _to_datetime(text: str) -> dt.datetime:
    body = text.strip()
    try:
        return dt.datetime.fromisoformat(body)
    except ValueError:
        pass

    # A named date may carry a time after it.
    head, _, tail = body.rpartition(" ")
    if ":" in tail:
        day = _from_month_name(head.strip())
        if day is not None:
            return dt.datetime.combine(day, dt.time.fromisoformat(tail))

    named = _from_month_name(body)
    if named is None:
        raise ValueError("expected an ISO timestamp, or a date naming its month")
    return dt.datetime.combine(named, dt.time())


def _from_month_name(body: str) -> dt.date | None:
    """Read a date whose month is written as a word, in any order."""
    for pattern, order in ((DAY_FIRST, "dmy"), (MONTH_FIRST, "mdy")):
        found = pattern.match(body)
        if not found:
            continue
        day, name, year = found.group(1, 2, 3) if order == "dmy" else found.group(2, 1, 3)
        month = MONTHS.get(name.casefold())
        if month is None:
            return None
        try:
            return dt.date(int(year), month, int(day))
        except ValueError:
            return None
    return None


def _to_json(text: str) -> Any:
    """Parse the JSON a model wrote into a string, and insist it is a container.

    Postgres would happily store a bare scalar in a json column, so nothing
    downstream would complain. It is almost always a mistake, and catching it
    here is the difference between noticing now and noticing in a query later.
    """
    try:
        value = json.loads(text)
    except json.JSONDecodeError as error:
        raise ValueError(f"not valid JSON: {error}") from error

    if not isinstance(value, dict | list):
        raise ValueError(f"a json column expects an object or an array, got {type(value).__name__}")
    return value


def _to_bool(text: str) -> bool:
    word = text.strip().casefold()
    if word in TRUE_WORDS:
        return True
    if word in FALSE_WORDS:
        return False
    raise ValueError(f"expected one of {sorted(TRUE_WORDS | FALSE_WORDS)}")


def _strip(text: str) -> tuple[str, bool]:
    """Remove presentation and report the sign.

    Accounting exports write a negative in parentheses, and currency symbols and
    thin spaces travel with the number rather than meaning anything.
    """
    body = text.strip()
    negative = False

    if body.startswith("(") and body.endswith(")"):
        negative = True
        body = body[1:-1]

    body = "".join(
        character
        for character in body
        if not character.isspace() and character not in CURRENCY_SYMBOLS
    )

    if body.startswith("-"):
        negative = not negative
        body = body[1:]
    elif body.startswith("+"):
        body = body[1:]

    return body, negative


def _normalise(body: str, scale: int | None = None, convention: str | None = None) -> str:
    """Resolve grouping and decimal separators into a plain decimal string.

    With both separators present the last one is the decimal point, which
    settles the common European and Anglo formats outright. A separator that
    repeats can only be grouping. What is left is a single separator followed by
    exactly three digits, which the string alone cannot decide, and which two
    other sources of truth are consulted for in turn.
    """
    dots = body.count(".")
    commas = body.count(",")

    if dots and commas:
        decimal_point = "." if body.rfind(".") > body.rfind(",") else ","
        grouping = "," if decimal_point == "." else "."
        return body.replace(grouping, "").replace(decimal_point, ".")

    separator = "." if dots else ("," if commas else "")
    if not separator:
        return body

    if (dots or commas) > 1:
        return body.replace(separator, "")

    head, _, tail = body.partition(separator)
    if len(tail) != GROUP_SIZE or not tail.isdigit():
        return f"{head}.{tail}"

    # First authority: the column. A column that holds fewer than three decimal
    # places cannot be holding three here, so the separator groups thousands.
    if scale is not None and scale < GROUP_SIZE:
        return head + tail

    # Second authority: the rest of the file, which may have written an
    # unambiguous value elsewhere in this same column.
    if convention is not None:
        return f"{head}.{tail}" if convention == separator else head + tail

    raise _AmbiguousNumberError(body)


def _evidence(body: str) -> str | None:
    """Which separator this value proves is the decimal point, if any."""
    dots = body.count(".")
    commas = body.count(",")

    if dots and commas:
        return "." if body.rfind(".") > body.rfind(",") else ","

    separator = "." if dots else ("," if commas else "")
    if not separator:
        return None

    if (dots or commas) > 1:
        # Repeated, so this separator groups thousands and the other one is
        # left as the only candidate for the decimal point.
        return "," if separator == "." else "."

    head, _, tail = body.partition(separator)
    del head
    if len(tail) == GROUP_SIZE and tail.isdigit():
        return None
    return separator


def _convention(values: Sequence[str]) -> str | None:
    """The decimal separator this column uses, when the column agrees with itself.

    Contradictory evidence returns None. A file that writes both 10,50 and
    9,876.50 in one column has not established a convention, and picking one
    would be the guess this whole path exists to avoid.
    """
    proven = {found for value in values if (found := _evidence(value))}
    return proven.pop() if len(proven) == 1 else None
